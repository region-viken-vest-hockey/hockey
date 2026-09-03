// ---------------------------------------------------------------------------
// Pipeline runner — executes all four stages
// ---------------------------------------------------------------------------

import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
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
import type { ProgressEvent } from "./types";

export interface PipelineRunResult {
  status: "success" | "failure" | "cancelled";
  text: string;
}

const DEFAULT_PLANNER_ITERATIONS = 1;

function computeExportTimestamp(): string {
  const now = new Date();
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}${pad(now.getMinutes())}`;
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
  const signal = ctx.signal;

  const timestampedExportDir = params.timestamped_export === false ? exportRoot : resolve(exportRoot, computeExportTimestamp());

  mkdirSync(workDir, { recursive: true });
  mkdirSync(timestampedExportDir, { recursive: true });

  const logger = new PipelineLogger(workDir, timestampedExportDir, exportRoot);
  const logStart = new Date();
  const runLogPath = resolve(timestampedExportDir, `pipeline_run_${logger.getRunId()}.log`);

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

  let lastFlush = 0;
  const FLUSH_INTERVAL_MS = 2000;
  const flushLine = (line: string) => {
    lines.push(line);
    const now = Date.now();
    if (now - lastFlush < FLUSH_INTERVAL_MS) return;
    lastFlush = now;
    try {
      writeRunLogFile(timestampedExportDir, logger.getRunId(), logStart, "running", lines);
    } catch { /* best effort */ }
  };

  lines.push(`=== RVV Miniputt Pipeline ===`);
  lines.push(`Kjøring: ${logger.getRunId()}`);
  lines.push(`Logg: ${logger.getLogPath()}`);
  lines.push(`Arbeidskatalog: ${workDir}`);
  lines.push(`Input: ${inputPath}`);
  if (resumeFrom > 1) lines.push(`Gjenopptar fra: Trinn ${resumeFrom}`);
  if (params.manual_bookup_login) {
    lines.push("BookUp manuell innlogging må gjøres på macOS-verten; Pi bruker lagret Playwright-state fra .pipeline/auth/.");
  }
  lines.push("");
  writeRunLogFile(timestampedExportDir, logger.getRunId(), logStart, "running", lines);

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
  // Stage 2 — deterministic Python + optional Pi browser recovery.
  // Python is always rerun strictly after recovery and owns readiness.
  // -------------------------------------------------------------------
  if (signal?.aborted) return buildCancelledResult("før Trinn 2");
  if (resumeFrom <= 2) {
    lines.push("Trinn 2: Skraper kalenderkilder (deterministisk)...");
    onProgress?.({ stage: "scraping", status: "start", message: "Skraper kalenderkilder (Trinn 2/4)..." });
    logger.stageStart("scraping");

    try {
      const stage2Args = [...baseArgs, "--non-strict"];
      if (params.force_refresh) stage2Args.push("--force-refresh");
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
      const msg = err instanceof Error ? err.message : String(err);
      lines.push(`Trinn 2 innledende skraping feilet: ${msg}\n`);
    }
    if (signal?.aborted) return buildCancelledResult("Trinn 2 (etter deterministisk skraping)");

    let stage2Checkpoint = readCheckpoint(workDir, "stage2_scraping.json");
    let unresolved: string[] = [];
    let cached: string[] = [];
    if (stage2Checkpoint?.data) {
      const data = stage2Checkpoint.data as Record<string, unknown>;
      unresolved = (data.blocked as string[]) ?? [];
      cached = (data.cached as string[]) ?? [];
      const sources = (data.sources as Array<Record<string, unknown>>) ?? [];
      for (const s of sources) {
        const cacheTag = s.from_cache ? " (cache)" : "";
        const cacheAge = typeof s.cache_age_hours === "number" ? `, ${s.cache_age_hours}h gammel` : "";
        lines.push(`  ${s.name}: ${s.event_count} events${cacheTag}${cacheAge}`);
      }
    }

    if (unresolved.length > 0) {
      flushLine(`\nTrinn 2 recovery: ${unresolved.length} uløste kilder vurderes av Pi...`);
      onProgress?.({ stage: "scraping-extended", status: "start", message: `Browser-recovery: ${unresolved.length} uløste kilder` });
      try {
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

        for (const name of unresolved) {
          if (signal?.aborted) {
            try { await agent.close(); } catch { /* best effort */ }
            return buildCancelledResult("Trinn 2 (browser-recovery med Pi)");
          }
          const strat = await fetchStrategy(name);
          if (!strat || !strat.url) {
            flushLine(`  ${name}: ingen browser-strategi — Python avgjør om kilden blokkerer`);
            continue;
          }

          const credentialEnvVars = (strat.credential_env_vars as string[]) ?? [];
          if (credentialEnvVars.length > 0) {
            // Credential/MFA establishment belongs on the visible macOS host.
            // Headless Pi/Lima consumes the Playwright storage state through Python.
            flushLine(`  ${name}: credentialed kilde — bruker Python/lagret auth-state; starter ikke ny Pi-login`);
            continue;
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

          if (events.length > 0) {
            const cachePath = resolve(workDir, "cache", "scraped_data.json");
            let cacheData: Record<string, any> = {};
            try {
              cacheData = JSON.parse(readFileSync(cachePath, "utf-8"));
            } catch {}
            if (!cacheData.sources) cacheData.sources = {};
            const existing = cacheData.sources[name] ?? {};
            cacheData.sources[name] = {
              ...existing,
              name,
              url: strat.url,
              scrape_timestamp: new Date().toISOString(),
              event_count: events.length,
              blocked: false,
              events,
            };
            cacheData.total_events = Object.values(cacheData.sources as Record<string, any>).reduce((s: number, src: any) => s + (src.event_count || 0), 0);
            cacheData.source_count = Object.keys(cacheData.sources as Record<string, any>).length;
            writeFileSync(cachePath, JSON.stringify(cacheData, null, 2));
          }
        }

        await agent.close();
        lines.push("Trinn 2 browser-recovery ferdig; returnerer kontroll til Python.\n");
      } catch (agentErr: unknown) {
        const msg = agentErr instanceof Error ? agentErr.message : String(agentErr);
        lines.push(`ScraperAgent recovery feilet: ${msg}\n`);
        onProgress?.({ stage: "scraping-extended", status: "error", message: `Browser-recovery feilet: ${msg}` });
      }
    }

    // Canonical validation pass. Never let Pi decide which missing sources are
    // acceptable: rerun Python Stage 2 strictly using the shared cache/policy.
    flushLine("Trinn 2: validerer recovery og source-readiness i Python...");
    try {
      const { stdout, stderr } = await runStage(
        cwdPath,
        "tournament_scheduler.pipeline.stage2_scraping",
        [...baseArgs],
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
        return buildCancelledResult("Trinn 2 (Python-validering)");
      }
      const msg = err instanceof Error ? err.message : String(err);
      lines.push(`Trinn 2 FEILET etter recovery:\n${msg}`);
      onProgress?.({ stage: "scraping", status: "error", message: "Kalenderkilder blokkerer planlegging", error: msg });
      logger.stageEnd("scraping", "failed", msg);
      logger.finalize("failure");
      lines.push("");
      lines.push(buildRunSummaryText(workDir, logger.getRunId()));
      writeRunLogFile(timestampedExportDir, logger.getRunId(), logStart, "failure", lines);
      lines.push(`Run log: ${runLogPath}`);
      return { status: "failure", text: lines.join("\n") };
    }

    stage2Checkpoint = readCheckpoint(workDir, "stage2_scraping.json");
    let blocking: string[] = [];
    let temporary: string[] = [];
    if (stage2Checkpoint?.data) {
      const data = stage2Checkpoint.data as Record<string, unknown>;
      unresolved = (data.blocked as string[]) ?? [];
      blocking = (data.blocking_sources as string[]) ?? unresolved;
      temporary = (data.temporarily_unresolved_sources as string[]) ?? [];
      cached = (data.cached as string[]) ?? [];
    }

    if (blocking.length > 0) {
      const msg = `Python rapporterte fortsatt blokkerende kalenderkilder: ${blocking.join(", ")}`;
      lines.push(msg);
      onProgress?.({ stage: "scraping", status: "error", message: msg, blockedCount: blocking.length });
      logger.stageEnd("scraping", "failed", msg);
      logger.finalize("failure");
      writeRunLogFile(timestampedExportDir, logger.getRunId(), logStart, "failure", lines);
      return { status: "failure", text: lines.join("\n") };
    }

    const cacheSuffix = cached.length > 0 ? ` (${cached.length} fra cache)` : "";
    const unresolvedSuffix = temporary.length > 0
      ? `; ${temporary.length} midlertidig uløst BookUp-kilde(r)${cacheSuffix}`
      : cacheSuffix;
    onProgress?.({
      stage: "scraping",
      status: "ok",
      message: temporary.length > 0 ? `Python godkjente Stage 2${unresolvedSuffix}` : `Alle kalenderkilder godkjent av Python${cacheSuffix}`,
      blockedCount: unresolved.length,
    });
    logger.stageEnd("scraping", "ok", undefined, estimateDataVolume(stage2Checkpoint));

    try {
      const { execFile } = await import("node:child_process");
      const { promisify } = await import("node:util");
      const execFileAsync = promisify(execFile);
      const python = resolve(cwdPath, "venv", "bin", "python3");
      const exe = existsSync(python) ? python : "python3";
      await execFileAsync(exe, ["-m", "tournament_scheduler.pipeline.calendar_viewer", "--work-dir", workDir, "--export-dir", timestampedExportDir], { cwd: cwdPath });
    } catch {}
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

  try {
    copyFileSync(inputPath, resolve(timestampedExportDir, basename(inputPath)));
    lines.push(`Input kopiert til ${timestampedExportDir}\n`);
  } catch {}

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

  lines.push(`Eksporter lagret i ${timestampedExportDir}\n`);

  logger.finalize(overallStatus);
  onProgress?.({ stage: "done", status: overallStatus === "success" ? "ok" : "error", message: overallStatus === "success" ? "Pipeline fullført" : "Pipeline feilet" });

  lines.push("=== Pipeline fullfort ===");
  lines.push(buildStatusText(workDir));

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
