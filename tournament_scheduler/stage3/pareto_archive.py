"""Pareto archive helpers for the Stage 3 multi-objective search.

Split out of :mod:`tournament_scheduler.stage3.optimizer` so that module stays
within its file-length ratchet. Pure functions: no search state, no scoring of
its own -- objective vectors come from the shared quality objectives.
"""

from __future__ import annotations

from typing import Any, Dict, List

from tournament_scheduler.scoring.pareto import representative_indices as _representative_indices
from tournament_scheduler.scoring.quality_objectives import quality_objective_vector

def objective_vector(score: Dict[str, Any]) -> Dict[str, float]:
    """Extract a uniformly "lower is better" objective vector from a
    :func:`tournament_scheduler.planning.contract.score_candidate` result."""
    return quality_objective_vector(score)


def default_pareto_weight_vectors(base_weights: Dict[str, float]) -> List[Dict[str, float]]:
    """Build the default set of search-epoch weight vectors: one "corner"
    per :data:`DEFAULT_WEIGHTS` key (that dimension emphasized, all others
    zeroed, so the epoch searches purely for that tradeoff extreme) plus one
    balanced epoch using *base_weights* unchanged."""
    vectors: List[Dict[str, float]] = []
    for key in base_weights:
        vector = {k: 0.0 for k in base_weights}
        vector[key] = max(base_weights[key], 1.0) * 5.0
        vectors.append(vector)
    vectors.append(dict(base_weights))
    return vectors


def downselect_archive(archive: List[Dict[str, Any]], max_size: int) -> List[Dict[str, Any]]:
    """Reduce *archive* to at most *max_size* entries via the canonical
    per-dimension-extreme down-select (see ``scoring.pareto.representative_indices``),
    so "return a small representative set — extremes plus useful
    knee/balanced points" is one shared implementation. A no-op when already
    within budget."""
    if len(archive) <= max_size or not archive:
        return archive
    indices = _representative_indices([entry["vector"] for entry in archive], max_size)
    return [archive[i] for i in indices]
