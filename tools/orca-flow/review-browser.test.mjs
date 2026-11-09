import assert from "node:assert/strict";
import test from "node:test";

import { parseArgs } from "./cli.mjs";
import {
  conversationId,
  GONE_CHAT_TEXT,
  isReviewProjectUrl,
  refInside,
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
    parseArgs(["review-delete", "--page", "page-1", "--project-id", CAMPSTY_PROJECT_ID]),
    { command: "review-delete", pageId: "page-1", messageFile: undefined, projectId: CAMPSTY_PROJECT_ID },
  );
  assert.deepEqual(
    parseArgs(["review-read", "--page", "page-1", "--project-id", CAMPSTY_PROJECT_ID]),
    { error: "--project-id is only supported by review-delete" },
  );
});
