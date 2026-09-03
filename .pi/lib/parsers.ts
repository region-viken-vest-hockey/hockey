// ---------------------------------------------------------------------------
// Argument parsers for rvv-miniputt slash commands
// ---------------------------------------------------------------------------

import type { RunArgs, StatusArgs, LogsArgs, CalendarsArgs, ScrapeArgs, ScrapeLlmArgs } from "./types";
import { normalizeArgs } from "./arg-utils";

export function parseRunArgs(args: unknown): RunArgs {
  const result: RunArgs = {};
  const tokens = normalizeArgs(args).split(/\s+/).filter(Boolean);
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t === "--input" && i + 1 < tokens.length) result.input = tokens[++i];
    else if (t === "--work-dir" && i + 1 < tokens.length) result.work_dir = tokens[++i];
    else if (t === "--resume-from" && i + 1 < tokens.length) result.resume_from = tokens[++i];
    else if (t === "--export-dir" && i + 1 < tokens.length) result.export_dir = tokens[++i];
    else if (t === "--log-level" && i + 1 < tokens.length) result.log_level = tokens[++i];
    else if (t === "--force-refresh") result.force_refresh = true;
    else if (t === "--non-strict") result.non_strict = true;
    else if (t === "--allow-missing-sources") result.allow_missing_sources = true;
    else if (t === "--manual-bookup-login") result.manual_bookup_login = true;
    else if (t === "--manual-bookup-login-timeout" && i + 1 < tokens.length) result.manual_bookup_login_timeout = parseInt(tokens[++i], 10);
    else if (t === "--no-timestamped-export") result.timestamped_export = false;
    else if (t === "--iterations" && i + 1 < tokens.length) result.iterations = parseInt(tokens[++i], 10);
  }
  return result;
}

export function parseStatusArgs(args: unknown): StatusArgs {
  const result: StatusArgs = {};
  const tokens = normalizeArgs(args).split(/\s+/).filter(Boolean);
  for (let i = 0; i < tokens.length; i++) {
    if (tokens[i] === "--work-dir" && i + 1 < tokens.length) result.work_dir = tokens[++i];
  }
  return result;
}

export function parseLogsArgs(args: unknown): LogsArgs {
  const result: LogsArgs = { subcommand: "list" };
  const tokens = normalizeArgs(args).split(/\s+/).filter(Boolean);
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t === "list") result.subcommand = "list";
    else if (t === "show" && i + 1 < tokens.length) {
      result.subcommand = "show";
      result.run_id = tokens[++i];
    } else if (t === "stats") result.subcommand = "stats";
    else if (t === "--count" && i + 1 < tokens.length) result.count = parseInt(tokens[++i], 10);
    else if (t === "--work-dir" && i + 1 < tokens.length) result.work_dir = tokens[++i];
  }
  return result;
}

export function parseCalendarsArgs(args: unknown): CalendarsArgs {
  const result: CalendarsArgs = {};
  const tokens = normalizeArgs(args).split(/\s+/).filter(Boolean);
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t === "--refresh") result.refresh = true;
    else if (t === "--work-dir" && i + 1 < tokens.length) result.work_dir = tokens[++i];
  }
  return result;
}

export function parseScrapeArgs(args: unknown): ScrapeArgs {
  const result: ScrapeArgs = {};
  const tokens = normalizeArgs(args).split(/\s+/).filter(Boolean);
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t === "--club" && i + 1 < tokens.length) result.club = tokens[++i];
    else if (t === "--work-dir" && i + 1 < tokens.length) result.work_dir = tokens[++i];
    else if (t === "--manual-bookup-login") result.manual_bookup_login = true;
    else if (t === "--manual-bookup-login-timeout" && i + 1 < tokens.length) result.manual_bookup_login_timeout = parseInt(tokens[++i], 10);
  }
  return result;
}

export function parseScrapeLlmArgs(args: unknown): ScrapeLlmArgs {
  const result: ScrapeLlmArgs = { cache_results: true };
  const tokens = normalizeArgs(args).split(/\s+/).filter(Boolean);
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t === "--club" && i + 1 < tokens.length) result.club = tokens[++i];
    else if (t === "--work-dir" && i + 1 < tokens.length) result.work_dir = tokens[++i];
    else if (t === "--export-dir" && i + 1 < tokens.length) result.export_dir = tokens[++i];
    else if (t === "--endpoint" && i + 1 < tokens.length) result.endpoint = tokens[++i];
    else if (t === "--model" && i + 1 < tokens.length) result.model = tokens[++i];
    else if (t === "--max-iterations" && i + 1 < tokens.length) result.max_iterations = parseInt(tokens[++i], 10);
    else if (t === "--cache-results") result.cache_results = true;
    else if (t === "--no-cache-results") result.cache_results = false;
    else if (t === "--debug-screenshots") result.debug_screenshots = true;
  }
  return result;
}

export function isVerbose(rawArgs: unknown): boolean {
  return parseRunArgs(rawArgs).log_level === "verbose";
}
