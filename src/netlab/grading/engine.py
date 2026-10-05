"""The deterministic grading engine (doc §21, §51–52).

Pipeline, evaluated once per assessment (no repeated parsing, no re-reading
YAML, no AI):

    Scenario requirements (loaded once)
        ↓
    resolve validator per requirement (registry)
        ↓
    evaluate each check against the authoritative NetworkState
        ↓
    aggregate earned points + category scores
        ↓
    Assessment (structured, frozen result)
"""

from __future__ import annotations

from netlab.grading.models import Assessment, RequirementResult
from netlab.grading.registry import validator_for
from netlab.grading.scoring import aggregate_categories, earned_points, status_for
from netlab.network.state import NetworkState
from netlab.scenarios.schema import Scenario


def grade(scenario: Scenario, state: NetworkState) -> Assessment:
    """Grade ``state`` against ``scenario``'s requirements deterministically.

    The same (scenario, state) pair always yields an equal
    :class:`Assessment` — results keep scenario order, there are no
    timestamps, no randomness, no dictionary-iteration dependence, and no
    LLM involvement anywhere in the pipeline.
    """
    results: list[RequirementResult] = []
    for requirement in scenario.requirements:
        validator = validator_for(requirement)
        outcome = validator(state, requirement)
        awarded = earned_points(requirement, outcome.earned_fraction)
        results.append(
            RequirementResult(
                requirement_id=requirement.id,
                category=requirement.category,
                description=requirement.description or requirement.id,
                status=status_for(outcome.earned_fraction),
                points_possible=requirement.points,
                points_earned=awarded,
                expected=outcome.expected,
                actual=outcome.actual,
                detail=outcome.detail,
            )
        )

    return Assessment(
        scenario_id=scenario.id,
        scenario_title=scenario.title,
        total_points=scenario.grading.total,
        earned_points=sum(result.points_earned for result in results),
        results=results,
        categories=aggregate_categories(results),
    )


__all__ = ["grade"]
