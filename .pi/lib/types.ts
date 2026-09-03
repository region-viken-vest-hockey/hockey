// ---------------------------------------------------------------------------
// Types & constants shared across the rvv-miniputt extension
// ---------------------------------------------------------------------------

export interface RunArgs {
  input?: string;
  work_dir?: string;
  resume_from?: string;
  export_dir?: string;
  log_level?: string;
  force_refresh?: boolean;
  non_strict?: boolean;
  allow_missing_sources?: boolean;
  manual_bookup_login?: boolean;
  manual_bookup_login_timeout?: number;
  timestamped_export?: boolean;
  iterations?: number;
}

export interface StatusArgs {
  work_dir?: string;
}

export interface CalendarsArgs {
  refresh?: boolean;
  work_dir?: string;
}

export interface ScrapeArgs {
  club?: string;
  work_dir?: string;
  manual_bookup_login?: boolean;
  manual_bookup_login_timeout?: number;
}

export interface ScrapeLlmArgs {
  club?: string;
  work_dir?: string;
  export_dir?: string;
  endpoint?: string;
  model?: string;
  max_iterations?: number;
  cache_results?: boolean;
  debug_screenshots?: boolean;
}

export interface LogsArgs {
  subcommand: "list" | "show" | "stats";
  count?: number;
  run_id?: string;
  work_dir?: string;
}

export interface LogEntry {
  type: "run_meta" | "stage_meta" | "stage_log" | "self_improve" | "llm_interaction" | "tournament_update";
  run_id: string;
  timestamp: string;
  [key: string]: unknown;
}

export interface RunMeta extends LogEntry {
  type: "run_meta";
  args: Record<string, string | undefined>;
  git_commit: string;
  git_dirty: boolean;
  start_time: string;
  end_time: string;
  duration_ms: number;
  exit_status: "success" | "failure" | "cancelled";
  stages: string[];
  resume_from: number;
}

export interface StageMeta extends LogEntry {
  type: "stage_meta";
  stage_name: string;
  stage_index: number;
  status: "ok" | "skipped" | "failed";
  start_time: string;
  end_time: string;
  duration_ms: number;
  error?: string;
  data_volume?: Record<string, number>;
}

export interface SelfImproveEntry extends LogEntry {
  type: "self_improve";
  run_count: number;
  avg_duration_ms: number;
  stage_stats: Record<string, {
    run_count: number;
    avg_duration_ms: number;
    failure_count: number;
    failure_rate: number;
  }>;
  total_failure_count: number;
  total_success_count: number;
  failure_rate_pct: number;
  duration_trend_ms: Array<{ run_id: string; date: string; duration_ms: number }>;
}

export const LOG_LEVELS = ["info", "verbose"] as const;

/** Progress event emitted by the pipeline runner as stages execute. */
export interface ProgressEvent {
  stage: "config" | "scraping" | "scraping-extended" | "planning" | "export" | "done";
  status: "start" | "ok" | "skip" | "error";
  message: string;
  /** Only set for status=error */
  error?: string;
  /** Number of blocked sources (stage=scraping, status=ok) */
  blockedCount?: number;
  /** Name of the blocked source being scraped (stage=scraping-extended) */
  blockedName?: string;
  /** Events found for blocked source (stage=scraping-extended, status=ok) */
  eventCount?: number;
}
