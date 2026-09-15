import type { ExtensionCommandContext } from "@earendil-works/pi-coding-agent";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { runPipeline } from "./pipeline-runner";
import { formatProcessFailure, runRepoCli } from "./repo-cli";

async function showRepoResult(ctx: ExtensionCommandContext, args: string[]): Promise<void> {
  const result = await runRepoCli(ctx, args, { timeoutMs: 10 * 60 * 1000 });
  if (result.code !== 0) {
    ctx.ui.notify(formatProcessFailure(result), result.cancelled ? "warning" : "error");
    return;
  }
  ctx.ui.notify([result.stdout.trim(), result.stderr.trim()].filter(Boolean).join("\n"), "info");
}

function sharedGuideText(cwd: string): string {
  try {
    return readFileSync(resolve(cwd, ".agents", "commands", "rvv-miniputt", "guide.md"), "utf-8");
  } catch {
    return "Use /rvv-miniputt run, status, logs, calendars, scrape, scrape-llm or publish. Shared policy lives in AGENTS.md and .agents/skills/rvv/SKILL.md.";
  }
}

export async function interactiveGuide(ctx: ExtensionCommandContext): Promise<void> {
  const mainChoice = await ctx.ui.select(
    "Hva vil du gjøre med RVV Miniputt?",
    [
      "Kjør sesongplan-pipeline",
      "Vis pipeline-status",
      "Vis logger",
      "Generer kalenderoversikt",
      "Hjelp",
      "Avbryt",
    ],
  );
  if (!mainChoice || mainChoice === "Avbryt") return;

  if (mainChoice === "Vis pipeline-status") {
    const workDir = await ctx.ui.input("Arbeidskatalog:", ".pipeline");
    await showRepoResult(ctx, ["status", "--work-dir", workDir || ".pipeline"]);
    return;
  }

  if (mainChoice === "Vis logger") {
    const logChoice = await ctx.ui.select("Logger", ["Siste kjøringer", "Nyeste kjøring", "Statistikk"]);
    if (!logChoice) return;
    if (logChoice === "Nyeste kjøring") await showRepoResult(ctx, ["logs", "show", "latest"]);
    else if (logChoice === "Statistikk") await showRepoResult(ctx, ["logs", "stats"]);
    else await showRepoResult(ctx, ["logs", "list", "--count", "10"]);
    return;
  }

  if (mainChoice === "Generer kalenderoversikt") {
    await showRepoResult(ctx, ["calendars"]);
    return;
  }

  if (mainChoice === "Hjelp") {
    ctx.ui.notify(sharedGuideText(ctx.cwd), "info");
    return;
  }

  await interactiveRunPipeline(ctx);
}

async function interactiveRunPipeline(ctx: ExtensionCommandContext): Promise<void> {
  const inputFile = (await ctx.ui.input("Input-fil:", "input.xlsx")) || "input.xlsx";
  if (!existsSync(resolve(ctx.cwd, inputFile))) {
    ctx.ui.notify(`Finner ikke ${inputFile}.`, "error");
    return;
  }

  const resumeChoice = await ctx.ui.select(
    "Startpunkt",
    ["Fra Stage 1", "Fra Stage 2", "Fra Stage 3", "Fra Stage 4"],
  );
  const resumeFrom = resumeChoice?.endsWith("2") ? "2"
    : resumeChoice?.endsWith("3") ? "3"
      : resumeChoice?.endsWith("4") ? "4"
        : "1";

  const workDir = (await ctx.ui.input("Arbeidskatalog:", ".pipeline")) || ".pipeline";
  const exportDir = (await ctx.ui.input("Eksportmappe:", "export")) || "export";
  const confirmed = await ctx.ui.confirm(
    "Start pipeline",
    `Input: ${inputFile}\nArbeidskatalog: ${workDir}\nEksport: ${exportDir}\nStart: Stage ${resumeFrom}`,
  );
  if (!confirmed) return;

  const args = [
    "--input", inputFile,
    "--work-dir", workDir,
    "--export-dir", exportDir,
    "--resume-from", resumeFrom,
  ].map((part) => /\s/.test(part) ? JSON.stringify(part) : part).join(" ");

  const result = await runPipeline(args, ctx, (event) => {
    if (event.stage === "done") ctx.ui.setStatus("rvv-miniputt", undefined);
    else ctx.ui.setStatus("rvv-miniputt", `${event.stage}: ${event.message}`);
  });
  ctx.ui.setStatus("rvv-miniputt", undefined);
  ctx.ui.notify(
    result.text,
    result.status === "success" ? "info" : result.status === "cancelled" || result.status === "needs_input" ? "warning" : "error",
  );
}
