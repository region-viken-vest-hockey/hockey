import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { resolve } from "node:path";
import { loadBookupEnvFromDotenvx } from "./dotenvx-helpers";
import { formatProcessFailure, runPythonModule, runRepoCli } from "./repo-cli";
import { ScraperAgent } from "./scraper-agent";

interface ScraperStrategyPayload {
  engine?: string;
  url?: string;
  has_iframe?: boolean;
  initial_navigation?: Array<Record<string, unknown>>;
  credential_env_vars?: string[];
}

export interface BrowserRecoveryResult {
  source: string;
  eventCount: number;
  text: string;
}

async function loadStrategy(source: string, ctx: ExtensionContext): Promise<ScraperStrategyPayload> {
  const result = await runPythonModule(
    ctx,
    "tournament_scheduler.pipeline.scraper_strategies",
    ["--name", source],
    { timeoutMs: 15_000 },
  );
  if (result.code !== 0) {
    throw new Error(`No repository browser strategy for ${source}: ${formatProcessFailure(result)}`);
  }
  try {
    return JSON.parse(result.stdout) as ScraperStrategyPayload;
  } catch (err: unknown) {
    throw new Error(`Invalid repository scraper strategy JSON for ${source}: ${err instanceof Error ? err.message : String(err)}`);
  }
}

async function ensureStrategyCredentials(
  source: string,
  strategy: ScraperStrategyPayload,
  ctx: ExtensionContext,
): Promise<void> {
  await loadBookupEnvFromDotenvx(ctx.cwd);
  for (const envVar of strategy.credential_env_vars ?? []) {
    if (process.env[envVar]) continue;
    const value = await ctx.ui.input(`Innlogging kreves for ${source}. Angi ${envVar}:`, "");
    if (!value) throw new Error(`Browser recovery for ${source} requires ${envVar}.`);
    process.env[envVar] = value;
  }
}

export async function recoverSourceWithPiBrowser(
  source: string,
  ctx: ExtensionContext,
  options: {
    workDir?: string;
    mergeAfter?: boolean;
    maxIterations?: number;
    onActivity?: (message: string) => void;
  } = {},
): Promise<BrowserRecoveryResult> {
  const workDir = resolve(ctx.cwd, options.workDir ?? ".pipeline");
  const strategy = await loadStrategy(source, ctx);
  if (!strategy.url) throw new Error(`Repository browser strategy for ${source} has no URL.`);
  await ensureStrategyCredentials(source, strategy, ctx);

  const activity = options.onActivity ?? (() => undefined);
  const agent = new ScraperAgent(ctx, undefined, activity);
  let events: unknown[] = [];
  try {
    activity(`${source}: starter Pi browser recovery`);
    await agent.start();
    const initialNavigation = strategy.initial_navigation ?? [];
    events = await agent.scrape(strategy.url, {
      strategy: (strategy.engine === "styled_calendar" ? "styledcalendar" : "auto") as any,
      iframe: strategy.has_iframe ?? false,
      maxIterations: options.maxIterations ?? 25,
      initialNavigation: initialNavigation.length > 0 ? initialNavigation as any : undefined,
    });
  } finally {
    try { await agent.close(); } catch { /* best effort */ }
  }

  if (events.length === 0) {
    throw new Error(`Pi browser recovery for ${source} returned zero events; nothing was injected.`);
  }

  const inject = await runRepoCli(
    ctx,
    ["recovery-inject", "--source", source, "--work-dir", workDir],
    { timeoutMs: 60_000, stdinText: JSON.stringify(events) },
  );
  if (inject.code !== 0) {
    throw new Error(`Repository recovery-inject failed for ${source}: ${formatProcessFailure(inject)}`);
  }

  if (options.mergeAfter) {
    const merge = await runRepoCli(
      ctx,
      ["scrape-merge", "--work-dir", workDir],
      { timeoutMs: 120_000 },
    );
    if (merge.code !== 0) {
      throw new Error(`Repository scrape-merge failed after recovering ${source}: ${formatProcessFailure(merge)}`);
    }
  }

  activity(`${source}: ${events.length} events recovered and validated through recovery-inject`);
  return {
    source,
    eventCount: events.length,
    text: `${source}: recovered ${events.length} events through Pi browser → repository recovery-inject${options.mergeAfter ? " → scrape-merge" : ""}.`,
  };
}
