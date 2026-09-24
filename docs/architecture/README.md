# Maintained architecture diagrams and Archify regeneration

The [planning/decision/lifecycle diagrams](planning-model.md) are **manually maintained semantic architecture**. The [generated rule catalog](rule-catalog.md) is generated exclusively by `scripts/render-rule-catalog.py` from `tournament_scheduler/rule_catalog.py`. The [system overview](../system-architecture.md) links the semantic view; [application architecture](../application-architecture.md) remains the owner of import/dependency rules. These documents must not contradict one another.

The repository currently has no checked-in Archify configuration or generated architecture drawing. A locally installed Claude/Archify skill may create supplementary structure/import maps, but those outputs are not a second authority. If using Archify, give it the following **repository-specific brief**:

1. Inspect the active system/application docs, this semantic view, the rule catalog/code, and relevant [ADRs](../adr/), **not just imports**.
2. Trace the independent `planning_contract.verify_candidate` and `final_verification`, `SeasonPlanner`, fixed-skeleton CP-SAT and local optimizer, `quality_objectives`, shared `pareto`, `application.pareto_convergence` and `application.convergence_refinement`.
3. Trace `Stage3Session` / controller and the Stage 4 review/audit boundary. Distinguish initial candidate generation from re-verification of retained candidates.
4. Trace `CanonicalSeasonService` / focused lifecycle / store, baseline and participation acceptance, approvals, bookings and locks, published-sealed reconciliation, canonical export and Pages publication.
5. Preserve the five catalog classifications without producing a duplicate hand-maintained rule table. The catalog is metadata, not an execution engine.
6. Keep this page's three focused Mermaid diagrams source-controlled and reviewable. Never replace them with an imports-only graph or assert global Pareto optimality from bounded search.
7. When code semantics differ from the diagrams, correct the diagrams **in the same PR** and update tests/owner links. Keep structural generated snapshots outside maintained docs unless reviewed and adopted as a permanent source.

Local validation: `scripts/check architecture-docs` checks required sections/owner links/classification names and emits diagram sources with `--extract-mermaid <directory>`. CI also renders every Mermaid block with Mermaid CLI. The diagram blocks are authored here, not generated; the rule-catalog page is generated, not manually edited.
