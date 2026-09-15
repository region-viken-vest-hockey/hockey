/**
 * Normalize whatever value the host passes as command arguments into a single
 * trimmed string suitable for tokenization. Local models may hand command
 * handlers undefined/null, arrays, or a string wrapped in stray quotes.
 */
export function normalizeArgs(args: unknown): string {
  if (args === null || args === undefined) return "";
  if (Array.isArray(args)) return args.map((a) => String(a)).join(" ").trim();

  const str = typeof args === "string" ? args : String(args);
  return str.trim().replace(/^(["'])(.*)\1$/, "$2").trim();
}

/**
 * Split command arguments without interpreting shell substitutions or other
 * shell syntax. This is transport parsing only: flag names/defaults remain
 * owned by the repository CLI.
 */
export function tokenizeArgs(args: unknown): string[] {
  const input = normalizeArgs(args);
  const tokens: string[] = [];
  let current = "";
  let quote: "'" | '"' | null = null;
  let escaped = false;

  const push = (): void => {
    if (current.length > 0) tokens.push(current);
    current = "";
  };

  for (const ch of input) {
    if (escaped) {
      current += ch;
      escaped = false;
      continue;
    }
    if (ch === "\\" && quote !== "'") {
      escaped = true;
      continue;
    }
    if (quote) {
      if (ch === quote) quote = null;
      else current += ch;
      continue;
    }
    if (ch === "'" || ch === '"') {
      quote = ch;
      continue;
    }
    if (/\s/.test(ch)) {
      push();
      continue;
    }
    current += ch;
  }

  if (escaped) current += "\\";
  push();
  return tokens;
}
