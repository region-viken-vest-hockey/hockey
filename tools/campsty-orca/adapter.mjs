// Campsty Orca coordinator adapter (#1059).
//
// One Orca runtime owns dispatch and worker completion. This module builds the
// Orca orchestration calls and the self-contained Task spec for one issue, and
// starts no loop, schedule or controller. The issue lease and worktree stay with
// `npm run auto` (#991) as the compatibility guard; review, merge and issue
// closure stay with the coordinating session running `orca-flow`.
import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { branchFor, worktreePathFor } from "../campsty-auto/worktrees.mjs";

const TERMINAL_STATES = ["COMPLETE", "PARKED", "FAILED"];
const PERMITTED_AUTHORITY = ["CLEAR", "FOUNDER_APPROVED"];

export function buildTaskSpec({ issue, title, branch, worktree }) {
  return [
    `Target: issue #${issue} "${title}", in the existing worktree ${worktree} on branch ${branch}.`,
    `Change: implement the acceptance criteria of issue #${issue} (read them with \`gh issue view ${issue} --comments\`), following docs/agents/workflow.md, repository.md and testing.md.`,
    "Constraints: edit only inside this worktree, never the repository root. Do not run `npm run auto -- claim`, `renew`, `release` or `founder-resume`; the coordinator alone manages the lease. Do not push, open a pull request, merge or close the issue. Do not add a model or vendor Co-Authored-By trailer. Stage files by explicit path. Leave unrelated files alone, including an uncommitted root mise.toml. The worktree boundary is an instruction, not a filesystem guard: do not write outside it.",
    "Ownership: you alone edit this worktree while the task runs.",
    "Before adding domain methods, search for existing equivalent implementations and reuse where contracts match; repair new complexity or duplication violations rather than weakening the quality gate.",
    `Observable acceptance: \`mise run check\` (including \`npm run quality:check\`) exits 0 in this worktree; the work is committed on ${branch} with a conventional prefix; \`git status --short\` is empty. Then send worker_done with --outcome succeeded and --files-modified listing the changed paths. If you cannot reach that state, send worker_done with --outcome failed and the failing command with its output summary.`,
  ].join("\n");
}

export function buildDispatchPlan({ issue, title, owner, runId, repoRoot, stateDir, agent = "claude", orca = "orca" }) {
  const worktree = worktreePathFor(repoRoot, issue);
  const branch = branchFor(issue);
  const spec = buildTaskSpec({ issue, title, branch, worktree });
  const auto = ["node", join(repoRoot, "tools", "campsty-auto", "cli.mjs")];
  return {
    issue,
    owner,
    runId,
    agent,
    worktree,
    branch,
    // One durable record per issue. Its exclusive create makes a second dispatch
    // for the same issue fail; a start that is not confirmed leaves it behind.
    recordFile: join(stateDir, "orca-dispatch", `issue-${issue}.json`),
    commands: {
      leaseStatus: [...auto, "status", "--issue", String(issue)],
      authCheck: [...auto, "auth-check", "--issue", String(issue), "--phase", "plan", "--owner", owner, "--run", runId],
      status: [orca, "status", "--json"],
      worktreeShow: [orca, "worktree", "show", "--worktree", `path:${worktree}`, "--json"],
      runCreate: [orca, "orchestration", "run-create", "--objective", `Deliver issue #${issue}: ${title}`, "--json"],
      workerStart: [orca, "orchestration", "worker-start", "--spec", spec, "--worktree", `path:${worktree}`, "--agent", agent, "--json"],
    },
  };
}

// The adapter refuses to dispatch unless this exact owner and run hold an
// unexpired, non-terminal lease. Anything else means another run owns the issue.
/** @param {Array<Record<string, any>>} runs */
export function leaseHeldBy(runs, { issue, owner, runId, now }) {
  const lease = runs.find((run) => run.issue === issue);
  return Boolean(
    lease &&
      lease.owner === owner &&
      lease.run_id === runId &&
      lease.lease_expires_at > now &&
      !TERMINAL_STATES.includes(lease.state),
  );
}

// Reads `orca status --json`, where readiness sits under result.runtime.
export function assertRuntimeReady(status) {
  return status?.result?.runtime?.reachable === true
    ? { ok: true }
    : { ok: false, code: "ORCA_UNREACHABLE", error: "Orca runtime is not reachable; run `orca open` or start the app." };
}

// Only CLEAR and FOUNDER_APPROVED permit dispatch. auth-check itself parks and
// releases the lease on REQUIRES_FOUNDER, so a refusal here needs no cleanup.
export function assertAuthorityPermits(authResult) {
  return PERMITTED_AUTHORITY.includes(authResult?.result)
    ? { ok: true }
    : { ok: false, code: "AUTHORITY_NOT_PERMITTED", verdict: authResult?.result ?? null };
}

/**
 * @typedef {{ status: number | null, stdout: string, stderr: string }} ExecResult
 * @typedef {(command: string, args: string[], cwd: string) => ExecResult} Exec
 */

/** @type {Exec} */
const defaultExec = (command, args, cwd) => spawnSync(command, args, { cwd, encoding: "utf8" });

const sleepArray = new Int32Array(new SharedArrayBuffer(4));
const defaultSleep = (ms) => Atomics.wait(sleepArray, 0, 0, ms);

function parseJson(text) {
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch {
    return { ok: false, error: "non-JSON output" };
  }
}

function readRecord(file) {
  try {
    return JSON.parse(readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}

function writeRecord(plan, state, detail = {}) {
  writeFileSync(plan.recordFile, `${JSON.stringify({ issue: plan.issue, runId: plan.runId, owner: plan.owner, state, ...detail })}\n`);
}

// Writes the record exclusively: a second dispatch for the same issue finds it
// and stops. Later state changes overwrite it through writeRecord.
function claimRecord(plan) {
  mkdirSync(join(plan.recordFile, ".."), { recursive: true });
  try {
    writeFileSync(plan.recordFile, `${JSON.stringify({ issue: plan.issue, runId: plan.runId, owner: plan.owner, state: "starting" })}\n`, { flag: "wx" });
    return { ok: true };
  } catch (error) {
    if (error?.code !== "EEXIST") throw error;
    return { ok: false, code: "DISPATCH_RECORD_EXISTS", recordFile: plan.recordFile, existing: readRecord(plan.recordFile) };
  }
}

// Positive contract for Orca's JSON envelope: ok must be true. worker-start must
// also report state "ready". A live pilot (#1047) returned state "ready" with stage
// "input_accepted" at exit 0, so the stage is not the success signal.
function isConfirmedReceipt(parsed, { state } = {}) {
  if (!parsed.ok || parsed.value?.ok !== true) return false;
  return state === undefined || (parsed.value.result?.state ?? parsed.value.state) === state;
}

function resolvedWorktreeSelector(parsed, expectedPath) {
  const worktree = parsed.ok && parsed.value?.ok === true ? parsed.value.result?.worktree : null;
  return typeof worktree?.id === "string" && worktree.path === expectedPath ? `id:${worktree.id}` : null;
}

function commandWithWorktreeSelector(command, selector) {
  const resolved = [...command];
  const flag = resolved.indexOf("--worktree");
  if (flag === -1 || flag === resolved.length - 1) throw new Error("worker-start command has no --worktree selector");
  resolved[flag + 1] = selector;
  return resolved;
}

// Runs the dispatch in order and stops at the first problem. Refusals before the
// record is claimed leave nothing behind. Once worker-start is attempted, any
// outcome that is not a confirmed start is recorded as "unconfirmed" and is never
// retried here: the operator reconciles with Orca and then removes the record.
/**
 * @param {ReturnType<typeof buildDispatchPlan>} plan
 * @param {{ exec?: Exec, cwd?: string, readLeaseRuns: () => Array<Record<string, any>>, now?: () => number, sleep?: (ms: number) => void, worktreeResolveAttempts?: number }} options
 */
export function dispatch(
  plan,
  { exec = defaultExec, cwd = plan.worktree, readLeaseRuns, now = Date.now, sleep = defaultSleep, worktreeResolveAttempts = 30 },
) {
  const steps = [];
  const call = (name, [command, ...args], dir) => {
    const result = exec(command, args, dir);
    const parsed = parseJson(result.stdout ?? "");
    steps.push({ step: name, exitCode: result.status, output: parsed.ok ? parsed.value : parsed.error });
    return { result, parsed };
  };
  const heldNow = () => leaseHeldBy(readLeaseRuns(), { issue: plan.issue, owner: plan.owner, runId: plan.runId, now: now() });

  if (!heldNow()) return { ok: false, code: "LEASE_NOT_HELD", issue: plan.issue };

  const auth = call("authCheck", plan.commands.authCheck, cwd);
  const authority = assertAuthorityPermits(auth.parsed.ok ? auth.parsed.value : null);
  if (auth.result.status !== 0 || !authority.ok) {
    return { ok: false, code: "AUTHORITY_NOT_PERMITTED", verdict: auth.parsed.value?.result ?? null, steps };
  }

  const status = call("status", plan.commands.status, cwd);
  const ready = status.parsed.ok ? assertRuntimeReady(status.parsed.value) : { ok: false, code: "ORCA_UNREACHABLE" };
  if (status.result.status !== 0 || !ready.ok) return { ok: false, code: ready.code ?? "ORCA_UNREACHABLE", steps };

  // A newly-created git worktree can briefly exist on disk before Orca's runtime
  // discovers it. Resolve it before creating either the durable dispatch record
  // or the Run, and use Orca's stable id for worker-start rather than re-resolving
  // the path after those side effects.
  let worktreeSelector = null;
  const attempts = Math.max(1, worktreeResolveAttempts);
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const shown = call("worktreeShow", plan.commands.worktreeShow, cwd);
    worktreeSelector = resolvedWorktreeSelector(shown.parsed, plan.worktree);
    if (shown.result.status === 0 && worktreeSelector) break;
    const errorCode = shown.parsed.ok ? shown.parsed.value?.error?.code : null;
    if (errorCode !== "selector_not_found" || attempt === attempts - 1) break;
    if (!heldNow()) return { ok: false, code: "LEASE_LOST_DURING_WORKTREE_RESOLUTION", worktree: plan.worktree, steps };
    sleep(1_000);
    if (!heldNow()) return { ok: false, code: "LEASE_LOST_DURING_WORKTREE_RESOLUTION", worktree: plan.worktree, steps };
  }
  if (!worktreeSelector) return { ok: false, code: "WORKTREE_UNRESOLVED", worktree: plan.worktree, steps };
  if (!heldNow()) return { ok: false, code: "LEASE_LOST_DURING_WORKTREE_RESOLUTION", worktree: plan.worktree, steps };

  const claim = claimRecord(plan);
  if (!claim.ok) return { ok: false, code: claim.code, recordFile: claim.recordFile, existing: claim.existing, steps };

  const runCreate = call("runCreate", plan.commands.runCreate, cwd);
  if (runCreate.result.status !== 0 || !isConfirmedReceipt(runCreate.parsed)) {
    writeRecord(plan, "unconfirmed", { failedStep: "runCreate" });
    return { ok: false, code: runCreate.result.status !== 0 ? "RUNCREATE_FAILED" : "RUNCREATE_UNVERIFIED", steps };
  }

  // Re-read the lease immediately before the worker starts, so a lease lost during
  // the calls above stops the dispatch. A narrow race remains; the runbook records it.
  if (!heldNow()) {
    writeRecord(plan, "not-started", { reason: "LEASE_LOST_BEFORE_START" });
    return { ok: false, code: "LEASE_LOST_BEFORE_START", steps };
  }

  const start = call("workerStart", commandWithWorktreeSelector(plan.commands.workerStart, worktreeSelector), cwd);
  if (start.result.status !== 0 || !isConfirmedReceipt(start.parsed, { state: "ready" })) {
    const body = start.parsed.ok ? start.parsed.value : {};
    const failedStage = body.failedStage ?? body.result?.failedStage ?? null;
    const failureCode = body.error?.code ?? null;
    if (start.result.status !== 0 && failureCode === "selector_not_found") {
      const detail = body.error?.data ?? {};
      writeRecord(plan, "not-started", {
        failedStep: "workerStart",
        failureCode,
        orchestrationRequestId: detail.orchestrationRequestId ?? null,
        selector: detail.selector ?? worktreeSelector,
      });
      return { ok: false, code: "WORKTREE_SELECTOR_NOT_FOUND", steps };
    }
    writeRecord(plan, "unconfirmed", { failedStep: "workerStart", failedStage, failureCode });
    return { ok: false, code: start.result.status !== 0 ? "WORKER_START_FAILED" : "WORKER_START_UNVERIFIED", steps };
  }
  writeRecord(plan, "started", { dispatchReceipt: start.parsed.value });
  return { ok: true, issue: plan.issue, worktree: plan.worktree, steps };
}
