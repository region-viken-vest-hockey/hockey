import assert from "node:assert/strict";
import { test } from "node:test";
import { buildTaskSpec } from "./adapter.mjs";
import { GATE } from "./gate.mjs";
import { verifyHead } from "./verify-head.mjs";

test("the worker task names the canonical gate and no other", () => {
  const spec = buildTaskSpec({ issue: 7, title: "t", branch: "agent/issue-7", worktree: "/w" });
  assert.ok(spec.includes(`\`${GATE.label}\``));
  assert.ok(!spec.includes("mise run check"));
  assert.ok(!spec.includes("quality:check"));
});

test("final-head verification runs the same gate", () => {
  const calls = [];
  const sha = "a".repeat(40);
  const base = "b".repeat(40);
  const exec = (command, args) => {
    calls.push([command, ...args].join(" "));
    if (command === "gh" && args[0] === "api") return { status: 0, stdout: `${base}\n`, stderr: "" };
    if (command === "gh") {
      return { status: 0, stdout: JSON.stringify({ number: 7, state: "OPEN", isDraft: false, baseRefName: "main", headRefName: "agent/issue-7", headRefOid: sha, url: "https://github.com/o/r/pull/7" }), stderr: "" };
    }
    if (args[0] === "branch") return { status: 0, stdout: "agent/issue-7\n", stderr: "" };
    if (args[0] === "status") return { status: 0, stdout: "", stderr: "" };
    if (args[0] === "rev-parse" && args.includes("refs/remotes/origin/main")) return { status: 0, stdout: `${base}\n`, stderr: "" };
    if (args[0] === "rev-parse") return { status: 0, stdout: `${sha}\n`, stderr: "" };
    return { status: 0, stdout: "", stderr: "" };
  };
  const receipt = verifyHead({ repoRoot: "/r", issue: 7, pr: 7, exec });
  assert.equal(receipt.ok, true, JSON.stringify(receipt));
  assert.equal(receipt.verified, GATE.label);
  assert.ok(calls.includes(`${GATE.command} ${GATE.args.join(" ")}`), calls.join("\n"));
});
