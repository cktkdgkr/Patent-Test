from core.access_control import AccessAction, AccessDenied, DataCategory, Layer, assert_access
from core.sanitizer import DataProtectionViolation, Sanitizer
from harness.deployment_gate import DeploymentGate
from harness.evaluator.models import EvalMetrics
from meta_harness_workspace.agent import MetaHarnessAgent


def test_sanitizer_blocks_restricted_payload():
    try:
        Sanitizer.sanitize("contact alice@example.com")
    except DataProtectionViolation:
        return
    raise AssertionError("restricted email payload was not blocked")


def test_access_policy_denies_pii_for_every_layer():
    for layer in (Layer.PRODUCTION, Layer.REVIEWER, Layer.META_HARNESS):
        try:
            assert_access(layer, DataCategory.PII_FINANCIAL, AccessAction.READ, "test")
        except AccessDenied:
            continue
        raise AssertionError(f"{layer.value} was allowed to read PII")


def test_layer3_scope_blocks_immutable_paths():
    try:
        MetaHarnessAgent.validate_change_scope(["harness/evaluator/runner.py"])
    except AccessDenied:
        return
    raise AssertionError("Layer 3 was allowed to target immutable evaluator code")


def test_deployment_gate_rejects_critical_miss_increase():
    baseline = EvalMetrics(
        total_cases=10,
        positive_cases=10,
        true_positives=10,
        recall=1.0,
        critical_misses=0,
        ece=0.1,
        per_category_recall={"percent_identity": 1.0},
        reasoning_quality=0.7,
    )
    candidate = baseline.model_copy(update={"critical_misses": 1})
    result = DeploymentGate.evaluate(
        baseline,
        candidate,
        changed_files=["production/analyzer/evaluator.py"],
        changed_loc=10,
    )
    assert not result.passed
    assert any("critical miss" in reason for reason in result.reasons)


def test_deployment_gate_accepts_pareto_candidate():
    baseline = EvalMetrics(
        total_cases=10,
        positive_cases=10,
        true_positives=9,
        recall=0.9,
        critical_misses=0,
        ece=0.2,
        per_category_recall={"percent_identity": 0.9},
        context_cost=10.0,
        latency_ms=100.0,
        reasoning_quality=0.7,
    )
    candidate = EvalMetrics(
        total_cases=10,
        positive_cases=10,
        true_positives=10,
        recall=1.0,
        critical_misses=0,
        ece=0.1,
        per_category_recall={"percent_identity": 1.0},
        context_cost=9.0,
        latency_ms=90.0,
        reasoning_quality=0.8,
    )
    result = DeploymentGate.evaluate(
        baseline,
        candidate,
        changed_files=["production/analyzer/evaluator.py"],
        changed_loc=10,
    )
    assert result.passed, result.reasons


if __name__ == "__main__":
    test_sanitizer_blocks_restricted_payload()
    test_access_policy_denies_pii_for_every_layer()
    test_layer3_scope_blocks_immutable_paths()
    test_deployment_gate_rejects_critical_miss_increase()
    test_deployment_gate_accepts_pareto_candidate()
    print("v3 control tests passed")
