from dataclasses import dataclass, field
from typing import Iterable, List

from core.access_control import AccessController, AccessDenied
from harness.evaluator.models import EvalMetrics


@dataclass
class GateResult:
    passed: bool
    reasons: List[str] = field(default_factory=list)


class DeploymentGate:
    """
    External gate for Layer 3 proposals.
    This code is outside the Layer 3 mutable scope and must remain immutable.
    """

    LOC_HUMAN_REVIEW_THRESHOLD = 120

    @classmethod
    def evaluate(
        cls,
        baseline: EvalMetrics,
        candidate: EvalMetrics,
        changed_files: Iterable[str],
        changed_loc: int = 0,
        human_approved: bool = False,
    ) -> GateResult:
        reasons: List[str] = []

        for path in changed_files:
            try:
                AccessController.assert_layer3_mutable_path(path)
            except AccessDenied as exc:
                reasons.append(str(exc))

        if candidate.data_protection_violations > 0:
            reasons.append("candidate has data protection violations")
        if candidate.recall < baseline.recall:
            reasons.append("recall floor regression")
        if candidate.critical_misses > baseline.critical_misses:
            reasons.append("critical miss count increased")
        if candidate.ece > baseline.ece:
            reasons.append("ECE worsened")

        categories = set(baseline.per_category_recall) | set(candidate.per_category_recall)
        for category in sorted(categories):
            baseline_recall = baseline.per_category_recall.get(category, 0.0)
            candidate_recall = candidate.per_category_recall.get(category, 0.0)
            if candidate_recall < baseline_recall:
                reasons.append(f"category recall regression: {category}")

        if not cls._pareto_non_dominated(baseline, candidate):
            reasons.append("candidate is not Pareto non-dominated")

        if changed_loc >= cls.LOC_HUMAN_REVIEW_THRESHOLD and not human_approved:
            reasons.append("large change requires explicit human approval")

        return GateResult(passed=not reasons, reasons=reasons)

    @staticmethod
    def _pareto_non_dominated(baseline: EvalMetrics, candidate: EvalMetrics) -> bool:
        dimensions = [
            (candidate.recall, baseline.recall, "max"),
            (-candidate.context_cost, -baseline.context_cost, "max"),
            (-candidate.latency_ms, -baseline.latency_ms, "max"),
            (candidate.reasoning_quality, baseline.reasoning_quality, "max"),
        ]
        at_least_equal = all(candidate_value >= baseline_value for candidate_value, baseline_value, _ in dimensions)
        strictly_better = any(candidate_value > baseline_value for candidate_value, baseline_value, _ in dimensions)
        return at_least_equal and strictly_better
