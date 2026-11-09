// ChatGPT review through the Orca browser: one command per step so agents never rebuild the
// focus/URL/draft/Send-ref dance by hand. Advisory plumbing; it never decides a verdict.
import { spawnSync } from "node:child_process";

// Every review/consult chat must live in the user's ChatGPT project "Hockey". The slug after the id is
// optional in ChatGPT URLs, so the project is matched by its id.
export const REVIEW_CHATGPT_PROJECT_ID = "g-p-6a6859a57a0881919c2f9a9266cec28c";
export const REVIEW_CHATGPT_PROJECT_URL = `https://chatgpt.com/g/${REVIEW_CHATGPT_PROJECT_ID}/project`;
// The project composer has been labelled both ways by ChatGPT.
const COMPOSER_NAMES = ["Ask ChatGPT", "New chat in Hockey", "Reply to ChatGPT"];
const DISCLAIMER = "ChatGPT can make mistakes";

/** @typedef {{status: number | null, stdout: string, stderr: string}} ExecResult */
/** @typedef {(command: string, args: string[], cwd: string) => ExecResult} Exec */
/** @type {Exec} */
const defaultExec = (command, args, cwd) =>
  spawnSync(command, args, { cwd, encoding: "utf8", maxBuffer: 32 * 1024 * 1024 });
/** @type {(ms: number) => void} */
const defaultSleep = (ms) => {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
};

// Orca scopes browser tabs to the worktree it runs in, so review tabs must be created and used from
// the main checkout, never from an issue worktree.
function mainCheckout(exec, cwd) {
  const r = exec("git", ["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd);
  const common = r.status === 0 ? r.stdout.trim() : "";
  return common.endsWith("/.git") ? common.slice(0, -"/.git".length) : cwd;
}

// ProseMirror innerText inflates blank paragraph separators; preserve single line breaks and indentation.
const normDraft = (t) => t.replace(/\r\n?/g, "\n").replace(/\n(?:[\t ]*\n)+/g, "\n\n").trim();

const refuse = (code, detail) => ({ ok: false, code, ...(detail ? { detail } : {}) });

function orca(exec, cwd, args) {
  const result = exec("orca", [...args, "--json"], cwd);
  if (result.status !== 0) return { error: (result.stderr || result.stdout || "").trim().slice(0, 300) };
  try {
    const body = JSON.parse(result.stdout);
    return body.ok === false ? { error: body.error?.message ?? "orca error" } : { value: body.result ?? {} };
  } catch {
    return { error: "orca returned invalid JSON" };
  }
}

export function isReviewProjectUrl(
  url,
  { conversation = false, projectId = REVIEW_CHATGPT_PROJECT_ID } = {},
) {
  try {
    const u = new URL(url);
    const inProject = u.pathname
      .split("/")
      .some((part) => part === projectId || part.startsWith(`${projectId}-`));
    if (u.origin !== "https://chatgpt.com" || !inProject) return false;
    return conversation ? /\/c\/[\w.%:-]+$/.test(u.pathname) : true;
  } catch {
    return false;
  }
}

export function conversationId(url) {
  try {
    return /\/c\/([\w.%:-]+)$/.exec(new URL(url).pathname)?.[1] ?? null;
  } catch {
    return null;
  }
}

function activeTab(exec, cwd, pageId) {
  const tabs = orca(exec, cwd, ["tab", "list"]);
  if (tabs.error) return refuse("TAB_LIST_FAILED", tabs.error);
  const tab = (tabs.value.tabs ?? []).find((t) => t.browserPageId === pageId);
  if (!tab) return refuse("PAGE_NOT_FOUND", pageId);
  return { ok: true, tab };
}

/** Focus the page, then prove it is the active tab and still inside the expected project. */
function focus(exec, cwd, pageId, projectId = REVIEW_CHATGPT_PROJECT_ID) {
  const switched = orca(exec, cwd, ["tab", "switch", "--page", pageId, "--focus"]);
  if (switched.error) return refuse("FOCUS_FAILED", switched.error);
  const found = activeTab(exec, cwd, pageId);
  if (!found.ok) return found;
  if (found.tab.active !== true) return refuse("PAGE_NOT_ACTIVE", pageId);
  if (!isReviewProjectUrl(found.tab.url, { projectId })) return refuse("WRONG_PROJECT", found.tab.url);
  return { ok: true, url: found.tab.url };
}

function snapshot(exec, cwd, pageId) {
  const snap = orca(exec, cwd, ["snapshot", "--page", pageId]);
  if (snap.error) return refuse("SNAPSHOT_FAILED", snap.error);
  const refs = Object.entries(snap.value.refs ?? {}).map(([ref, v]) => ({ ref, ...v }));
  return {
    ok: true,
    refs,
    origin: snap.value.origin ?? "",
    text: String(snap.value.snapshot ?? ""),
  };
}

const findRef = (refs, role, names) =>
  refs.find((r) => r.role === role && names.some((n) => r.name === n))?.ref;

function evaluate(exec, cwd, pageId, expression) {
  const r = orca(exec, cwd, ["eval", "--page", pageId, "--expression", expression]);
  return r.error ? refuse("EVAL_FAILED", r.error) : { ok: true, text: String(r.value.result ?? "") };
}

export function composerDraftText(element) {
  if (!element) return "";
  if (typeof element.value === "string") return element.value;
  const text = (node) => {
    if (node.nodeType === 3) return node.nodeValue || "";
    if (node.nodeName === "BR") return "\n";
    return Array.from(node.childNodes ?? []).map(text).join("");
  };
  return Array.from(element.children ?? [])
    .map((node) => (node.matches?.("[data-empty-paragraph=true]") ? "" : text(node)))
    .join("\n");
}

const DRAFT_JS = `(() => { const e = document.querySelector('#prompt-textarea, textarea, [contenteditable=true]'); return (${composerDraftText.toString()})(e); })()`;
export const FIRST_MESSAGE_JS =
  "(() => { const user = document.querySelector('main [data-message-author-role=\"user\"], main [data-user-message-bubble=\"true\"]'); const message = user?.querySelector('.whitespace-pre-wrap') || user; if (message) return JSON.stringify({ text: message.innerText || '', fallback: false }); const m = document.querySelector('main')?.innerText || ''; const i = m.indexOf('You said:'); if (i < 0) return JSON.stringify({ text: '', fallback: true }); const j = m.indexOf('ChatGPT said:', i); return JSON.stringify({ text: m.slice(i + 9, j < 0 ? undefined : j), fallback: true }); })()";

const normMessage = (t) => String(t ?? "").replace(/\s+/g, " ").trim();
const withoutReasoningDuration = (text) =>
  text.replace(/\n+Worked for (?:(?:\d+h )?(?:\d+m )?\d+s|(?:\d+h )?\d+m)\s*$/, "");

function firstMessageResult(value) {
  try {
    const parsed = JSON.parse(value);
    return typeof parsed?.text === "string" && typeof parsed?.fallback === "boolean" ? parsed : null;
  } catch {
    return null;
  }
}

const REPLY_JS =
  "(() => { const m = document.querySelector('main')?.innerText || ''; const i = m.lastIndexOf('ChatGPT said:'); return JSON.stringify({ reply: i < 0 ? '' : m.slice(i), blocked: /cloudflare_challenge|verify you are human/i.test(m) }); })()";

/** Open the Hockey project page and prove it is ready: right project, logged in, composer present. */
export function reviewOpen({ repoRoot, exec = defaultExec, sleep = defaultSleep, attempts = 8, intervalMs = 4000 }) {
  repoRoot = mainCheckout(exec, repoRoot);
  const created = orca(exec, repoRoot, ["tab", "create", "--url", REVIEW_CHATGPT_PROJECT_URL]);
  if (created.error) return refuse("BROWSER_UNAVAILABLE", created.error);
  const pageId = created.value.browserPageId;
  const focused = focus(exec, repoRoot, pageId);
  if (!focused.ok) return focused;
  // The page is still loading right after create; poll for the composer before giving up.
  let snap;
  for (let i = 0; i < attempts; i += 1) {
    snap = snapshot(exec, repoRoot, pageId);
    if (!snap.ok) return snap;
    if (snap.refs.some((r) => r.role === "button" && /^(log in|sign up|sign in)/i.test(r.name ?? ""))) {
      return refuse("NOT_LOGGED_IN", "Log in to chatgpt.com in the Orca browser, then rerun");
    }
    if (findRef(snap.refs, "textbox", COMPOSER_NAMES)) return { ok: true, pageId, url: focused.url };
    if (i < attempts - 1) sleep(intervalMs);
  }
  orca(exec, repoRoot, ["tab", "close", "--page", pageId]);
  return refuse("COMPOSER_MISSING", snap?.origin);
}

/** Fill the composer, verify the draft is exactly the message, click Send, require a project chat URL. */
export function reviewSend({ repoRoot, exec = defaultExec, pageId, message }) {
  repoRoot = mainCheckout(exec, repoRoot);
  let focused = focus(exec, repoRoot, pageId);
  if (!focused.ok) return focused;
  const before = snapshot(exec, repoRoot, pageId);
  if (!before.ok) return before;
  const composer = findRef(before.refs, "textbox", COMPOSER_NAMES);
  if (!composer) return refuse("COMPOSER_MISSING", before.origin);
  const filled = orca(exec, repoRoot, ["fill", "--element", composer, "--value", message]);
  if (filled.error) return refuse("FILL_FAILED", filled.error);
  const draft = evaluate(exec, repoRoot, pageId, DRAFT_JS);
  if (!draft.ok) return draft;
  if (normDraft(draft.text) !== normDraft(message)) return refuse("DRAFT_MISMATCH", "Composer text differs from the message");
  const after = snapshot(exec, repoRoot, pageId);
  if (!after.ok) return after;
  const send = findRef(after.refs, "button", ["Send", "Send prompt"]);
  if (!send) return refuse("SEND_MISSING", after.origin);
  focused = focus(exec, repoRoot, pageId);
  if (!focused.ok) return focused;
  const clicked = orca(exec, repoRoot, ["click", "--element", send]);
  if (clicked.error) return refuse("CLICK_FAILED", clicked.error);
  const sent = activeTab(exec, repoRoot, pageId);
  if (!sent.ok) return sent;
  if (!isReviewProjectUrl(sent.tab.url, { conversation: true })) return refuse("NOT_A_PROJECT_CHAT", sent.tab.url);
  return { ok: true, pageId, url: sent.tab.url };
}

/** One read of the latest reply. `complete` is true only when the disclaimer ends the text. */
export function reviewRead({ repoRoot, exec = defaultExec, pageId }) {
  repoRoot = mainCheckout(exec, repoRoot);
  const found = activeTab(exec, repoRoot, pageId);
  if (!found.ok) return found;
  if (!isReviewProjectUrl(found.tab.url)) return refuse("WRONG_PROJECT", found.tab.url);
  const reply = evaluate(exec, repoRoot, pageId, REPLY_JS);
  if (!reply.ok) return reply;
  let parsed;
  try { parsed = JSON.parse(reply.text); } catch { return refuse("EVAL_FAILED", "Unreadable page text"); }
  if (parsed.blocked) {
    return refuse("BROWSER_CHALLENGE", "ChatGPT shows a Cloudflare/human check; complete it in the Orca browser, then rerun the send");
  }
  // ChatGPT appends UI text ("Latest response", "Thinking effort") after the disclaimer, so the
  // reply is complete once the disclaimer appears, and the text stops at it.
  const body = parsed.reply.trim();
  const end = body.indexOf(DISCLAIMER);
  const complete = end >= 0;
  return { ok: true, pageId, complete, text: complete ? body.slice(0, end + DISCLAIMER.length) : body };
}

/** Poll until two consecutive complete reads match, or the timeout passes. */
export function reviewWait({ repoRoot, exec = defaultExec, pageId, timeoutMs = 600_000, intervalMs = 10_000, sleep = defaultSleep, now = Date.now }) {
  const deadline = now() + timeoutMs;
  let previous = null;
  for (;;) {
    const read = reviewRead({ repoRoot, exec, pageId });
    if (!read.ok) return read;
    if (read.complete && read.text === previous) return read;
    previous = read.complete ? read.text : null;
    if (now() + intervalMs > deadline) return refuse("REPLY_TIMEOUT", "No complete reply before the timeout");
    sleep(intervalMs);
  }
}

const refByName = (refs, role, name) => refs.find((r) => r.role === role && r.name === name)?.ref;

export function refInside(snapshotText, container, role, names) {
  const lines = snapshotText.split("\n");
  const start = lines.findIndex((line) => container.test(line.trim()));
  if (start < 0) return undefined;
  const containerIndent = lines[start].search(/\S/);
  const choices = Array.isArray(names) ? names : [names];
  for (const line of lines.slice(start + 1)) {
    const indent = line.search(/\S/);
    if (indent >= 0 && indent <= containerIndent) break;
    const match = /^\s*- ([\w]+) "([^"]+)".*\[[^\]]*\bref=(e\d+)[^\]]*\]/.exec(line);
    if (match?.[1] === role && choices.includes(match[2])) return match[3];
  }
  return undefined;
}

export const GONE_CHAT_TEXT = /unable to load conversation|could not load this chatgpt conversation|conversation not found|chat (?:does not|doesn't) exist/i;

function closeTab(exec, cwd, pageId) {
  return !orca(exec, cwd, ["tab", "close", "--page", pageId]).error;
}

function verifyConversationGone(exec, cwd, url, deletedConversationId) {
  const created = orca(exec, cwd, ["tab", "create", "--url", url]);
  if (created.error) return refuse("DELETE_VERIFY_OPEN_FAILED", created.error);
  const verifyPageId = created.value.browserPageId;
  let confirmed = false;
  let detail = url;
  for (let attempt = 0; attempt < 10; attempt += 1) {
    const found = activeTab(exec, cwd, verifyPageId);
    if (!found.ok) {
      detail = found.code;
      break;
    }
    detail = found.tab.url;
    if (conversationId(found.tab.url) !== deletedConversationId) {
      confirmed = true;
      break;
    }
    const pageText = evaluate(exec, cwd, verifyPageId, "document.querySelector('main')?.innerText || document.body?.innerText || ''");
    if (pageText.ok && GONE_CHAT_TEXT.test(pageText.text)) {
      confirmed = true;
      break;
    }
    if (attempt < 9) defaultSleep(1_000);
  }
  const verificationTabClosed = closeTab(exec, cwd, verifyPageId);
  return confirmed
    ? { ok: true, verificationTabClosed }
    : refuse("DELETE_NOT_CONFIRMED", `Re-opening ${url} still showed conversation ${deletedConversationId} (${detail})`);
}

/**
 * Delete the chat open in `pageId`. It must be a conversation inside the expected project, and its
 * first user message must equal `firstMessage` (the text this flow sent) before anything is
 * clicked; otherwise it refuses with NOT_A_FLOW_CHAT. `projectId` is an explicit cleanup
 * override; ordinary review commands remain Hockey-only.
 */
export function reviewDelete({
  repoRoot,
  exec = defaultExec,
  pageId,
  firstMessage,
  projectId = REVIEW_CHATGPT_PROJECT_ID,
}) {
  if (!normMessage(firstMessage)) {
    return refuse("FIRST_MESSAGE_REQUIRED", "Pass the exact first message this flow sent to the chat");
  }
  repoRoot = mainCheckout(exec, repoRoot);
  const focused = focus(exec, repoRoot, pageId, projectId);
  if (!focused.ok) return focused;
  if (!isReviewProjectUrl(focused.url, { conversation: true, projectId })) {
    return refuse("NOT_A_PROJECT_CHAT", focused.url);
  }
  const deletedConversationId = conversationId(focused.url);
  if (!deletedConversationId) return refuse("NOT_A_PROJECT_CHAT", focused.url);

  const first = evaluate(exec, repoRoot, pageId, FIRST_MESSAGE_JS);
  if (!first.ok) return first;
  const parsed = firstMessageResult(first.text);
  const actual = normMessage(parsed?.fallback ? withoutReasoningDuration(parsed.text) : parsed?.text);
  if (!actual || actual !== normMessage(firstMessage)) {
    return refuse("NOT_A_FLOW_CHAT", "First message differs from the one this flow sent; refusing to delete");
  }

  const step = (needRole, needNames, resolve) => {
    const page = snapshot(exec, repoRoot, pageId);
    if (!page.ok) return page;
    const names = Array.isArray(needNames) ? needNames : [needNames];
    const ref = resolve?.(page) ?? names.map((name) => refByName(page.refs, needRole, name)).find(Boolean);
    if (!ref) return refuse("DELETE_STEP_MISSING", names.join(" / "));
    const f = focus(exec, repoRoot, pageId, projectId);
    if (!f.ok) return f;
    const clicked = orca(exec, repoRoot, ["click", "--element", ref]);
    return clicked.error ? refuse("CLICK_FAILED", clicked.error) : { ok: true };
  };
  for (const [role, name, resolve] of [
    [
      "button",
      ["More", "Chat actions"],
      (page) => refInside(page.text, /^- sectionheader$/, "button", ["More", "Chat actions"]),
    ],
    ["menuitem", "Delete"],
    [
      "button",
      "Delete chat",
      (page) => refInside(page.text, /^- dialog "Delete chat\?"$/, "button", "Delete chat"),
    ],
  ]) {
    const done = step(role, name, resolve);
    if (!done.ok) return done;
  }

  const verified = verifyConversationGone(exec, repoRoot, focused.url, deletedConversationId);
  const tabClosed = closeTab(exec, repoRoot, pageId);
  if (!verified.ok) return { ...verified, tabClosed };
  return {
    ok: true,
    deletedConversationId,
    url: focused.url,
    tabClosed,
    verificationTabClosed: verified.verificationTabClosed,
  };
}
