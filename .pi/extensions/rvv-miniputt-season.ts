import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { tokenizeArgs } from "../lib/arg-utils";
import { formatProcessFailure, runRepoCli } from "../lib/repo-cli";

interface CommandResult {
  status: "success" | "failure" | "cancelled";
  text: string;
}

async function runSeason(rawArgs: unknown, ctx: ExtensionContext): Promise<CommandResult> {
  const tokens = tokenizeArgs(rawArgs);
  if (tokens.length === 0) {
    return {
      status: "failure",
      text: "Oppgi en canonical season-operasjon, f.eks. 'status --season 2026-2027'. Se .agents/commands/rvv-miniputt/season.md.",
    };
  }

  const result = await runRepoCli(ctx, ["season", ...tokens], { timeoutMs: 30 * 60 * 1000 });
  if (result.cancelled || ctx.signal?.aborted) {
    return { status: "cancelled", text: "Season-operasjon avbrutt av bruker." };
  }
  if (result.code !== 0) {
    return { status: "failure", text: formatProcessFailure(result) };
  }
  return {
    status: "success",
    text: [result.stdout.trim(), result.stderr.trim() ? `[stderr] ${result.stderr.trim()}` : ""]
      .filter(Boolean)
      .join("\n"),
  };
}

function notifyResult(ctx: ExtensionContext, result: CommandResult): void {
  const level = result.status === "success" ? "info" : result.status === "cancelled" ? "warning" : "error";
  ctx.ui.notify(result.text, level);
}

export default function rvvMiniputtSeason(pi: ExtensionAPI): void {
  pi.registerCommand("rvv-miniputt season", {
    description:
      "Vedlikehold promotert canonical RVV-sesong via repoets season-CLI (promote/status/approvals/approve/unapprove/move/replan/diff/apply/export).",
    handler: async (args, ctx) => notifyResult(ctx, await runSeason(args, ctx)),
  });

  pi.registerTool({
    name: "rvv_miniputt_season",
    label: "RVV Miniputt: Season",
    description:
      "Operate on the promoted canonical RVV season through the repository-owned season CLI. Use for approval/locks, targeted moves, baseline-aware replanning, diff/apply and canonical export.",
    promptSnippet: "Maintain the promoted canonical RVV season",
    promptGuidelines: [
      "Read .agents/commands/rvv-miniputt/season.md and .agents/skills/rvv/SKILL.md before using this tool.",
      "Do not hand-edit season/<season>/schedule.json, decisions.json, checkpoints or generated exports.",
      "Approved/placement-locked tournaments must be explicitly unapproved before a requested move; never bypass locks.",
      "After canonical schedule or decision state changes, use season export before semantic audit/publication.",
    ],
    parameters: Type.Object({
      args: Type.String({
        description:
          "Arguments after 'season', e.g. 'status --season 2026-2027', 'approve --season 2026-2027 --tournament-id abc123', or 'replan --season 2026-2027 --iterations 4000'.",
      }),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const result = await runSeason(params.args, ctx);
      return { content: [{ type: "text", text: result.text }], details: result };
    },
  });
}
