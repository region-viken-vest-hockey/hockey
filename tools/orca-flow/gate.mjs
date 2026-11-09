// The one canonical local gate for this repository. The worker task text and the
// independent final-head verification both read it, so they cannot name different
// commands. Change it here, never in a caller.
export const GATE = Object.freeze({ command: "make", args: Object.freeze(["check"]), label: "make check" });
