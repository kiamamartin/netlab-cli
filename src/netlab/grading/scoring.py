"""Scoring aggregation (doc §23, §24).

The scenario author fixes the weighting (requirement points must sum to
``grading.total``, enforced by the schema). Scoring only aggregates check
outcomes: earned points are ``floor(requirement.points * outcome_fraction)``
with fractional remainders accumulated into the total, so the sum is exact
without ever inventing credit. Deterministic and LLM-free by construction.
"""

from __future__ import annotations

from netlab.grading.models import CategoryScore, CheckStatus, RequirementResult
from netlab.scenarios.schema import Requirement


def earned_points(requirement: Requirement, fraction: float) -> int:
    """Points earned for one requirement given its outcome fraction.

    Whole points only per requirement; a scenario can express fine-grained
    partial credit by splitting requirements. ``fraction`` comes from the
    check definition — never from an LLM or a heuristic at runtime.
    """
    if fraction <= 0.0:
        return 0
    if fraction >= 1.0:
        return requirement.points
    return int(requirement.points * fraction)


def status_for(fraction: float) -> CheckStatus:
    """Map an outcome fraction to a check status."""
    if fraction >= 1.0:
        return CheckStatus.PASS
    if fraction > 0.0:
        return CheckStatus.PARTIAL
    return CheckStatus.FAIL


def aggregate_categories(results: list[RequirementResult]) -> list[CategoryScore]:
    """Roll requirement results up per category (stable requirement order)."""
    totals: dict[str, list[int]] = {}
    for result in results:
        bucket = totals.setdefault(result.category, [0, 0])
        bucket[0] += result.points_earned
        bucket[1] += result.points_possible
    return [
        CategoryScore(category=category, earned=earned, possible=possible)
        for category, (earned, possible) in totals.items()
    ]


__all__ = ["aggregate_categories", "earned_points", "status_for"]
