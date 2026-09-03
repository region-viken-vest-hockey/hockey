/**
 * ScraperAgent — Pi-driven browser scraping agent.
 *
 * Launches the Python browserWorker as a child process, sends commands,
 * and uses Pi's configured model to analyze page snapshots and decide
 * the next action.
 *
 * Usage:
 *   const agent = new ScraperAgent(ctx);
 *   const events = await agent.scrape("https://...", { iframe: true });
 *   await agent.close();
 */

import { spawn, type ChildProcess } from "node:child_process";
import { resolve } from "node:path";
import { cwd } from "node:process";
import { existsSync } from "node:fs";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface NavigationStep {
  cmd: "click" | "goto" | "type" | "wait" | "extract" | "manual_login";
  selector?: string;
  text?: string;
  url?: string;
  wait_ms?: number;
  timeout_s?: number;
  iframe?: boolean;
  strategy?: string;
}

export interface ScrapeOptions {
  /** Navigate/click inside the page's first iframe. */
  iframe?: boolean;
  /** Strategy for event extraction. */
  strategy?: "outlook" | "date_param" | "auto";
  /** Month start for date-param extraction (YYYY-MM-DD). */
  month_start?: string;
  /** Maximum number of LLM-guided iterations before giving up. */
  maxIterations?: number;
  /** Pre-loop navigation steps (e.g. login), from scraper strategy. */
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

// ---------------------------------------------------------------------------
// System prompts by calendar system type
// ---------------------------------------------------------------------------

function systemPrompt(type: string, url: string): string {
  const base = [
    "Du er en agent som navigerer ishall-kalendere for å finne bookinger.",
    "",
    "**Hva du ser etter:**",
    "Ishall-bookinger ser typisk slik ut:",
    "- Datoer med tidsluker (f.eks. '08:00-09:30' eller 'kl 08.00-09.30')",
    "- Hallnavn som 'Kongsberghallen', 'Jarhallen', 'Bærum ishall'",
    "- Lag-/klubbnavn som 'Kongsberg', 'Jar', 'Jutul', 'Skien'",
    "- Aktiviteter som 'ishockey', 'kunstløp', 'trening', 'kamp'",
    "- Månedsoversikter med ukedager og datoer",
    "",
    "**Dine mulige handlinger:**",
    '1. **click** -- Klikk på en knapp eller lenke. Bruk CSS/text-selector fra interactive-listen.',
    '2. **goto** -- Naviger til en ny URL (for date-parameter kalendere).',
    '3. **extract** -- Ekstraher kalenderdata fra siden (kaller innebygd parser).',
    '4. **wait** -- Vent i N millisekunder.',
    '5. **scroll** -- Rull siden (up/down).',
    '6. **done** -- Signaliser at du er ferdig. Returner events hvis du har ekstrahert noen.',
    "",
    "**Regler:**",
    "- Svar ALLTID med et JSON-objekt -- ingen forklarende tekst utenfor JSON.",
    "- For Outlook iframe-kalendere: se etter en 'Go to next month'-knapp.",
    "- For date-parameter kalendere: endre datoen i URL-en.",
    '- Bruk **extract** for å kalle den innebygde event-parseren.',
    "- Når du er ferdig, returner **done**.",
  ];

  if (type === "outlook") {
    base.push(
      "",
      "**Outlook iframe:**",
      "- Siden har et iframe-element med kalenderen.",
      "- Klikk 'Go to next month' / 'Go to previous month' for å navigere.",
      "- events finnes som aria-label-attributter i iframen.",
    );
  } else if (type === "bookup") {
    base.push(
      "",
      "**Bookup:**",
      "- Bruk date-parameter for å navigere: ?date=YYYY-MM-DD",
      "- Bookinger vises som tabellrekker med tidspunkt og formål.",
    );
  } else if (type === "forumbooking") {
    base.push(
      "",
      "**Forumbooking:**",
      "- Nettsiden viser en månedskalender.",
      "- Se etter navigasjonsknapper for å bytte måned.",
    );
  }

  base.push("", `**Kilde:** ${url}`);
  return base.join("\n");
}

export function userMessage(
  snapshot: WorkerResponse,
  iteration: number,
  maxIterations: number,
): string {
  const lines: string[] = [
    `Iterasjon ${iteration}/${maxIterations}`,
    "",
    "--- Side-status ---",
    `Tittel: ${snapshot.title ?? "ukjent"}`,
    `URL: ${snapshot.url ?? "ukjent"}`,
    "",
    "Synlig HTML (første 3000 tegn):",
    redactCredentials((snapshot.html ?? "").slice(0, 3000)),
  ];

  if (snapshot.iframe_html) {
    lines.push("", "Iframe HTML (første 3000 tegn):");
    lines.push(redactCredentials(snapshot.iframe_html.slice(0, 3000)));
  }

  if (snapshot.interactive && snapshot.interactive.length > 0) {
    lines.push("", "Interaktive elementer:");
    for (const el of snapshot.interactive.slice(0, 30)) {
      lines.push(`  <${el.tag}> "${redactCredentials(el.text)}" → ${el.selector}`);
    }
  }

  if (snapshot.events && snapshot.events.length > 0) {
    lines.push("", `Allerede ekstraherte events (${snapshot.events.length}):`);
    for (const e of snapshot.events.slice(0, 10)) {
      lines.push(`  ${e.date} ${e.name} (${e.duration_hours}h)`);
    }
  }

  lines.push("", "Hva vil du gjøre? Svar med et JSON-objekt.");
  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// LLM client — calls Pi's configured model via HTTP
// ---------------------------------------------------------------------------

async function callLLM(
  ctx: ExtensionContext,
  system: string,
  user: string,
  onUsage?: (details: Record<string, unknown>) => void,
  meta: Record<string, unknown> = {},
): Promise<string> {
  const model = ctx.model;
  if (!model) {
    throw new Error("Ingen modell konfigurert i Pi");
  }

  const baseUrl = model.baseUrl.replace(/\/+$/, "");
  const apiKey = model.provider
    ? await ctx.modelRegistry.getApiKeyForProvider(model.provider)
    : undefined;

  // Determine the endpoint and payload format based on the API type
  // OpenAI-compatible format is the most universal
  const url = `${baseUrl}/v1/chat/completions`;

  const payload = {
    model: model.id,
    messages: [
      { role: "system", content: system },
      { role: "user", content: user },
    ],
    temperature: 0.1,
    max_tokens: 2000,
  };

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (apiKey) {
    headers["Authorization"] = `Bearer ${apiKey}`;
  }

  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
    signal: ctx.signal,
  });

  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`LLM API feil (${response.status}): ${text.slice(0, 200)}`);
  }

  const result = (await response.json()) as {
    choices?: Array<{ message?: { content?: string } }>;
    usage?: {
      prompt_tokens?: number;
      completion_tokens?: number;
      total_tokens?: number;
    };
  };
  const content = result?.choices?.[0]?.message?.content ?? "";
  const usage = result.usage;
  if (usage && onUsage) {
    const totalTokens = usage.total_tokens ?? (usage.prompt_tokens ?? 0) + (usage.completion_tokens ?? 0);
    onUsage({
      ...meta,
      model: model.id,
      provider: model.provider ?? undefined,
      prompt_tokens: usage.prompt_tokens ?? 0,
      completion_tokens: usage.completion_tokens ?? 0,
      total_tokens: totalTokens,
      tokens: totalTokens,
      response_chars: content.length,
    });
  }
  return content;
}

// ---------------------------------------------------------------------------
// JSON extraction from LLM output
// ---------------------------------------------------------------------------

function extractJSON(text: string): Record<string, unknown> | null {
  let cleaned = text.trim();
  for (const fence of ["```json", "```"]) {
    if (cleaned.startsWith(fence)) {
      cleaned = cleaned.slice(fence.length).trim();
      if (cleaned.endsWith("```")) cleaned = cleaned.slice(0, -3).trim();
    }
  }
  const start = cleaned.indexOf("{");
  const end = cleaned.lastIndexOf("}");
  if (start !== -1 && end !== -1 && end > start) {
    cleaned = cleaned.slice(start, end + 1);
  }
  try {
    return JSON.parse(cleaned) as Record<string, unknown>;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Action dispatch
// ---------------------------------------------------------------------------

function isManualBookupLoginEnabled(): boolean {
  return [process.env.RVV_BOOKUP_MANUAL_LOGIN, process.env.BOOKUP_MANUAL_LOGIN]
    .some((value) => ["1", "true", "yes", "y", "on"].includes(String(value ?? "").trim().toLowerCase()));
}

function parseAction(data: Record<string, unknown>): LLMAction | null {
  const action = String(data.action ?? "");
  if (!["click", "goto", "extract", "done", "wait", "scroll"].includes(action)) {
    return null;
  }
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

// ---------------------------------------------------------------------------
// ScraperAgent class
// ---------------------------------------------------------------------------

export class ScraperAgent {
  private proc: ChildProcess | null = null;
  private buffer = "";
  private ctx: ExtensionContext;
  private pythonPath: string;
  private onLLMInteraction?: (details: Record<string, unknown>) => void;
  private onActivity?: (message: string) => void;

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

  /** Start the browser worker process. */
  async start(): Promise<void> {
    if (this.proc) return;

    const workerPath = resolve(
      this.ctx.cwd,
      "tournament_scheduler",
      "pipeline",
      "browser_worker.py",
    );

    this.proc = spawn(this.pythonPath, [workerPath], {
      stdio: ["pipe", "pipe", "pipe"],
      cwd: this.ctx.cwd,
    });
    this.onActivity?.("Browser worker startet");

    this.proc.stderr?.on("data", (chunk: Buffer) => {
      const text = chunk.toString();
      if (text.trim()) {
        console.error(`[browserWorker stderr] ${text.trim().slice(0, 200)}`);
      }
    });

    this.proc.on("exit", (code) => {
      this.proc = null;
    });
  }

  /** Send a command and wait for the response. */
  async send(cmd: Record<string, unknown>): Promise<WorkerResponse> {
    if (!this.proc) throw new Error("Worker not started");

    return new Promise((resolve, reject) => {
      const data = JSON.stringify(cmd) + "\n";
      const timeout = setTimeout(() => {
        reject(new Error(`Worker timeout for command: ${cmd.cmd}`));
      }, 45_000);

      const onData = (chunk: Buffer) => {
        this.buffer += chunk.toString();
        const nl = this.buffer.indexOf("\n");
        if (nl === -1) return;

        const line = this.buffer.slice(0, nl);
        this.buffer = this.buffer.slice(nl + 1);
        clearTimeout(timeout);
        this.proc?.stdout?.removeListener("data", onData);

        try {
          resolve(JSON.parse(line) as WorkerResponse);
        } catch (e) {
          reject(new Error(`Ugyldig JSON fra worker: ${line.slice(0, 200)}`));
        }
      };

      this.proc?.stdout?.on("data", onData);
      this.proc?.stdin?.write(data);
    });
  }

  /** Scrape a single calendar source. */
  async scrape(
    url: string,
    options: ScrapeOptions = {},
  ): Promise<CalendarEvent[]> {
    await this.start();
    const maxIter = options.maxIterations ?? 15;
    const systemType = options.strategy ?? "auto";
    const allEvents: CalendarEvent[] = [];

    // Step 1: Load the page
    this.onActivity?.(`Laster ${url}`);
    let snap = await this.send({
      cmd: "goto",
      url,
      wait_ms: 3000,
    });

    if (!snap.ok) {
      throw new Error(`Kunne ikke laste ${url}: ${snap.error}`);
    }

    // Step 1.5: Execute pre-loop navigation (e.g. login for BookUp)
    const navSteps = options.initialNavigation ?? [];

    // Credential pre-flight: warn if placeholders resolve to empty strings
    const credWarnings: string[] = [];
    for (const step of navSteps) {
      if (step.cmd === "type" || step.cmd === "goto") {
        const raw = (step as Record<string, unknown>).text ?? (step as Record<string, unknown>).url ?? "";
        const matches = String(raw).matchAll(/\$\{(\w+)\}/g);
        for (const m of matches) {
          const varName = m[1];
          const resolved = process.env[varName];
          if (!resolved) {
            const sourceName = step.cmd === "type"
              ? `${step.cmd} ${(step as Record<string, unknown>).selector ?? "?"}`
              : `${step.cmd} ${String(raw).slice(0, 60)}`;
            credWarnings.push(`  ${varName} (brukes i ${sourceName})`);
          }
        }
      }
    }
    if (credWarnings.length > 0) {
      console.warn(
        `[ScraperAgent] Advarsel: ${credWarnings.length} credential-plassholdere uten verdi for ${url}:\n` +
        credWarnings.join("\n")
      );
    }

    for (let si = 0; si < navSteps.length; si++) {
      const step = navSteps[si];
      const wait_ms = step.wait_ms ?? 1500;
      try {
        if (step.cmd === "manual_login") {
          if (isManualBookupLoginEnabled()) {
            const message = step.text || "Fullfør eventuell Vipps/SMS-MFA i nettleseren.";
            this.onActivity?.("Venter på manuell BookUp-innlogging/MFA");
            await this.ctx.ui.input(`${message}\n\nTrykk Enter her når BookUp-kalenderen er synlig.`, "");
            snap = await this.send({ cmd: "snapshot" });
          }
        } else if (step.cmd === "click") {
          snap = await this.send({
            cmd: "click",
            selector: step.selector ?? "",
            iframe: step.iframe ?? false,
            wait_ms,
          });
        } else if (step.cmd === "type") {
          const typedText = step.text ? substituteEnvVars(step.text) : "";
          snap = await this.send({
            cmd: "type",
            selector: step.selector ?? "",
            text: typedText,
            wait_ms,
          });
        } else if (step.cmd === "goto") {
          const gotoUrl = step.url ? substituteEnvVars(step.url) : url;
          snap = await this.send({
            cmd: "goto",
            url: gotoUrl,
            wait_ms: step.wait_ms ?? 3000,
          });
        } else if (step.cmd === "wait") {
          await new Promise((r) => setTimeout(r, wait_ms));
          // Re-snapshot after wait
          snap = { ...snap, html: snap.html, iframe_html: snap.iframe_html };
        }
      } catch (err) {
        console.error(`init-nav step ${si + 1}/${navSteps.length} feilet:`, err);
        // Continue — initial nav is best-effort
      }
    }

    // Step 2: If there's an iframe, detect it
    const hasIframe = !!(snap.iframe_html && snap.iframe_html.length > 100);

    // Step 3: Agent loop
    for (let i = 1; i <= maxIter; i++) {
      this.onActivity?.(`Iterasjon ${i}/${maxIter} — prøver ekstraksjon`);
      // Try extraction first
      const extractResult = await this.send({
        cmd: "extract",
        strategy: systemType,
        iframe: options.iframe ?? hasIframe,
        month_start: options.month_start,
      });
      if (extractResult.ok && extractResult.events) {
        allEvents.push(...extractResult.events);
        if (extractResult.events.length > 0) {
          this.onActivity?.(`Iterasjon ${i}/${maxIter} — fant ${extractResult.events.length} events (totalt ${allEvents.length})`);
        }
        if (allEvents.length > 0) {
          // We got events — if it's a date-param calendar we're likely done
          // For iframe/outlook we need to navigate all months
        }
      }

      // Send snapshot to Pi's model
      const system = systemPrompt(systemType, url);
      const user = userMessage(snap, i, maxIter);
      let llmText: string;
      try {
        this.onActivity?.(`Iterasjon ${i}/${maxIter} — spør modellen om neste trekk`);
        llmText = await callLLM(this.ctx, system, user, this.onLLMInteraction, {
          iteration: i,
          max_iterations: maxIter,
          url,
          strategy: systemType,
          iframe: options.iframe ?? hasIframe,
        });
      } catch (err) {
        console.error(`LLM-feil (iter ${i}):`, err);
        // Continue anyway — try a generic approach
        if (hasIframe) {
          // Try clicking "next month"
          const clickResult = await this.send({
            cmd: "click",
            selector: 'button[aria-label*="next month"]',
            iframe: true,
            wait_ms: 1500,
          });
          if (clickResult.ok) {
            snap = clickResult;
            continue;
          }
        }
        break;
      }

      const parsed = extractJSON(llmText);
      if (!parsed) {
        console.error(`Kunne ikke tolke LLM-svar (iter ${i}): ${llmText.slice(0, 200)}`);
        continue;
      }

      const action = parseAction(parsed);
      if (!action) {
        continue;
      }

      if (action.action === "done") {
        this.onActivity?.(`Iterasjon ${i}/${maxIter} — modellen ba om ferdig`);
        break;
      }

      // Execute the action
      this.onActivity?.(`Iterasjon ${i}/${maxIter} — utfører ${action.action}`);
      if (action.action === "click") {
        snap = await this.send({
          cmd: "click",
          selector: action.selector,
          iframe: action.iframe ?? hasIframe,
          wait_ms: action.wait_ms ?? 1500,
        });
      } else if (action.action === "goto") {
        snap = await this.send({
          cmd: "goto",
          url: action.url,
          wait_ms: 3000,
        });
      } else if (action.action === "extract") {
        const ex = await this.send({
          cmd: "extract",
          strategy: action.strategy ?? systemType,
          iframe: action.iframe ?? hasIframe,
          month_start: options.month_start,
        });
        if (ex.ok && ex.events) {
          allEvents.push(...ex.events);
        }
      } else if (action.action === "wait") {
        await new Promise((r) => setTimeout(r, action.wait_ms ?? 1000));
        snap = {
          ...snap,
          html: snap.html,
          iframe_html: snap.iframe_html,
        };
      }

      if (!snap.ok) {
        break;
      }
    }

    this.onActivity?.(`Fullført — ${allEvents.length} events samlet`);
    return allEvents;
  }

  /** Shut down the worker. */
  async close(): Promise<void> {
    if (this.proc) {
      try {
        await this.send({ cmd: "exit" });
      } catch {
        this.proc.kill("SIGTERM");
      }
      this.proc = null;
    }
  }
}


// ---------------------------------------------------------------------------
// Env-var substitution helper
// ---------------------------------------------------------------------------

/** Substitute ``$VAR_NAME`` placeholders from ``process.env``. */
export function substituteEnvVars(text: string): string {
  return text.replace(/\$\{(\w+)\}/g, (_match, name: string) => {
    return process.env[name] ?? '';
  });
}

/**
 * Defense-in-depth (layer 3): scrub any literal occurrences of the resolved
 * ``BOOKUP_EMAIL``/``BOOKUP_PASSWORD`` credential values from text before it
 * is sent to the LLM. This is a fallback in case the Python-side DOM
 * sanitization in browser_worker.py (`_sanitize_html()` /
 * `_redact_credentials()`, layers 1-2) misses a path (e.g. a credential
 * value echoed into an interactive-element label or placeholder). Used by
 * `userMessage()` on `snapshot.html`, `snapshot.iframe_html`, and
 * interactive-element text.
 *
 * Longer-term alternative (out of scope here): out-of-band browser auth —
 * a persistent authenticated browser session established once outside the
 * LLM loop, so credential/login UI state never reaches a snapshot at all.
 */
export function redactCredentials(text: string): string {
  let result = text;
  for (const envVar of ['BOOKUP_EMAIL', 'BOOKUP_PASSWORD']) {
    const value = process.env[envVar];
    if (value) {
      result = result.split(value).join('[REDACTED]');
    }
  }
  return result;
}


