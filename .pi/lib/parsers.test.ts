import { describe, test, expect } from "vitest";
import { parseRunArgs, parseStatusArgs, parseLogsArgs, parseCalendarsArgs } from "./parsers";

// Test that all parser functions handle undefined, null, empty string, and arrays correctly
describe("Parser Functions", () => {
  test("parseRunArgs handles undefined, null, empty string, and arrays", () => {
    expect(parseRunArgs(undefined)).toEqual({});
    expect(parseRunArgs(null)).toEqual({});
    expect(parseRunArgs("")).toEqual({});
    expect(parseRunArgs([])).toEqual({});
  });

  test("parseStatusArgs handles undefined, null, empty string, and arrays", () => {
    expect(parseStatusArgs(undefined)).toEqual({});
    expect(parseStatusArgs(null)).toEqual({});
    expect(parseStatusArgs("")).toEqual({});
    expect(parseStatusArgs([])).toEqual({});
  });

  test("parseLogsArgs handles undefined, null, empty string, and arrays", () => {
    expect(parseLogsArgs(undefined)).toEqual({ subcommand: "list" });
    expect(parseLogsArgs(null)).toEqual({ subcommand: "list" });
    expect(parseLogsArgs("")).toEqual({ subcommand: "list" });
    expect(parseLogsArgs([])).toEqual({ subcommand: "list" });
  });

  test("parseCalendarsArgs handles undefined, null, empty string, and arrays", () => {
    expect(parseCalendarsArgs(undefined)).toEqual({});
    expect(parseCalendarsArgs(null)).toEqual({});
    expect(parseCalendarsArgs("")).toEqual({});
    expect(parseCalendarsArgs([])).toEqual({});
  });

  // Test that string input still works as before (regression test)
  test("parseStatusArgs with string input works as before", () => {
    const result = parseStatusArgs("--work-dir .pipeline");
    expect(result).toEqual({ work_dir: ".pipeline" });
  });

  test("parseLogsArgs with string input works as before", () => {
    const result = parseLogsArgs("show latest --count 5");
    expect(result).toEqual({
      subcommand: "show",
      run_id: "latest",
      count: 5,
    });
  });

  // Local models may pass quoted strings or pre-tokenized arrays
  test("parsers handle quoted strings and array-of-tokens input from local models", () => {
    expect(parseCalendarsArgs('"--refresh"')).toEqual({ refresh: true });
    expect(parseStatusArgs(["--work-dir", ".pipeline"])).toEqual({ work_dir: ".pipeline" });
    expect(parseRunArgs(["--log-level", "verbose"])).toEqual({ log_level: "verbose" });
  });

  test("parseRunArgs handles manual BookUp login flags", () => {
    expect(parseRunArgs("--manual-bookup-login --manual-bookup-login-timeout 600")).toEqual({
      manual_bookup_login: true,
      manual_bookup_login_timeout: 600,
    });
  });
});
