# Independent season planning benchmark

This branch supports an **independent planning track** that runs in parallel with canonical planner development on `main`.

The experiment is useful only if the two tracks keep their responsibilities separate:

- `main` improves canonical planner/verifier/repair logic.
- this benchmark branch owns one frozen factual season snapshot plus benchmark infrastructure.
- candidate branches (recommended: `benchmark-plan/<name>`) contain independently authored plan candidates and evaluation output.

See GitHub issue #370 for the benchmark/evaluation tooling work.

## DO

- Treat `.benchmark/snapshot/` as factual provenance for this benchmark season.
- Use the normalized `planning_problem.json` as the authoritative planning input once #370 produces it.
- Use `planning_problem_summary.json` only as a bounded orientation/index, not as another rule source.
- Edit the independent `candidate.json` on a candidate branch.
- Use repository verification, scoring and repair-option output as deterministic feedback.
- Fix hard violations before spending effort on marginal soft-quality optimization.
- Inspect raw Stage 2 events only when investigating a disputed normalized availability fact.
- Record the planning-problem, candidate and evaluator fingerprints for every meaningful evaluation.
- Create a new benchmark identity if registrations/calendar evidence are intentionally refreshed.

## DO NOT

- Do not re-scrape calendars during normal candidate iteration.
- Do not silently regenerate the frozen benchmark facts because `main` changed.
- Do not use `SeasonPlanner` / canonical Stage 3 output as the working independent candidate.
- Do not copy the canonical plan and call small modifications an independent result.
- Do not reproduce hard hockey rules in prompts or one-off scripts when the repository can verify them.
- Do not waive or reinterpret a hard verifier failure locally.
- Do not modify raw evidence to make a candidate pass.
- Do not patch planner/verifier/business-rule code on a candidate branch.
- Do not add club-specific benchmark heuristics.

If the benchmark exposes a likely rule/verifier/repair bug, create/update an issue for the `main` logic track instead of fixing it inside the independent candidate branch.

## Branch roles

```text
main
  canonical product + planning logic

benchmark-independent-season-20260917
  frozen facts + benchmark infrastructure

benchmark-plan/<name>
  candidate.json + optional concise planning notes + generated evaluation
```

Until #370 completes setup, benchmark infrastructure may still be added here. The frozen factual snapshot itself must not be silently rewritten.

## Normal candidate loop

Once #370 is implemented, use this loop:

```text
read bounded problem summary
        ↓
inspect relevant planning_problem facts
        ↓
edit candidate.json
        ↓
push candidate branch
        ↓
repository verify + score + repair evidence
        ↓
read compact evaluation
        ↓
make the next planning decision
        ↺
```

For a localized hard defect, prefer repository-generated local/coupled repair options before rebuilding the whole season.

## Independence rule

Construct the first serious candidate without using the canonical planner's selected schedule as a template.

After an independently authored hard-valid candidate exists, compare it to the canonical planner output produced from the same frozen factual problem. Exact schedule identity is not required or desired.

## What counts as ready for comparison

At minimum:

- zero unwaived hard verifier violations;
- problem/candidate fingerprints match the intended benchmark;
- unresolved/manual work is explicit and supported by repository evidence;
- quality metrics are available across the season;
- pinned benchmark evaluation is reproducible.

Then compare independent vs canonical output on validity, participation, hosting responsibility/coverage, manual work, opponent diversity/repetition, same-club concentration, turnaround/distribution, travel/start-time metrics where available, and the other canonical score dimensions.

## Frozen facts

One benchmark identity corresponds to one factual season snapshot:

```text
input fingerprint
+ config fingerprint
+ Stage 2 evidence fingerprint
+ planning_problem fingerprint
= benchmark facts identity
```

The candidate may evolve repeatedly. The factual identity may not.

A new workbook or fresh calendar snapshot creates a new benchmark identity rather than silently replacing this one.
