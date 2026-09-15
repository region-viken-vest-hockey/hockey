// Small shared transport/UI types for the RVV Pi adapter.

export const LOG_LEVELS = ["info", "verbose"] as const;

/** Progress emitted by the thin Pi adapter while repository capabilities run. */
export interface ProgressEvent {
  stage: "config" | "scraping" | "scraping-extended" | "planning" | "export" | "audit" | "done";
  status: "start" | "ok" | "skip" | "error";
  message: string;
  error?: string;
  blockedCount?: number;
  blockedName?: string;
  eventCount?: number;
}
