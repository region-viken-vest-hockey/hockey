import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { normalizeArgs } from "../lib/arg-utils";
import { parseStatusArgs, parseLogsArgs, parseCalendarsArgs, parseScrapeArgs, parseScrapeLlmArgs } from "../lib/parsers";
import { runPipeline, type PipelineRunResult } from "../lib/pipeline-runner";
import { interactiveGuide } from "../lib/interactive-guide";
import { LOG_LEVELS } from "../lib/types";
import { loadBookupEnvFromDotenvx } from "../lib/dotenvx-helpers";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";

const DEFAULT_PUBLISH_PLANNER_ITERATIONS = 1;

function buildStatusCommandArgs(rawArgs: unknown, cwd: string): string[] {
  const params = parseStatusArgs(rawArgs);
  return ["status", "--work-dir", resolve(cwd, params.work_dir ?? ".pipeline")];
}

function buildLogsCommandArgs(rawArgs: unknown, cwd: string): string[] {
  const params = parseLogsArgs(rawArgs);
  const args = ["logs", "--work-dir", resolve(cwd, params.work_dir ?? ".pipeline")];
  switch (params.subcommand) {
    case "show":
      args.push("show", params.run_id ?? "latest");
      break;
    case "stats":
      args.push("stats");
      break;
    case "list":
    default:
      args.push("list", "--count", String(params.count ?? 10));
      break;
  }
  return args;
}

function buildCalendarsCommandArgs(rawArgs: unknown, cwd: string): string[] {
  const params = parseCalendarsArgs(rawArgs);
  const args = ["calendars", "--work-dir", resolve(cwd, params.work_dir ?? ".pipeline")];
  if (params.refresh) args.push("--refresh");
  return args;
}

function buildPublishCommandArgs(rawArgs: unknown): string[] {
  const tokens = normalizeArgs(rawArgs).split(/\s+/).filter(Boolean);
  const args = ["operator", "run", "--resume-from", "1", "--publish", "--confirm-public"];
  if (!tokens.includes("--iterations")) args.push("--iterations", String(DEFAULT_PUBLISH_PLANNER_ITERATIONS));
  args.push(...tokens);
  return args;
}

function buildScrapeCommandArgs(rawArgs: unknown, cwd: string): string[] {
  const params = parseScrapeArgs(rawArgs);
  const args = ["scrape"];
  if (params.club) args.push("--club", params.club);
  args.push("--work-dir", resolve(cwd, params.work_dir ?? ".pipeline"));
  if (params.manual_bookup_login) args.push("--manual-bookup-login");
  if (typeof params.manual_bookup_login_timeout === "number" && Number.isFinite(params.manual_bookup_login_timeout)) {
    args.push("--manual-bookup-login-timeout", String(params.manual_bookup_login_timeout));
  }
  return args;
}

function buildScrapeLlmCommandArgs(rawArgs: unknown, cwd: string): string[] {
  const params = parseScrapeLlmArgs(rawArgs);
  const args = ["scrape-llm"];
  if (params.club) args.push("--club", params.club);
  args.push("--work-dir", resolve(cwd, params.work_dir ?? ".pipeline"));
  if (params.export_dir) args.push("--export-dir", resolve(cwd, params.export_dir));
  if (params.endpoint) args.push("--endpoint", params.endpoint);
  if (params.model) args.push("--model", params.model);
  if (typeof params.max_iterations === "number" && Number.isFinite(params.max_iterations)) {
    args.push("--max-iterations", String(params.max_iterations));
  }
  if (params.cache_results !== false) args.push("--cache-results");
  if (params.debug_screenshots) args.push("--debug-screenshots");
  return args;
}

async function runRepoCli(commandArgs: string[], ctx: ExtensionContext, timeout = 60_000): Promise<{ status: "success" | "failure"; text: string }> {
  const python = resolve(ctx.cwd, "venv", "bin", "python3");
  const exe = existsSync(python) ? python : "python3";
  try {
    await loadBookupEnvFromDotenvx(ctx.cwd);
    const { execFile } = await import("node:child_process");
    const { promisify } = await import("node:util");
    const execFileAsync = promisify(execFile);
    const { stdout, stderr } = await execFileAsync(exe, ["-m", "tournament_scheduler.cli.rvv_cli", ...commandArgs], {
      cwd: ctx.cwd,
      timeout,
      maxBuffer: 10 * 1024 * 1024,
    });
    const parts = [stdout.trim(), stderr.trim() ? `[stderr] ${stderr.trim()}` : ""].filter(Boolean);
    return { status: "success", text: parts.join("\n") };
  } catch (err: unknown) {
    const execError = err as { stdout?: string; stderr?: string; message?: string };
    const parts = [
      execError.stdout?.trim(),
      execError.stderr?.trim() ? `[stderr] ${execError.stderr.trim()}` : "",
      execError.stdout || execError.stderr ? "" : (execError.message ?? String(err)),
    ].filter(Boolean);
    return { status: "failure", text: parts.join("\n") };
  }
}

async function runCalendars(rawArgs: unknown, ctx: ExtensionContext): Promise<{ status: "success" | "failure"; text: string }> {
  const params = parseCalendarsArgs(rawArgs);
  const result = await runRepoCli(buildCalendarsCommandArgs(rawArgs, ctx.cwd), ctx, params.refresh ? 300_000 : 60_000);

  if (result.status === "success" && !params.refresh) {
    try {
      const { copyFileSync } = await import("node:fs");
      const absWorkDir = resolve(ctx.cwd, params.work_dir ?? ".pipeline");
      const src = resolve(ctx.cwd, "export", "season_plan.html");
      const dst = resolve(absWorkDir, "season_plan.html");
      if (existsSync(src)) copyFileSync(src, dst);
    } catch { /* best-effort */ }
  }

  return result;
}

async function runPublish(rawArgs: unknown, ctx: ExtensionContext): Promise<{ status: "success" | "failure"; text: string }> {
  return runRepoCli(buildPublishCommandArgs(rawArgs), ctx, 1_800_000);
}

function wantsPublish(rawArgs: unknown): boolean {
  return normalizeArgs(rawArgs).split(/\s+/).filter(Boolean).includes("--publish");
}

function notifyPipelineResult(ctx: ExtensionContext, result: PipelineRunResult): void {
  const level = result.status === "success" ? "info" : result.status === "cancelled" ? "warning" : "error";
  ctx.ui.notify(result.text, level);
}

export default function rvvMiniputt(pi: ExtensionAPI): void {
  // -------------------------------------------------------------------------
  // /rvv-miniputt run
  // -------------------------------------------------------------------------
  pi.registerCommand("rvv-miniputt run", {
    description:
      "Kjør den firetrinns sesongplanleggingspipelinen for RVV-hockeyklubber. " +
      "Støtter gjenopptak fra et bestemt trinn. Trinn 3 kjører ett planleggingsforsøk som standard (sett --iterations for flere frø).\n" +
      "Valgfrie flagg: --input <input.xlsx> --work-dir <sti> --resume-from <trinn> --export-dir <sti> " +
      "--log-level <info|verbose> --force-refresh --iterations <N> --publish\n" +
      "Trinn 2 gjenbruker kalenderdata fra cache (under 24 timer gammel) med mindre --force-refresh er satt.\n" +
      "Hver kjøring logges strukturelt i eksportmappen som run-<dato>.jsonl for selvforbedringsanalyse.",
    getArgumentCompletions: (prefix) => {
      const words = ["--input", "--work-dir", "--resume-from", "--export-dir", "--log-level", "--force-refresh", "--iterations", "--publish"];
      const filtered = words.filter((w) => w.startsWith(prefix));
      if (prefix.startsWith("--log-level")) {
        return LOG_LEVELS.map((value) => ({ value, label: value }));
      }
      return filtered.length ? filtered.map((value) => ({ value, label: value })) : null;
    },
    handler: async (args, ctx) => {
      if (wantsPublish(args)) {
        ctx.ui.setStatus("rvv-miniputt", "Kjører og publiserer...");
        const result = await runPublish(args, ctx);
        ctx.ui.setStatus("rvv-miniputt", undefined);
        ctx.ui.notify(result.text, result.status === "success" ? "info" : "error");
        return;
      }

      const result = await runPipeline(args, ctx, (e) => {
        if (e.status === "error") {
          ctx.ui.notify(`❌ ${e.message}`, "error");
        } else if (e.stage === "done") {
          ctx.ui.setStatus("rvv-miniputt", undefined);
          ctx.ui.notify(e.status === "ok" ? "✅ Pipeline fullført" : `❌ ${e.message}`, e.status === "ok" ? "info" : "error");
        } else {
          const stageLabel = e.stage === "scraping-extended"
            ? `2x: ${e.blockedName ?? "?"}`
            : e.stage === "scraping" ? "2/4 Skraping"
            : e.stage === "config" ? "1/4 Konfig"
            : e.stage === "planning" ? "3/4 Planlegging"
            : e.stage === "export" ? "4/4 Eksport"
            : e.stage;
          ctx.ui.setStatus("rvv-miniputt", `${stageLabel}...`);
          if (e.status === "ok" && e.stage !== "scraping-extended") {
            ctx.ui.notify(`✅ ${e.message}`, "info");
          }
        }
      });
      notifyPipelineResult(ctx, result);
    },
  });

  // -------------------------------------------------------------------------
  // /rvv-miniputt publish
  // -------------------------------------------------------------------------
  pi.registerCommand("rvv-miniputt publish", {
    description:
      "Kjør hele RVV Miniputt-pipelinen og publiser resultatet til GitHub Pages. " +
      "Dette bruker repoets operator-flyt: operator run --resume-from 1 --publish --confirm-public, " +
      "slik at Pages-publisering faktisk commits/pushes til gh-pages og verifiseres etterpå.\n" +
      "Valgfrie flagg: --input <input.xlsx> --work-dir <sti> --export-dir <sti> " +
      "--log-level <info|verbose> --force-refresh --non-strict --allow-missing-sources " +
      "--iterations <N> --no-push --dry-run --no-verify",
    getArgumentCompletions: (prefix) => {
      const words = [
        "--input", "--work-dir", "--export-dir", "--log-level", "--force-refresh", "--non-strict",
        "--allow-missing-sources", "--iterations", "--no-push", "--dry-run", "--no-verify",
        "--verify-max-attempts", "--verify-retry-delay",
      ];
      if (prefix.startsWith("--log-level")) {
        return LOG_LEVELS.map((value) => ({ value, label: value }));
      }
      return words.filter((w) => w.startsWith(prefix)).map((value) => ({ value, label: value }));
    },
    handler: async (args, ctx) => {
      ctx.ui.setStatus("rvv-miniputt", "Kjører og publiserer...");
      const result = await runPublish(args, ctx);
      ctx.ui.setStatus("rvv-miniputt", undefined);
      ctx.ui.notify(result.text, result.status === "success" ? "info" : "error");
    },
  });

  // -------------------------------------------------------------------------
  // /rvv-miniputt guide — interaktiv veiviser
  // -------------------------------------------------------------------------
  pi.registerCommand("rvv-miniputt guide", {
    description:
      "Åpne en interaktiv veiviser som stiller spørsmål om hva du vil gjøre " +
      "og guider deg gjennom pipeline-prosessen trinn for trinn.\n" +
      "Ingen parametere nødvendig — veiviseren spor deg om alt som trengs.\n" +
      "Anbefalt for nye brukere og énskjørs-kjøringer.",
    handler: async (_args, ctx) => {
      await interactiveGuide(ctx);
    },
  });

  // -------------------------------------------------------------------------
  // /rvv-miniputt status
  // -------------------------------------------------------------------------
  pi.registerCommand("rvv-miniputt status", {
    description:
      "Vis gjeldende status for alle fire trinn i sesongplanleggingspipelinen.\n" +
      "Valgfritt flagg: --work-dir <sti>",
    handler: async (args, ctx) => {
      const result = await runRepoCli(buildStatusCommandArgs(args, ctx.cwd), ctx);
      ctx.ui.notify(result.text, result.status === "success" ? "info" : "error");
    },
  });

  // -------------------------------------------------------------------------
  // /rvv-miniputt logs
  // -------------------------------------------------------------------------
  pi.registerCommand("rvv-miniputt logs", {
    description:
      "Vis pipeline-logging for selvforbedring. Underspørsmål:\n" +
      "  list              — vis de siste kjøringene (standard)\n" +
      "  show <run-id>     — vis detaljer for en bestemt kjøring\n" +
      "  show latest       — vis detaljer for den nyeste kjøringen\n" +
      "  stats             — vis aggregerte selvforbedringsstatistikker\n" +
      "Viser også turneringsoppdateringer (team-drop/date-move) i show-visningen.\n" +
      "Flagg: --count <N> (standard 10), --work-dir <sti>",
    getArgumentCompletions: (prefix) => {
      const words = ["list", "show", "stats", "--count", "--work-dir"];
      return words.filter((w) => w.startsWith(prefix)).map((value) => ({ value, label: value }));
    },
    handler: async (args, ctx) => {
      const result = await runRepoCli(buildLogsCommandArgs(args, ctx.cwd), ctx);
      ctx.ui.notify(result.text, result.status === "success" ? "info" : "error");
    },
  });

  // -------------------------------------------------------------------------
  // /rvv-miniputt calendars
  // -------------------------------------------------------------------------
  pi.registerCommand("rvv-miniputt calendars", {
    description:
      "Generer kalender-rapporter. Uten flagg: rask regenerering fra cache.\n" +
      "Flagg: --refresh (full re-skraping via rvv-miniputt CLI), --work-dir <sti>\n" +
      "Rapportene ligger i .pipeline/calendars.html og .pipeline/season_plan.html.",
    getArgumentCompletions: (prefix) => {
      const words = ["--refresh", "--work-dir"];
      return words.filter((w) => w.startsWith(prefix)).map((value) => ({ value, label: value }));
    },
    handler: async (args, ctx) => {
      const result = await runCalendars(args, ctx);
      ctx.ui.notify(result.text, result.status === "success" ? "info" : "error");
    },
  });

  // -------------------------------------------------------------------------
  // /rvv-miniputt scrape
  // -------------------------------------------------------------------------
  pi.registerCommand("rvv-miniputt scrape", {
    description:
      "Skraper en enkelt kalenderkilde for feilsøking.\n" +
      "Flagg: --club <navn> --work-dir <sti>",
    getArgumentCompletions: (prefix) => {
      const words = ["--club", "--work-dir"];
      return words.filter((w) => w.startsWith(prefix)).map((value) => ({ value, label: value }));
    },
    handler: async (args, ctx) => {
      const result = await runRepoCli(buildScrapeCommandArgs(args, ctx.cwd), ctx, 120_000);
      ctx.ui.notify(result.text, result.status === "success" ? "info" : "error");
    },
  });

  // -------------------------------------------------------------------------
  // /rvv-miniputt scrape-llm
  // -------------------------------------------------------------------------
  pi.registerCommand("rvv-miniputt scrape-llm", {
    description:
      "Skraper en enkelt kalenderkilde med LLM-styrt navigering.\n" +
      "Flagg: --club <navn> --work-dir <sti> --export-dir <sti> --endpoint <url> --model <navn>",
    getArgumentCompletions: (prefix) => {
      const words = ["--club", "--work-dir", "--export-dir", "--endpoint", "--model", "--max-iterations", "--cache-results", "--debug-screenshots"];
      return words.filter((w) => w.startsWith(prefix)).map((value) => ({ value, label: value }));
    },
    handler: async (args, ctx) => {
      const result = await runRepoCli(buildScrapeLlmCommandArgs(args, ctx.cwd), ctx, 300_000);
      ctx.ui.notify(result.text, result.status === "success" ? "info" : "error");
    },
  });

  // ===========================================================================
  // Agent-callable tools
  //
  // The /rvv-miniputt commands above are Pi slash commands, NOT shell binaries —
  // running `/rvv-miniputt run` via the Bash tool will fail with "command not
  // found". These tools are the agent-callable equivalents: call them directly
  // instead of shelling out, and instead of reimplementing the pipeline by
  // invoking tournament_scheduler.pipeline.stageN_* Python modules by hand
  // (which skips checkpointing, resumption, and structured run logging).
  // ===========================================================================

  pi.registerTool({
    name: "rvv_miniputt_run",
    label: "RVV Miniputt: Run Pipeline",
    description:
      "Run the RVV Miniputt season-planning pipeline (config → scraping → planning → export). " +
      "Stage 3 runs one planning attempt by default (override with --iterations for multiple seeds). " +
      "This is the agent-callable equivalent of the '/rvv-miniputt run' slash command — that " +
      "command is not a shell binary and cannot be invoked via Bash.",
    promptSnippet: "Run the RVV Miniputt season-planning pipeline",
    promptGuidelines: [
      "Use rvv_miniputt_run instead of running '/rvv-miniputt run' via Bash — it is a Pi slash command, not a shell command.",
      "Do not reimplement the pipeline by calling tournament_scheduler.pipeline.stageN_* Python modules directly; rvv_miniputt_run runs the full orchestrated pipeline with checkpointing and structured logging.",
    ],
    parameters: Type.Object({
      args: Type.Optional(Type.String({
        description: "Same flags as '/rvv-miniputt run', e.g. '--resume-from 2 --log-level verbose --manual-bookup-login'",
      })),
    }),
    async execute(_toolCallId, params, _signal, onUpdate, ctx) {
      if (wantsPublish(params.args ?? "")) {
        onUpdate?.({ content: [{ type: "text", text: "[publish] ▶ Kjører pipeline og publiserer til GitHub Pages" }] });
        const result = await runPublish(params.args ?? "", ctx);
        return { content: [{ type: "text", text: result.text }], details: result };
      }

      const result = await runPipeline(params.args ?? "", ctx, (e) => {
        onUpdate?.({
          content: [{ type: "text", text: `[${e.stage}] ${e.status === "start" ? "▶" : e.status === "ok" ? "✅" : e.status === "skip" ? "⏭️" : "❌"} ${e.message}` }],
        });
      });
      return { content: [{ type: "text", text: result.text }], details: result };
    },
  });

  pi.registerTool({
    name: "rvv_miniputt_publish",
    label: "RVV Miniputt: Publish to GitHub Pages",
    description:
      "Run the full RVV Miniputt pipeline and publish the result to GitHub Pages. " +
      "Agent-callable equivalent of the '/rvv-miniputt publish' slash command. " +
      "Uses the repo operator flow with --publish --confirm-public so the gh-pages branch is actually updated.",
    promptSnippet: "Run and publish RVV Miniputt to GitHub Pages",
    promptGuidelines: [
      "Use rvv_miniputt_publish instead of trying to pass publish flags through '/rvv-miniputt run'.",
      "This performs the real Pages publish step (commit/push to gh-pages) after a successful pipeline run.",
    ],
    parameters: Type.Object({
      args: Type.Optional(Type.String({
        description: "Same flags as '/rvv-miniputt publish', e.g. '--force-refresh --iterations 3' when you explicitly want a wider seed search",
      })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const result = await runPublish(params.args ?? "", ctx);
      return { content: [{ type: "text", text: result.text }], details: result };
    },
  });

  pi.registerTool({
    name: "rvv_miniputt_status",
    label: "RVV Miniputt: Status",
    description:
      "Show the current status of all four RVV Miniputt pipeline stages. " +
      "Agent-callable equivalent of the '/rvv-miniputt status' slash command.",
    promptSnippet: "Show RVV Miniputt pipeline stage status",
    parameters: Type.Object({
      args: Type.Optional(Type.String({
        description: "Same flags as '/rvv-miniputt status', e.g. '--work-dir .pipeline'",
      })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const result = await runRepoCli(buildStatusCommandArgs(params.args ?? "", ctx.cwd), ctx);
      return { content: [{ type: "text", text: result.text }], details: result };
    },
  });

  pi.registerTool({
    name: "rvv_miniputt_logs",
    label: "RVV Miniputt: Logs",
    description:
      "Read RVV Miniputt pipeline run logs and self-improvement statistics " +
      "('list', 'show <run-id>'/'show latest', or 'stats'). " +
      "Agent-callable equivalent of the '/rvv-miniputt logs' slash command.",
    promptSnippet: "Read RVV Miniputt pipeline run logs and stats",
    parameters: Type.Object({
      args: Type.Optional(Type.String({
        description: "Same arguments as '/rvv-miniputt logs', e.g. 'show latest', 'stats', 'list --count 5'",
      })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const result = await runRepoCli(buildLogsCommandArgs(params.args ?? "", ctx.cwd), ctx);
      return { content: [{ type: "text", text: result.text }], details: result };
    },
  });

  pi.registerTool({
    name: "rvv_miniputt_calendars",
    label: "RVV Miniputt: Calendars",
    description:
      "Generate calendar reports from cache, or force a full re-scrape with '--refresh'. " +
      "Agent-callable equivalent of the '/rvv-miniputt calendars' slash command.",
    promptSnippet: "Generate RVV Miniputt calendar reports",
    parameters: Type.Object({
      args: Type.Optional(Type.String({
        description: "Same flags as '/rvv-miniputt calendars', e.g. '--refresh'",
      })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const result = await runCalendars(params.args ?? "", ctx);
      return { content: [{ type: "text", text: result.text }], details: result };
    },
  });

  pi.registerTool({
    name: "rvv_miniputt_scrape",
    label: "RVV Miniputt: Scrape",
    description:
      "Scrape a single club's calendar for troubleshooting. " +
      "Agent-callable equivalent of the '/rvv-miniputt scrape' slash command.",
    promptSnippet: "Scrape a single RVV Miniputt club calendar",
    parameters: Type.Object({
      args: Type.Optional(Type.String({
        description: "Same flags as '/rvv-miniputt scrape', e.g. '--club Jar --work-dir .pipeline'",
      })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const parsed = parseScrapeArgs(params.args ?? "");
      const timeout = parsed.manual_bookup_login ? 900_000 : 120_000;
      const result = await runRepoCli(buildScrapeCommandArgs(params.args ?? "", ctx.cwd), ctx, timeout);
      return { content: [{ type: "text", text: result.text }], details: result };
    },
  });

  pi.registerTool({
    name: "rvv_miniputt_scrape_llm",
    label: "RVV Miniputt: Scrape LLM",
    description:
      "Scrape a single club's calendar with LLM-guided browser navigation. " +
      "Agent-callable equivalent of the '/rvv-miniputt scrape-llm' slash command.",
    promptSnippet: "Scrape a single RVV Miniputt club calendar with LLM navigation",
    parameters: Type.Object({
      args: Type.Optional(Type.String({
        description: "Same flags as '/rvv-miniputt scrape-llm', e.g. '--club Holmen --debug-screenshots'",
      })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const result = await runRepoCli(buildScrapeLlmCommandArgs(params.args ?? "", ctx.cwd), ctx, 300_000);
      return { content: [{ type: "text", text: result.text }], details: result };
    },
  });
}
