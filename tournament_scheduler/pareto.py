"""Canonical Pareto/dominance arithmetic shared by multi-objective surfaces.

Both Stage 3's multi-objective search (``stage3_optimizer.optimize_candidate_pareto``)
and the promoted-season maintenance loop compare several independently verified
candidates on the same kind of objective vector and ask which candidates are
*not worse* than every other. That arithmetic is not a scheduling rule and must
not be re-implemented, with subtly different tie-breaking, per surface, so it
lives here.

Every vector is a mapping of objective name to a numeric value and is oriented
"lower is better" by the caller. This module owns only the dominance relation
and a deterministic bounded down-select of a non-dominated set; it never
computes a vector and never decides which objectives matter.
"""

from __future__ import annotations

from typing import List, Mapping, Sequence

DEFAULT_TOLERANCE = 1e-9

ObjectiveVector = Mapping[str, float]


def dominates(a: ObjectiveVector, b: ObjectiveVector, tol: float = DEFAULT_TOLERANCE) -> bool:
    """True if *a* Pareto-dominates *b*.

    Weakly better (or equal, within *tol*) in every dimension of *a*, and
    strictly better in at least one. The caller must pass vectors over the same
    dimension set.
    """
    at_least_as_good = all(a[key] <= b[key] + tol for key in a)
    strictly_better = any(a[key] < b[key] - tol for key in a)
    return at_least_as_good and strictly_better


def vectors_equal(a: ObjectiveVector, b: ObjectiveVector, tol: float = DEFAULT_TOLERANCE) -> bool:
    """True if every dimension of *a* is within *tol* of the same dimension of *b*."""
    return all(abs(a[key] - b[key]) <= tol for key in a)


def non_dominated_indices(
    vectors: Sequence[ObjectiveVector], tol: float = DEFAULT_TOLERANCE
) -> List[int]:
    """Indices of the non-dominated vectors, first occurrence wins on ties.

    A vector is non-dominated when no other vector dominates it. Two equal
    vectors do not dominate each other, so an exact duplicate is dropped in
    favour of its first occurrence to keep the front a set of distinct
    trade-offs rather than a list of copies.
    """
    front: List[int] = []
    for index, vector in enumerate(vectors):
        if any(vectors_equal(vectors[kept], vector, tol) for kept in front):
            continue
        if any(
            dominates(vectors[other], vector, tol)
            for other in range(len(vectors))
            if other != index
        ):
            continue
        front.append(index)
    return front


def representative_indices(
    vectors: Sequence[ObjectiveVector],
    max_size: int,
    tol: float = DEFAULT_TOLERANCE,
) -> List[int]:
    """Bounded, deterministic down-select of a non-dominated set (issue #264).

    Prefers each objective's own extreme first (so every trade-off is
    represented), then fills any remaining budget with the vectors whose total
    objective mass is smallest. Returns input indices in ascending order; a
    no-op when the set already fits.
    """
    if max_size <= 0:
        return []
    if len(vectors) <= max_size or not vectors:
        return list(range(len(vectors)))

    dimensions = list(vectors[0])
    chosen: List[int] = []
    chosen_set: set = set()
    for dimension in dimensions:
        best = min(range(len(vectors)), key=lambda i: vectors[i][dimension])
        if best not in chosen_set:
            chosen_set.add(best)
            chosen.append(best)
        if len(chosen) >= max_size:
            break

    if len(chosen) < max_size:
        remaining = [i for i in range(len(vectors)) if i not in chosen_set]
        remaining.sort(key=lambda i: (sum(vectors[i].values()), i))
        for index in remaining:
            if len(chosen) >= max_size:
                break
            chosen.append(index)
            chosen_set.add(index)

    return sorted(chosen)
