---
name: prototype
description: Time-boxed exploration to answer a concrete design question. Use when "how would this work" or "can we" comes up during design. Produces a decision artifact, not production code. Auto-cleans up after decision.
---

# Prototype: Time-Boxed Design Exploration

A prototype answers **one concrete design question** in a fixed time box. It produces a **decision artifact** (not production code) and then **auto-cleans up**. The goal is to reduce uncertainty before committing to a design.

## When to Use

- "How would this work?" during design/grilling
- "Can we integrate X with Y?" at a seam boundary
- "What's the performance of approach A vs B?"
- "Does this library support our use case?"

**Not for:** Building features, fixing bugs, writing production code, or anything that should go through TDD.

## Protocol

### 1. Frame the Question
Write the **exact question** the prototype answers. One question per prototype.

```markdown
# Prototype: <Question>

**Question:** Can we use `zod` schemas as the single source of truth for both RVV canonical validation and TypeScript types?

**Success Criteria:** 
- [ ] Zod schema validates canonical tournament placement
- [ ] TypeScript types inferred from schema match manual types
- [ ] No `any` or type assertions needed
- [ ] Validation runs in < 5ms per tournament
```

### 2. Set Time Box
Default: **30 minutes**. Maximum: **2 hours**. Set a timer.

### 3. Minimal Scaffold Only
- No tests (the prototype IS the test)
- No linting, formatting, or CI
- No commit to main
- Work in a throwaway directory or branch: `prototype/<question-slug>`

### 4. Decision Artifact
At the end of the time box, produce **one markdown file** with:

```markdown
# Decision: <Question>

## Answer
<Yes/No/Partial with conditions>

## Evidence
- What worked
- What didn't
- Performance numbers
- Type inference results
- Integration friction points

## Recommendation
<Adopt / Reject / Defer with conditions>

## Cleanup
- [ ] Prototype branch deleted
- [ ] No prototype code in main
- [ ] Decision recorded in ADR if adopted
```

### 5. Auto-Cleanup
**Mandatory.** The prototype branch is deleted. The decision artifact is the only output. If adopted, the real implementation goes through TDD at the confirmed seam.

## RVV-Specific Prototype Patterns

### Seam Integration Prototype
```markdown
# Prototype: Can ReconciliationEngine use BookingRepository as a seam?

**Question:** Does the BookingRepository interface expose everything ReconciliationEngine needs without leaking internals?

**Scaffold:**
- Implement minimal BookingRepository interface
- Wire ReconciliationEngine to it
- Run reconciliation on fixture data
- Check: no internal BookingRepository calls from ReconciliationEngine
```

### Library Evaluation Prototype
```markdown
# Prototype: Does date-fns-tz handle RVV arena timezone rules?

**Question:** Can date-fns-tz correctly convert "2024-01-15 18:00 Europe/Stockholm" to UTC for all Swedish DST transitions?

**Scaffold:**
- Test all 2024-2025 DST transitions for Stockholm
- Compare with known-good IANA data
- Check bundle size impact
```

### Algorithm Feasibility Prototype
```markdown
# Prototype: Can Stage 3 optimization run incrementally?

**Question:** Can we resume Stage 3 from a checkpoint without recomputing Stage 1-2?

**Scaffold:**
- Serialize Stage 3 state
- Deserialize and continue
- Verify identical output to full run
- Measure checkpoint size / resume time
```

## Anti-Patterns

### The "Prototype That Ships"
```markdown
# BAD: "Let's just clean this up and merge it"
```
If it's good enough to ship, it's not a prototype — it's a TDD vertical slice.

### The "Open-Ended Exploration"
```markdown
# BAD: "Explore the calendar API"
```
```markdown
# GOOD: "Can the Google Calendar API return events for club HC-123 with recurrence expanded, in < 2s, paginated?"
```

### The "No Decision Artifact"
```markdown
# BAD: Prototype runs, learns something, but no decision recorded
```
Every prototype ends with a decision artifact. No exceptions.

## Integration with Grilling

During grilling, when a frontier question needs a fact that only code can answer:
1. Grilling agent frames the prototype question
2. Grilling agent dispatches prototype sub-agent (30 min box)
3. Prototype returns decision artifact
4. Grilling integrates answer into frontier

## Integration with Codebase Design

Prototypes **inform** design maps. They don't create seams — they answer "will this seam work?" The design map is updated after the prototype decision.

## Integration with TDD

If prototype answer is "Adopt":
1. Update design map with confirmed seam
2. Write first TDD test at that seam
3. Implement via red-green loop
4. Prototype code discarded