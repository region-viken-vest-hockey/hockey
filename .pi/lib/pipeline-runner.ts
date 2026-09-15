// ---------------------------------------------------------------------------
// Pi pipeline adapter — thin controller over the canonical interactive CLI.
//
// Pi owns only transport/UI/model/browser integration here. Stage sequencing,
// defaults, checkpoints, action validation, planning, verification and export
// remain repository/Python responsibilities.
// ---------------------------------------------------------------------------

import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { resolve } from "node:path";
import { tokenizeArgs } from "./arg-utils";
import { recoverSourceWithPiBrowser } from "./browser-recovery";
import {
  chooseDecisionAction,
  type DecisionActionPayload,
  type DecisionContextPayload,
} from "./decision-controller";
import { isAuditPublicationBlocking, runPiHarnessAudit } from "./operator-audit";
import { formatProcessFailure, runRepoCli } from "./repo-cli";
import type { ProgressEvent } from "./types";

export interface PipelineRunResult {
  status: "success" | "failure" | "cancelled" | "needs_input";
  text: string;
}

type StageProgress = ProgressEvent["stage"];

function optionValue(tokens: string[], option: string): string | undefined {
  const index = tokens.lastIndexOf(option);
  return index >= 0 && index + 1 < tokens.length ? tokens[index + 1] : undefined;
}

function stripManagedRunArgs(tokens: string[]): string[] {
  const result: string[] = [];
  const valueOptions = new Set(["--resume-from", "--decision-action", "--decision-action-file"]);
  const flagOptions = new Set(["--interactive"]);
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    if (valueOptions.has(token)) {
      i += 1;
      continue;
    }
    if (flagOptions.has(token)) continue;
    result.push(token);
  }
  return result;
}

function stageFromContext(context: DecisionContextPayload): StageProgress {
  const capability = String(context.capability ?? "").toLowerCase();
  const stage = String(context.stage ?? "").toLowerCase();
  if (capability.includes("shared_host") || capability.includes("stage3") || stage.includes("planning") || stage.includes("stage3")) {
    return "planning";
  }
  if (capability.includes("scrap") || stage.includes("scrap") || stage.includes("stage2")) return "scraping";
  if (capability.includes("export") || stage.includes("export") || stage.includes("stage4")) return "export";
  return "config";
}

function resumeAfterContext(context: DecisionContextPayload): string {
  const capability = String(context.capability ?? "").toLowerCase();
  const stage = String(context.stage ?? "").toLowerCase();
  if (capability.includes("shared_host")) return "3";
  if (capability.includes("stage3") || stage.includes("planning") || stage.includes("stage3")) return "4";
  if (capability.includes("scrap") || stage.includes("scrap") || stage.includes("stage2")) return "3";
  return "2";
}

function isExportContext(context: DecisionContextPayload): boolean {
  const capability = String(context.capability ?? "").toLowerCase();
  const stage = String(context.stage ?? "").toLowerCase();
  return capability === "export" || stage === "export" || stage.includes("stage4");
}

function extractDecisionContext(raw: string): DecisionContextPayload | null {
  const text = raw.trim();
  if (!text) return null;
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const candidate = parsed as DecisionContextPayload;
      if (Array.isArray(candidate.available_actions)) return candidate;
    }
  } catch { /* scan for the final JSON object below */ }

  const end = text.lastIndexOf("}");
  if (end < 0) return null;
  const starts: number[] = [];
  for (let i = 0; i <= end; i++) if (text[i] === "{") starts.push(i);
  for (let i = starts.length - 1; i >= 0; i--) {
    try {
      const parsed = JSON.parse(text.slice(starts[i], end + 1));
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        const candidate = parsed as DecisionContextPayload;
        if (Array.isArray(candidate.available_actions)) return candidate;
      }
    } catch { /* continue */ }
  }
  return null;
}

function progressForResume(resumeFrom: string, action?: DecisionActionPayload): StageProgress {
  if (action?.action_id === "assign_shared_host") return "planning";
  if (action?.action_id === "optimize_plan" || action?.action_id === "apply_candidate" || action?.action_id === "keep_baseline") {
    return "planning";
  }
  if (action?.action_id === "retry_stage") {
    const stage = String(action.arguments?.stage ?? "");
    if (stage === "1" || stage === "config") return "config";
    if (stage === "2" || stage === "scraping") return "scraping";
    if (stage === "3" || stage === "planning") return "planning";
  }
  if (resumeFrom === "2" || resumeFrom === "scraping") return "scraping";
  if (resumeFrom === "3" || resumeFrom === "planning") return "planning";
  if (resumeFrom === "4" || resumeFrom === "export") return "export";
  return "config";
}

function streamProgressLine(
  line: string,
  stage: StageProgress,
  onProgress?: (event: ProgressEvent) => void,
): void {
  const trimmed = line.trim();
  const match = trimmed.match(/^\[(heartbeat|progress|plan)\]\s*(.*)$/);
  if (!match || !match[2]) return;
  onProgress?.({ stage, status: "start", message: match[2] });
}

function actionQuestion(action: DecisionActionPayload, context: DecisionContextPayload): string {
  const question = action.arguments?.question;
  if (typeof question === "string" && question.trim()) return question.trim();
  return String(context.objective ?? "Repository decision requires operator input.");
}

function recoverySource(action: DecisionActionPayload, context: DecisionContextPayload): string | null {
  const requested = action.arguments?.source;
  if (typeof requested !== "string" || !requested.trim()) return null;
  const blocked = context.facts?.blocked_sources;
  if (Array.isArray(blocked) && blocked.length > 0 && !blocked.map(String).includes(requested)) return null;
  return requested;
}

export async function runPipeline(
  rawArgs: unknown,
  ctx: ExtensionContext,
  onProgress?: (event: ProgressEvent) => void,
): Promise<PipelineRunResult> {
  const originalTokens = tokenizeArgs(rawArgs);
  if (originalTokens.includes("--publish")) {
    return {
      status: "failure",
      text: "Publication is a separate capability. Run the pipeline first, then use /rvv-miniputt publish.",
    };
  }

  const workDirArg = optionValue(originalTokens, "--work-dir") ?? ".pipeline";
  const workDir = resolve(ctx.cwd, workDirArg);
  const baseArgs = stripManagedRunArgs(originalTokens);
  let resumeFrom = optionValue(originalTokens, "--resume-from") ?? "1";
  let pendingAction: DecisionActionPayload | undefined;
  const transcript: string[] = [
    "RVV Miniputt — Pi thin adapter",
    "Stage sequencing, defaults, verification and persistence are owned by the repository CLI.",
  ];

  for (let turn = 0; turn < 50; turn++) {
    if (ctx.signal?.aborted) {
      onProgress?.({ stage: "done", status: "error", message: "Pipeline cancelled" });
      return { status: "cancelled", text: [...transcript, "Pipeline cancelled by user."].join("\n") };
    }

    const stage = progressForResume(resumeFrom, pendingAction);
    const commandArgs = [
      "run",
      ...baseArgs,
      "--resume-from",
      resumeFrom,
      "--interactive",
    ];
    if (pendingAction) commandArgs.push("--decision-action", JSON.stringify(pendingAction));

    onProgress?.({ stage, status: "start", message: `Canonical interactive run (resume ${resumeFrom})` });
    const invocation = await runRepoCli(ctx, commandArgs, {
      timeoutMs: 60 * 60 * 1000,
      onStdoutLine: (line) => streamProgressLine(line, stage, onProgress),
    });

    if (invocation.cancelled || ctx.signal?.aborted) {
      onProgress?.({ stage: "done", status: "error", message: "Pipeline cancelled" });
      return { status: "cancelled", text: [...transcript, "Pipeline cancelled by user."].join("\n") };
    }

    // Exit code 2 is the documented interactive pause: stdout contains the
    // repository-owned DecisionContext. Any other non-zero code is a real
    // failure (including a submitted abort decision).
    if (invocation.code !== 2) {
      if (invocation.code === 0) {
        onProgress?.({ stage: "done", status: "ok", message: "Pipeline completed" });
        const output = [invocation.stdout.trim(), invocation.stderr.trim()].filter(Boolean).join("\n");
        return { status: "success", text: [...transcript, output].filter(Boolean).join("\n") };
      }
      onProgress?.({ stage, status: "error", message: "Canonical repository run failed", error: invocation.stderr.trim() });
      return {
        status: "failure",
        text: [...transcript, formatProcessFailure(invocation)].filter(Boolean).join("\n"),
      };
    }

    const context = extractDecisionContext(invocation.stdout);
    if (!context) {
      return {
        status: "failure",
        text: [
          ...transcript,
          "Canonical interactive run paused but Pi could not parse its DecisionContext.",
          invocation.stdout.trim(),
          invocation.stderr.trim(),
        ].filter(Boolean).join("\n"),
      };
    }

    const contextStage = stageFromContext(context);
    const actions = Array.isArray(context.available_actions) ? context.available_actions.join(", ") : "";
    transcript.push(`DecisionContext ${String(context.capability ?? context.stage ?? "unknown")}: ${actions}`);
    onProgress?.({ contextStage, stage: contextStage, status: "ok", message: `DecisionContext ready: ${actions}` } as ProgressEvent);

    if (isExportContext(context)) {
      onProgress?.({ stage: "audit", status: "start", message: "Running Pi semantic safety-net audit" });
      const audit = await runPiHarnessAudit(ctx.cwd, workDir, ctx);
      transcript.push(audit.text);
      const auditOk = audit.status === "success" && !isAuditPublicationBlocking(audit.auditStatus);
      onProgress?.({
        stage: "audit",
        status: auditOk ? "ok" : "error",
        message: `Semantic audit: ${audit.auditStatus ?? "INCOMPLETE"}`,
      });
      onProgress?.({
        stage: "done",
        status: auditOk ? "ok" : "error",
        message: auditOk ? "Pipeline completed" : "Pipeline completed but audit blocks publication",
      });
      return { status: auditOk ? "success" : "failure", text: transcript.join("\n") };
    }

    let action: DecisionActionPayload;
    try {
      action = await chooseDecisionAction(context, ctx);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      return { status: ctx.signal?.aborted ? "cancelled" : "failure", text: [...transcript, message].join("\n") };
    }
    transcript.push(`Pi chose ${action.action_id}${action.rationale ? ` — ${action.rationale}` : ""}`);

    if (action.action_id === "request_operator" || action.action_id === "present_for_review") {
      const question = actionQuestion(action, context);
      onProgress?.({ stage: contextStage, status: "skip", message: `Operator input required: ${question}` });
      return {
        status: "needs_input",
        text: [...transcript, `Operator input required: ${question}`].join("\n"),
      };
    }

    if (action.action_id === "recover_source") {
      const source = recoverySource(action, context);
      if (!source) {
        return {
          status: "failure",
          text: [...transcript, "recover_source did not name one of the blocked sources from DecisionContext."].join("\n"),
        };
      }
      try {
        onProgress?.({ stage: "scraping-extended", status: "start", message: `Pi browser recovery: ${source}` });
        const recovery = await recoverSourceWithPiBrowser(source, ctx, {
          workDir,
          mergeAfter: false,
          onActivity: (message) => onProgress?.({ stage: "scraping-extended", status: "start", message }),
        });
        transcript.push(recovery.text);
        onProgress?.({
          stage: "scraping-extended",
          status: "ok",
          message: `${source}: ${recovery.eventCount} events recovered`,
          blockedName: source,
          eventCount: recovery.eventCount,
        });
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : String(err);
        onProgress?.({ stage: "scraping-extended", status: "error", message, error: message, blockedName: source });
        return { status: "failure", text: [...transcript, message].join("\n") };
      }

      // recovery-inject has updated canonical cache evidence. Ask the repo to
      // rerun Stage 2 through its normal path rather than Pi normalizing or
      // deciding source sufficiency itself.
      pendingAction = {
        action_id: "retry_stage",
        arguments: { stage: "2" },
        rationale: `Pi browser recovery completed for ${source}; rerun canonical Stage 2 to normalize and reassess source evidence.`,
      };
      resumeFrom = "3";
      continue;
    }

    pendingAction = action;
    resumeFrom = resumeAfterContext(context);
  }

  return {
    status: "failure",
    text: [...transcript, "Stopped after 50 interactive decisions to avoid an unbounded adapter loop."].join("\n"),
  };
}
