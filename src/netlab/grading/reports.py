"""Assessment report rendering (doc §24, §64).

The renderer consumes a structured :class:`~netlab.grading.models.Assessment`
and produces terminal text. It calculates nothing: scores, percentages, and
groupings are read from the assessment. Plain text with no terminal-width
assumptions, so the same report works interactively, piped, and in tests.
The label column is sized from the report's own content so long descriptions
cannot shift the score column.
"""

from __future__ import annotations

from netlab.grading.models import Assessment, CheckStatus, RequirementResult

_WIDTH = 62

_MARKS: dict[CheckStatus, str] = {
    CheckStatus.PASS: "✓",
    CheckStatus.PARTIAL: "~",
    CheckStatus.FAIL: "✗",
    CheckStatus.NOT_APPLICABLE: "-",
}


def _rule() -> str:
    return "─" * _WIDTH


def _result_line(result: RequirementResult, label_width: int) -> str:
    mark = _MARKS[result.status]
    score = f"{result.points_earned}/{result.points_possible}"
    label = result.description or result.requirement_id
    return f"{mark} {label:<{label_width}} {score:>8}"


def render_assessment(assessment: Assessment) -> str:
    """Render the machine-readable assessment as a terminal report."""
    label_width = max(
        (len(result.description or result.requirement_id) for result in assessment.results),
        default=8,
    )
    lines: list[str] = [
        "NETLAB ASSESSMENT",
        _rule(),
        f"Scenario: {assessment.scenario_title} ({assessment.scenario_id})",
        f"Score: {assessment.earned_points} / {assessment.total_points}"
        f"    ({assessment.percentage}%)",
        "",
    ]

    groups: list[tuple[str, list[RequirementResult]]] = [
        ("PASSED", assessment.passed_checks),
        ("PARTIAL", assessment.partial_checks),
        ("FAILED", assessment.failed_checks),
    ]
    for title, group in groups:
        if not group:
            continue
        lines.append(title)
        lines.extend(_result_line(result, label_width) for result in group)
        lines.append("")

    lines.append("By category")
    for category in assessment.categories:
        lines.append(
            f"  {category.category:<44} {category.earned:>4}/{category.possible}"
        )
    lines.append("")

    if assessment.failed_checks or assessment.partial_checks:
        lines.append("RECOMMENDED REVIEW")
        for description in assessment.unmet_requirements:
            lines.append(f"  • {description}")
        lines.append("")

    lines.append(_rule())
    lines.append(f"Final Score: {assessment.percentage}%")
    return "\n".join(lines)


__all__ = ["render_assessment"]
