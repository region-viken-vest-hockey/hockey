// Transactional local run/lease state for the autonomous controller.
// GitHub stays authoritative for issue and PR state; this store only records
// which local run owns an issue, its state-machine position and its attempts.
import { existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { DatabaseSync } from "node:sqlite";

export const STATES = [
  "DISCOVERED",
  "ELIGIBLE",
  "CLAIMED",
  "PRODUCT_CHECK",
  "NEEDS_DECISION",
  "IMPLEMENTING",
  "VERIFYING",
  "REVIEWING",
  "REPAIR",
  "PR_READY",
  "MERGED",
  "COMPLETE",
  "PARKED",
  "FAILED",
  "EXPIRED",
  "PAUSED",
];

const ACTIVE = ["CLAIMED", "PRODUCT_CHECK", "IMPLEMENTING", "VERIFYING", "REVIEWING", "REPAIR", "PR_READY"];

export const TRANSITIONS = {
  DISCOVERED: ["ELIGIBLE"],
  ELIGIBLE: ["CLAIMED"],
  CLAIMED: ["PRODUCT_CHECK"],
  PRODUCT_CHECK: ["IMPLEMENTING", "NEEDS_DECISION"],
  NEEDS_DECISION: ["PARKED"],
  IMPLEMENTING: ["VERIFYING"],
  VERIFYING: ["REVIEWING", "REPAIR"],
  REVIEWING: ["PR_READY", "REPAIR"],
  REPAIR: ["IMPLEMENTING"],
  PR_READY: ["MERGED"],
  MERGED: ["COMPLETE"],
};

const TERMINAL = ["COMPLETE", "PARKED"];

// One implementation attempt plus two repairs. `attempts` is per run, not per
// finding: findings are not tracked yet, so the bound is on the run as a whole.
export const MAX_ATTEMPTS = 3;
// With findings named (the normal path), each finding gets two repairs and the
// run as a whole a finite number, so new findings cannot extend it for ever.
export const MAX_FINDING_REPAIRS = 2;
export const MAX_RUN_REPAIRS = 6;
const LEGACY_MARKER = "_legacy";
const DEFAULT_TTL_MS = 30 * 60 * 1000;

export function defaultStateDir(repoRoot) {
  return join(repoRoot, ".local", "auto");
}

export function openState(dbPath) {
  if (dbPath !== ":memory:") mkdirSync(dirname(dbPath), { recursive: true });
  const db = new DatabaseSync(dbPath);
  db.exec("PRAGMA busy_timeout = 5000;");
  db.exec("PRAGMA journal_mode = WAL;");
  db.exec(`CREATE TABLE IF NOT EXISTS runs (
    issue INTEGER PRIMARY KEY,
    state TEXT NOT NULL,
    owner TEXT,
    run_id TEXT,
    lease_expires_at INTEGER,
    heartbeat_at INTEGER,
    head_sha TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    stop_reason TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
  )`);
  // Scope evidence arrived after the first release; add its columns to stores
  // created before it rather than failing on them.
  const have = new Set(db.prepare("PRAGMA table_info(runs)").all().map((c) => c.name));
  if (!have.has("scope_fingerprint")) db.exec("ALTER TABLE runs ADD COLUMN scope_fingerprint TEXT");
  if (!have.has("comments_acked")) db.exec("ALTER TABLE runs ADD COLUMN comments_acked TEXT");
  if (!have.has("review_failures")) db.exec("ALTER TABLE runs ADD COLUMN review_failures INTEGER NOT NULL DEFAULT 0");
  db.exec(`CREATE TABLE IF NOT EXISTS repair_findings (
    issue INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    key TEXT NOT NULL,
    repairs INTEGER NOT NULL,
    PRIMARY KEY (issue, run_id, key)
  )`);
  // The in-session skills' ledger: one row per repair started from a
  // reviewed head, keyed by issue and pull request rather than by run, so it
  // survives a context reset, a hand-off to another session and a lease change.
  db.exec(`CREATE TABLE IF NOT EXISTS finding_ledger (
    issue INTEGER NOT NULL,
    pr INTEGER NOT NULL,
    key TEXT NOT NULL,
    head_sha TEXT NOT NULL,
    owner TEXT NOT NULL,
    run_id TEXT NOT NULL,
    recorded_at INTEGER NOT NULL,
    PRIMARY KEY (issue, pr, key, head_sha)
  )`);
  // Why an issue was parked for the founder, kept with the evidence that
  // triggered it. Rows are only ever added.
  db.exec(`CREATE TABLE IF NOT EXISTS authority_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    pr INTEGER,
    head_sha TEXT,
    detail TEXT NOT NULL,
    recorded_at INTEGER NOT NULL
  )`);
  // Explicit approvals given by the user in the active session. Approval is
  // scoped to the protected domains in one recorded block and to that run.
  db.exec(`CREATE TABLE IF NOT EXISTS authority_approvals (
    issue INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    block_id INTEGER NOT NULL,
    domain TEXT NOT NULL,
    approved_at INTEGER NOT NULL,
    PRIMARY KEY (issue, run_id, block_id, domain)
  )`);
  return db;
}

function immediate(db, fn) {
  db.exec("BEGIN IMMEDIATE");
  try {
    const result = fn();
    db.exec("COMMIT");
    return result;
  } catch (error) {
    db.exec("ROLLBACK");
    throw error;
  }
}

// Records that a repair of these findings is starting from this reviewed head of
// this pull request. A repair is one (finding, head) pair, so recording the same
// pair again after a context reset changes nothing. A finding already repaired
// from two other heads, or a pull request already repaired from six, parks the
// run and releases the lease, as the controller's own bounds do. Check and
// write share one lock, and nothing is written when a bound is hit.
export function recordRepairAttempt(db, { issue, owner, runId, pr, headSha, keys, now = Date.now() }) {
  return immediate(db, () => {
    const current = row(db, issue);
    if (!current || !holds(current, owner, runId) || !leaseLive(current, now)) return { ok: false, code: "LEASE_LOST" };
    const rows = db.prepare("SELECT key, head_sha FROM finding_ledger WHERE issue = ? AND pr = ?").all(issue, pr);
    const attempts = (key) => rows.filter((r) => r.key === key).length;
    const fresh = [...new Set(keys)].filter((k) => !rows.some((r) => r.key === k && r.head_sha === headSha));
    const counts = () => Object.fromEntries([...new Set(keys)].sort().map((k) => [k, db.prepare("SELECT COUNT(*) AS n FROM finding_ledger WHERE issue = ? AND pr = ? AND key = ?").get(issue, pr, k).n]));
    if (!fresh.length) return { ok: true, already: true, counts: counts() };
    const park = (limit, finding = null) => {
      db.prepare(
        "UPDATE runs SET state = 'PARKED', stop_reason = 'REPAIR_LIMIT', owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE issue = ?",
      ).run(now, issue);
      return { ok: false, code: "REPAIR_LIMIT", limit, ...(finding ? { finding } : {}), parked: true };
    };
    const over = fresh.find((k) => attempts(k) + 1 > MAX_FINDING_REPAIRS);
    if (over) return park("FINDING", over);
    const heads = new Set(rows.map((r) => r.head_sha));
    if (!heads.has(headSha) && heads.size + 1 > MAX_RUN_REPAIRS) return park("RUN");
    const insert = db.prepare("INSERT INTO finding_ledger (issue, pr, key, head_sha, owner, run_id, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)");
    for (const key of fresh) insert.run(issue, pr, key, headSha, owner, runId, now);
    return { ok: true, already: false, counts: counts() };
  });
}

// Parks the issue because the founder has to decide, releases the lease and
// keeps the evidence in one write. The active session can resume only after
// the user explicitly approves the recorded protected domains.
export function parkForFounder(db, { issue, owner, runId, phase, pr = null, headSha = null, detail, now = Date.now() }) {
  return immediate(db, () => {
    const current = row(db, issue);
    if (!current || !holds(current, owner, runId) || !leaseLive(current, now)) return { ok: false, code: "LEASE_LOST" };
    db.prepare("UPDATE runs SET state = 'PARKED', stop_reason = 'FOUNDER_APPROVAL_REQUIRED', owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE issue = ?").run(now, issue);
    db.prepare("INSERT INTO authority_blocks (issue, run_id, phase, pr, head_sha, detail, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)").run(issue, runId, phase, pr, headSha, JSON.stringify(detail), now);
    return { ok: true, run: row(db, issue) };
  });
}

export function approvedAuthorityDomains(db, { issue, runId }) {
  return db.prepare("SELECT DISTINCT domain FROM authority_approvals WHERE issue = ? AND run_id = ? ORDER BY domain").all(issue, runId).map((row) => row.domain);
}

// Records the user's in-session approval for the latest founder block and
// restores the same fenced run's lease. The CLI cannot prove what was said in
// chat; orca-flow is responsible for invoking this only after explicit approval.
export function resumeFounderPark(db, { issue, owner, runId, ttlMs = DEFAULT_TTL_MS, now = Date.now() }) {
  return immediate(db, () => {
    const current = row(db, issue);
    if (!current || current.state !== "PARKED" || current.stop_reason !== "FOUNDER_APPROVAL_REQUIRED") return { ok: false, code: "NOT_FOUNDER_PARK" };
    if (current.run_id !== runId) return { ok: false, code: "RUN_MISMATCH" };
    const block = db.prepare("SELECT id, detail FROM authority_blocks WHERE issue = ? AND run_id = ? ORDER BY id DESC LIMIT 1").get(issue, runId);
    if (!block) return { ok: false, code: "APPROVAL_EVIDENCE_MISSING" };
    let domains;
    try {
      domains = JSON.parse(block.detail).domains;
    } catch {
      return { ok: false, code: "APPROVAL_EVIDENCE_INVALID" };
    }
    if (!Array.isArray(domains) || !domains.length || domains.some((domain) => typeof domain !== "string")) return { ok: false, code: "APPROVAL_EVIDENCE_INVALID" };
    const approve = db.prepare("INSERT OR IGNORE INTO authority_approvals (issue, run_id, block_id, domain, approved_at) VALUES (?, ?, ?, ?, ?)");
    for (const domain of [...new Set(domains)]) approve.run(issue, runId, block.id, domain, now);
    db.prepare(
      "UPDATE runs SET state = 'CLAIMED', owner = ?, lease_expires_at = ?, heartbeat_at = ?, stop_reason = NULL, updated_at = ? WHERE issue = ?",
    ).run(owner, now + ttlMs, now, now, issue);
    return { ok: true, approvedDomains: approvedAuthorityDomains(db, { issue, runId }), run: row(db, issue) };
  });
}

export function ledgerFor(db, { issue, pr = null }) {
  const rows = pr === null ? db.prepare("SELECT * FROM finding_ledger WHERE issue = ? ORDER BY pr, key, recorded_at").all(issue) : db.prepare("SELECT * FROM finding_ledger WHERE issue = ? AND pr = ? ORDER BY key, recorded_at").all(issue, pr);
  return rows;
}

function row(db, issue) {
  return db.prepare("SELECT * FROM runs WHERE issue = ?").get(issue) ?? null;
}

// A lease is held by a run, not by an owner label: two processes sharing an
// owner name must still be told apart, so every owner-checked operation also
// compares run_id (the fencing identity).
const holds = (r, owner, runId) => r.owner === owner && r.run_id === runId;

const leaseLive = (r, now) => r.owner !== null && r.lease_expires_at !== null && r.lease_expires_at > now;

// Claim is atomic: BEGIN IMMEDIATE takes the write lock before the read, so two
// concurrent ticks cannot both observe "no live lease".
// With `exclusive`, the claim also fails while any other issue holds a live
// lease; the check shares the claim's write lock, so two concurrent callers
// cannot both pass it.
export function claimIssue(db, { issue, owner, runId, ttlMs = DEFAULT_TTL_MS, now = Date.now(), exclusive = false }) {
  return immediate(db, () => {
    if (exclusive) {
      const busy = db.prepare("SELECT issue FROM runs WHERE owner IS NOT NULL AND lease_expires_at > ? AND issue != ?").all(now, issue);
      if (busy.length) return { ok: false, code: "BUSY", holding: busy.map((r) => r.issue) };
    }
    const current = row(db, issue);
    if (current && (TERMINAL.includes(current.state) || (current.state === "NEEDS_DECISION" && !leaseLive(current, now)))) {
      // A pending product decision is never silently resumed or reset: it stays
      // blocked until a person or the decision gate resolves it.
      return { ok: false, code: "ISSUE_TERMINAL", state: current.state };
    }
    if (current && leaseLive(current, now) && !holds(current, owner, runId)) {
      return { ok: false, code: "LEASE_CONFLICT", holder: current.owner, expiresAt: current.lease_expires_at };
    }
    const expires = now + ttlMs;
    if (!current) {
      db.prepare(
        "INSERT INTO runs (issue, state, owner, run_id, lease_expires_at, heartbeat_at, created_at, updated_at) VALUES (?, 'CLAIMED', ?, ?, ?, ?, ?, ?)",
      ).run(issue, owner, runId, expires, now, now, now);
    } else {
      // Takeover of an expired lease or re-claim by the same owner keeps the
      // branch-level history (head_sha, attempts) and restarts from CLAIMED
      // unless the same owner is already mid-run.
      const resume = holds(current, owner, runId) && [...ACTIVE, "NEEDS_DECISION"].includes(current.state);
      db.prepare(
        "UPDATE runs SET state = ?, owner = ?, run_id = ?, lease_expires_at = ?, heartbeat_at = ?, stop_reason = NULL, updated_at = ? WHERE issue = ?",
      ).run(resume ? current.state : "CLAIMED", owner, runId, expires, now, now, issue);
      // Scope evidence belongs to the run that read it. A different run starts
      // without a baseline until it records its own.
      if (!holds(current, owner, runId)) {
        db.prepare("UPDATE runs SET scope_fingerprint = NULL, comments_acked = NULL, review_failures = 0 WHERE issue = ?").run(issue);
      }
    }
    return { ok: true, run: row(db, issue) };
  });
}

export function renewLease(db, { issue, owner, runId, ttlMs = DEFAULT_TTL_MS, now = Date.now() }) {
  return immediate(db, () => {
    const current = row(db, issue);
    if (!current || !holds(current, owner, runId) || !leaseLive(current, now)) {
      return { ok: false, code: "LEASE_LOST" };
    }
    db.prepare("UPDATE runs SET lease_expires_at = ?, heartbeat_at = ?, updated_at = ? WHERE issue = ?").run(
      now + ttlMs,
      now,
      now,
      issue,
    );
    return { ok: true, run: row(db, issue) };
  });
}

// FAILED and PARKED are reachable from any state so a run can always stop.
// `findings` names what a move into REPAIR is asked to fix (see findings.mjs).
// Each key is counted per run; a key already repaired twice parks the run, as
// does a run that has used its repair budget. Without keys the older per-run
// bound of MAX_ATTEMPTS applies.
export function transition(db, { issue, owner, runId, to, headSha = /** @type {string | null} */ (null), stopReason = /** @type {string | null} */ (null), findings = /** @type {string[] | null} */ (null), now = Date.now() }) {
  return immediate(db, () => {
    const current = row(db, issue);
    if (!current || !holds(current, owner, runId) || !leaseLive(current, now)) {
      return { ok: false, code: "LEASE_LOST" };
    }
    if (!(TRANSITIONS[current.state] ?? []).includes(to) && !["FAILED", "PARKED"].includes(to)) {
      return { ok: false, code: "INVALID_TRANSITION", from: current.state, to };
    }
    // A repair beyond the bound parks the run instead: repeated failures need
    // a human, and the lease is released like releaseLease does (run_id kept).
    // The caller sees state PARKED.
    const park = (limit, finding = null) => {
      db.prepare(
        "UPDATE runs SET state = 'PARKED', head_sha = COALESCE(?, head_sha), stop_reason = 'REPAIR_LIMIT', owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE issue = ?",
      ).run(headSha ?? null, now, issue);
      return { ok: true, run: row(db, issue), parked: true, limit, ...(finding ? { finding } : {}) };
    };
    const keys = to === "REPAIR" && findings?.length ? [...new Set(findings)] : null;
    if (to === "REPAIR" && !keys && current.attempts + 1 > MAX_ATTEMPTS) return park("RUN");
    if (keys) {
      const seen = db.prepare("SELECT key, repairs FROM repair_findings WHERE issue = ? AND run_id = ?").all(issue, runId);
      // A run that spent repairs before findings were tracked has no rows for
      // them. It keeps the per-run bound it was started under, recorded as a
      // marker so the rule stays the same for the rest of the run.
      const legacy = seen.some((r) => r.key === LEGACY_MARKER) || (current.attempts > 1 && seen.length === 0);
      if (legacy) {
        if (current.attempts + 1 > MAX_ATTEMPTS) return park("RUN");
        db.prepare("INSERT OR IGNORE INTO repair_findings (issue, run_id, key, repairs) VALUES (?, ?, ?, 0)").run(issue, runId, LEGACY_MARKER);
      }
      if (current.attempts + 1 > 1 + MAX_RUN_REPAIRS) return park("RUN");
      const repaired = new Map(seen.filter((r) => r.key !== LEGACY_MARKER).map((r) => [r.key, r.repairs]));
      const over = keys.find((k) => (repaired.get(k) ?? 0) + 1 > MAX_FINDING_REPAIRS);
      if (over) return park("FINDING", over);
      const bump = db.prepare("INSERT INTO repair_findings (issue, run_id, key, repairs) VALUES (?, ?, ?, 1) ON CONFLICT (issue, run_id, key) DO UPDATE SET repairs = repairs + 1");
      for (const key of keys) bump.run(issue, runId, key);
    }
    // Entering REPAIR is the counted attempt; the repair worker's start
    // (REPAIR -> IMPLEMENTING) is that same attempt, not a new one.
    const attempts = to === "REPAIR" || (to === "IMPLEMENTING" && current.state !== "REPAIR") ? current.attempts + 1 : current.attempts;
    // A verdict ends the streak of reviews that reached none.
    const reviewFailures = current.state === "REVIEWING" && (to === "PR_READY" || to === "REPAIR") ? 0 : current.review_failures;
    db.prepare(
      "UPDATE runs SET state = ?, head_sha = COALESCE(?, head_sha), attempts = ?, review_failures = ?, stop_reason = ?, updated_at = ? WHERE issue = ?",
    ).run(to, headSha ?? null, attempts, reviewFailures, stopReason ?? null, now, issue);
    return { ok: true, run: row(db, issue) };
  });
}

// Release is owner-checked; it frees the lease but keeps state so the branch
// and history stay recoverable.
export function releaseLease(db, { issue, owner, runId, now = Date.now() }) {
  return immediate(db, () => {
    const current = row(db, issue);
    if (!current || !holds(current, owner, runId)) return { ok: false, code: "NOT_OWNER" };
    db.prepare("UPDATE runs SET owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE issue = ?").run(now, issue);
    return { ok: true, run: row(db, issue) };
  });
}

// Marks active runs whose lease lapsed as EXPIRED so a later claim can recover
// them. Returns the affected issues; it never deletes evidence.
export function recoverExpired(db, { now = Date.now() } = {}) {
  return immediate(db, () => {
    const stale = db
      .prepare(
        `SELECT issue, state FROM runs WHERE state IN (${[...ACTIVE, "NEEDS_DECISION"].map(() => "?").join(",")}) AND owner IS NOT NULL AND lease_expires_at <= ?`,
      )
      .all(...ACTIVE, "NEEDS_DECISION", now);
    for (const { issue, state } of stale) {
      // Work in flight can be recovered; a pending decision is preserved as
      // PARKED (the declared NEEDS_DECISION destination), never reset.
      const [next, reason] = state === "NEEDS_DECISION" ? ["PARKED", "NEEDS_DECISION"] : ["EXPIRED", "LEASE_EXPIRED"];
      db.prepare(
        "UPDATE runs SET state = ?, owner = NULL, lease_expires_at = NULL, stop_reason = ?, updated_at = ? WHERE issue = ?",
      ).run(next, reason, now, issue);
    }
    return stale.map((s) => s.issue);
  });
}

export function getRun(db, issue) {
  return row(db, issue);
}

export function isPaused(stateDir) {
  return existsSync(join(stateDir, "PAUSED"));
}
