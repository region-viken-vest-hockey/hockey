# Schedule quality and distribution (lazy-loaded)

Load for a distribution review, candidate comparison or any scheduling mutation with material downstream consequences. This is an analytical procedure, not an independent verifier or invented scoring policy. Use repository-owned findings, quality vectors, dry-run consequences, diff and canonical verification.

Compare current and proposed state for every affected team and relevant season/age group: participation against targets; before/after-Christmas coverage; gaps under 7/14 days and clustering; opponent repetition/diversity; host obligations and balance; travel; booking evidence and exact-interval conflicts; operational/manual/host-confirmation dependencies; and changed-tournament count. Distinguish hard violations, strong-goal deviations, soft findings and operator-accepted exceptions. Preserve approved/locked commitments and active constraints.

Compare multiple meaningfully distinct feasible candidates, including alternatives on both sides of Christmas where relevant. Prefer non-dominated, minimally disruptive acceptable candidates; a first hard-valid or nearest date is not automatically preferable. Do not present a candidate with a new material regression as acceptable without the repository's explicit authorized opt-in. Bounded search is not proof of global optimality or infeasibility.

Produce a before/after impact report with source/revision, affected teams, objective and consequence deltas, remaining findings, evidence gaps and rationale. For an investigation-only request, do not mutate. After an authorized mutation rerun canonical verification and fresh findings; audit the derived export when required.

## Participant fairness audit

When the operator asks whether the season is fair, uneven, imbalanced or otherwise disadvantaged from the player/team perspective, treat that as a stable participant-fairness audit.

Hosting responsibility and hosting balance are out of scope unless the operator explicitly asks for them. Do not let already-settled hosting differences dominate or distort the participant-facing fairness result.

### Fairness population: booked reality only

By default, participant-fairness metrics must be computed only from tournaments that the repository classifies as actually booked/confirmed through accepted source-backed booking evidence for the effective interval. Use repository-owned booking-status/evidence projections to establish that population.

Do **not** count merely planned, proposed, unplaced, awaiting-confirmation, default-duration, or otherwise unconfirmed tournaments in fairness totals, before/after-Christmas balance, season span, gaps, clustering, opponent diversity, travel, or game-opportunity metrics. A canonical placement is not evidence that the tournament will actually happen.

Unbooked/planned tournaments may be reported separately as planning coverage or future potential, but they must not make the real season look fairer or less fair. If the operator explicitly asks for a planning-view fairness audit, label that view clearly and keep it separate from the default booked-reality audit.

If booking evidence is too incomplete to support a meaningful fairness conclusion for an age group or club, report the audit as incomplete for that scope rather than filling the gaps with planned tournaments.

Evaluate fairness against this booked-reality population, using the correct comparison unit:

- **Regional allocation fairness (primary):** for clubs with multiple teams in the same age group, compare clubs by total participant opportunity normalized by the number of registered teams in that club/age group. A sibling-team spread by itself is not automatically regional unfairness.
- **Internal sibling-team distribution (secondary diagnostic):** report how unevenly a multi-team club's opportunities are distributed among its own team labels, but classify this separately from regional allocation fairness unless repository evidence shows those labels are stable, non-interchangeable participant groups or an individual team suffers an extreme deprivation.
- **Single-team/small-club experience (first-class):** when a club has only one team in an age group, that team's season shape directly represents the club's participant experience and must not be diluted by multi-team normalization.
- **Extreme individual-team experience:** regardless of club size, flag cases such as very low total participation, no meaningful activity in one half of the season, excessive inactive gaps, or a season that effectively starts or ends far earlier than comparable peers.

Within the booked-reality population, use the accepted/calendar-confirmed effective date/start/end and participants as truth where they supersede earlier planning assumptions. Do not treat superseded proposal/default dates, times, durations or placements as current truth.

For multi-team clubs, calculate and compare a normalized club/age-group opportunity measure where data permits:

`normalized club opportunity = total team-tournament participations for the club/age group / number of registered teams in that club/age group`

Use the same principle for actual game/match opportunities when tournament formats differ.

At minimum quantify:
- total tournaments per team relative to same-age peers and applicable targets;
- actual game/match opportunities per team when tournament formats differ, so equal tournament counts are not mistaken for equal participation;
- distribution before versus after Christmas, both as raw counts and as imbalance/share of the team's or normalized club-age total; treat strongly front-loaded or back-loaded seasons as a major fairness concern, especially for single-team/small clubs (for example 5 tournaments before Christmas and 2 after);
- season span from first to last tournament;
- unusually late first tournament or unusually early last tournament;
- long inactive gaps;
- unusually compressed periods or clusters, including consecutive-day/weekend load where relevant;
- cadence/frequency through the active season;
- opponent repetition and opponent diversity within the same age group;
- participant travel burden and repeated long-away trips, independently of who has hosting responsibility;
- material participation deviations from comparable teams in the same age group;
- schedule stability/churn when repository history supports it: repeated moves, cancellations or late changes that disproportionately affect the same team.

Where authoritative data exists, also inspect time-of-day/weekend distribution and major school-holiday exposure if those differ materially between comparable teams. Do not invent fairness findings from unavailable or unreliable data, and do not infer competitive strength/skill balance unless the repository has an explicit authoritative classification for it.

Also surface other material participant-facing inequities evidenced by repository-owned findings or projections.

Rank the largest current inequities by measurable deviation and participant impact, using this priority:

1. severe half-season deprivation or imbalance, especially for single-team/small clubs;
2. regional club/age-group under-allocation after normalizing for registered team count;
3. extreme individual-team deprivation (very low participation, effectively missing half a season, excessive gaps, very late start or early finish);
4. internal sibling-team distribution imbalance within an otherwise fairly allocated multi-team club;
5. other cadence, diversity, travel or churn inequities.

Do not let a large sibling-label spread inside a multi-team club outrank a smaller but more consequential regional or half-season imbalance merely because the raw team-count range is numerically larger.

For each significant inequity report the affected team(s)/club(s)/age group, whether it is **regional allocation**, **half-season balance**, **internal sibling distribution**, or **individual-team experience**, the relevant measured value, the same-age comparable baseline/range, the size of the deviation, the booked-evidence coverage behind the comparison, and whether it appears repairable without violating hard constraints, active accepted requests/protections or accepted calendar-confirmed facts.

Do not compare unlike age groups as if they had the same expected season shape unless repository policy explicitly defines a shared target.

A fairness audit is read-only by default. If the operator asks only to investigate, compare or identify unfairness, do not mutate the season. If repair is later requested, use the normal verified mutation flow and compare before/after participant-fairness consequences rather than optimizing one metric in isolation.

