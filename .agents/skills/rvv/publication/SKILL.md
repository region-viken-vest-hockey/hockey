---
name: rvv-publication
description: Shared harness-neutral policy for RVV export audit, publication, republish and rollback. Load only when publication or exact public projection is in scope.
---

# RVV publication

Read `AGENTS.md` and `../SKILL.md` first. For an existing published/sealed season also load `../production/SKILL.md`. This file is shared by every harness.

Planning, canonical mutation, export, semantic audit and publication are separate authorities. Publication must use the exact current canonical revision and a fresh matching export. Never edit generated artifacts/audit JSON to satisfy a gate and never infer publication permission from booking evidence, approval, a chat instruction, or a historical export.

Resolve the authoritative published baseline/history through repository commands, not `latest/` or an arbitrary export directory. Preserve immutable publication evidence and exact delta identity.

## Routing

- Export/delivery: `../../../commands/rvv-miniputt/season-delivery.md`
- Semantic audit/publication scope: `../../../commands/rvv-miniputt/audit-publication.md`
- First/current publication: `../../../commands/rvv-miniputt/publish.md`
- Republish sealed season: `../../../commands/rvv-miniputt/republish.md`

The repository-owned deterministic preflight decides publication eligibility for the exact projection. Full-season planning debt and publication-scope eligibility are related evidence but not interchangeable gates. A failed/incomplete semantic audit remains evidence; do not rewrite it merely to enable publication.

Publication/rollback is an explicit operator-authorized external action. Report the resulting run/revision and verify deployment through the supported procedure.
