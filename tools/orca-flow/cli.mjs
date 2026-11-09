// Entry point for the Orca coordinator adapter.
//   npm run orca -- plan     --issue <n> --owner <name> --run <uuid> [--agent claude]
//   npm run orca -- dispatch --issue <n> --owner <name> --run <uuid> [--agent claude]
//   npm run orca -- verify-head --issue <n> --pr <pr> --owner <name> --run <uuid>
// `plan` prints the Orca calls and starts nothing. `dispatch` requires the issue
// lease to be held by --owner/--run (`npm run auto -- claim` first), then starts
// one supervised worker in the issue worktree. It prints one JSON document.
//   npm run orca -- tick     one single-shot dispatcher wake (disabled until .local/auto/dispatcher.json enables it)
//   npm run orca -- status   last dispatcher report, enabled and paused state
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defaultStateDir } from "../orca-auto/state.mjs";
import { buildDispatchPlan, dispatch } from "./adapter.mjs";
import { readStatus, tick } from "./dispatcher.mjs";
import { verifyHead } from "./verify-head.mjs";
import { cleanupIssue } from "./cleanup.mjs";
import { reviewDelete, reviewOpen, reviewRead, reviewSend, reviewWait } from "./review-browser.mjs";

export function parseArgs(argv) {
  const [command, ...rest] = argv;
  if (["tick", "status", "review-open"].includes(command)) return rest.length ? { error: `${command} takes no flags` } : { command };
  const flags = {};
  for (let i = 0; i < rest.length; i += 2) {
    const key = rest[i];
    const value = rest[i + 1];
    if (!key?.startsWith("--") || value === undefined || value.startsWith("--")) {
      return { error: `expected --flag value pairs, got ${key ?? "nothing"}` };
    }
    flags[key.slice(2)] = value;
  }
  if (command === "cleanup") {
    const n = Number(flags.issue);
    return Number.isInteger(n) && n > 0 ? { command, issue: n } : { error: "--issue must be a positive integer" };
  }
  if (["review-send", "review-read", "review-wait", "review-delete"].includes(command)) {
    if (!flags.page) return { error: `${command} requires --page` };
    if (["review-send", "review-delete"].includes(command) && !flags["message-file"]) return { error: `${command} requires --message-file` };
    if (flags["project-id"] && command !== "review-delete") {
      return { error: "--project-id is only supported by review-delete" };
    }
    return {
      command,
      pageId: flags.page,
      messageFile: flags["message-file"],
      ...(command === "review-delete" && flags["project-id"] ? { projectId: flags["project-id"] } : {}),
    };
  }
  const issue = Number(flags.issue);
  if (!["plan", "dispatch", "verify-head"].includes(command)) return { error: `unknown command ${command ?? ""}`.trim() };
  if (!Number.isInteger(issue) || issue <= 0) return { error: "--issue must be a positive integer" };
  if (!flags.owner || !flags.run) return { error: "--owner and --run are required" };
  if (command === "verify-head" && (!Number.isInteger(Number(flags.pr)) || Number(flags.pr) <= 0)) return { error: "--pr must be a positive integer" };
  return { command, issue, owner: flags.owner, runId: flags.run, agent: flags.agent ?? "claude", ...(command === "verify-head" ? { pr: Number(flags.pr) } : {}) };
}

function run(cmd, args, cwd) {
  return spawnSync(cmd, args, { cwd, encoding: "utf8" });
}

/**
 * @param {string[]} argv
 * @param {{ repoRoot: string, exec?: (command: string, args: string[], cwd: string) => { status: number | null, stdout: string, stderr: string }, now?: () => number }} options
 */
export function main(argv, { repoRoot, exec = run, now = () => Date.now() }) {
  const parsed = parseArgs(argv);
  if (parsed.error) return { code: 2, result: { ok: false, code: "INVALID_ARGS", error: parsed.error } };
  const { command, issue, owner, runId, agent } = parsed;
  if (command === "cleanup") {
    const receipt = cleanupIssue({ repoRoot, issue: parsed.issue, exec });
    return { code: receipt.ok ? 0 : 1, result: receipt };
  }
  if (command.startsWith("review-")) {
    const base = { repoRoot, exec };
    const receipt = command === "review-open" ? reviewOpen(base)
      : command === "review-send" ? reviewSend({ ...base, pageId: parsed.pageId, message: readFileSync(parsed.messageFile, "utf8").trim() })
      : command === "review-delete" ? reviewDelete({ ...base, pageId: parsed.pageId, firstMessage: readFileSync(parsed.messageFile, "utf8"), projectId: parsed.projectId })
      : command === "review-read" ? reviewRead({ ...base, pageId: parsed.pageId })
      : reviewWait({ ...base, pageId: parsed.pageId });
    return { code: receipt.ok ? 0 : 1, result: receipt };
  }
  if (command === "status") return { code: 0, result: readStatus(defaultStateDir(repoRoot)) };
  if (command === "tick") {
    const report = tick({ repoRoot, stateDir: defaultStateDir(repoRoot), exec, now });
    return { code: report.ok ? 0 : 1, result: report };
  }
  if (command === "verify-head") {
    const receipt = verifyHead({ repoRoot, issue, pr: parsed.pr, exec });
    return { code: receipt.ok ? 0 : 1, result: receipt };
  }
  const titleResult = exec("gh", ["issue", "view", String(issue), "--json", "title", "--jq", ".title"], repoRoot);
  if (titleResult.status !== 0) {
    return { code: 1, result: { ok: false, code: "ISSUE_UNREADABLE", error: titleResult.stderr?.trim() } };
  }
  const stateDir = defaultStateDir(repoRoot);
  const plan = buildDispatchPlan({ issue, title: titleResult.stdout.trim(), owner, runId, repoRoot, stateDir, agent });
  if (command === "plan") return { code: 0, result: { ok: true, plan } };
  // Each lease read is fresh: the store, not a snapshot from earlier, decides.
  const readLeaseRuns = () => {
    const lease = exec(plan.commands.leaseStatus[0], plan.commands.leaseStatus.slice(1), repoRoot);
    if (lease.status !== 0) return [];
    try {
      return JSON.parse(lease.stdout).runs ?? [];
    } catch {
      return [];
    }
  };
  // Authority and Orca calls run from the issue worktree; the auto CLI resolves
  // the main checkout itself, so the cwd does not change which state it reads.
  const receipt = dispatch(plan, { exec, cwd: plan.worktree, readLeaseRuns, now });
  return { code: receipt.ok ? 0 : 1, result: receipt };
}

// Command-line entry. Worktrees and lease state belong to the main checkout even
// when this runs from an issue worktree, so the root is the common git dir.
if (import.meta.url === `file://${process.argv[1]}`) {
  const here = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
  const common = spawnSync("git", ["rev-parse", "--path-format=absolute", "--git-common-dir"], { cwd: here, encoding: "utf8" });
  const repoRoot = common.status === 0 ? dirname(common.stdout.trim()) : here;
  const { code, result } = main(process.argv.slice(2), { repoRoot });
  process.stdout.write(`${JSON.stringify(result)}\n`);
  process.exitCode = code;
}
