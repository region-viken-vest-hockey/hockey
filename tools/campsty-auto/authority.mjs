// Product-authority detector for the in-session skills (#962, S6). Pricing,
// fees, refunds, payments, identity and tenant boundaries, privacy and legal
// obligations and irreversible data changes need the founder's decision before
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
    id: "pricing-fees-refunds",
    labels: ["pricing"],
    words: word(["pric(?:e|es|ing)", "fees?", "refund", "cancellation polic", "cancellation rule", "pris(?:er|sättning)?(?![\\p{L}])", "avgift", "återbetal", "avbokningsregl", "avbokningspolicy"]),
    paths: [/^src\/(?:lib|app\/api)\/.*(?:pricing|cancel|refund)/i],
  },
  {
    id: "payments",
    labels: ["payments", "area:payments"],
    words: word(["payments?", "stripe", "checkout", "payouts?", "invoice", "betalning", "utbetalning", "faktura"]),
    paths: [/^src\/lib\/(?:payments|billing-documents)\//, /^src\/lib\/[\w-]*(?:payment|payout|stripe|checkout)/i, /^src\/app\/api\/(?:stripe|webhooks|checkout)\//, /^src\/app\/\[locale\]\/(?:admin\/(?:payments|billing)|owner\/payouts)\//],
  },
  {
    id: "identity-tenant",
    labels: ["area:auth"],
    words: word(["authenticat", "authoriz", "authoris", "permissions?", "tenants?", "sign-?in", "login", "behörighet", "inloggning", "hyresgäst"]),
    paths: [
      /^src\/proxy[\w-]*\.ts$/,
      /^src\/lib\/(?:auth[\w-]*|require-auth|access-grants[\w-]*|guest-booking-access|roles|permissions?)(?:\.|\/)/,
      /^src\/app\/api\/(?:auth|admin\/(?:access-grants|impersonate))\//,
      /^src\/app\/\[locale\]\/(?:auth|admin\/access-grants)\//,
    ],
  },
  {
    id: "privacy-legal",
    labels: ["compliance", "privacy"],
    words: word(["privacy", "gdpr", "personal data", "consent", "legal", "terms of", "data processing", "integritet", "samtycke", "personuppgift", "villkor"]),
    paths: [
      /^src\/lib\/(?:privacy|legal|dsar|breach)\//,
      /^src\/lib\/cookie-consent\./,
      /^src\/app\/api\/(?:privacy|dsar|legal|breach)\//,
      /^src\/app\/api\/owner\/(?:consents|dpa-acceptance|merchant-agreement-acceptance|data|cancel-account)\//,
    ],
  },
  {
    id: "irreversible",
    labels: ["risk:high"],
    words: word(["irreversible", "drop table", "delete (?:all )?(?:production )?data", "production migration", "go-live", "oåterkallelig"]),
    paths: [/^prisma\/migrations\//, /^prisma\/schema\.prisma$/],
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
