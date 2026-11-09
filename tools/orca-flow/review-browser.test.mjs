import assert from "node:assert/strict";
import test from "node:test";

import { parseArgs } from "./cli.mjs";
import {
  conversationId,
  GONE_CHAT_TEXT,
  isReviewProjectUrl,
  refInside,
  reviewDelete,
  REVIEW_CHATGPT_PROJECT_ID,
} from "./review-browser.mjs";

const CAMPSTY_PROJECT_ID = "g-p-6a3f627992648191a41d0b2097584655";
const HOCKEY_CHAT = `https://chatgpt.com/g/${REVIEW_CHATGPT_PROJECT_ID}-hockey/c/hockey-chat`;
const CAMPSTY_CHAT = `https://chatgpt.com/g/${CAMPSTY_PROJECT_ID}-campsty/c/campsty-chat`;

test("project URL checks default to Hockey and accept an explicit cleanup project", () => {
  assert.equal(isReviewProjectUrl(HOCKEY_CHAT, { conversation: true }), true);
  assert.equal(isReviewProjectUrl(CAMPSTY_CHAT, { conversation: true }), false);
  assert.equal(
    isReviewProjectUrl(CAMPSTY_CHAT, { conversation: true, projectId: CAMPSTY_PROJECT_ID }),
    true,
  );
  assert.equal(isReviewProjectUrl("https://chatgpt.com/c/top-level", { conversation: true }), false);
});

test("conversationId extracts only a terminal conversation path", () => {
  assert.equal(conversationId(`${CAMPSTY_CHAT}?model=auto`), "campsty-chat");
  assert.equal(conversationId(`https://chatgpt.com/g/${CAMPSTY_PROJECT_ID}-campsty/project`), null);
  assert.equal(conversationId("not a URL"), null);
});

test("refInside selects the header action instead of a sidebar action", () => {
  const snapshot = `- complementary\n  - button "Chat actions" [expanded=false, ref=e120]\n- main\n  - sectionheader\n    - button "Chat actions" [expanded=false, ref=e4]\n  - region "Conversation" [ref=e25]`;
  assert.equal(refInside(snapshot, /^- sectionheader$/, "button", ["More", "Chat actions"]), "e4");
});

test("deleted-chat text recognises ChatGPT missing-conversation responses", () => {
  assert.match("Unable to load conversation 123", GONE_CHAT_TEXT);
  assert.match("Could not load this ChatGPT conversation", GONE_CHAT_TEXT);
  assert.match("This chat doesn't exist", GONE_CHAT_TEXT);
  assert.doesNotMatch("ChatGPT can make mistakes", GONE_CHAT_TEXT);
});

test("review-delete alone accepts a project-id override", () => {
  assert.deepEqual(
    parseArgs(["review-delete", "--page", "page-1", "--message-file", "/tmp/m", "--project-id", CAMPSTY_PROJECT_ID]),
    { command: "review-delete", pageId: "page-1", messageFile: "/tmp/m", projectId: CAMPSTY_PROJECT_ID },
  );
  assert.deepEqual(
    parseArgs(["review-read", "--page", "page-1", "--project-id", CAMPSTY_PROJECT_ID]),
    { error: "--project-id is only supported by review-delete" },
  );
});

test("review-delete requires the first-message file", () => {
  assert.deepEqual(parseArgs(["review-delete", "--page", "page-1"]), {
    error: "review-delete requires --message-file",
  });
});

// A scripted Orca CLI: one open Hockey chat whose first user message is `first`.
function fakeChat(first, { fallback = false } = {}) {
  const calls = [];
  const exec = (command, args) => {
    const sub = args.slice(0, 2).join(" ");
    calls.push(sub);
    const ok = (result = {}) => ({ status: 0, stdout: JSON.stringify({ ok: true, result }), stderr: "" });
    if (command === "git") return { status: 0, stdout: "/r/.git\n", stderr: "" };
    if (sub === "tab switch") return ok();
    if (sub === "tab list") return ok({ tabs: [{ browserPageId: "p1", active: true, url: HOCKEY_CHAT }] });
    if (command === "orca" && args[0] === "eval") return ok({ result: JSON.stringify({ text: first, fallback }) });
    return { status: 1, stdout: "", stderr: "unexpected " + sub };
  };
  return { exec, calls };
}

test("review-delete refuses a chat whose first message was not sent by the flow", () => {
  const chat = fakeChat("someone else's question");
  const receipt = reviewDelete({ repoRoot: "/r", exec: chat.exec, pageId: "p1", firstMessage: "ping" });
  assert.equal(receipt.code, "NOT_A_FLOW_CHAT");
  assert.ok(!chat.calls.some((c) => c.startsWith("click") || c.startsWith("snapshot")));
});

test("review-delete refuses an empty or missing first message before touching the browser", () => {
  const chat = fakeChat("ping");
  assert.equal(reviewDelete({ repoRoot: "/r", exec: chat.exec, pageId: "p1" }).code, "FIRST_MESSAGE_REQUIRED");
  assert.equal(reviewDelete({ repoRoot: "/r", exec: chat.exec, pageId: "p1", firstMessage: "  \n" }).code, "FIRST_MESSAGE_REQUIRED");
  assert.deepEqual(chat.calls, []);
});

test("review-delete proceeds past the guard when the first message matches", () => {
  const chat = fakeChat("ping\n  pong");
  const receipt = reviewDelete({ repoRoot: "/r", exec: chat.exec, pageId: "p1", firstMessage: "ping pong\n" });
  // The scripted CLI has no snapshot, so it stops at the first delete step, after the guard passed.
  assert.equal(receipt.code, "SNAPSHOT_FAILED");
  assert.ok(!chat.calls.some((c) => c.startsWith("click")));
});

test("review-delete ignores a trailing reasoning-duration line only in the fallback reading", () => {
  const fallback = fakeChat("ping\nWorked for 1m 2s", { fallback: true });
  assert.equal(reviewDelete({ repoRoot: "/r", exec: fallback.exec, pageId: "p1", firstMessage: "ping" }).code, "SNAPSHOT_FAILED");
  const genuine = fakeChat("ping\nWorked for 1m 2s");
  assert.equal(reviewDelete({ repoRoot: "/r", exec: genuine.exec, pageId: "p1", firstMessage: "ping" }).code, "NOT_A_FLOW_CHAT");
});
