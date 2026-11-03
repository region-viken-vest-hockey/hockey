// GitHub issue reads for the in-session delivery skills (#962, #991). The
// product-authority gate and the repair ledger re-read an issue from GitHub
// rather than trusting a cached copy, and fail closed when GitHub does not
// answer with a complete issue.
import { spawnSync } from "node:child_process";

export const REPO = "niclas-lindgren/camping-booking";

// One issue with its comments, read fresh from GitHub.
export function fetchIssue(number, repo = REPO) {
  const args = ["issue", "view", String(number), "--repo", repo, "--json", "number,title,body,labels,comments,state"];
  const r = spawnSync("gh", args, { encoding: "utf8" });
  if (r.status !== 0) return { ok: false, error: (r.stderr ?? "").trim().split("\n")[0] || "gh issue view failed" };
  try {
    const issue = JSON.parse(r.stdout);
    return issue && typeof issue === "object" ? { ok: true, issue } : { ok: false, error: "gh returned unexpected JSON" };
  } catch {
    return { ok: false, error: "gh returned unparseable JSON" };
  }
}

// A reply that lacks what the comparison needs is not evidence. Missing fields
// must not read as "open", "no comments" or an equally empty baseline.
export function invalidIssue(live, number) {
  if (!live || typeof live !== "object") return "not an object";
  if (live.number !== number) return "wrong issue number";
  if (typeof live.state !== "string") return "missing state";
  if (typeof live.title !== "string" || typeof live.body !== "string") return "missing title or body";
  if (!Array.isArray(live.labels)) return "missing labels";
  if (!Array.isArray(live.comments)) return "missing comments";
  if (live.comments.some((c) => !c || typeof c.id !== "string" || typeof c.body !== "string")) return "malformed comment";
  return null;
}
