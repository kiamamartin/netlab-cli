"""Deterministic grading engine (architecture milestone 6).

The grader compares the authoritative :class:`~netlab.network.state.NetworkState`
against a scenario's declarative requirements. It never reads rendered CLI text
and never involves an LLM: the same scenario plus the same network state always
produces the same assessment.

    Scenario → Requirements → Checks → Engine → Assessment → Report renderer
"""

from netlab.grading.engine import grade
from netlab.grading.errors import GradingError  # re-export convenience
from netlab.grading.models import Assessment, CategoryScore, CheckStatus, RequirementResult
from netlab.grading.registry import registered_types, validator_for
from netlab.grading.reports import render_assessment

__all__ = [
    "Assessment",
    "CategoryScore",
    "CheckStatus",
    "GradingError",
    "RequirementResult",
    "grade",
    "registered_types",
    "render_assessment",
    "validator_for",
]
