// ---------------------------------------------------------------------------
// PipelineLogger — structured JSONL logging for self-improvement analysis
// ---------------------------------------------------------------------------

import type { LogEntry, RunMeta, StageMeta, SelfImproveEntry } from "./types";
import { STAGE_ORDER } from "./pipeline-helpers";
import { existsSync, mkdirSync, readFileSync, readdirSync, appendFileSync, statSync } from "node:fs";
import { basename, join } from "node:path";
import { cwd } from "node:process";

function nowISO(): string {
  return new Date().toISOString();
}

function nowCompact(): string {
  const d = new Date();
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}-${pad(d.getMinutes())}-${pad(d.getSeconds())}`;
}

function runId(): string {
  return `run-${nowCompact()}`;
}

function gitCommit(cwdPath: string): { hash: string; dirty: boolean } {
  try {
    const hash = readFileSync(join(cwdPath, ".git", "HEAD"), "utf-8").trim();
    // If it's a ref, resolve it
    if (hash.startsWith("ref: ")) {
      const refPath = join(cwdPath, ".git", hash.slice(5));
      const resolved = existsSync(refPath)
        ? readFileSync(refPath, "utf-8").trim()
        : hash;
      return { hash: resolved, dirty: true }; // can't easily check dirty without git cmd
    }
    return { hash, dirty: true };
  } catch {
    return { hash: "unknown", dirty: true };
  }
}

export class PipelineLogger {
  private workDir: string;
  private logDir: string;
  private historyRoot: string;
  private logPath: string;
  private runId: string;
  private startTime: number;
  private stageStarts: Map<string, number> = new Map();

  constructor(workDir: string, logDir?: string, historyRoot?: string) {
    this.workDir = workDir;
    this.historyRoot = historyRoot ?? workDir;
    this.logDir = logDir ?? this.historyRoot;
    mkdirSync(this.logDir, { recursive: true });
    this.runId = runId();
    this.logPath = join(this.logDir, `${this.runId}.jsonl`);
    this.startTime = Date.now();
  }

  getRunId(): string { return this.runId; }
  getLogPath(): string { return this.logPath; }

  private write(entry: LogEntry): void {
    appendFileSync(this.logPath, JSON.stringify(entry) + "\n", "utf-8");
  }

  private collectRunLogCandidates(root: string): string[] {
    if (!existsSync(root)) return [];
    const files: string[] = [];
    const stack: string[] = [root];
    while (stack.length > 0) {
      const current = stack.pop();
      if (!current) continue;
      for (const entry of readdirSync(current, { withFileTypes: true })) {
        const fullPath = join(current, entry.name);
        if (entry.isDirectory()) {
          stack.push(fullPath);
        } else if (entry.isFile() && entry.name.startsWith("run-") && entry.name.endsWith(".jsonl")) {
          files.push(fullPath);
        }
      }
    }
    return files;
  }

  private historicalRunLogPaths(): string[] {
    const candidates = this.collectRunLogCandidates(this.historyRoot);

    const deduped = new Map<string, string>();
    for (const path of candidates) {
      const key = basename(path);
      const existing = deduped.get(key);
      if (!existing) {
        deduped.set(key, path);
        continue;
      }
      if (statSync(path).mtimeMs >= statSync(existing).mtimeMs) {
        deduped.set(key, path);
      }
    }

    return [...deduped.values()].sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs);
  }

  logRunMeta(args: Record<string, string | undefined>, resumeFrom: number, stages: string[]): void {
    const git = gitCommit(cwd());
    const entry: RunMeta = {
      type: "run_meta",
      run_id: this.runId,
      timestamp: nowISO(),
      args: { ...args },
      git_commit: git.hash,
      git_dirty: git.dirty,
      start_time: nowISO(),
      end_time: "",
      duration_ms: 0,
      exit_status: "cancelled",
      stages,
      resume_from: resumeFrom,
    };
    this.write(entry);
  }

  stageStart(stageName: string): void {
    this.stageStarts.set(stageName, Date.now());
    this.write({
      type: "stage_meta",
      run_id: this.runId,
      timestamp: nowISO(),
      stage_name: stageName,
      stage_index: STAGE_ORDER.indexOf(stageName) + 1,
      status: "ok",
      start_time: nowISO(),
      end_time: "",
      duration_ms: 0,
    });
  }

  stageEnd(
    stageName: string,
    status: "ok" | "skipped" | "failed",
    error?: string,
    dataVolume?: Record<string, number>,
  ): void {
    const start = this.stageStarts.get(stageName) ?? Date.now();
    const entry: StageMeta = {
      type: "stage_meta",
      run_id: this.runId,
      timestamp: nowISO(),
      stage_name: stageName,
      stage_index: STAGE_ORDER.indexOf(stageName) + 1,
      status,
      start_time: new Date(start).toISOString(),
      end_time: nowISO(),
      duration_ms: Date.now() - start,
    };
    if (error) entry.error = error;
    if (dataVolume) entry.data_volume = dataVolume;
    this.write(entry);
  }

  logStageOutput(stageName: string, stdout: string, stderr: string): void {
    if (!stdout && !stderr) return;
    this.write({
      type: "stage_log",
      run_id: this.runId,
      timestamp: nowISO(),
      stage_name: stageName,
      stdout: stdout.slice(0, 10000),
      stderr: stderr.slice(0, 5000),
    });
  }

  logLLMInteraction(stageName: string, details: Record<string, unknown>): void {
    this.write({
      type: "llm_interaction",
      run_id: this.runId,
      timestamp: nowISO(),
      stage_name: stageName,
      ...details,
    });
  }

  finalize(exitStatus: "success" | "failure" | "cancelled"): void {
    const duration = Date.now() - this.startTime;

    // Update run_meta with final state (append a final entry with end_time)
    this.write({
      type: "run_meta",
      run_id: this.runId,
      timestamp: nowISO(),
      end_time: nowISO(),
      duration_ms: duration,
      exit_status: exitStatus,
    });

    // Compute and append self-improvement stats
    this.appendSelfImproveStats();
  }

  private appendSelfImproveStats(): void {
    try {
      const files = this.historicalRunLogPaths();

      const allRuns: RunMeta[] = [];
      const stageRuns: Record<string, StageMeta[]> = {};

      for (const file of files) {
        const lines = readFileSync(file, "utf-8")
          .trim()
          .split("\n")
          .filter(Boolean);
        let runMeta: RunMeta | null = null;
        for (const line of lines) {
          try {
            const entry = JSON.parse(line) as LogEntry;
            if (entry.type === "run_meta" && entry.end_time) {
              runMeta = entry as RunMeta;
            } else if (entry.type === "stage_meta") {
              const sm = entry as StageMeta;
              if (sm.duration_ms > 0) {
                (stageRuns[sm.stage_name] ??= []).push(sm);
              }
            }
          } catch { /* skip malformed lines */ }
        }
        if (runMeta && runMeta.duration_ms > 0) {
          allRuns.push(runMeta);
        }
      }

      const totalCount = allRuns.length;
      if (totalCount === 0) return;

      const avgDuration = Math.round(
        allRuns.reduce((s, r) => s + r.duration_ms, 0) / totalCount,
      );
      const successCount = allRuns.filter((r) => r.exit_status === "success").length;
      const failCount = allRuns.filter((r) => r.exit_status === "failure").length;

      const stageStats: Record<string, {
        run_count: number;
        avg_duration_ms: number;
        failure_count: number;
        failure_rate: number;
      }> = {};

      for (const [name, metas] of Object.entries(stageRuns)) {
        const stageFailCount = metas.filter((m) => m.status === "failed").length;
        stageStats[name] = {
          run_count: metas.length,
          avg_duration_ms: Math.round(
            metas.reduce((s, m) => s + m.duration_ms, 0) / metas.length,
          ),
          failure_count: stageFailCount,
          failure_rate: metas.length > 0
            ? Math.round((stageFailCount / metas.length) * 100)
            : 0,
        };
      }

      const durationTrend = allRuns.slice(0, 20).map((r) => ({
        run_id: r.run_id,
        date: r.start_time?.slice(0, 10) ?? "unknown",
        duration_ms: r.duration_ms,
      }));

      const si: SelfImproveEntry = {
        type: "self_improve",
        run_id: this.runId,
        timestamp: nowISO(),
        run_count: totalCount,
        avg_duration_ms: avgDuration,
        stage_stats: stageStats,
        total_failure_count: failCount,
        total_success_count: successCount,
        failure_rate_pct: totalCount > 0
          ? Math.round((failCount / totalCount) * 100)
          : 0,
        duration_trend_ms: durationTrend,
      };

      this.write(si);
    } catch {
      // Self-improve stats are best-effort; don't crash if log parsing fails
    }
  }
}
