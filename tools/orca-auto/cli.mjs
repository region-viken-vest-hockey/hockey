// Coordination entry points for the in-session delivery skills:
// claim, renew, release, recover, pause, resume, auth-check, founder-resume,
// repair-record, repair-status, status.
// `orca-flow` (run from Claude Code via /goal or Pi via /skill:orca-flow) owns
// the implementation, review and merge; this CLI only holds the shared issue
// lease, its worktree, the product-authority gate and the repair ledger. Every
// command prints one JSON document and starts no worker.
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defaultStateDir, claimIssue, getRun, isPaused, openState, recoverExpired, releaseLease, renewLease, resumeFounderPark } from "./state.mjs";
import { fetchIssue as fetchOneIssue } from "./selector.mjs";
import { authCheck } from "./authcheck.mjs";
import { fetchPr as fetchOnePr, repairRecord, repairStatus } from "./ledger.mjs";
import { ensureWorktree } from "./worktrees.mjs";

export function parseFlags(argv) {
  const flags = {};
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (!arg.startsWith("--")) return { error: `unexpected argument ${arg}` };
    const value = argv[i + 1];
    if (value === undefined || value.startsWith("--")) return { error: `${arg} needs a value` };
    if (arg.slice(2) in flags) return { error: `${arg} given more than once` };
    flags[arg.slice(2)] = value;
    i += 1;
  }
  return { flags };
}

export function lease(flags) {
  const issue = Number(flags.issue);
  if (!Number.isInteger(issue) || issue <= 0) return { error: "--issue must be a positive integer" };
  if (!flags.owner || !flags.run) return { error: "--owner and --run are required" };
  return { issue, owner: flags.owner, runId: flags.run };
}

// A dispatch record (tools/orca-flow) in one of these states means an Orca worker may be writing in
// the issue worktree. `not-started` is a dispatch that never reached the worker.
// Only `not-started` (a dispatch that never reached the worker) is inactive; an unknown or malformed
// record is treated as a live worker, because the guard must fail closed.
const INACTIVE_DISPATCH_STATES = new Set(["not-started"]);

/**
 * The live-worker record that stops another session from taking an issue. The lease alone
 * is not enough: it expires after its TTL while a worker keeps running, and a session that then
 * claims the issue receives the very worktree the worker is writing in. The run that dispatched
 * the worker may reclaim its own lease. Everyone else waits for the operator to confirm the worker
 * is gone and remove the record, as the Orca coordinator runbook describes.
 */
function activeDispatchRecord(stateDir, { issue, owner, runId }) {
  const file = join(stateDir, "orca-dispatch", `issue-${issue}.json`);
  if (!existsSync(file)) return null;
  let record;
  try {
    record = JSON.parse(readFileSync(file, "utf8"));
  } catch {
    // An unreadable record is not evidence that no worker exists: treat it as active.
    return { file, record: { state: "unreadable" } };
  }
  if (record === null || typeof record !== "object" || Array.isArray(record)) return { file, record: { state: "malformed" } };
  if (INACTIVE_DISPATCH_STATES.has(record.state)) return null;
  if (record.owner === owner && record.runId === runId) return null;
  return { file, record };
}

// Claim, then prepare the issue worktree. The worktree is only touched after
// the lease is ours, and only a lease this call acquired may be rolled back: a
// repeated claim by the live holder must not drop the run that is still using it.
function claimWithWorktree(db, { who, ttlMs, now, repoRoot, stateDir, base, afterLeaseAcquired = () => {} }) {
  const refusal = (active) => {
    const { owner, runId, state } = active.record;
    return { code: 1, result: { ok: false, code: "DISPATCH_ACTIVE", recordFile: active.file, record: { owner, runId, state } } };
  };
  const first = activeDispatchRecord(stateDir, who);
  if (first) return refusal(first);
  const before = getRun(db, who.issue);
  const claimed = claimIssue(db, { ...who, ttlMs, now });
  if (!claimed.ok) return { code: 1, result: claimed };
  const acquired = !(before && before.owner === who.owner && before.run_id === who.runId && before.lease_expires_at > now);
  const rollback = () => {
    if (acquired) releaseLease(db, { ...who, now });
  };
  // Look again now that the lease is ours. A dispatch writes its record before it re-reads the lease
  // and starts the worker, so either the record is visible here, or the dispatching run's lease was
  // still ours until after it re-checked and it never starts the worker (LEASE_LOST_BEFORE_START).
  // Together that closes the window between the first look and the claim.
  afterLeaseAcquired();
  const second = activeDispatchRecord(stateDir, who);
  if (second) {
    rollback();
    return refusal(second);
  }
  if (isPaused(stateDir)) {
    rollback();
    return { code: 1, result: { ok: false, code: "PAUSED" } };
  }
  const tree = ensureWorktree(repoRoot, who.issue, base);
  if (!tree.ok) {
    rollback();
    return { code: 1, result: tree };
  }
  return { code: 0, result: { ok: true, lease: claimed.run, worktree: tree.path, branch: tree.branch, dirty: tree.dirty } };
}

export function runCommand(argv, { repoRoot, stateDir = defaultStateDir(repoRoot), now = Date.now(), fetchIssue = fetchOneIssue, fetchPr = fetchOnePr, clock = () => Date.now(), afterLeaseAcquired = () => {} }) {
  const [command, ...rest] = argv;
  const parsed = parseFlags(rest);
  if (parsed.error) return { code: 2, result: { ok: false, code: "INVALID_ARGS", error: parsed.error } };
  const { flags } = parsed;
  const paused = isPaused(stateDir);
  if (command === "pause" || command === "resume") {
    // Operator stop switch: while paused, claim and auth-check refuse to start work.
    mkdirSync(stateDir, { recursive: true });
    if (command === "pause") writeFileSync(join(stateDir, "PAUSED"), `${new Date(now).toISOString()}\n`);
    else rmSync(join(stateDir, "PAUSED"), { force: true });
    return { code: 0, result: { ok: true, paused: command === "pause" } };
  }
  const db = openState(join(stateDir, "state.db"));
  try {
    if (command === "status") {
      const runs = flags.issue
        ? [getRun(db, Number(flags.issue))].filter(Boolean)
        : db.prepare("SELECT * FROM runs ORDER BY issue").all();
      return { code: 0, result: { ok: true, paused, runs } };
    }
    if (command === "auth-check") {
      // Product-authority gate for the in-session skills; see authority.mjs.
      // With --owner and --run, a finding that needs the founder parks the issue
      // and releases that lease in one write.
      const unknown = Object.keys(flags).filter((k) => !["issue", "phase", "pr", "owner", "run"].includes(k));
      if (unknown.length) return { code: 2, result: { ok: false, result: "INVALID_ARGS", error: `unknown flag --${unknown[0]}` } };
      const issue = Number(flags.issue);
      if (!Number.isInteger(issue) || issue <= 0) return { code: 2, result: { ok: false, result: "INVALID_ARGS", error: "--issue must be a positive integer" } };
      if ((flags.owner === undefined) !== (flags.run === undefined)) return { code: 2, result: { ok: false, result: "INVALID_ARGS", error: "--owner and --run go together" } };
      const who = flags.owner === undefined ? null : { issue, owner: flags.owner, runId: flags.run };
      return authCheck(db, { repoRoot, stateDir, issue, phase: flags.phase, pr: flags.pr === undefined ? null : Number(flags.pr), who, fetchIssue, fetchPr, clock });
    }
    if (command === "recover") {
      // Expired leases are released so a stalled claim cannot block the issue.
      return { code: 0, result: { ok: true, recovered: recoverExpired(db, { now }) } };
    }
    if (command === "founder-resume") {
      // Called by orca-flow only after the user explicitly approves the latest
      // founder decision request in this active session.
      const unknown = Object.keys(flags).filter((k) => !["issue", "owner", "run", "ttl-min"].includes(k));
      if (unknown.length) return { code: 2, result: { ok: false, code: "INVALID_ARGS", error: `unknown flag --${unknown[0]}` } };
      const who = lease(flags);
      if (who.error) return { code: 2, result: { ok: false, code: "INVALID_ARGS", error: who.error } };
      const ttlMs = flags["ttl-min"] ? Number(flags["ttl-min"]) * 60_000 : undefined;
      if (ttlMs !== undefined && !(ttlMs > 0)) return { code: 2, result: { ok: false, code: "INVALID_ARGS", error: "--ttl-min must be positive" } };
      if (paused) return { code: 1, result: { ok: false, code: "PAUSED" } };
      const result = resumeFounderPark(db, { ...who, ttlMs, now });
      return { code: result.ok ? 0 : 1, result };
    }
    if (command === "repair-status") {
      const issue = Number(flags.issue);
      if (!Number.isInteger(issue) || issue <= 0) return { code: 2, result: { ok: false, code: "INVALID_ARGS", error: "--issue must be a positive integer" } };
      return { code: 0, result: { ok: true, issue, prs: repairStatus(db, { issue, pr: flags.pr === undefined ? null : Number(flags.pr) }) } };
    }
    if (command === "repair-record") {
      // The in-session skills' ledger: call it before each repair of review
      // findings, naming the reviewed head. See ledger.mjs.
      const unknown = Object.keys(flags).filter((k) => !["issue", "owner", "run", "pr", "head", "findings-file"].includes(k));
      if (unknown.length) return { code: 2, result: { ok: false, code: "INVALID_ARGS", error: `unknown flag --${unknown[0]}` } };
      const who = lease(flags);
      if (who.error) return { code: 2, result: { ok: false, code: "INVALID_ARGS", error: who.error } };
      return repairRecord(db, { repoRoot, stateDir, who, pr: Number(flags.pr), headSha: flags.head, findingsFile: flags["findings-file"], fetchIssue, fetchPr, now });
    }
    if (!["claim", "renew", "release"].includes(command)) {
      return { code: 2, result: { ok: false, code: "INVALID_ARGS", error: `unknown command ${command ?? ""}`.trim() } };
    }
    const who = lease(flags);
    if (who.error) return { code: 2, result: { ok: false, code: "INVALID_ARGS", error: who.error } };
    const ttlMs = flags["ttl-min"] ? Number(flags["ttl-min"]) * 60_000 : undefined;
    if (ttlMs !== undefined && !(ttlMs > 0)) {
      return { code: 2, result: { ok: false, code: "INVALID_ARGS", error: "--ttl-min must be positive" } };
    }
    if (command === "renew") {
      const r = renewLease(db, { ...who, ttlMs, now });
      return { code: r.ok ? 0 : 1, result: r };
    }
    if (command === "release") {
      const r = releaseLease(db, { ...who, now });
      return { code: r.ok ? 0 : 1, result: r };
    }
    // claim: a paused coordinator starts nothing.
    if (paused) return { code: 1, result: { ok: false, code: "PAUSED" } };
    return claimWithWorktree(db, { who, ttlMs, now, repoRoot, stateDir, base: flags.base ?? "main", afterLeaseAcquired });
  } finally {
    db.close();
  }
}

// Command-line entry. State and worktrees belong to the main checkout even when
// this script runs from an issue worktree, so the root is the common git dir.
if (import.meta.url === `file://${process.argv[1]}`) {
  const here = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
  const common = spawnSync("git", ["rev-parse", "--path-format=absolute", "--git-common-dir"], { cwd: here, encoding: "utf8" });
  const repoRoot = common.status === 0 ? dirname(common.stdout.trim()) : here;
  const { code, result } = runCommand(process.argv.slice(2), { repoRoot });
  process.stdout.write(`${JSON.stringify(result)}\n`);
  process.exitCode = code;
}
