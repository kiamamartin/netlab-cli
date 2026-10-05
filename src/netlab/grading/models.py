"""Deterministic grading result models (doc §17, §21, §64).

The assessment is a **structured object** — the contract between the grading
engine and every consumer (CLI report renderer, future AI instructor, analytics).
Rendering may evolve; this object is the interface. No timestamps, randomness,
or LLM involvement ever enters these models, so identical state always produces
an identical assessment.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class CheckStatus(StrEnum):
    """Outcome of one deterministic check (doc §16)."""

    PASS = "pass"
    FAIL = "fail"
    PARTIAL = "partial"
    NOT_APPLICABLE = "not_applicable"


class RequirementResult(BaseModel):
    """The graded outcome of a single scenario requirement.

    ``expected`` / ``actual`` carry the evidence a report (or the AI
    instructor) needs to explain the result; they are plain strings so the
    result stays renderable and serializable.
    """

    model_config = ConfigDict(frozen=True)

    requirement_id: str
    category: str
    description: str = ""
    status: CheckStatus
    points_possible: int = Field(gt=0)
    points_earned: int = Field(ge=0)

    expected: str = ""
    actual: str = ""
    detail: str = ""  # human-readable explanation of a partial/failed result

    @property
    def is_pass(self) -> bool:
        return self.status is CheckStatus.PASS

    @property
    def is_partial(self) -> bool:
        return self.status is CheckStatus.PARTIAL

    @property
    def is_fail(self) -> bool:
        return self.status is CheckStatus.FAIL


class CategoryScore(BaseModel):
    """Aggregated points for one assessment category (doc §23)."""

    model_config = ConfigDict(frozen=True)

    category: str
    possible: int
    earned: int


class Assessment(BaseModel):
    """Complete, deterministic grading result for one scenario state (doc §64).

    Invariants:

    - ``earned_points <= total_points`` and both are non-negative.
    - ``passed/partial/failed`` partition all requirement results.
    - Results are ordered exactly as the scenario orders its requirements, so
      report rendering is stable across runs and Python versions.
    """

    model_config = ConfigDict(frozen=True)

    scenario_id: str
    scenario_title: str
    total_points: int
    earned_points: int
    results: list[RequirementResult]
    categories: list[CategoryScore]

    @property
    def percentage(self) -> int:
        if self.total_points == 0:
            return 0
        return round(self.earned_points * 100 / self.total_points)

    @property
    def passed_checks(self) -> list[RequirementResult]:
        return [result for result in self.results if result.is_pass]

    @property
    def partial_checks(self) -> list[RequirementResult]:
        return [result for result in self.results if result.is_partial]

    @property
    def failed_checks(self) -> list[RequirementResult]:
        return [result for result in self.results if result.is_fail]

    @property
    def unmet_requirements(self) -> list[str]:
        """Descriptions of everything not fully earned (failed + partial)."""
        return [
            result.description or result.requirement_id
            for result in self.results
            if result.points_earned < result.points_possible
        ]

    @property
    def summary(self) -> str:
        """One-line deterministic summary (doc §16: ``8 / 10`` style)."""
        return (
            f"{self.earned_points} / {self.total_points} points "
            f"({self.percentage}%) — "
            f"{len(self.passed_checks)} passed, "
            f"{len(self.partial_checks)} partial, "
            f"{len(self.failed_checks)} failed"
        )


__all__ = [
    "Assessment",
    "CategoryScore",
    "CheckStatus",
    "RequirementResult",
]
