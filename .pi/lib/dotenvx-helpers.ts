import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const BOOKUP_ENV_KEYS = ["BOOKUP_EMAIL", "BOOKUP_PASSWORD"] as const;

function dotenvxBinary(cwd: string): string | null {
  const local = resolve(cwd, "node_modules", ".bin", "dotenvx");
  if (existsSync(local)) return local;
  return null;
}

function dotenvxEnvFile(cwd: string): string | null {
  const configured = process.env.DOTENVX_ENV_FILE;
  const candidate = configured ? resolve(cwd, configured) : resolve(cwd, ".env.bookup");
  return existsSync(candidate) ? candidate : null;
}

function parseLastJsonObject(stdout: string): Record<string, string> | null {
  const lines = stdout.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  for (let i = lines.length - 1; i >= 0; i--) {
    try {
      return JSON.parse(lines[i]) as Record<string, string>;
    } catch {
      // dotenvx may print status lines; keep looking for the JSON payload.
    }
  }
  return null;
}

/**
 * Load BookUp credentials from the repository dotenvx file into process.env.
 *
 * Values are never returned or logged; callers only get the key names that were
 * populated. Existing environment variables win, so this is safe to call before
 * every run/scrape command.
 */
export async function loadBookupEnvFromDotenvx(cwd: string): Promise<string[]> {
  const missing = BOOKUP_ENV_KEYS.filter((key) => !process.env[key]);
  if (missing.length === 0) return [];

  const dotenvx = dotenvxBinary(cwd);
  const envFile = dotenvxEnvFile(cwd);
  if (!dotenvx || !envFile) return [];

  const nodeScript = [
    "const keys=['BOOKUP_EMAIL','BOOKUP_PASSWORD'];",
    "const out={};",
    "for (const key of keys) out[key]=process.env[key]||'';",
    "console.log(JSON.stringify(out));",
  ].join("");

  try {
    const { stdout } = await execFileAsync(
      dotenvx,
      ["run", "-f", envFile, "--", process.execPath, "-e", nodeScript],
      { cwd, timeout: 15_000, maxBuffer: 1024 * 1024 },
    );
    const parsed = parseLastJsonObject(stdout);
    if (!parsed) return [];

    const loaded: string[] = [];
    for (const key of missing) {
      const value = parsed[key];
      if (value) {
        process.env[key] = value;
        loaded.push(key);
      }
    }
    return loaded;
  } catch {
    return [];
  }
}
