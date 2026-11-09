// Product-authority detector for the in-session skills. Public
// publication or rollback, promotion or reopening of the canonical season,
// and credentials or privacy need the founder's decision before
// they are committed. This module only DETECTS that such work is involved; it
// never decides that approval was given. An agent shares the owner's GitHub
// credentials, so a comment or label in the owner's name is not proof, and
// nothing here reads one as approval.
//
// Signals are fixed and version-controlled. Strong signals (a protected label,
// a protected path in the pull request, a protected word in the issue title)
// make the result REQUIRES_FOUNDER. Words only in the body or comments are weak:
// they are reported, but the result stays CLEAR, because an issue that merely
// mentions payments in a list of things it must not do is not a payments change.
// Whether a weak signal is a real decision is a judgment left to the skill, and
// so is a tenant-boundary change in an ordinary route, which no path pattern
// can tell apart from any other edit to it.

export const RESULTS = ["CLEAR", "FOUNDER_APPROVED", "REQUIRES_FOUNDER", "CHECK_UNAVAILABLE"];

const word = (stems) => new RegExp(`(?<![\\p{L}\\p{N}])(?:${stems.join("|")})`, "iu");

export const DOMAINS = [
  {
    id: "publication",
    labels: ["publication"],
    words: word(["publish(?:ing)?", "republish", "rollback", "roll back", "gh-pages", "github pages", "publiser", "publisering", "rull tilbake"]),
    paths: [/^\.github\/workflows\/season-(?:publish|rollback)\.yml$/, /^tournament_scheduler\/pipeline\/pages_publish/, /^tournament_scheduler\/publication\//],
  },
  {
    id: "canonical-season",
    labels: ["canonical-season"],
    words: word(["promot(?:e|ion) (?:the )?season", "reopen", "replan (?:the )?(?:published|sealed)", "seal(?:ed)? season", "published[- ]sealed"]),
    paths: [/^season\//],
  },
  {
    id: "credentials-privacy",
    labels: ["security", "privacy"],
    words: word(["credentials?", "secrets?", "tokens?", "mfa", "gdpr", "personal data", "personvern"]),
    paths: [/^\.gitleaks\.toml$/, /^docs\/security\.md$/],
  },
];

const labelNames = (issue) => (issue.labels ?? []).map((l) => String(typeof l === "string" ? l : l.name).toLowerCase());
const excerpt = (text, match) => {
  const start = Math.max(0, match.index - 30);
  return text.slice(start, match.index + match[0].length + 30).replace(/\s+/g, " ").trim();
};

/**
 * @param {{ issue: { title?: string, body?: string, labels?: any[], comments?: { body?: string }[] }, files?: string[] | null }} input
 * @returns {{ result: "CLEAR" | "REQUIRES_FOUNDER", domains: string[], strong: any[], weak: any[] }}
 */
export function classify({ issue, files = null }) {
  const labels = labelNames(issue);
  const strong = [];
  const weak = [];
  const bodyText = [issue.body ?? "", ...(issue.comments ?? []).map((c) => c.body ?? "")].join("\n");
  for (const domain of DOMAINS) {
    for (const label of domain.labels) if (labels.includes(label)) strong.push({ domain: domain.id, kind: "label", evidence: label });
    const title = domain.words.exec(issue.title ?? "");
    if (title) strong.push({ domain: domain.id, kind: "title", evidence: excerpt(issue.title ?? "", title) });
    for (const file of files ?? []) if (domain.paths.some((p) => p.test(file))) strong.push({ domain: domain.id, kind: "path", evidence: file });
    const body = domain.words.exec(bodyText);
    if (body) weak.push({ domain: domain.id, kind: "text", evidence: excerpt(bodyText, body) });
  }
  const domains = [...new Set(strong.map((s) => s.domain))];
  return { result: strong.length ? "REQUIRES_FOUNDER" : "CLEAR", domains, strong, weak };
}
