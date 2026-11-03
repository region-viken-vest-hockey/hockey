// `auth-check`: the in-session skills' product-authority gate. See
// authority.mjs for what is detected and why approval is never inferred.
import { classify } from "./authority.mjs";
import { invalidIssue } from "./selector.mjs";
import { approvedAuthorityDomains, getRun, isPaused, parkForFounder } from "./state.mjs";
import { worktreePathFor } from "./worktrees.mjs";
import { spawnSync } from "node:child_process";

// gh returns at most this many changed files; a PR at the limit may have more.
export const FILE_LIMIT = 100;

const git = (cwd, args) => {
  const r = spawnSync("git", args, { cwd, encoding: "utf8" });
  return r.status === 0 ? r.stdout.trim() : null;
};

/**
 * @param {import("node:sqlite").DatabaseSync} db
 */
export function authCheck(db, { repoRoot, stateDir, issue, phase, pr = null, who = null, fetchIssue, fetchPr, clock = () => Date.now(), headOf = (cwd) => git(cwd, ["rev-parse", "HEAD"]), dirtyIn = (cwd) => { const s = git(cwd, ["status", "--porcelain"]); return s === null ? true : s !== ""; } }) {
  const invalid = (error) => ({ code: 2, result: { ok: false, result: "INVALID_ARGS", error } });
  const unavailable = (code, extra = {}) => ({ code: 1, result: { ok: false, result: "CHECK_UNAVAILABLE", code, ...extra } });
  if (!["plan", "merge"].includes(phase)) return invalid("--phase must be plan or merge");
  if (phase === "merge" && !pr) return invalid("--pr is required for --phase merge");
  if (pr !== null && (!Number.isInteger(pr) || pr <= 0)) return invalid("--pr must be a positive integer");
  // The merge gate is for the session that holds the issue, so it must say who it is.
  // A verdict is only meaningful for the session that holds the issue lease, so
  // both phases must name it. An unleased read is not a plan authorization.
  if (!who) return invalid("--owner and --run are required for --phase plan and --phase merge");
  if (isPaused(stateDir)) return unavailable("PAUSED");
  // Any verdict, CLEAR included, is only for the lease holder, and the lease
  // is looked at again, on a fresh clock reading, after the GitHub calls, which
  // can outlast it.
  const holds = () => {
    const run = who ? getRun(db, issue) : null;
    return !who || (run && run.owner === who.owner && run.run_id === who.runId && run.lease_expires_at > clock());
  };
  if (!holds()) return unavailable("LEASE_LOST");

  let live;
  try {
    live = fetchIssue(issue);
  } catch (error) {
    live = { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
  if (!live.ok) return unavailable("GITHUB_UNAVAILABLE", { error: live.error });
  // Title, body, labels and every comment are the evidence; a payload missing
  // any of them could hide the very text that makes the issue protected.
  const bad = invalidIssue(live.issue, issue);
  if (bad) return unavailable("INVALID_ISSUE_PAYLOAD", { error: bad });
  if (String(live.issue.state).toUpperCase() !== "OPEN") return unavailable("ISSUE_NOT_OPEN", { state: live.issue.state });

  let files = null;
  let head = null;
  if (pr !== null) {
    let found;
    try {
      found = fetchPr(pr);
    } catch (error) {
      found = { ok: false, error: error instanceof Error ? error.message : String(error) };
    }
    if (!found.ok) return unavailable("GITHUB_UNAVAILABLE", { error: found.error });
    const p = found.pr;
    if (p.number !== pr || String(p.state).toUpperCase() !== "OPEN" || p.headRefName !== `agent/issue-${issue}` || p.isCrossRepository === true) {
      return unavailable("PR_MISMATCH", { pr, state: p.state ?? null, branch: p.headRefName ?? null, expected: `agent/issue-${issue}` });
    }
    // At merge the pull request must be the commit in the worktree, or the
    // paths checked are not the paths being merged.
    head = headOf(worktreePathFor(repoRoot, issue));
    if (phase === "merge" && (!head || head !== p.headRefOid)) return unavailable("PR_HEAD_MISMATCH", { pr, prHead: p.headRefOid ?? null, headSha: head });
    if (phase === "merge" && dirtyIn(worktreePathFor(repoRoot, issue))) return unavailable("DIRTY_WORKTREE", { headSha: head });
    if (!Array.isArray(p.files) || p.files.some((f) => typeof f?.path !== "string")) return unavailable("FILES_UNAVAILABLE");
    // A list cut off at the limit cannot show that no protected path is in it.
    if (p.files.length >= FILE_LIMIT) return unavailable("FILES_TRUNCATED", { count: p.files.length });
    files = p.files.map((f) => f.path);
  }

  if (!holds()) return unavailable("LEASE_LOST");
  const verdict = classify({ issue: live.issue, files });
  const body = { phase, issue, pr, headSha: head, domains: verdict.domains, strong: verdict.strong, weak: verdict.weak };
  if (verdict.result === "CLEAR") {
    return { code: 0, result: { ok: true, result: "CLEAR", ...body, note: "CLEAR means no protected-domain signal was detected. It is not founder approval, and weak signals still need a judgment." } };
  }
  const approvedDomains = approvedAuthorityDomains(db, { issue, runId: who.runId });
  if (verdict.domains.every((domain) => approvedDomains.includes(domain))) {
    return { code: 0, result: { ok: true, result: "FOUNDER_APPROVED", ...body, approvedDomains, note: "The user approved these protected domains in this active run. New protected domains require another approval." } };
  }
  if (!who) return { code: 1, result: { ok: false, result: "REQUIRES_FOUNDER", ...body, parked: false } };
  const run = getRun(db, issue);
  if (!run || run.owner !== who.owner || run.run_id !== who.runId) return { code: 1, result: { ok: false, result: "REQUIRES_FOUNDER", ...body, parked: false, code: "LEASE_LOST" } };
  const parked = parkForFounder(db, { ...who, phase, pr, headSha: head, detail: { domains: verdict.domains, strong: verdict.strong, weak: verdict.weak }, now: clock() });
  return { code: 1, result: { ok: false, result: "REQUIRES_FOUNDER", ...body, parked: parked.ok, ...(parked.ok ? { state: "PARKED", stopReason: "FOUNDER_APPROVAL_REQUIRED" } : { code: parked.code }) } };
}
