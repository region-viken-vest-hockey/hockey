// Stable identities for what a repair is asked to fix (, M4 task 4: two
// attempts per finding). A key must survive a rewrite of the surrounding code,
// so it ignores line numbers and the case, spacing and digits of the wording,
// and it never depends on the model's own severity beyond which findings block.
import { createHash } from "node:crypto";

const normalize = (text) => String(text).toLowerCase().replace(/\d+/g, "#").replace(/[^a-z#]+/g, " ").trim();
const digest = (text) => createHash("sha256").update(text).digest("hex").slice(0, 16);

// Used when a failing report names nothing the controller can key on, so a
// repair is still counted against something and the loop stays bounded.
export const UNSPECIFIED_REVIEW = "review:unspecified";
export const UNSPECIFIED_QA = "qa:unspecified";

// Blocking review findings (P1, P2) and unmet acceptance criteria.
export function reviewFindingKeys({ findings = /** @type {any[]} */ ([]), acceptance = /** @type {any[]} */ ([]) }) {
  const keys = new Set();
  for (const f of findings) if (f.severity !== "P3") keys.add(`review:${digest(`${f.file}\n${normalize(f.summary)}`)}`);
  for (const a of acceptance) if (a.status === "UNMET") keys.add(`criterion:${digest(normalize(a.criterion))}`);
  return keys.size ? [...keys].sort() : [UNSPECIFIED_REVIEW];
}

// A failing check is keyed by what failed inside it when its output says so:
// a TypeScript error (code and file) or a failing test (file and name), so a
// different failure in the same gate is a different finding with its own
// budget. Output that names nothing falls back to the check itself.
const TS_ERROR = /^(\S+?)\(\d+,\d+\): error (TS\d+)/gm;
const TEST_FAIL = /^\s*(?:FAIL|×)\s+(?:\|[^|]*\|\s*)?(\S.*)$/gm;
const ANSI = /\u001b\[[0-9;]*m/g;

function failureIds(tail) {
  const text = String(tail ?? "").replace(ANSI, "");
  const ids = new Set();
  for (const m of text.matchAll(TS_ERROR)) ids.add(`${m[2]} ${m[1]}`);
  for (const m of text.matchAll(TEST_FAIL)) ids.add(normalize(m[1]));
  return [...ids];
}

export function qaFindingKeys(checks = /** @type {any[]} */ ([])) {
  const keys = new Set();
  for (const check of checks) {
    if (check.status === "PASS") continue;
    const name = normalize(check.name) || "check";
    const ids = failureIds(check.outputTail);
    if (ids.length) for (const id of ids.slice(0, 20)) keys.add(`qa:${name}:${digest(id)}`);
    else keys.add(`qa:${name}`);
  }
  return keys.size ? [...keys].sort() : [UNSPECIFIED_QA];
}
