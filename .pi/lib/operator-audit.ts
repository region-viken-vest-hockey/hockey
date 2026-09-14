// ---------------------------------------------------------------------------
// Pi harness semantic safety-net audit
// ---------------------------------------------------------------------------

import type { UserMessage } from "@earendil-works/pi-ai";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

export interface PiHarnessAuditResult {
  status: "success" | "failure";
  text: string;
  auditStatus?: string;
}

type AuditContext = Record<string, unknown>;
type AuditResultPayload = Record<string, unknown>;

const VALID_AUDIT_STATUSES = new Set(["PASS", "REVIEW_REQUIRED", "FAIL", "INCOMPLETE"]);

function pythonExe(cwd: string): string {
  const venvPython = resolve(cwd, "venv", "bin", "python3");
  return existsSync(venvPython) ? venvPython : "python3";
}

async function runRepoCli(
  cwd: string,
  commandArgs: string[],
  timeout = 120_000,
): Promise<{ status: "success" | "failure"; stdout: string; stderr: string; text: string }> {
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const execFileAsync = promisify(execFile);
  try {
    const { stdout, stderr } = await execFileAsync(
      pythonExe(cwd),
      ["-m", "tournament_scheduler.cli.rvv_cli", ...commandArgs],
      { cwd, timeout, maxBuffer: 10 * 1024 * 1024 },
    );
    const parts = [stdout.trim(), stderr.trim() ? `[stderr] ${stderr.trim()}` : ""].filter(Boolean);
    return { status: "success", stdout, stderr, text: parts.join("\n") };
  } catch (err: unknown) {
    const execError = err as { stdout?: string; stderr?: string; message?: string };
    const stdout = execError.stdout ?? "";
    const stderr = execError.stderr ?? "";
    const parts = [
      stdout.trim(),
      stderr.trim() ? `[stderr] ${stderr.trim()}` : "",
      stdout || stderr ? "" : (execError.message ?? String(err)),
    ].filter(Boolean);
    return { status: "failure", stdout, stderr, text: parts.join("\n") };
  }
}

function checklistLines(context: AuditContext): string[] {
  const checklist = Array.isArray(context.checklist) ? context.checklist : [];
  return checklist.map((entry) => {
    const item = entry as Record<string, unknown>;
    return `  ${item.item_id}. ${item.question}`;
  });
}

function buildPiAuditPrompt(context: AuditContext): string {
  return [
    "You are performing an independent, adversarial semantic safety-net audit of a youth hockey tournament season-plan export, immediately before publication.",
    "",
    "Do not assume the schedule is correct merely because deterministic verification passed. The scheduler and its deterministic verifier may share a logic defect, or may simply be missing a rule. Reconstruct important facts from the evidence below, look for counterexamples and suspicious outliers across the whole season, and explain anything that does not make operational sense.",
    "",
    "Important boundaries:",
    "- Use only the persisted audit context below; do not perform fresh live scraping.",
    "- Do not expose hidden reasoning. Return concise findings only.",
    "- A deterministic hard failure can never be overridden by this audit.",
    "",
    `Run id: ${context.run_id ?? ""}`,
    `Export fingerprint: ${context.export_fingerprint ?? ""}`,
    `Export directory: ${context.export_dir ?? ""}`,
    "",
    "Deterministic verification result (already computed, do not recompute — cross-check and look beyond it):",
    JSON.stringify(context.deterministic_verify_result ?? {}, null, 2),
    "",
    "Publication readiness (already computed):",
    JSON.stringify(context.publication_readiness ?? {}, null, 2),
    "",
    "Persisted source/calendar evidence (do not perform fresh live scraping):",
    JSON.stringify(context.calendar_evidence_summary ?? {}, null, 2),
    "",
    "Export output files (cross-check export-format consistency, checklist item 8):",
    JSON.stringify(context.output_files ?? {}, null, 2),
    "",
    "Operator checklist — answer every item; item 9 is open-ended and the most important:",
    ...checklistLines(context),
    "",
    "Respond with a single JSON object with this exact shape:",
    JSON.stringify({
      status: "PASS | REVIEW_REQUIRED | FAIL",
      checklist_findings: [
        {
          item_id: "int 1-9",
          question: "string",
          finding: "string",
          severity: "info | minor | major | critical",
          confidence: "low | medium | high",
          evidence: ["string", "..."],
          could_not_establish: "bool",
        },
      ],
      potential_missing_rule: [
        {
          description: "string",
          severity: "info | minor | major | critical",
          confidence: "low | medium | high",
          evidence: ["string", "..."],
        },
      ],
      could_not_independently_establish: ["string", "..."],
    }, null, 2),
    "",
    "Respond with only the JSON object, no other text.",
  ].join("\n");
}

function extractJsonObject(raw: string): Record<string, unknown> | null {
  let text = raw.trim();
  if (text.startsWith("```")) {
    text = text.replace(/^```(?:json)?/i, "").replace(/```$/i, "").trim();
  }
  try {
    const parsed = JSON.parse(text);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : null;
  } catch { /* fall through */ }

  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start < 0 || end <= start) return null;
  try {
    const parsed = JSON.parse(text.slice(start, end + 1));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

function stablePayloadSha256(payload: unknown): string {
  const stable = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(stable);
    if (value && typeof value === "object") {
      const obj = value as Record<string, unknown>;
      return Object.keys(obj).sort().reduce<Record<string, unknown>>((acc, key) => {
        acc[key] = stable(obj[key]);
        return acc;
      }, {});
    }
    return value;
  };
  return createHash("sha256").update(JSON.stringify(stable(payload))).digest("hex");
}

function auditId(context: AuditContext, generatedAt: string): string {
  return stablePayloadSha256({
    export_fingerprint: String(context.export_fingerprint ?? ""),
    run_id: String(context.run_id ?? ""),
    generated_at: generatedAt,
  }).slice(0, 16);
}

function incompletePayload(context: AuditContext, backend: string, reason: string): AuditResultPayload {
  const generatedAt = new Date().toISOString();
  const checklist = Array.isArray(context.checklist) ? context.checklist : [];
  return {
    schema_version: 1,
    audit_id: auditId(context, generatedAt),
    generated_at: generatedAt,
    run_id: String(context.run_id ?? ""),
    export_fingerprint: String(context.export_fingerprint ?? ""),
    source_fingerprints: context.source_fingerprints ?? {},
    prompt_version: context.audit_prompt_version,
    runbook_version: context.runbook_version,
    backend,
    execution_mode: "interactive_harness",
    status: "INCOMPLETE",
    checklist_findings: checklist.map((entry) => {
      const item = entry as Record<string, unknown>;
      return {
        item_id: item.item_id,
        question: item.question,
        finding: reason,
        severity: "critical",
        confidence: "high",
        evidence: [],
        could_not_establish: true,
      };
    }),
    potential_missing_rule: [],
    could_not_independently_establish: [reason],
    raw_response_ref: null,
  };
}

function payloadFromModel(context: AuditContext, parsed: Record<string, unknown>, backend: string): AuditResultPayload {
  const generatedAt = new Date().toISOString();
  const status = typeof parsed.status === "string" && VALID_AUDIT_STATUSES.has(parsed.status)
    ? parsed.status
    : "INCOMPLETE";
  return {
    schema_version: 1,
    audit_id: auditId(context, generatedAt),
    generated_at: generatedAt,
    run_id: String(context.run_id ?? ""),
    export_fingerprint: String(context.export_fingerprint ?? ""),
    source_fingerprints: context.source_fingerprints ?? {},
    prompt_version: context.audit_prompt_version,
    runbook_version: context.runbook_version,
    backend,
    execution_mode: "interactive_harness",
    status,
    checklist_findings: Array.isArray(parsed.checklist_findings) ? parsed.checklist_findings : [],
    potential_missing_rule: Array.isArray(parsed.potential_missing_rule) ? parsed.potential_missing_rule : [],
    could_not_independently_establish: Array.isArray(parsed.could_not_independently_establish)
      ? parsed.could_not_independently_establish
      : [],
    raw_response_ref: null,
  };
}

async function submitAuditPayload(cwd: string, workDir: string, payload: AuditResultPayload) {
  // Persist only through the repository operator audit-submit action.
  return runRepoCli(cwd, ["operator", "audit-submit", "--work-dir", workDir, "--result-json", JSON.stringify(payload)]);
}

function responseText(response: { content?: Array<{ type: string; text?: string }> }): string {
  return (response.content ?? [])
    .filter((content): content is { type: "text"; text: string } => content.type === "text" && typeof content.text === "string")
    .map((content) => content.text)
    .join("\n");
}

export function isAuditPublicationBlocking(status: string | undefined): boolean {
  return !status || status === "FAIL" || status === "INCOMPLETE";
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : String(value ?? "");
}

function compactEvidence(value: unknown): string {
  if (!Array.isArray(value) || value.length === 0) return "";
  return value.map(stringValue).filter(Boolean).join("; ");
}

function formatAuditPayloadForOutput(payload: AuditResultPayload): string {
  const lines: string[] = [];
  const checklistFindings = Array.isArray(payload.checklist_findings) ? payload.checklist_findings : [];
  if (checklistFindings.length > 0) {
    lines.push("Audit-funn:");
    for (const rawFinding of checklistFindings) {
      const finding = rawFinding as Record<string, unknown>;
      const severity = stringValue(finding.severity).trim() || "severity?";
      const confidence = stringValue(finding.confidence).trim();
      const prefix = `  ${stringValue(finding.item_id) || "?"}. ${severity}${confidence ? `/${confidence}` : ""}`;
      const text = stringValue(finding.finding).trim() || stringValue(finding.question).trim() || "Uten tekst";
      lines.push(`${prefix}: ${text}`);
      const evidence = compactEvidence(finding.evidence);
      if (evidence) lines.push(`     Evidens: ${evidence}`);
    }
  }

  const potentialMissingRules = Array.isArray(payload.potential_missing_rule) ? payload.potential_missing_rule : [];
  if (potentialMissingRules.length > 0) {
    lines.push("Mulige manglende regler:");
    for (const rawRule of potentialMissingRules) {
      const rule = rawRule as Record<string, unknown>;
      const text = stringValue(rule.description).trim() || "Uten beskrivelse";
      const severity = stringValue(rule.severity).trim();
      const confidence = stringValue(rule.confidence).trim();
      const label = [severity, confidence].filter(Boolean).join("/");
      lines.push(`  - ${label ? `${label}: ` : ""}${text}`);
      const evidence = compactEvidence(rule.evidence);
      if (evidence) lines.push(`    Evidens: ${evidence}`);
    }
  }

  const missingEvidence = Array.isArray(payload.could_not_independently_establish)
    ? payload.could_not_independently_establish.map(stringValue).filter(Boolean)
    : [];
  if (missingEvidence.length > 0) {
    lines.push("Kunne ikke etableres uavhengig:");
    for (const item of missingEvidence) lines.push(`  - ${item}`);
  }

  return lines.join("\n");
}

export async function runPiHarnessAudit(
  cwd: string,
  workDir: string,
  ctx: ExtensionContext,
  logLLMInteraction?: (details: Record<string, unknown>) => void,
): Promise<PiHarnessAuditResult> {
  const contextResult = await runRepoCli(cwd, ["operator", "audit-context", "--work-dir", workDir]);
  if (contextResult.status !== "success") {
    return { status: "failure", text: `Semantisk revisjon: kunne ikke hente audit-context.\n${contextResult.text}` };
  }

  let context: AuditContext;
  try {
    context = JSON.parse(contextResult.stdout) as AuditContext;
  } catch (err: unknown) {
    return { status: "failure", text: `Semantisk revisjon: audit-context var ikke gyldig JSON: ${err instanceof Error ? err.message : String(err)}` };
  }

  if (!context.export_fingerprint) {
    return { status: "failure", text: "Semantisk revisjon: ingen Stage 4-eksport funnet." };
  }

  const backend = ctx.model ? `pi:${ctx.model.provider}/${ctx.model.id}` : "pi:no-active-model";
  let payload: AuditResultPayload;
  let raw = "";
  if (!ctx.model) {
    payload = incompletePayload(context, backend, "Pi harness has no active model available for the semantic audit.");
  } else {
    const prompt = buildPiAuditPrompt(context);
    const started = Date.now();
    const userMessage: UserMessage = {
      role: "user",
      content: [{ type: "text", text: prompt }],
      timestamp: Date.now(),
    };
    try {
      const response = await ctx.modelRegistry.complete(
        ctx.model,
        { systemPrompt: "You are an adversarial audit judge. Return only the requested JSON object.", messages: [userMessage] },
        { signal: ctx.signal },
      );
      raw = responseText(response);
      logLLMInteraction?.({ backend, duration_ms: Date.now() - started, raw_chars: raw.length });
      if (response.stopReason === "aborted") {
        payload = incompletePayload(context, backend, "Pi harness semantic audit was aborted.");
      } else {
        const parsed = extractJsonObject(raw);
        payload = parsed
          ? payloadFromModel(context, parsed, backend)
          : incompletePayload(context, backend, `Pi harness could not parse audit model response as JSON: ${raw.slice(0, 500)}`);
      }
    } catch (err: unknown) {
      const reason = `Pi harness audit model call failed: ${err instanceof Error ? err.message : String(err)}`;
      logLLMInteraction?.({ backend, duration_ms: Date.now() - started, error: reason });
      payload = incompletePayload(context, backend, reason);
    }
  }

  let submit = await submitAuditPayload(cwd, workDir, payload);
  if (submit.status !== "success") {
    const reason = `Pi harness audit result failed repository submission: ${submit.text}`;
    payload = incompletePayload(context, backend, reason);
    submit = await submitAuditPayload(cwd, workDir, payload);
  }

  const auditStatus = String(payload.status ?? "INCOMPLETE");
  const summary = `Semantisk revisjon (${backend}): ${auditStatus}`;
  if (submit.status !== "success") {
    return {
      status: "failure",
      auditStatus: "INCOMPLETE",
      text: `${summary}\nKunne ikke lagre audit_result.json.\n${submit.text}`,
    };
  }

  const status: "success" | "failure" = isAuditPublicationBlocking(auditStatus) ? "failure" : "success";
  const findings = Array.isArray(payload.checklist_findings) ? ` (${payload.checklist_findings.length} checklist-funn)` : "";
  const details = formatAuditPayloadForOutput(payload);
  return {
    status,
    auditStatus,
    text: [`${summary}${findings}`, submit.text, details].filter(Boolean).join("\n"),
  };
}
