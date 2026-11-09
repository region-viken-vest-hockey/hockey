// Coordinator-side final-head verification; separate from the implementation worker.
// This is a cooperative Orca procedure, not GitHub branch protection.
import { spawnSync } from "node:child_process";
import { worktreePathFor, branchFor } from "../orca-auto/worktrees.mjs";

/**
 * @typedef {{status: number | null, stdout: string, stderr: string}} ExecResult
 * @typedef {(command: string, args: string[], cwd: string) => ExecResult} Exec
 */
/** @type {Exec} */
const defaultExec = (command, args, cwd) => spawnSync(command, args, {
  cwd, encoding: "utf8", maxBuffer: 32 * 1024 * 1024,
});

function refuse(code, detail) {
  return { ok: false, code, ...(detail ? { detail } : {}) };
}
function git(exec, cwd, args) {
  const result = exec("git", args, cwd);
  return result.status === 0 ? (result.stdout ?? "").trim() : null;
}
function snapshot(exec, root, worktree, issue, pr) {
  const result = exec("gh", ["pr", "view", String(pr), "--json",
    "number,state,isDraft,baseRefName,headRefName,headRefOid,url"], root);
  if (result.status !== 0) return refuse("PR_UNREADABLE", result.stderr?.trim());
  let remote;
  try { remote = JSON.parse(result.stdout); }
  catch { return refuse("PR_UNREADABLE", "Invalid GitHub JSON response"); }
  if (remote.number !== pr || remote.state !== "OPEN" || remote.isDraft !== false
      || remote.baseRefName !== "main" || remote.headRefName !== branchFor(issue)
      || !/^[0-9a-f]{40}$/.test(remote.headRefOid ?? "")) {
    return refuse("PR_NOT_ELIGIBLE", "Expected open, non-draft PR for this issue targeting main");
  }
  const branch = git(exec, worktree, ["branch", "--show-current"]);
  const head = git(exec, worktree, ["rev-parse", "HEAD"]);
  const status = git(exec, worktree, ["status", "--porcelain", "--untracked-files=all"]);
  // `gh pr view --json baseRefOid` is missing in older gh releases. Read the base commit from the
  // GitHub API for the repository named by the PR's own URL, not from the configurable `origin`.
  const repo = /^https:\/\/github\.com\/([\w.-]+\/[\w.-]+)\/pull\/\d+$/.exec(remote.url ?? "")?.[1];
  if (!repo) return refuse("PR_UNREADABLE", "Could not determine the PR repository from its URL");
  const ref = exec("gh", ["api", `repos/${repo}/git/ref/heads/${remote.baseRefName}`, "--jq", ".object.sha"], root);
  const base = ref.status === 0 ? (ref.stdout ?? "").trim() : "";
  if (!/^[0-9a-f]{40}$/.test(base)) return refuse("PR_UNREADABLE", "Could not read the base branch commit from GitHub");
  if (branch === null || head === null || status === null) return refuse("WORKTREE_UNREADABLE");
  if (branch !== branchFor(issue) || head !== remote.headRefOid)
    return refuse("STALE_HEAD", "Local branch/head does not match the GitHub PR head");
  if (status !== "") return refuse("DIRTY_WORKTREE", "The issue worktree must be clean");
  return { ok: true, head, base };
}

/** @param {{repoRoot: string, issue: number, pr: number, exec?: Exec}} options */
export function verifyHead({ repoRoot, issue, pr, exec = defaultExec }) {
  const worktree = worktreePathFor(repoRoot, issue);
  const before = snapshot(exec, repoRoot, worktree, issue, pr);
  if (!before.ok) return before;
  const fetched = exec("git", ["fetch", "origin", "main"], worktree);
  if (fetched.status !== 0) return refuse("BASE_FETCH_FAILED", fetched.stderr?.trim());
  if (git(exec, worktree, ["rev-parse", "refs/remotes/origin/main"]) !== before.base)
    return refuse("STALE_BASE", "The local main tracking ref does not match the current PR base");
  const result = exec("make", ["check"], worktree);
  if (result.status !== 0) {
    const output = (result.stdout ?? "") + "\n" + (result.stderr ?? "");
    return refuse("VERIFICATION_FAILED",
      "make check failed (" + (result.status ?? "unknown exit") + "): " + output.slice(-3000));
  }
  const after = snapshot(exec, repoRoot, worktree, issue, pr);
  if (!after.ok) return after;
  if (before.head !== after.head || before.base !== after.base)
    return refuse("PR_CHANGED_DURING_VERIFICATION", "Head or base changed; rerun the gate");
  return { ok: true, issue, pr, head: after.head, base: after.base, verified: "make check" };
}
