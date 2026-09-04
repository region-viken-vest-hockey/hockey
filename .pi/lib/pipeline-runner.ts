// ---------------------------------------------------------------------------
// Pipeline runner — executes all four stages
// ---------------------------------------------------------------------------

import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { copyFileSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import { basename, resolve } from "node:path";
import { parseRunArgs } from "./parsers";
import { PipelineLogger } from "./pipeline-logger";
import {
  STAGE_ORDER,
  runStage,
  readCheckpoint,
  buildStatusText,
  resolveResumeStage,
  estimateDataVolume,
  StageCancelledError,
} from "./pipeline-helpers";
import { buildRunSummaryText } from "./log-inspector";
import { loadBookupEnvFromDotenvx } from "./dotenvx-helpers";
import type { ProgressEvent } from "./types";

export interface PipelineRunResult {
  status: "success" | "failure" | "cancelled";
  text: string;
}

// Keep the default fast and deterministic. Operators can opt into a wider
// multi-seed Stage 3 search explicitly with --iterations N when needed.
const DEFAULT_PLANNER_ITERATIONS = 1;

// Python %Y-%m-%dT%H%M format
function computeExportTimestamp(): string {
  const now = new Date();
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}${pad(now.getMinutes())}`;
}

// Pipes a recovered event list into the canonical `rvv-miniputt
// recovery-inject` CLI (stdin JSON) rather than writing the scrape cache
// directly, so the same validated writer normalizes recovered Stage 2
// evidence regardless of which harness recovered it.
async function runRecoveryInject(
  cwd: string,
  workDir: string,
  source: string,
  events: unknown[],
): Promise<void> {
  const { spawn } = await import("node:child_process");
  const python = resolve(cwd, "venv", "bin", "python3");
  const exe = existsSync(python) ? python : "python3";
  await new Promise<void>((resolvePromise, rejectPromise) => {
    const child = spawn(
      exe,
      ["-m", "tournament_scheduler.cli.rvv_cli", "recovery-inject", "--source", source, "--work-dir", workDir],
      { cwd, stdio: ["pipe", "ignore", "pipe"] },
    );
    let stderr = "";
    child.stderr?.on("data", (chunk: Buffer) => { stderr += chunk.toString(); });
    child.on("error", rejectPromise);
    child.on("close", (code: number | null) => {
      if (code === 0) resolvePromise();
      else rejectPromise(new Error(stderr.trim() || `recovery-inject exited with code ${code ?? "unknown"}`));
    });
    child.stdin?.write(JSON.stringify(events));
    child.stdin?.end();
  });
}

function writeRunLogFile(
  exportDir: string,
  runId: string,
  startedAt: Date,
  status: "running" | "success" | "failure" | "cancelled",
  lines: string[],
): string {
  const runLogPath = resolve(exportDir, `pipeline_run_${runId}.log`);
  mkdirSync(exportDir, { recursive: true });
  writeFileSync(
    runLogPath,
    [
      "# RVV Miniputt pipeline run",
      `# Run ID: ${runId}`,
      `# Status: ${status.toUpperCase()}`,
      `# Started: ${startedAt.toISOString()}`,
      `# Export dir: ${exportDir}`,
      "",
      ...lines,
    ].join("\n"),
    "utf-8",
  );
  return runLogPath;
}

export async function runPipeline(rawArgs: unknown, ctx: ExtensionContext, onProgress?: (e: ProgressEvent) => void): Promise<PipelineRunResult> {
  const params = parseRunArgs(rawArgs);
  const cwdPath = ctx.cwd;
  const inputPath  = resolve(cwdPath, params.input     ?? "input.xlsx");
  const workDir    = resolve(cwdPath, params.work_dir  ?? ".pipeline");
  const exportRoot = resolve(cwdPath, params.export_dir ?? "export");
  const resumeFrom = params.resume_from ? resolveResumeStage(params.resume_from) : 1;
  const verbose    = params.log_level === "verbose";
  // The current agent turn's abort signal — fires when the user cancels/stops
  // (e.g. Escape) while this pipeline is running. Threaded through to every
  // runStage() call below so cancelling actually kills the Python subprocess
  // instead of leaving it running detached (issue: cancellation was a no-op
  // before this, so the only way to stop a run was to kill Pi itself).
  const signal = ctx.signal;

  // Compute timestamped export subfolder
  const timestampedExportDir = params.timestamped_export === false ? exportRoot : resolve(exportRoot, computeExportTimestamp());

  mkdirSync(workDir, { recursive: true });
  mkdirSync(timestampedExportDir, { recursive: true });

  const logger = new PipelineLogger(workDir, timestampedExportDir, exportRoot);
  const logStart = new Date();
  const runLogPath = resolve(timestampedExportDir, `pipeline_run_${logger.getRunId()}.log`);

  // Determine which stages to run
  const stagesToRun = STAGE_ORDER.slice(resumeFrom - 1);
  logger.logRunMeta(
    {
      input: params.input,
      work_dir: params.work_dir,
      resume_from: params.resume_from,
      export_dir: params.export_dir,
      log_level: params.log_level,
    },
    resumeFrom,
    stagesToRun,
  );

  const lines: string[] = [];
  let overallStatus: "success" | "failure" = "success";

  // Long-running stages (Stage 3 especially, which can take 20+ minutes
  // across several seed attempts) only used to update the persisted run log
  // once the whole stage finished — so a run in progress looked frozen even
  // though it was actively working. Flush progress lines to disk as they
  // arrive instead of only at stage boundaries.
  let lastFlush = 0;
  const FLUSH_INTERVAL_MS = 2000;
  const flushLine = (line: string) => {
    lines.push(line);
    const now = Date.now();
    if (now - lastFlush < FLUSH_INTERVAL_MS) return;
    lastFlush = now;
    try {
      writeRunLogFile(timestampedExportDir, logger.getRunId(), logStart, "running", lines);
    } catch { /* best effort — don't let logging failures abort the run */ }
  };

  lines.push(`=== RVV Miniputt Pipeline ===`);
  lines.push(`Kjøring: ${logger.getRunId()}`);
  lines.push(`Logg: ${logger.getLogPath()}`);
  lines.push(`Arbeidskatalog: ${workDir}`);
  lines.push(`Input: ${inputPath}`);
  if (resumeFrom > 1) lines.push(`Gjenopptar fra: Trinn ${resumeFrom}`);
  if (params.manual_bookup_login) {
    const timeout = typeof params.manual_bookup_login_timeout === "number" ? params.manual_bookup_login_timeout : 300;
    lines.push(`BookUp manuell innlogging: aktiv (timeout ${timeout}s)`);
  }
  lines.push("");
  writeRunLogFile(timestampedExportDir, logger.getRunId(), logStart, "running", lines);

  // Builds the terminal result for a user-cancelled run — shared by every
  // stage below so cancelling at any point produces the same clean, logged
  // outcome instead of looking like an unexplained crash.
  const buildCancelledResult = (note: string): PipelineRunResult => {
    lines.push("");
    lines.push(`=== Kjøring avbrutt av bruker: ${note} ===`);
    logger.finalize("cancelled");
    lines.push("");
    lines.push(buildRunSummaryText(workDir, logger.getRunId()));
    try {
      writeRunLogFile(timestampedExportDir, logger.getRunId(), logStart, "cancelled", lines);
    } catch { /* best effort */ }
    lines.push(`Run log: ${runLogPath}`);
    // Clear the extension's status-bar indicator directly rather than going through
    // onProgress (which has no "cancelled" state and would print a second, wrongly
    // "error"-styled notification alongside the one the caller shows for this result).
    try { ctx.ui.setStatus("rvv-miniputt", undefined); } catch { /* best effort */ }
    return { status: "cancelled", text: lines.join("\n") };
  };

  const baseArgs = ["--work-dir", workDir];

  // -------------------------------------------------------------------
  // Stage 1 — Config
  // -------------------------------------------------------------------
  if (signal?.aborted) return buildCancelledResult("før Trinn 1");
  if (resumeFrom <= 1) {
    lines.push("Trinn 1: Laster og validerer konfigurasjon...");
    onProgress?.({ stage: "config", status: "start", message: "Laster og validerer konfigurasjon..." });
    logger.stageStart("config");
    try {
      const { stdout, stderr } = await runStage(
        cwdPath,
        "tournament_scheduler.pipeline.stage1_config",
        [...baseArgs, "--input", inputPath],
        (event) => {
          if (event.stream !== "stdout") return;
          const line = event.line.trim();
          if (!line.startsWith("[heartbeat]")) return;
          const message = line.replace(/^\[heartbeat\]\s*/, "");
          onProgress?.({ stage: "config", status: "start", message });
          flushLine(message);
        },
        signal,
      );
      if (verbose) logger.logStageOutput("config", stdout, stderr);
      if (stdout) {
        const remaining = stdout.split("\n").filter((l) => !l.trim().startsWith("[heartbeat]")).join("\n");
        if (remaining.trim()) lines.push(remaining);
      }
      if (stderr) lines.push(`[stderr] ${stderr}`);
      lines.push("Trinn 1: OK\n");
      onProgress?.({ stage: "config", status: "ok", message: "Konfigurasjon validert (OK)" });

      // Log data volume from checkpoint
      const ckpt = readCheckpoint(workDir, "stage1_config.json");
      logger.stageEnd("config", "ok", undefined, estimateDataVolume(ckpt));
    } catch (err: unknown) {
      if (err instanceof StageCancelledError) {
        logger.stageEnd("config", "failed", err.message);
        return buildCancelledResult("Trinn 1 (konfigurasjon)");
      }
      const msg = err instanceof Error ? err.message : String(err);
      lines.push(`Trinn 1 FEILET:\n${msg}`);
      onProgress?.({ stage: "config", status: "error", message: "Konfigurasjon feilet", error: msg });
      logger.stageEnd("config", "failed", msg);
      logger.finalize("failure");
      lines.push("");
      lines.push(buildRunSummaryText(workDir, logger.getRunId()));
      writeRunLogFile(timestampedExportDir, logger.getRunId(), logStart, "failure", lines);
      lines.push(`Run log: ${runLogPath}`);
      return { status: "failure", text: lines.join("\n") };
    }
  } else {
    lines.push("Trinn 1: Hoppet over (gjenopptatt)\n");
    onProgress?.({ stage: "config", status: "skip", message: "Konfigurasjon hoppet over (gjenopptatt)" });
    logger.stageStart("config");
    logger.stageEnd("config", "skipped");
  }
  writeRunLogFile(timestampedExportDir, logger.getRunId(), logStart, "running", lines);

  // -------------------------------------------------------------------
  // Stage 2 — Scraping + ScraperAgent for blocked sources
  // -------------------------------------------------------------------
  if (signal?.aborted) return buildCancelledResult("før Trinn 2");
  if (resumeFrom <= 2) {
    lines.push("Trinn 2: Skraper kalenderkilder (deterministisk)...");
    onProgress?.({ stage: "scraping", status: "start", message: "Skraper kalenderkilder (Trinn 2/4)..." });
    logger.stageStart("scraping");
    let stage2ok = true;
    let stage2error = "";
    try {
      const loadedBookupEnv = await loadBookupEnvFromDotenvx(cwdPath);
      if (loadedBookupEnv.length > 0) {
        flushLine(`BookUp credentials loaded from dotenvx (${loadedBookupEnv.join(", ")})`);
      }
      const stage2Args = [...baseArgs, "--non-strict"];
      if (params.force_refresh) stage2Args.push("--force-refresh");
      if (params.manual_bookup_login) stage2Args.push("--manual-bookup-login");
      if (typeof params.manual_bookup_login_timeout === "number" && Number.isFinite(params.manual_bookup_login_timeout)) {
        stage2Args.push("--manual-bookup-login-timeout", String(params.manual_bookup_login_timeout));
      }
      const { stdout, stderr } = await runStage(
        cwdPath,
        "tournament_scheduler.pipeline.stage2_scraping",
        stage2Args,
        (event) => {
          if (event.stream !== "stdout") return;
          const line = event.line.trim();
          if (!line.startsWith("[heartbeat]")) return;
          const message = line.replace(/^\[heartbeat\]\s*/, "");
          onProgress?.({ stage: "scraping", status: "start", message });
          flushLine(message);
        },
        signal,
      );
      if (verbose) logger.logStageOutput("scraping", stdout, stderr);
      if (stdout) {
        const remaining = stdout.split("\n").filter((l) => !l.trim().startsWith("[heartbeat]")).join("\n");
        if (remaining.trim()) lines.push(remaining);
      }
      if (stderr) lines.push(`[stderr] ${stderr}`);
    } catch (err: unknown) {
      if (err instanceof StageCancelledError) {
        logger.stageEnd("scraping", "failed", err.message);
        return buildCancelledResult("Trinn 2 (skraping)");
      }
      stage2ok = false;
      stage2error = err instanceof Error ? err.message : String(err);
      lines.push(`Trinn 2 deterministisk delvis: ${stage2error}\n`);
    }
    if (signal?.aborted) return buildCancelledResult("Trinn 2 (etter deterministisk skraping)");

    const ckpt = readCheckpoint(workDir, "stage2_scraping.json");
    let blocked: string[] = [];
    let cached: string[] = [];
    if (ckpt?.data) {
      const data = ckpt.data as Record<string, unknown>;
      blocked = (data.blocked as string[]) ?? [];
      cached = (data.cached as string[]) ?? [];
      const sources = (data.sources as Array<Record<string, unknown>>) ?? [];
      for (const s of sources) {
        const cacheTag = s.from_cache ? " (cache)" : "";
        const cacheAge = typeof s.cache_age_hours === "number" ? `, ${s.cache_age_hours}h gammel` : "";
        lines.push(`  ${s.name}: ${s.event_count} events${cacheTag}${cacheAge}`);
      }
    }

    if (blocked.length > 0) {
      flushLine(`\nTrinn 2 utvidet: Skraper ${blocked.length} blokkerte kilder med Pi...`);
      onProgress?.({ stage: "scraping-extended", status: "start", message: `Utvidet skraping: ${blocked.length} blokkerte kilder` });
      const previousManualBookupLogin = process.env.RVV_BOOKUP_MANUAL_LOGIN;
      const previousManualBookupLoginTimeout = process.env.RVV_BOOKUP_MANUAL_LOGIN_TIMEOUT;
      try {
        if (params.manual_bookup_login) process.env.RVV_BOOKUP_MANUAL_LOGIN = "1";
        if (typeof params.manual_bookup_login_timeout === "number" && Number.isFinite(params.manual_bookup_login_timeout)) {
          process.env.RVV_BOOKUP_MANUAL_LOGIN_TIMEOUT = String(params.manual_bookup_login_timeout);
        }
        const { ScraperAgent } = await import("./scraper-agent");
        const agent = new ScraperAgent(
          ctx,
          (details) => logger.logLLMInteraction("scraping-extended", details),
          (message) => {
            onProgress?.({ stage: "scraping-extended", status: "start", message });
            flushLine(`  ${message}`);
          },
        );
        await agent.start();

        // Fetch strategies from Python for blocked sources
        async function fetchStrategy(clubName: string): Promise<Record<string, unknown> | null> {
          try {
            const { execFile } = await import("node:child_process");
            const { promisify } = await import("node:util");
            const efa = promisify(execFile);
            const python = resolve(cwdPath, "venv", "bin", "python3");
            const exe = existsSync(python) ? python : "python3";
            const { stdout } = await efa(exe, [
              "-m", "tournament_scheduler.pipeline.scraper_strategies",
              "--name", clubName,
            ], { cwd: cwdPath, timeout: 10_000 });
            return JSON.parse(stdout) as Record<string, unknown>;
          } catch {
            return null;
          }
        }

        for (const name of blocked) {
          if (signal?.aborted) {
            try { await agent.close(); } catch { /* best effort */ }
            return buildCancelledResult("Trinn 2 (utvidet skraping med Pi)");
          }
          const strat = await fetchStrategy(name);
          if (!strat || !strat.url) {
            flushLine(`  ${name}: ingen strategi — hopper over`);
            continue;
          }

          await loadBookupEnvFromDotenvx(cwdPath);

          // Credential pre-flight: prompt for missing env vars
          const credEnvVars = (strat.credential_env_vars as string[]) ?? [];
          for (const envVar of credEnvVars) {
            if (!process.env[envVar]) {
              const value = await ctx.ui.input(
                `Innlogging kreves for ${name}. Angi ${envVar}:`,
                "",
              );
              if (value) {
                process.env[envVar] = value;
                flushLine(`  ${name}: ${envVar} satt (${value.length} tegn)`);
              } else {
                flushLine(`  ${name}: ${envVar} ikke angitt — scraping kan feile`);
              }
            }
          }

          flushLine(`  ${name}: skraper med ScraperAgent...`);
          onProgress?.({ stage: "scraping-extended", status: "start", message: `Skraper ${name} med LLM-agent...`, blockedName: name });
          const initialNav = (strat.initial_navigation as Array<Record<string, unknown>>) ?? [];
          const events = await agent.scrape(strat.url as string, {
            strategy: (strat.engine === "styled_calendar" ? "styledcalendar" : "auto") as any,
            iframe: (strat.has_iframe as boolean) ?? false,
            maxIterations: 25,
            initialNavigation: initialNav.length > 0 ? initialNav as any : undefined,
          });
          flushLine(`  ${name}: ${events.length} events funnet\n`);
          onProgress?.({ stage: "scraping-extended", status: "ok", message: `${name}: ${events.length} events funnet`, blockedName: name, eventCount: events.length });

          // Extracted evidence goes through the same repo-owned recovery
          // path a terminal-only harness uses (`rvv-miniputt recovery-inject`)
          // rather than Pi patching the cache file directly — this is the one
          // validated writer for recovered Stage 2 events (issue #260 Phase 5:
          // Pi does not independently define recovery validity).
          if (events.length > 0) {
            try {
              await runRecoveryInject(cwdPath, workDir, name, events);
            } catch (injectErr: unknown) {
              const msg = injectErr instanceof Error ? injectErr.message : String(injectErr);
              flushLine(`  ${name}: recovery-inject feilet — ${msg}`);
            }
          }
        }

        await agent.close();
        lines.push("Trinn 2 utvidet: OK\n");

        // Rebuild the Stage 2 checkpoint from the recovered cache — the same
        // `scrape-merge` normalization a terminal-only harness runs after
        // `recovery-inject` — so Stage 3 sees updated event counts and
        // unblocked sources instead of a stale checkpoint.
        try {
          const { execFile } = await import("node:child_process");
          const { promisify } = await import("node:util");
          const execFileAsync = promisify(execFile);
          const python = resolve(cwdPath, "venv", "bin", "python3");
          const exe = existsSync(python) ? python : "python3";
          await execFileAsync(exe, ["-m", "tournament_scheduler.cli.rvv_cli", "scrape-merge", "--work-dir", workDir], { cwd: cwdPath });
        } catch {}

        // Regenerate viewer
        try {
          const { execFile } = await import("node:child_process");
          const { promisify } = await import("node:util");
          const execFileAsync = promisify(execFile);
          const python = resolve(cwdPath, "venv", "bin", "python3");
          const exe = existsSync(python) ? python : "python3";
          await execFileAsync(exe, ["-m", "tournament_scheduler.pipeline.calendar_viewer", "--work-dir", workDir, "--export-dir", timestampedExportDir], { cwd: cwdPath });
        } catch {}
      } catch (agentErr: unknown) {
        const msg = agentErr instanceof Error ? agentErr.message : String(agentErr);
        lines.push(`ScraperAgent feilet: ${msg}\n`);
        onProgress?.({ stage: "scraping-extended", status: "error", message: `Utvidet skraping feilet: ${msg}` });
      } finally {
        if (previousManualBookupLogin === undefined) delete process.env.RVV_BOOKUP_MANUAL_LOGIN;
        else process.env.RVV_BOOKUP_MANUAL_LOGIN = previousManualBookupLogin;
        if (previousManualBookupLoginTimeout === undefined) delete process.env.RVV_BOOKUP_MANUAL_LOGIN_TIMEOUT;
        else process.env.RVV_BOOKUP_MANUAL_LOGIN_TIMEOUT = previousManualBookupLoginTimeout;
      }
    }

    const scrapingOk = stage2ok && blocked.length === 0;
    const cacheSuffix = cached.length > 0 ? ` (${cached.length} fra cache)` : "";
    onProgress?.({ stage: "scraping", status: "ok", message: scrapingOk ? `Alle kalendere skrapet (OK)${cacheSuffix}` : `Kalendere skrapet (${blocked.length} kilder krevde LLM-assistanse)${cacheSuffix}`, blockedCount: blocked.length });
    logger.stageEnd("scraping", stage2ok && blocked.length === 0 ? "ok" : "ok", undefined);
  } else {
    lines.push("Trinn 2: Hoppet over (gjenopptatt)\n");
    onProgress?.({ stage: "scraping", status: "skip", message: "Skraping hoppet over (gjenopptatt)" });
    logger.stageStart("scraping");
    logger.stageEnd("scraping", "skipped");
  }
  writeRunLogFile(timestampedExportDir, logger.getRunId(), logStart, "running", lines);

  // -------------------------------------------------------------------
  // Stage 3 — Planning
  // -------------------------------------------------------------------
  if (signal?.aborted) return buildCancelledResult("før Trinn 3");
  if (resumeFrom <= 3) {
    lines.push("Trinn 3: Bygger sesongplan...");
    onProgress?.({ stage: "planning", status: "start", message: "Bygger sesongplan (Trinn 3/4)..." });
    logger.stageStart("planning");
    try {
      const stage3Args = [...baseArgs];
      const iterations = typeof params.iterations === "number" && Number.isFinite(params.iterations)
        ? params.iterations
        : DEFAULT_PLANNER_ITERATIONS;
      stage3Args.push("--iterations", String(iterations));
      const { stdout, stderr } = await runStage(
        cwdPath,
        "tournament_scheduler.pipeline.stage3_planning",
        stage3Args,
        (event) => {
          if (event.stream !== "stdout") return;
          const line = event.line.trim();
          if (line.startsWith("[heartbeat]")) {
            const message = line.replace(/^\[heartbeat\]\s*/, "");
            onProgress?.({ stage: "planning", status: "start", message });
            flushLine(message);
            return;
          }
          if (!line.startsWith("[plan]")) return;
          const message = line.replace(/^\[plan\]\s*/, "");
          onProgress?.({ stage: "planning", status: "start", message });
          flushLine(message);
        },
        signal,
      );
      if (verbose) logger.logStageOutput("planning", stdout, stderr);
      if (stdout) {
        // [heartbeat]/[plan] lines already streamed to the log live above —
        // only append what wasn't already flushed, to avoid duplicating it.
        const remaining = stdout
          .split("\n")
          .filter((l) => {
            const trimmed = l.trim();
            return !trimmed.startsWith("[heartbeat]") && !trimmed.startsWith("[plan]");
          })
          .join("\n");
        if (remaining.trim()) lines.push(remaining);
      }
      if (stderr) lines.push(`[stderr] ${stderr}`);
      lines.push("Trinn 3: OK\n");
      onProgress?.({ stage: "planning", status: "ok", message: "Sesongplan bygget (OK)" });

      const ckpt = readCheckpoint(workDir, "stage3_planning.json");
      logger.stageEnd("planning", "ok", undefined, estimateDataVolume(ckpt));
    } catch (err: unknown) {
      if (err instanceof StageCancelledError) {
        logger.stageEnd("planning", "failed", err.message);
        return buildCancelledResult("Trinn 3 (planlegging)");
      }
      const msg = err instanceof Error ? err.message : String(err);
      lines.push(`Trinn 3 FEILET:\n${msg}`);
      onProgress?.({ stage: "planning", status: "error", message: "Planlegging feilet", error: msg });
      logger.stageEnd("planning", "failed", msg);
      logger.finalize("failure");
      lines.push("");
      lines.push(buildRunSummaryText(workDir, logger.getRunId()));
      writeRunLogFile(timestampedExportDir, logger.getRunId(), logStart, "failure", lines);
      lines.push(`Run log: ${runLogPath}`);
      return { status: "failure", text: lines.join("\n") };
    }
  } else {
    lines.push("Trinn 3: Hoppet over (gjenopptatt)\n");
    onProgress?.({ stage: "planning", status: "skip", message: "Planlegging hoppet over (gjenopptatt)" });
    logger.stageStart("planning");
    logger.stageEnd("planning", "skipped");
  }
  writeRunLogFile(timestampedExportDir, logger.getRunId(), logStart, "running", lines);

  // -------------------------------------------------------------------
  // Stage 4 — Export
  // -------------------------------------------------------------------
  if (signal?.aborted) return buildCancelledResult("før Trinn 4");
  if (resumeFrom <= 4) {
    lines.push("Trinn 4: Eksporterer til Excel, iCal og CSV...");
    onProgress?.({ stage: "export", status: "start", message: "Eksporterer til Excel, iCal og CSV (Trinn 4/4)..." });
    logger.stageStart("export");
    try {
      const { stdout, stderr } = await runStage(
        cwdPath,
        "tournament_scheduler.pipeline.stage4_export",
        [...baseArgs, "--export-dir", timestampedExportDir, "--no-timestamped-export"],
        (event) => {
          if (event.stream !== "stdout") return;
          const line = event.line.trim();
          if (line.startsWith("[heartbeat]")) {
            const message = line.replace(/^\[heartbeat\]\s*/, "");
            onProgress?.({ stage: "export", status: "start", message });
            flushLine(message);
            return;
          }
          if (!line.startsWith("[progress]")) return;
          const message = line.replace(/^\[progress\]\s*/, "");
          onProgress?.({ stage: "export", status: "start", message });
          flushLine(message);
        },
        signal,
      );
      if (verbose) logger.logStageOutput("export", stdout, stderr);
      if (stdout) {
        const remaining = stdout
          .split("\n")
          .filter((l) => {
            const trimmed = l.trim();
            return !trimmed.startsWith("[heartbeat]") && !trimmed.startsWith("[progress]");
          })
          .join("\n");
        if (remaining.trim()) lines.push(remaining);
      }
      if (stderr) lines.push(`[stderr] ${stderr}`);
      lines.push(`Trinn 4: OK → ${timestampedExportDir}\n`);
      onProgress?.({ stage: "export", status: "ok", message: "Eksport fullført (OK)" });

      const ckpt = readCheckpoint(workDir, "stage4_export.json");
      logger.stageEnd("export", "ok", undefined, estimateDataVolume(ckpt));
    } catch (err: unknown) {
      if (err instanceof StageCancelledError) {
        logger.stageEnd("export", "failed", err.message);
        return buildCancelledResult("Trinn 4 (eksport)");
      }
      const msg = err instanceof Error ? err.message : String(err);
      lines.push(`Trinn 4 FEILET:\n${msg}`);
      onProgress?.({ stage: "export", status: "error", message: "Eksport feilet", error: msg });
      logger.stageEnd("export", "failed", msg);
      logger.finalize("failure");
      lines.push("");
      lines.push(buildRunSummaryText(workDir, logger.getRunId()));
      writeRunLogFile(timestampedExportDir, logger.getRunId(), logStart, "failure", lines);
      lines.push(`Run log: ${runLogPath}`);
      return { status: "failure", text: lines.join("\n") };
    }
  } else {
    lines.push("Trinn 4: Hoppet over (gjenopptatt)\n");
    onProgress?.({ stage: "export", status: "skip", message: "Eksport hoppet over (gjenopptatt)" });
    logger.stageStart("export");
    logger.stageEnd("export", "skipped");
  }
  writeRunLogFile(timestampedExportDir, logger.getRunId(), logStart, "running", lines);

  // Bundle: copy the input workbook into the timestamped export folder
  try {
    copyFileSync(inputPath, resolve(timestampedExportDir, basename(inputPath)));
    lines.push(`Input kopiert til ${timestampedExportDir}\n`);
  } catch {}

  // Regenerate viewer into timestamped export folder, except for the "not started"
  // placeholder export where calendars.html should remain the dummy page written by
  // Stage 4 instead of being overwritten from stale scrape cache.
  try {
    const exportCkpt = readCheckpoint(workDir, "stage4_export.json");
    const exportData = (exportCkpt?.data ?? {}) as Record<string, unknown>;
    if (!exportData.not_started) {
      const { execFile } = await import("node:child_process");
      const { promisify } = await import("node:util");
      const execFileAsync = promisify(execFile);
      const python = resolve(cwdPath, "venv", "bin", "python3");
      const exe = existsSync(python) ? python : "python3";
      await execFileAsync(exe, ["-m", "tournament_scheduler.pipeline.calendar_viewer", "--work-dir", workDir, "--export-dir", timestampedExportDir], { cwd: cwdPath });
    }
  } catch {}

  // Keep exports only in the timestamped folder.
  lines.push(`Eksporter lagret i ${timestampedExportDir}\n`);

  // Finalize
  logger.finalize(overallStatus);
  onProgress?.({ stage: "done", status: overallStatus === "success" ? "ok" : "error", message: overallStatus === "success" ? "Pipeline fullført" : "Pipeline feilet" });

  lines.push("=== Pipeline fullfort ===");
  lines.push(buildStatusText(workDir));

  // Add a self-improvement summary
  if (overallStatus === "success") {
    lines.push("");
    lines.push("Genererte filer:");
    lines.push(`  📁 Eksport-mappe:       ${timestampedExportDir}`);
    lines.push(`  🗓️  Skrapede kalendere:  ${resolve(timestampedExportDir, "calendars.html")}`);
    const sp = resolve(timestampedExportDir, "season_plan.html");
    if (existsSync(sp)) lines.push(`  📋 Sesongplan:         ${sp}`);
    lines.push(`  📊 Sesongplan (Excel):  ${resolve(timestampedExportDir, "season_plan.xlsx")}`);
    lines.push("");
    lines.push("For å se kjøringshistorikk og trender:");
    lines.push("  /rvv-miniputt logs list   — vis siste kjøringer");
    lines.push("  /rvv-miniputt logs stats  — vis selvforbedringsstatistikk");
    lines.push(`  /rvv-miniputt logs show ${logger.getRunId()}  — vis detaljer for denne kjøringen`);
  }

  lines.push("");
  lines.push(`Run log: ${runLogPath}`);
  lines.push("");
  lines.push(buildRunSummaryText(workDir, logger.getRunId()));
  lines.push(`Run log written: ${runLogPath}`);

  try {
    writeRunLogFile(timestampedExportDir, logger.getRunId(), logStart, overallStatus, lines);
  } catch (err) {
    lines.push(`Run log write failed: ${err instanceof Error ? err.message : String(err)}`);
  }

  return { status: overallStatus, text: lines.join("\n") };
}

