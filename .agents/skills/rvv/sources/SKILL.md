---
name: rvv-sources
description: Shared harness-neutral policy for RVV calendar/source scraping, freshness, recovery and source evidence. Load when acquiring or troubleshooting source facts.
---

# RVV source acquisition and recovery

Read `AGENTS.md` and `../SKILL.md` first. This file is shared by every harness.

Controlled source configuration and repository source-capability code own source identity, URL, trust and supported acquisition behavior. Harnesses may assist with browser/navigation extraction only when the repository requests recovery; recovered evidence must return through repository-owned validation/merge paths.

Do not implement source-specific scraper policy in a harness, infer free ice from a failed/partial feed, or treat a healthy scrape status as proof that every relevant event was extracted correctly.

## Routing

- Deterministic one-source troubleshooting: `../../../commands/rvv-miniputt/scrape.md`
- Browser/LLM-assisted recovery when explicitly requested: `../../../commands/rvv-miniputt/scrape-llm.md`
- Calendar operations/refresh: `../../../commands/rvv-miniputt/calendars.md`
- Booking comparison/reconciliation: load `../production/SKILL.md` and `../../../commands/rvv-miniputt/booking-reconciliation.md`

For disagreement investigations compare the live source, raw extraction, normalized event, persisted association/evidence and effective canonical projection with source fingerprint/freshness. Fix extraction defects at their canonical owner before drawing operational conclusions.
