/**
 * Pi browser-recovery agent.
 *
 * Repository code owns source strategy, cache validation and Stage 2 state.
 * This class owns only Pi/browser transport: drive browser_worker.py, ask the
 * active Pi model what navigation action to take, and return extracted events.
 */

import type { UserMessage } from "@earendil-works/pi-ai";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

export interface NavigationStep {
  cmd: "click" | "goto" | "type" | "wait" | "extract";
  selector?: string;
  text?: string;
  url?: string;
  wait_ms?: number;
  timeout_s?: number;
  iframe?: boolean;
  strategy?: string;
}

export interface ScrapeOptions {
  iframe?: boolean;
  strategy?: "outlook" | "date_param" | "styledcalendar" | "bookup" | "forumbooking" | "auto";
  month_start?: string;
  maxIterations?: number;
  initialNavigation?: NavigationStep[];
}

export interface CalendarEvent {
  date: string;
  name: string;
  datetime: string;
  duration_hours: number;
}

interface WorkerResponse {
  ok: boolean;
  error?: string;
  html?: string;
  iframe_html?: string;
  url?: string;
  title?: string;
  interactive?: Array<{ tag: string; text: string; selector: string }>;
  events?: CalendarEvent[];
  screenshot?: string;
  message?: string;
}

interface LLMAction {
  action: "click" | "goto" | "extract" | "done" | "wait" | "scroll";
  selector?: string;
  url?: string;
  reasoning?: string;
  strategy?: string;
  iframe?: boolean;
  wait_ms?: number;
}

function systemPrompt(type: string, url: string): string {
  const lines = [
    "Du navigerer en ishall-kalender for å hente bookingdata.",
    "Repository-strategien har allerede valgt kilde og URL; du skal bare navigere siden.",
    "",
    "Mulige handlinger:",
    "- click: klikk en knapp/lenke med selector fra interactive-listen",
    "- goto: naviger til URL",
    "- extract: kall browser-workerens innebygde event-parser",
    "- wait: vent kort på JS-rendering",
    "- scroll: rull siden",
    "- done: avslutt når relevant periode er hentet",
    "",
    "Svar alltid med ett JSON-objekt og ingen tekst utenfor JSON.",
    "Ikke finn opp events; bruk extract for faktiske bookingdata.",
    `Strategi: ${type}`,
    `Kilde: ${url}`,
  ];
  if (type === "outlook") lines.push("Outlook-kalendere kan ligge i iframe og ha 'next month'-knapp.");
  if (type === "forumbooking") lines.push("Forumbooking viser vanligvis uke/måned og har navigasjonsknapper.");
  if (type === "bookup") lines.push("BookUp kan kreve at 'Se tilgjengelighet' åpnes før kalenderen vises.");
  return lines.join("\n");
}

export function userMessage(snapshot: WorkerResponse, iteration: number, maxIterations: number): string {
  const lines = [
    `Iterasjon ${iteration}/${maxIterations}`,
    `Tittel: ${snapshot.title ?? "ukjent"}`,
    `URL: ${snapshot.url ?? "ukjent"}`,
    "",
    "Synlig HTML (første 3000 tegn):",
    (snapshot.html ?? "").slice(0, 3000),
  ];
  if (snapshot.iframe_html) {
    lines.push("", "Iframe HTML (første 3000 tegn):", snapshot.iframe_html.slice(0, 3000));
  }
  if (snapshot.interactive?.length) {
    lines.push("", "Interaktive elementer:");
    for (const el of snapshot.interactive.slice(0, 30)) {
      lines.push(`  <${el.tag}> "${el.text}" → ${el.selector}`);
    }
  }
  if (snapshot.events?.length) {
    lines.push("", `Allerede ekstraherte events (${snapshot.events.length}):`);
    for (const event of snapshot.events.slice(0, 10)) {
      lines.push(`  ${event.date} ${event.name} (${event.duration_hours}h)`);
    }
  }
  lines.push("", "Hva vil du gjøre? Returner ett JSON-objekt.");
  return lines.join("\n");
}

function responseText(response: { content?: Array<{ type: string; text?: string }> }): string {
  return (response.content ?? [])
    .filter((item): item is { type: "text"; text: string } => item.type === "text" && typeof item.text === "string")
    .map((item) => item.text)
    .join("\n");
}

async function callLLM(
  ctx: ExtensionContext,
  system: string,
  user: string,
  onUsage?: (details: Record<string, unknown>) => void,
  meta: Record<string, unknown> = {},
): Promise<string> {
  if (!ctx.model) throw new Error("Ingen aktiv modell konfigurert i Pi");
  const started = Date.now();
  const message: UserMessage = {
    role: "user",
    content: [{ type: "text", text: user }],
    timestamp: Date.now(),
  };
  const response = await ctx.modelRegistry.complete(
    ctx.model,
    { systemPrompt: system, messages: [message] },
    { signal: ctx.signal },
  );
  if (response.stopReason === "aborted") throw new Error("Pi model call ble avbrutt");
  const content = responseText(response);
  onUsage?.({
    ...meta,
    backend: `pi:${ctx.model.provider}/${ctx.model.id}`,
    model: ctx.model.id,
    provider: ctx.model.provider,
    duration_ms: Date.now() - started,
    response_chars: content.length,
  });
  return content;
}

function extractJSON(text: string): Record<string, unknown> | null {
  let cleaned = text.trim();
  if (cleaned.startsWith("```")) {
    cleaned = cleaned.replace(/^```(?:json)?/i, "").replace(/```$/i, "").trim();
  }
  const start = cleaned.indexOf("{");
  const end = cleaned.lastIndexOf("}");
  if (start >= 0 && end > start) cleaned = cleaned.slice(start, end + 1);
  try {
    const parsed = JSON.parse(cleaned);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

function parseAction(data: Record<string, unknown>): LLMAction | null {
  const action = String(data.action ?? "");
  if (!["click", "goto", "extract", "done", "wait", "scroll"].includes(action)) return null;
  return {
    action: action as LLMAction["action"],
    selector: String(data.selector ?? data.css ?? ""),
    url: String(data.url ?? ""),
    reasoning: String(data.reasoning ?? data.reason ?? ""),
    strategy: String(data.strategy ?? "auto"),
    iframe: Boolean(data.iframe ?? false),
    wait_ms: Number(data.wait_ms ?? data.ms ?? 1000),
  };
}


export class ScraperAgent {
  private proc: ChildProcess | null = null;
  private buffer = "";
  private readonly ctx: ExtensionContext;
  private readonly pythonPath: string;
  private readonly onLLMInteraction?: (details: Record<string, unknown>) => void;
  private readonly onActivity?: (message: string) => void;
  private abortHandler?: () => void;

  constructor(
    ctx: ExtensionContext,
    onLLMInteraction?: (details: Record<string, unknown>) => void,
    onActivity?: (message: string) => void,
  ) {
    this.ctx = ctx;
    this.onLLMInteraction = onLLMInteraction;
    this.onActivity = onActivity;
    const venv = resolve(ctx.cwd, "venv", "bin", "python3");
    this.pythonPath = existsSync(venv) ? venv : "python3";
  }

  async start(): Promise<void> {
    if (this.proc) return;
    const workerPath = resolve(this.ctx.cwd, "tournament_scheduler", "pipeline", "browser_worker.py");
    this.proc = spawn(this.pythonPath, [workerPath], { stdio: ["pipe", "pipe", "pipe"], cwd: this.ctx.cwd });
    this.onActivity?.("Browser worker startet");
    this.proc.stderr?.on("data", (chunk: Buffer) => {
      const text = chunk.toString().trim();
      if (text) console.error(`[browserWorker stderr] ${text.slice(0, 500)}`);
    });
    this.proc.on("exit", () => { this.proc = null; });
    this.abortHandler = () => this.proc?.kill("SIGTERM");
    if (this.ctx.signal?.aborted) this.abortHandler();
    else this.ctx.signal?.addEventListener("abort", this.abortHandler, { once: true });
  }

  async send(cmd: Record<string, unknown>): Promise<WorkerResponse> {
    if (!this.proc) throw new Error("Browser worker er ikke startet");
    return new Promise((resolvePromise, rejectPromise) => {
      const timeout = setTimeout(() => rejectPromise(new Error(`Worker timeout for command: ${cmd.cmd}`)), 45_000);
      const onData = (chunk: Buffer): void => {
        this.buffer += chunk.toString();
        const newline = this.buffer.indexOf("\n");
        if (newline < 0) return;
        const line = this.buffer.slice(0, newline);
        this.buffer = this.buffer.slice(newline + 1);
        clearTimeout(timeout);
        this.proc?.stdout?.removeListener("data", onData);
        try {
          resolvePromise(JSON.parse(line) as WorkerResponse);
        } catch {
          rejectPromise(new Error(`Ugyldig JSON fra browser worker: ${line.slice(0, 300)}`));
        }
      };
      this.proc?.stdout?.on("data", onData);
      this.proc?.stdin?.write(`${JSON.stringify(cmd)}\n`);
    });
  }

  private async runInitialNavigation(steps: NavigationStep[], snap: WorkerResponse, fallbackUrl: string): Promise<WorkerResponse> {
    let current = snap;
    for (const step of steps) {
      const waitMs = step.wait_ms ?? 1500;
      try {
        if (step.cmd === "click") {
          current = await this.send({ cmd: "click", selector: step.selector ?? "", iframe: step.iframe ?? false, wait_ms: waitMs });
        } else if (step.cmd === "type") {
          current = await this.send({ cmd: "type", selector: step.selector ?? "", text: substituteEnvVars(step.text ?? ""), wait_ms: waitMs });
        } else if (step.cmd === "goto") {
          current = await this.send({ cmd: "goto", url: substituteEnvVars(step.url ?? fallbackUrl), wait_ms: step.wait_ms ?? 3000 });
        } else if (step.cmd === "wait") {
          await new Promise((resolveWait) => setTimeout(resolveWait, waitMs));
        } else if (step.cmd === "extract") {
          current = await this.send({ cmd: "extract", strategy: step.strategy ?? "auto", iframe: step.iframe ?? false });
        }
      } catch (err: unknown) {
        console.error(`Initial browser navigation step failed: ${err instanceof Error ? err.message : String(err)}`);
      }
    }
    return current;
  }

  async scrape(url: string, options: ScrapeOptions = {}): Promise<CalendarEvent[]> {
    await this.start();
    const maxIterations = options.maxIterations ?? 15;
    const strategy = options.strategy ?? "auto";
    const events: CalendarEvent[] = [];

    this.onActivity?.(`Laster ${url}`);
    let snapshot = await this.send({ cmd: "goto", url, wait_ms: 3000 });
    if (!snapshot.ok) throw new Error(`Kunne ikke laste ${url}: ${snapshot.error ?? "ukjent feil"}`);
    snapshot = await this.runInitialNavigation(options.initialNavigation ?? [], snapshot, url);
    const detectedIframe = Boolean(snapshot.iframe_html && snapshot.iframe_html.length > 100);

    for (let iteration = 1; iteration <= maxIterations; iteration++) {
      if (this.ctx.signal?.aborted) throw new Error("Browser recovery ble avbrutt");
      this.onActivity?.(`Iterasjon ${iteration}/${maxIterations} — prøver ekstraksjon`);
      const extracted = await this.send({
        cmd: "extract",
        strategy,
        iframe: options.iframe ?? detectedIframe,
        month_start: options.month_start,
      });
      if (extracted.ok && extracted.events?.length) {
        events.push(...extracted.events);
        this.onActivity?.(`Fant ${extracted.events.length} events (totalt ${events.length})`);
      }

      let raw: string;
      try {
        this.onActivity?.(`Iterasjon ${iteration}/${maxIterations} — spør aktiv Pi-modell`);
        raw = await callLLM(
          this.ctx,
          systemPrompt(strategy, url),
          userMessage({ ...snapshot, events: extracted.events }, iteration, maxIterations),
          this.onLLMInteraction,
          { iteration, max_iterations: maxIterations, url, strategy },
        );
      } catch (err: unknown) {
        console.error(`Pi model call failed during browser recovery: ${err instanceof Error ? err.message : String(err)}`);
        if (detectedIframe) {
          const fallback = await this.send({
            cmd: "click",
            selector: 'button[aria-label*="next month"]',
            iframe: true,
            wait_ms: 1500,
          });
          if (fallback.ok) {
            snapshot = fallback;
            continue;
          }
        }
        break;
      }

      const parsed = extractJSON(raw);
      const action = parsed ? parseAction(parsed) : null;
      if (!action) continue;
      if (action.action === "done") break;

      this.onActivity?.(`Iterasjon ${iteration}/${maxIterations} — ${action.action}`);
      if (action.action === "click") {
        snapshot = await this.send({
          cmd: "click",
          selector: action.selector,
          iframe: action.iframe ?? detectedIframe,
          wait_ms: action.wait_ms ?? 1500,
        });
      } else if (action.action === "goto") {
        snapshot = await this.send({ cmd: "goto", url: action.url, wait_ms: 3000 });
      } else if (action.action === "extract") {
        const extra = await this.send({
          cmd: "extract",
          strategy: action.strategy ?? strategy,
          iframe: action.iframe ?? detectedIframe,
          month_start: options.month_start,
        });
        if (extra.ok && extra.events?.length) events.push(...extra.events);
      } else if (action.action === "wait") {
        await new Promise((resolveWait) => setTimeout(resolveWait, action.wait_ms ?? 1000));
      } else if (action.action === "scroll") {
        snapshot = await this.send({ cmd: "scroll", direction: "down" });
      }

      if (!snapshot.ok) break;
    }

    this.onActivity?.(`Fullført — ${events.length} events samlet`);
    return events;
  }

  async close(): Promise<void> {
    if (this.abortHandler) this.ctx.signal?.removeEventListener("abort", this.abortHandler);
    this.abortHandler = undefined;
    if (!this.proc) return;
    try {
      await this.send({ cmd: "exit" });
    } catch {
      this.proc.kill("SIGTERM");
    }
    this.proc = null;
  }
}


export function substituteEnvVars(text: string): string {
  return text.replace(/\$\{(\w+)\}/g, (_match, name: string) => process.env[name] ?? "");
}
