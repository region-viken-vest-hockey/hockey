import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";

export interface RepoProcessResult {
  code: number | null;
  stdout: string;
  stderr: string;
  cancelled: boolean;
}

export interface RepoProcessOptions {
  timeoutMs?: number;
  stdinText?: string;
  onStdoutLine?: (line: string) => void;
  onStderrLine?: (line: string) => void;
}

export function pythonExe(cwd: string): string {
  const venvPython = resolve(cwd, "venv", "bin", "python3");
  return existsSync(venvPython) ? venvPython : "python3";
}

function streamLines(
  chunk: Buffer,
  state: { buffer: string },
  callback?: (line: string) => void,
): void {
  if (!callback) return;
  state.buffer += chunk.toString();
  const lines = state.buffer.split(/\r?\n/);
  state.buffer = lines.pop() ?? "";
  for (const line of lines) callback(line);
}

export async function runPythonModule(
  ctx: ExtensionContext,
  moduleName: string,
  args: string[],
  options: RepoProcessOptions = {},
): Promise<RepoProcessResult> {
  const stdoutState = { buffer: "" };
  const stderrState = { buffer: "" };
  const stdoutChunks: Buffer[] = [];
  const stderrChunks: Buffer[] = [];
  const signal = ctx.signal;

  return new Promise<RepoProcessResult>((resolvePromise, rejectPromise) => {
    const child = spawn(
      pythonExe(ctx.cwd),
      ["-m", moduleName, ...args],
      {
        cwd: ctx.cwd,
        stdio: ["pipe", "pipe", "pipe"],
        env: { ...process.env, RVV_HARNESS: process.env.RVV_HARNESS || "pi" },
      },
    );

    let cancelled = false;
    let settled = false;

    const finish = (result: RepoProcessResult): void => {
      if (settled) return;
      settled = true;
      if (timeout) clearTimeout(timeout);
      signal?.removeEventListener("abort", onAbort);
      resolvePromise(result);
    };

    const onAbort = (): void => {
      cancelled = true;
      child.kill("SIGTERM");
    };
    if (signal) {
      if (signal.aborted) onAbort();
      else signal.addEventListener("abort", onAbort, { once: true });
    }

    const timeout = options.timeoutMs && options.timeoutMs > 0
      ? setTimeout(() => {
          child.kill("SIGTERM");
        }, options.timeoutMs)
      : undefined;

    child.stdout?.on("data", (chunk: Buffer) => {
      stdoutChunks.push(chunk);
      streamLines(chunk, stdoutState, options.onStdoutLine);
    });
    child.stderr?.on("data", (chunk: Buffer) => {
      stderrChunks.push(chunk);
      streamLines(chunk, stderrState, options.onStderrLine);
    });
    child.on("error", (err) => {
      if (settled) return;
      settled = true;
      if (timeout) clearTimeout(timeout);
      signal?.removeEventListener("abort", onAbort);
      rejectPromise(err);
    });
    child.on("close", (code) => {
      if (stdoutState.buffer && options.onStdoutLine) options.onStdoutLine(stdoutState.buffer);
      if (stderrState.buffer && options.onStderrLine) options.onStderrLine(stderrState.buffer);
      finish({
        code,
        stdout: Buffer.concat(stdoutChunks).toString(),
        stderr: Buffer.concat(stderrChunks).toString(),
        cancelled,
      });
    });

    if (options.stdinText !== undefined) child.stdin?.write(options.stdinText);
    child.stdin?.end();
  });
}

export async function runRepoCli(
  ctx: ExtensionContext,
  args: string[],
  options: RepoProcessOptions = {},
): Promise<RepoProcessResult> {
  return runPythonModule(ctx, "tournament_scheduler.cli.rvv_cli", args, options);
}

export function formatProcessFailure(result: RepoProcessResult): string {
  return [result.stdout.trim(), result.stderr.trim(), `exit code: ${result.code ?? "unknown"}`]
    .filter(Boolean)
    .join("\n");
}
