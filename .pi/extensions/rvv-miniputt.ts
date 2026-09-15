import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { resolve } from "node:path";
import { tokenizeArgs } from "../lib/arg-utils";
import { recoverSourceWithPiBrowser } from "../lib/browser-recovery";
import { interactiveGuide } from "../lib/interactive-guide";
import { isAuditPublicationBlocking, runPiHarnessAudit } from "../lib/operator-audit";
import { runPipeline, type PipelineRunResult } from "../lib/pipeline-runner";
import { formatProcessFailure, runRepoCli as runCanonicalCli } from "../lib/repo-cli";
import { LOG_LEVELS, type ProgressEvent } from "../lib/types";

interface CommandResult {
  status: "success" | "failure" | "cancelled";
  text: string;
}

function optionValue(tokens: string[], name: string): string | undefined {
  const index = tokens.lastIndexOf(name);
  return index >= 0 && index + 1 < tokens.length ? tokens[index + 1] : undefined;
}

function wantsPublish(rawArgs: unknown): boolean {
  return tokenizeArgs(rawArgs).includes("--publish");
}

async function runSimple(
  command: string,
  rawArgs: unknown,
  ctx: ExtensionContext,
  timeoutMs = 300_000,
): Promise<CommandResult> {
  const result = await runCanonicalCli(ctx, [command, ...tokenizeArgs(rawArgs)], { timeoutMs });
  if (result.cancelled || ctx.signal?.aborted) return { status: "cancelled", text: "Avbrutt av bruker." };
  if (result.code !== 0) return { status: "failure", text: formatProcessFailure(result) };
  return {
    status: "success",
    text: [result.stdout.trim(), result.stderr.trim() ? `[stderr] ${result.stderr.trim()}` : ""].filter(Boolean).join("\n"),
  };
}

async function runPublish(rawArgs: unknown, ctx: ExtensionContext): Promise<CommandResult> {
  const tokens = tokenizeArgs(rawArgs);
  const workDir = resolve(ctx.cwd, optionValue(tokens, "--work-dir") ?? ".pipeline");

  const audit = await runPiHarnessAudit(ctx.cwd, workDir, ctx);
  const auditOk = audit.status === "success" && !isAuditPublicationBlocking(audit.auditStatus);
  if (!auditOk) {
    return {
      status: "failure",
      text: [audit.text, "Publisering ble ikke forsøkt fordi semantisk revisjon blokkerer den."].join("\n\n"),
    };
  }

  const publishTokens = tokens.filter((token) => token !== "--confirm-public");
  const result = await runCanonicalCli(
    ctx,
    ["operator", "publish", "--confirm-public", ...publishTokens],
    { timeoutMs: 30 * 60 * 1000 },
  );
  if (result.cancelled || ctx.signal?.aborted) return { status: "cancelled", text: "Publisering avbrutt av bruker." };
  if (result.code !== 0) {
    return { status: "failure", text: [audit.text, formatProcessFailure(result)].join("\n\n") };
  }
  return {
    status: "success",
    text: [audit.text, result.stdout.trim(), result.stderr.trim()].filter(Boolean).join("\n\n"),
  };
}

async function runPiScrapeLlm(rawArgs: unknown, ctx: ExtensionContext): Promise<CommandResult> {
  const tokens = tokenizeArgs(rawArgs);
  const club = optionValue(tokens, "--club");
  if (!club) return { status: "failure", text: "--club <navn> er påkrevd." };
  const workDir = resolve(ctx.cwd, optionValue(tokens, "--work-dir") ?? ".pipeline");
  const maxIterationsRaw = optionValue(tokens, "--max-iterations");
  const maxIterations = maxIterationsRaw ? Number.parseInt(maxIterationsRaw, 10) : undefined;

  try {
    const recovery = await recoverSourceWithPiBrowser(club, ctx, {
      workDir,
      mergeAfter: true,
      maxIterations: Number.isFinite(maxIterations) ? maxIterations : undefined,
      onActivity: (message) => ctx.ui.setStatus("rvv-miniputt", message),
    });
    return { status: "success", text: recovery.text };
  } catch (err: unknown) {
    return { status: ctx.signal?.aborted ? "cancelled" : "failure", text: err instanceof Error ? err.message : String(err) };
  } finally {
    ctx.ui.setStatus("rvv-miniputt", undefined);
  }
}

function notifyResult(ctx: ExtensionContext, result: CommandResult | PipelineRunResult): void {
  const level = result.status === "success"
    ? "info"
    : result.status === "cancelled" || result.status === "needs_input"
      ? "warning"
      : "error";
  ctx.ui.notify(result.text, level);
}

function progressLabel(event: ProgressEvent): string {
  if (event.stage === "config") return "1/4 Konfig";
  if (event.stage === "scraping") return "2/4 Skraping";
  if (event.stage === "scraping-extended") return `2x ${event.blockedName ?? "Recovery"}`;
  if (event.stage === "planning") return "3/4 Planlegging";
  if (event.stage === "export") return "4/4 Eksport";
  if (event.stage === "audit") return "Audit";
  return event.stage;
}

function handleProgress(ctx: ExtensionContext, event: ProgressEvent): void {
  if (event.stage === "done") {
    ctx.ui.setStatus("rvv-miniputt", undefined);
    if (event.status === "ok") ctx.ui.notify(`✅ ${event.message}`, "info");
    return;
  }
  ctx.ui.setStatus("rvv-miniputt", `${progressLabel(event)}: ${event.message}`);
  if (event.status === "error") ctx.ui.notify(`❌ ${event.message}`, "error");
}

function pipelineToolUpdate(event: ProgressEvent): string {
  const icon = event.status === "start" ? "▶" : event.status === "ok" ? "✅" : event.status === "skip" ? "⏭️" : "❌";
  return `[${event.stage}] ${icon} ${event.message}`;
}

export default function rvvMiniputt(pi: ExtensionAPI): void {
  pi.registerCommand("rvv-miniputt run", {
    description:
      "Kjør RVV Miniputt gjennom repoets kanoniske interaktive pipeline. Pi leverer bare UI, modellvalg, cancellation og browser-recovery; Python eier Stage 1–4, defaults, regler og verifikasjon.",
    getArgumentCompletions: (prefix) => {
      const words = ["--input", "--work-dir", "--resume-from", "--export-dir", "--log-level", "--force-refresh", "--iterations", "--non-strict"];
      if (prefix.startsWith("--log-level")) return LOG_LEVELS.map((value) => ({ value, label: value }));
      return words.filter((word) => word.startsWith(prefix)).map((value) => ({ value, label: value }));
    },
    handler: async (args, ctx) => {
      if (wantsPublish(args)) {
        ctx.ui.notify("Publisering er en egen capability. Kjør /rvv-miniputt run først og /rvv-miniputt publish etterpå.", "error");
        return;
      }
      const result = await runPipeline(args, ctx, (event) => handleProgress(ctx, event));
      ctx.ui.setStatus("rvv-miniputt", undefined);
      notifyResult(ctx, result);
    },
  });

  pi.registerCommand("rvv-miniputt publish", {
    description:
      "Publiser eksisterende Stage 4-eksport. Pi kjører harness-audit fra repoets audit-context og delegere selve publiseringen til operator publish.",
    handler: async (args, ctx) => {
      ctx.ui.setStatus("rvv-miniputt", "Audit/publisering...");
      const result = await runPublish(args, ctx);
      ctx.ui.setStatus("rvv-miniputt", undefined);
      notifyResult(ctx, result);
    },
  });

  pi.registerCommand("rvv-miniputt guide", {
    description: "Åpne Pi-veiviseren for RVV Miniputt.",
    handler: async (_args, ctx) => interactiveGuide(ctx),
  });

  pi.registerCommand("rvv-miniputt status", {
    description: "Vis kanonisk pipeline-status fra repo-CLI.",
    handler: async (args, ctx) => notifyResult(ctx, await runSimple("status", args, ctx)),
  });

  pi.registerCommand("rvv-miniputt logs", {
    description: "Vis kanoniske RVV-kjøringslogger via repo-CLI.",
    handler: async (args, ctx) => notifyResult(ctx, await runSimple("logs", args, ctx)),
  });

  pi.registerCommand("rvv-miniputt calendars", {
    description: "Generer/oppdater kalenderoversikt via repo-CLI.",
    handler: async (args, ctx) => notifyResult(ctx, await runSimple("calendars", args, ctx, 10 * 60 * 1000)),
  });

  pi.registerCommand("rvv-miniputt scrape", {
    description: "Kjør repoets deterministiske scrape for én kilde.",
    handler: async (args, ctx) => notifyResult(ctx, await runSimple("scrape", args, ctx, 10 * 60 * 1000)),
  });

  pi.registerCommand("rvv-miniputt scrape-llm", {
    description:
      "Recover én browser-kilde med Pi sin aktive modell/browser-worker, deretter repository recovery-inject + scrape-merge.",
    handler: async (args, ctx) => notifyResult(ctx, await runPiScrapeLlm(args, ctx)),
  });

  pi.registerTool({
    name: "rvv_miniputt_run",
    label: "RVV Miniputt: Run",
    description:
      "Run the canonical RVV interactive pipeline. Pi is a thin controller; repository code owns all Stage 1–4 behavior and verification.",
    promptSnippet: "Run the canonical RVV Miniputt pipeline",
    promptGuidelines: [
      "Use this tool instead of invoking stageN_* modules directly.",
      "The tool consumes repository DecisionContext objects and submits only declared DecisionActions.",
      "Pi browser recovery is used only when the repository exposes recover_source; recovered evidence returns through recovery-inject and canonical Stage 2.",
    ],
    parameters: Type.Object({
      args: Type.Optional(Type.String({ description: "Flags for rvv-miniputt run, e.g. '--resume-from 2 --log-level verbose'." })),
    }),
    async execute(_toolCallId, params, _signal, onUpdate, ctx) {
      if (wantsPublish(params.args ?? "")) {
        const text = "Publication is separate; use rvv_miniputt_run first and rvv_miniputt_publish afterwards.";
        return { content: [{ type: "text", text }], details: { status: "failure", text } };
      }
      const result = await runPipeline(params.args ?? "", ctx, (event) => {
        onUpdate?.({ content: [{ type: "text", text: pipelineToolUpdate(event) }], details: {} });
      });
      return { content: [{ type: "text", text: result.text }], details: result };
    },
  });

  pi.registerTool({
    name: "rvv_miniputt_publish",
    label: "RVV Miniputt: Publish",
    description: "Run the Pi semantic audit for the current export and publish through the canonical repository operator command.",
    promptSnippet: "Publish the current verified RVV Miniputt export",
    parameters: Type.Object({
      args: Type.Optional(Type.String({ description: "Flags for operator publish, e.g. '--dry-run' or '--no-push'." })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const result = await runPublish(params.args ?? "", ctx);
      return { content: [{ type: "text", text: result.text }], details: result };
    },
  });

  pi.registerTool({
    name: "rvv_miniputt_status",
    label: "RVV Miniputt: Status",
    description: "Show canonical RVV pipeline status.",
    promptSnippet: "Show RVV Miniputt pipeline status",
    parameters: Type.Object({ args: Type.Optional(Type.String()) }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const result = await runSimple("status", params.args ?? "", ctx);
      return { content: [{ type: "text", text: result.text }], details: result };
    },
  });

  pi.registerTool({
    name: "rvv_miniputt_logs",
    label: "RVV Miniputt: Logs",
    description: "Read canonical RVV run logs.",
    promptSnippet: "Read RVV Miniputt logs",
    parameters: Type.Object({ args: Type.Optional(Type.String()) }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const result = await runSimple("logs", params.args ?? "", ctx);
      return { content: [{ type: "text", text: result.text }], details: result };
    },
  });

  pi.registerTool({
    name: "rvv_miniputt_calendars",
    label: "RVV Miniputt: Calendars",
    description: "Generate calendar reports through the canonical repo CLI.",
    promptSnippet: "Generate RVV calendar reports",
    parameters: Type.Object({ args: Type.Optional(Type.String()) }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const result = await runSimple("calendars", params.args ?? "", ctx, 10 * 60 * 1000);
      return { content: [{ type: "text", text: result.text }], details: result };
    },
  });

  pi.registerTool({
    name: "rvv_miniputt_scrape",
    label: "RVV Miniputt: Scrape",
    description: "Run deterministic single-source scraping through the canonical repo CLI.",
    promptSnippet: "Scrape one RVV calendar source deterministically",
    parameters: Type.Object({ args: Type.Optional(Type.String()) }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const result = await runSimple("scrape", params.args ?? "", ctx, 10 * 60 * 1000);
      return { content: [{ type: "text", text: result.text }], details: result };
    },
  });

  pi.registerTool({
    name: "rvv_miniputt_scrape_llm",
    label: "RVV Miniputt: Browser Recovery",
    description:
      "Recover one browser-backed calendar source using Pi's active model/browser worker and feed evidence through repository recovery-inject + scrape-merge.",
    promptSnippet: "Recover one RVV calendar source with Pi browser navigation",
    parameters: Type.Object({
      args: Type.Optional(Type.String({ description: "Requires '--club <name>'; optional '--work-dir' and '--max-iterations'." })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const result = await runPiScrapeLlm(params.args ?? "", ctx);
      return { content: [{ type: "text", text: result.text }], details: result };
    },
  });
}
