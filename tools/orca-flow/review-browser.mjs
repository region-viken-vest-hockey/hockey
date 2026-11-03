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

export function isReviewProjectUrl(url, { conversation = false } = {}) {
  try {
    const u = new URL(url);
    if (u.origin !== "https://chatgpt.com" || !u.pathname.split("/").some((part) => part === REVIEW_CHATGPT_PROJECT_ID || part.startsWith(`${REVIEW_CHATGPT_PROJECT_ID}-`))) return false;
    return conversation ? /\/c\/[\w.%:-]+$/.test(u.pathname) : true;
  } catch {
    return false;
  }
}

function activeTab(exec, cwd, pageId) {
  const tabs = orca(exec, cwd, ["tab", "list"]);
  if (tabs.error) return refuse("TAB_LIST_FAILED", tabs.error);
  const tab = (tabs.value.tabs ?? []).find((t) => t.browserPageId === pageId);
  if (!tab) return refuse("PAGE_NOT_FOUND", pageId);
  return { ok: true, tab };
}

/** Focus the page, then prove it is the active tab and still inside the Hockey project. */
function focus(exec, cwd, pageId) {
  const switched = orca(exec, cwd, ["tab", "switch", "--page", pageId, "--focus"]);
  if (switched.error) return refuse("FOCUS_FAILED", switched.error);
  const found = activeTab(exec, cwd, pageId);
  if (!found.ok) return found;
  if (found.tab.active !== true) return refuse("PAGE_NOT_ACTIVE", pageId);
  if (!isReviewProjectUrl(found.tab.url)) return refuse("WRONG_PROJECT", found.tab.url);
  return { ok: true, url: found.tab.url };
}

function snapshot(exec, cwd, pageId) {
  const snap = orca(exec, cwd, ["snapshot", "--page", pageId]);
  if (snap.error) return refuse("SNAPSHOT_FAILED", snap.error);
  const refs = Object.entries(snap.value.refs ?? {}).map(([ref, v]) => ({ ref, ...v }));
  return { ok: true, refs, origin: snap.value.origin ?? "" };
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

/** Find `Chat actions` inside the sidebar entry whose group title matches, from snapshot text. */
function chatActionsRef(snapshotText, title) {
  const lines = snapshotText.split("\n");
  const at = lines.findIndex((l) => l.includes(`"${title}"`) && /group|link|listitem/.test(l));
  if (at < 0) return undefined;
  const hit = lines.slice(at, at + 13).find((l) => l.includes("Chat actions"));
  return hit && /\[ref=(e\d+)\]/.exec(hit)?.[1];
}

const refByName = (refs, role, name) => refs.find((r) => r.role === role && r.name === name)?.ref;

/**
 * Delete the chat open in `pageId`. It must be a conversation inside the Hockey project; the
 * first message is not compared, so the caller is responsible for passing a page it created.
 */
export function reviewDelete({ repoRoot, exec = defaultExec, pageId }) {
  repoRoot = mainCheckout(exec, repoRoot);
  const focused = focus(exec, repoRoot, pageId);
  if (!focused.ok) return focused;
  if (!isReviewProjectUrl(focused.url, { conversation: true })) return refuse("NOT_A_PROJECT_CHAT", focused.url);
  const titled = evaluate(exec, repoRoot, pageId, "document.title");
  if (!titled.ok) return titled;
  const title = titled.text.replace(/\s*[|\-–]\s*ChatGPT$/, "").trim();
  if (!title) return refuse("TITLE_UNKNOWN", "Chat has no title yet; wait for the reply first");

  const step = (needRole, needName, resolve) => {
    const snap = orca(exec, repoRoot, ["snapshot", "--page", pageId]);
    if (snap.error) return refuse("SNAPSHOT_FAILED", snap.error);
    const refs = Object.entries(snap.value.refs ?? {}).map(([ref, v]) => ({ ref, ...v }));
    const ref = resolve ? resolve(String(snap.value.snapshot ?? "")) : refByName(refs, needRole, needName);
    if (!ref) return refuse("DELETE_STEP_MISSING", needName);
    const f = focus(exec, repoRoot, pageId);
    if (!f.ok) return f;
    const clicked = orca(exec, repoRoot, ["click", "--element", ref]);
    return clicked.error ? refuse("CLICK_FAILED", clicked.error) : { ok: true };
  };
  for (const args of [
    ["button", "Chat actions", (text) => chatActionsRef(text, title)],
    ["menuitem", "Delete"],
    ["button", "Delete chat"],
  ]) {
    const done = step(...args);
    if (!done.ok) return done;
  }
  const after = orca(exec, repoRoot, ["snapshot", "--page", pageId]);
  const remaining = after.error ? 1 : String(after.value.snapshot ?? "").split(`"${title}"`).length - 1;
  const closed = orca(exec, repoRoot, ["tab", "close", "--page", pageId]);
  if (remaining > 0) return refuse("DELETE_NOT_CONFIRMED", `${remaining} sidebar entries still show "${title}"`);
  return { ok: true, deleted: title, tabClosed: !closed.error };
}
