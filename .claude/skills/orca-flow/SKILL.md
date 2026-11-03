---
name: orca-flow
description: Deliver one GitHub issue end to end with an independent ChatGPT review through the Orca browser. Thin alias for the shared skill; use when the user asks to run the orca flow or take an issue through review.
---

# orca-flow

The procedure is harness-neutral and lives in [`.agents/skills/orca-flow/SKILL.md`](../../../.agents/skills/orca-flow/SKILL.md).
Read it and follow it; this file intentionally holds no policy so the two cannot drift. Implementation and verification
follow `.agents/commands/implement-issue.md`; the flow stops at a merge-ready PR unless the operator explicitly authorizes
merging.
