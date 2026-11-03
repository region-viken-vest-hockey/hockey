// The in-session skills' repair ledger. A repair is counted per
// finding and per reviewed head: a retry on the same head is the same attempt,
// and a head with no new commit has no new review, so it cannot be promoted.
// These counts are separate from the headless controller's (state.mjs
// repair_findings), which the default skill path does not use. `orca-auto` and
// `orca-flow` run in a Claude or Pi conversation that can be reset or handed
// over, so "two repairs per finding" cannot live in that conversation. Before
// each repair the session records the findings it is about to fix against the
// exact reviewed head; the answer says whether the repair is allowed.
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { reviewFindingKeys } from "./findings.mjs";
import { REPO } from "./selector.mjs";
import { getRun, isPaused, ledgerFor, recordRepairAttempt } from "./state.mjs";
import { worktreePathFor } from "./worktrees.mjs";

const MAX_FINDINGS = 100;
const BLOCKED_LABEL = "status:blocked";

function git(cwd, args) {
  const r = spawnSync("git", args, { cwd, encoding: "utf8" });
  return r.status === 0 ? r.stdout.trim() : null;
}

// The pull request the caller names must be the issue's own: open, from the
// issue branch, and at the reviewed head. Without this, a new --pr number would
// start a fresh budget for the same branch.
export function fetchPr(number, repo = REPO) {
  const r = spawnSync("gh", ["pr", "view", String(number), "--repo", repo, "--json", "number,state,headRefName,headRefOid,isCrossRepository,files"], { encoding: "utf8" });
  if (r.status !== 0) return { ok: false, error: (r.stderr ?? "").trim().split("\n")[0] || "gh pr view failed" };
  try {
    const pr = JSON.parse(r.stdout);
    return pr && typeof pr === "object" ? { ok: true, pr } : { ok: false, error: "gh returned unexpected JSON" };
  } catch {
    return { ok: false, error: "gh returned unparseable JSON" };
  }
}

export function readFindings(path) {
  let parsed;
  try {
    parsed = JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return { error: "findings file is missing or not JSON" };
  }
  const list = Array.isArray(parsed) ? parsed : parsed?.findings;
  if (!Array.isArray(list) || list.length === 0 || list.length > MAX_FINDINGS) return { error: `findings must be a list of 1 to ${MAX_FINDINGS} entries` };
  const bad = list.find((f) => !f || typeof f !== "object" || !["P1", "P2", "P3"].includes(f.severity) || typeof f.file !== "string" || typeof f.summary !== "string" || !f.file || !f.summary);
  if (bad) return { error: "every finding needs severity P1|P2|P3, file and summary" };
  return { findings: list };
}

/**
 * @param {import("node:sqlite").DatabaseSync} db
 */
export function repairRecord(db, { repoRoot, stateDir, who, pr, headSha, findingsFile, fetchIssue, fetchPr: loadPr = fetchPr, now = Date.now(), headOf = (cwd) => git(cwd, ["rev-parse", "HEAD"]), dirtyIn = (cwd) => { const s = git(cwd, ["status", "--porcelain"]); return s === null ? true : s !== ""; } }) {
  const fail = (result) => ({ code: 1, result: { ok: false, ...result } });
  if (!Number.isInteger(pr) || pr <= 0) return { code: 2, result: { ok: false, code: "INVALID_ARGS", error: "--pr must be a positive integer" } };
  if (!/^[0-9a-f]{40}$/.test(headSha ?? "")) return { code: 2, result: { ok: false, code: "INVALID_ARGS", error: "--head must be a full 40-character commit id" } };
  const read = readFindings(findingsFile ?? "");
  if (read.error) return { code: 2, result: { ok: false, code: "INVALID_ARGS", error: read.error } };
  if (isPaused(stateDir)) return fail({ code: "PAUSED" });
  const run = getRun(db, who.issue);
  if (!run || run.owner !== who.owner || run.run_id !== who.runId || !(run.lease_expires_at > now)) return fail({ code: "LEASE_LOST" });
  // The evidence is the reviewed commit, so the tree must be that commit, whole.
  const cwd = worktreePathFor(repoRoot, who.issue);
  const current = headOf(cwd);
  if (!current) return fail({ code: "WORKTREE_UNAVAILABLE" });
  if (current !== headSha) return fail({ code: "STALE_HEAD", expected: headSha, headSha: current });
  if (dirtyIn(cwd)) return fail({ code: "DIRTY_WORKTREE", headSha });
  // The ledger is per pull request, so the pull request must be this issue's.
  let found;
  try {
    found = loadPr(pr);
  } catch (error) {
    found = { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
  if (!found.ok) return fail({ code: "GITHUB_UNAVAILABLE", error: found.error });
  const livePr = found.pr;
  if (livePr.number !== pr || String(livePr.state).toUpperCase() !== "OPEN" || livePr.headRefName !== `agent/issue-${who.issue}` || livePr.isCrossRepository === true) {
    return fail({ code: "PR_MISMATCH", pr, state: livePr.state ?? null, branch: livePr.headRefName ?? null, expected: `agent/issue-${who.issue}` });
  }
  if (livePr.headRefOid !== headSha) return fail({ code: "PR_HEAD_MISMATCH", pr, prHead: livePr.headRefOid ?? null, headSha });
  // The issue must still be there to work on.
  let live;
  try {
    live = fetchIssue(who.issue);
  } catch (error) {
    live = { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
  if (!live.ok) return fail({ code: "GITHUB_UNAVAILABLE", error: live.error });
  const labels = (live.issue.labels ?? []).map((l) => (typeof l === "string" ? l : l.name));
  if (String(live.issue.state).toUpperCase() !== "OPEN" || labels.includes(BLOCKED_LABEL)) return fail({ code: "ISSUE_BLOCKED", state: live.issue.state, blocked: labels.includes(BLOCKED_LABEL) });
  const keys = reviewFindingKeys({ findings: read.findings });
  const recorded = recordRepairAttempt(db, { ...who, pr, headSha, keys, now });
  if (!recorded.ok) return fail({ ...recorded, pr, headSha });
  return { code: 0, result: { ok: true, action: recorded.already ? "ALREADY_RECORDED" : "RECORDED", pr, headSha, counts: recorded.counts } };
}

export function repairStatus(db, { issue, pr = null }) {
  const rows = ledgerFor(db, { issue, pr });
  const byPr = {};
  for (const r of rows) {
    const entry = (byPr[r.pr] ??= { findings: {}, heads: new Set() });
    (entry.findings[r.key] ??= []).push(r.head_sha);
    entry.heads.add(r.head_sha);
  }
  return Object.fromEntries(Object.entries(byPr).map(([n, e]) => [n, { repairs: e.heads.size, findings: Object.fromEntries(Object.entries(e.findings).map(([k, h]) => [k, { attempts: h.length, heads: h }])) }]));
}
