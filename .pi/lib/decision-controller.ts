import type { UserMessage } from "@earendil-works/pi-ai";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

export interface DecisionContextPayload {
  capability?: string;
  stage?: string;
  objective?: string;
  facts?: Record<string, unknown>;
  hard_violations?: unknown[];
  baseline_hard_violations?: unknown[];
  warnings?: unknown[];
  scorecard?: Record<string, unknown>;
  available_actions?: string[];
  action_parameters?: Record<string, unknown>;
  decision_action_template?: Record<string, unknown>;
  requires_human_approval?: boolean;
  [key: string]: unknown;
}

export interface DecisionActionPayload {
  action_id: string;
  target?: string;
  arguments?: Record<string, unknown>;
  rationale?: string;
  [key: string]: unknown;
}

function responseText(response: { content?: Array<{ type: string; text?: string }> }): string {
  return (response.content ?? [])
    .filter((item): item is { type: "text"; text: string } => item.type === "text" && typeof item.text === "string")
    .map((item) => item.text)
    .join("\n");
}

function extractJsonObject(raw: string): Record<string, unknown> | null {
  let text = raw.trim();
  if (text.startsWith("```")) {
    text = text.replace(/^```(?:json)?/i, "").replace(/```$/i, "").trim();
  }
  try {
    const parsed = JSON.parse(text);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null;
  } catch { /* try a contained object below */ }

  const end = text.lastIndexOf("}");
  if (end < 0) return null;
  const starts: number[] = [];
  for (let i = 0; i <= end; i++) if (text[i] === "{") starts.push(i);
  for (let i = starts.length - 1; i >= 0; i--) {
    try {
      const parsed = JSON.parse(text.slice(starts[i], end + 1));
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch { /* keep looking */ }
  }
  return null;
}

function extractMarkdownSection(markdown: string, heading: string): string {
  const lines = markdown.split(/\r?\n/);
  const start = lines.findIndex((line) => line.trim() === heading);
  if (start < 0) return "";
  const level = heading.match(/^#+/)?.[0].length ?? 1;
  const out: string[] = [lines[start]];
  for (const line of lines.slice(start + 1)) {
    const match = line.trim().match(/^(#+)\s/);
    if (match && match[1].length <= level) break;
    out.push(line);
  }
  return out.join("\n").trim();
}

function sharedPolicyExcerpt(cwd: string): string {
  try {
    const skill = readFileSync(resolve(cwd, ".agents", "skills", "rvv", "SKILL.md"), "utf-8");
    const sections = [
      "## Four-stage pipeline",
      "## Stage gating policy (soft judgment)",
      "## Structured decision protocol",
      "## Human escalation",
    ]
      .map((heading) => extractMarkdownSection(skill, heading))
      .filter(Boolean);
    const runProcedure = readFileSync(
      resolve(cwd, ".agents", "commands", "rvv-miniputt", "run.md"),
      "utf-8",
    );
    return [...sections, runProcedure].join("\n\n");
  } catch (err: unknown) {
    throw new Error(`Could not load shared RVV policy: ${err instanceof Error ? err.message : String(err)}`);
  }
}

function buildDecisionPrompt(context: DecisionContextPayload, policy: string, retryNote = ""): string {
  return [
    "Choose the next RVV Miniputt DecisionAction for the repository-owned DecisionContext below.",
    "",
    "The repository will deterministically validate the action. You do not own hard rules, planner semantics, source-validity rules, or action schemas.",
    "Use only an action_id listed in available_actions and fill arguments only from decision_action_template/action_parameters.",
    "Never invent an argument, candidate_ref, club, source, threshold, or override.",
    "Use request_operator only when actual human authority or unavailable information is required.",
    "For a blocked browser-recoverable source, recover_source is the action that asks the Pi transport to perform browser recovery; after recovery the adapter will rerun canonical Stage 2.",
    "Return only one JSON object. Keep rationale concise and operational; do not include chain-of-thought.",
    retryNote ? `Previous response problem: ${retryNote}` : "",
    "",
    "Shared repository policy (lazy-loaded from agent-neutral files):",
    policy,
    "",
    "DecisionContext:",
    JSON.stringify(context, null, 2),
    "",
    "Return a filled object matching one of decision_action_template's entries, for example:",
    '{"action_id":"proceed","arguments":{},"rationale":"Concise reason based on the supplied facts."}',
  ].filter(Boolean).join("\n");
}

function normalizeAction(
  parsed: Record<string, unknown>,
  context: DecisionContextPayload,
): DecisionActionPayload | null {
  const actionId = typeof parsed.action_id === "string" ? parsed.action_id : "";
  const available = Array.isArray(context.available_actions) ? context.available_actions : [];
  if (!actionId || !available.includes(actionId)) return null;
  const args = parsed.arguments;
  if (args !== undefined && (!args || typeof args !== "object" || Array.isArray(args))) return null;
  return {
    action_id: actionId,
    target: typeof parsed.target === "string" ? parsed.target : "",
    arguments: (args as Record<string, unknown> | undefined) ?? {},
    rationale: typeof parsed.rationale === "string" ? parsed.rationale : "",
  };
}

export async function chooseDecisionAction(
  context: DecisionContextPayload,
  ctx: ExtensionContext,
): Promise<DecisionActionPayload> {
  if (!ctx.model) throw new Error("Pi has no active model available for the RVV decision loop.");
  const policy = sharedPolicyExcerpt(ctx.cwd);
  let retryNote = "";

  for (let attempt = 0; attempt < 2; attempt++) {
    const prompt = buildDecisionPrompt(context, policy, retryNote);
    const userMessage: UserMessage = {
      role: "user",
      content: [{ type: "text", text: prompt }],
      timestamp: Date.now(),
    };
    const response = await ctx.modelRegistry.complete(
      ctx.model,
      {
        systemPrompt: "You are a thin controller over repository-owned capabilities. Return only the requested DecisionAction JSON.",
        messages: [userMessage],
      },
      { signal: ctx.signal },
    );
    if (response.stopReason === "aborted") throw new Error("Pi decision model call was aborted.");
    const raw = responseText(response);
    const parsed = extractJsonObject(raw);
    const action = parsed ? normalizeAction(parsed, context) : null;
    if (action) return action;
    retryNote = `Expected one valid DecisionAction JSON using available_actions=${JSON.stringify(context.available_actions ?? [])}; received ${raw.slice(0, 500)}`;
  }

  throw new Error(`Pi model did not return a valid DecisionAction. ${retryNote}`);
}
