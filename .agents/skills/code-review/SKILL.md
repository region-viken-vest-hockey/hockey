---
name: code-review
description: "Two-axis code review: correctness (does it work?) and design (is it maintainable?). Use for PR reviews, pre-merge checks, or reviewing agent-produced code. Produces structured findings with file:line references."
---

# Code Review: Two-Axis Review

Every review operates on two independent axes. They do not trade off.

## Axis 1: Correctness — Does It Work?

**Behavioral contract**: Does the code do what the spec/contract says?
- Passes all tests (existing + new)
- Handles error cases in the contract
- No data loss, corruption, or invariant violations
- Idempotency where required
- Concurrency safety where required

**Verification**: Run the test suite. Check contract tests at seams. Verify edge cases.

## Axis 2: Design — Is It Maintainable?

**Structural contract**: Will the next change be safe and obvious?
- Seams confirmed and tested at seams (not internals)
- One owner per contract (no duplicated policy)
- No circular dependencies
- Glossary terms used exactly
- No hidden coupling (shared mutable state, temporal coupling)
- Testability at confirmed seams
- No implementation leakage into interfaces

**Verification**: Static analysis, architecture check, design map alignment.

## Review Output Format

Every finding uses this format:

```markdown
### [CORRECTNESS|DESIGN] <Severity> — <file.ts:line>

**Finding:** <One sentence what is wrong>

**Contract Violated:** <Which seam contract, invariant, or design rule>

**Evidence:** <Code snippet or test failure>

**Fix:** <Exact change needed — the owner module, not a compensating caller>

**Confidence:** <High/Medium/Low>
```

### Severity Levels
- **Critical**: Data loss, security, hard invariant violation, breaks seam contract
- **High**: Wrong behavior at seam, missing error handling, design conflict
- **Medium**: Design smell, test coupling, glossary deviation, missing verification
- **Low**: Style, naming, documentation, minor test improvement

## Review Procedure

### 1. Entry Gate
- [ ] PR/branch has passing CI (`mise run check`)
- [ ] Author has run `mise run test` locally
- [ ] Design map exists for new/changed seams (link in PR)
- [ ] Glossary terms used (check `GLOSSARY.md`)

### 2. Correctness Pass
Review only for behavioral correctness. Ignore design preferences.

```bash
# Run tests at changed seams
mise run test -- --testPathPattern="approval|reconciliation|booking"

# Check contract tests exist for new seams
grep -r "test.*approveTournament" tests/
```

**Output**: Findings tagged `[CORRECTNESS]`

### 3. Design Pass
Review only for structural maintainability. Ignore behavioral preferences.

```bash
# Check for circular deps
npx madge --circular src/

# Check seam testability
grep -r "jest.mock.*ApprovalService" tests/  # Should not mock own modules

# Verify glossary compliance
grep -r "booking.*lock" src/ | grep -v "placement-lock"  # Should use canonical term
```

**Output**: Findings tagged `[DESIGN]`

### 4. Synthesis
- Group findings by owner module
- Critical/High correctness findings block merge
- High design findings block if they create future correctness risk
- Medium/Low: author discretion, but must acknowledge

### 5. Exit Gate
- [ ] All Critical/High correctness findings resolved
- [ ] All High design findings resolved or explicitly accepted with rationale
- [ ] Design map updated if seams changed
- [ ] Tests pass at `mise run check`

## RVV-Specific Review Rules

### Canonical Mutation Reviews
For any change to `ApprovalService`, `ReconciliationEngine`, `BookingRepository`, `SeasonPlanner`:

| Check | Tool |
|-------|------|
| Revision increments correctly | `grep -n "revision" src/approval/` |
| Fingerprint verification present | `grep -n "fingerprint" src/approval/` |
| Idempotency test exists | `grep -n "idempotent" tests/approval.test.ts` |
| No waiver bypass | `grep -n "waive" src/approval/` — must be explicit |
| Hard validity re-verified | `grep -n "verify" src/approval/` |

### Seam Boundary Reviews
```bash
# Check no internal mocks
grep -rn "jest.mock.*\(CalendarSource\|BookingRepository\|ReconciliationEngine\)" tests/

# Check interface imports (not implementation)
grep -rn "from.*src/.*/impl" tests/  # Should be empty

# Verify seam tests exist
for seam in approveTournament fetchCalendar assertBooking reconcile approve publish; do
  grep -rn "test.*$seam" tests/ || echo "MISSING: $seam"
done
```

### Glossary Compliance
```bash
# Check for avoided terms
for term in "booking lock" "schedule lock" "confirmed tournament" "draft season"; do
  grep -ri "$term" src/ docs/ && echo "AVOIDED TERM: $term"
done
```

## Two-Axis Independence

**Correctness does not excuse design debt.** A correct but unmaintainable module will produce correctness bugs on the next change.

**Design purity does not excuse incorrectness.** A beautiful architecture that violates the canonical mutation contract is broken.

Review both axes. Report both axes. Fix both axes.

## Integration with TDD

- TDD ensures correctness at seams (Axis 1)
- Code review ensures design at seams (Axis 2)
- They are complementary, not redundant

## Integration with Grilling

When a review finds a design conflict (two modules claiming same seam, unclear ownership):
1. Stop review
2. Run grilling on the design tree
3. Resolve ownership
4. Resume review with updated design map

## Anti-Patterns

### The "Nitpick Review"
```markdown
# BAD: 20 comments on variable names, 0 on seam contracts
```
Focus on the two axes. Style is Low severity.

### The "Compensating Fix Review"
```markdown
# BAD: "Add a check in the caller to handle the module's bug"
```
Fix the **owner module**, not the caller. The finding must name the owner.

### The "Design Preference Review"
```markdown
# BAD: "I would have used a factory pattern here"
```
Unless it violates a design rule (circular dep, seam coupling, ownership), it's not a finding.

### The "Missing Context Review"
```markdown
# BAD: Reviews without reading the design map or glossary
```
Entry gate requires both.