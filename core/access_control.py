import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, Set


class AccessDenied(Exception):
    """Raised when a layer attempts an operation outside its policy."""


class Layer(str, Enum):
    PRODUCTION = "layer1_production"
    REVIEWER = "layer2_reviewer"
    META_HARNESS = "layer3_meta_harness"
    ORCHESTRATOR = "external_orchestrator"


class DataCategory(str, Enum):
    PATENT_DATA = "patent_data"
    GOLDEN_SET = "golden_set"
    HUMAN_LABELS = "human_labels"
    MODEL_OUTPUTS = "model_outputs"
    RAW_TRACE_LOGS = "raw_trace_logs"
    PROTECTED_METRICS = "protected_metrics"
    BUILD_LOGS = "build_logs"
    PRODUCTION_CODE = "production_code"
    HARNESS_CODE = "harness_code"
    SANITIZER_CODE = "sanitizer_code"
    DEPLOYMENT_GATE_CODE = "deployment_gate_code"
    TEST_SET = "hidden_test_set"
    PII_FINANCIAL = "pii_financial"


class AccessAction(str, Enum):
    READ = "read"
    WRITE = "write"
    PROPOSE = "propose"


POLICY_MATRIX: Dict[Layer, Dict[DataCategory, Set[AccessAction]]] = {
    Layer.PRODUCTION: {
        DataCategory.PATENT_DATA: {AccessAction.READ},
        DataCategory.MODEL_OUTPUTS: {AccessAction.WRITE},
        DataCategory.RAW_TRACE_LOGS: {AccessAction.WRITE},
    },
    Layer.REVIEWER: {
        DataCategory.PATENT_DATA: {AccessAction.READ},
        DataCategory.GOLDEN_SET: {AccessAction.READ, AccessAction.WRITE},
        DataCategory.HUMAN_LABELS: {AccessAction.READ, AccessAction.WRITE},
        DataCategory.MODEL_OUTPUTS: {AccessAction.READ},
        DataCategory.RAW_TRACE_LOGS: {AccessAction.WRITE},
        DataCategory.PROTECTED_METRICS: {AccessAction.READ},
    },
    Layer.META_HARNESS: {
        DataCategory.MODEL_OUTPUTS: {AccessAction.READ},
        DataCategory.RAW_TRACE_LOGS: {AccessAction.READ},
        DataCategory.BUILD_LOGS: {AccessAction.READ, AccessAction.WRITE},
        DataCategory.PRODUCTION_CODE: {AccessAction.READ, AccessAction.PROPOSE},
        DataCategory.HARNESS_CODE: {AccessAction.READ, AccessAction.PROPOSE},
    },
    Layer.ORCHESTRATOR: {
        DataCategory.PATENT_DATA: {AccessAction.READ},
        DataCategory.GOLDEN_SET: {AccessAction.READ},
        DataCategory.HUMAN_LABELS: {AccessAction.READ},
        DataCategory.MODEL_OUTPUTS: {AccessAction.READ},
        DataCategory.RAW_TRACE_LOGS: {AccessAction.READ, AccessAction.WRITE},
        DataCategory.PROTECTED_METRICS: {AccessAction.READ},
        DataCategory.BUILD_LOGS: {AccessAction.READ},
        DataCategory.PRODUCTION_CODE: {AccessAction.READ},
        DataCategory.HARNESS_CODE: {AccessAction.READ},
        DataCategory.DEPLOYMENT_GATE_CODE: {AccessAction.READ},
    },
}


IMMUTABLE_PATH_PARTS = (
    "meta_harness_workspace/protected/golden_set",
    "meta_harness_workspace/protected/metrics_definitions",
    "meta_harness_workspace/protected/test_set",
    "core/sanitizer.py",
    "harness/evaluator",
    "harness/deployment_gate.py",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _audit_log_path() -> Path:
    return _repo_root() / "meta_harness_workspace" / "protected" / "audit_log" / "access_events.jsonl"


def _append_audit_event(event: dict) -> None:
    path = _audit_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=True, sort_keys=True) + "\n")


class AccessController:
    @staticmethod
    def is_allowed(layer: Layer, category: DataCategory, action: AccessAction) -> bool:
        if category == DataCategory.PII_FINANCIAL:
            return False
        return action in POLICY_MATRIX.get(layer, {}).get(category, set())

    @classmethod
    def assert_access(
        cls,
        layer: Layer,
        category: DataCategory,
        action: AccessAction,
        reason: str = "",
    ) -> None:
        allowed = cls.is_allowed(layer, category, action)
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "layer": layer.value,
            "category": category.value,
            "action": action.value,
            "allowed": allowed,
            "reason": reason,
        }
        _append_audit_event(event)
        if not allowed:
            raise AccessDenied(
                f"{layer.value} is not allowed to {action.value} {category.value}"
            )

    @staticmethod
    def assert_layer3_mutable_path(path: str) -> None:
        normalized = path.replace("\\", "/").lstrip("./")
        for protected_part in IMMUTABLE_PATH_PARTS:
            if protected_part in normalized:
                _append_audit_event(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "layer": Layer.META_HARNESS.value,
                        "category": "immutable_path",
                        "action": AccessAction.PROPOSE.value,
                        "allowed": False,
                        "reason": normalized,
                    }
                )
                raise AccessDenied(f"Layer 3 cannot propose changes to immutable path: {path}")


def assert_access(
    layer: Layer,
    category: DataCategory,
    action: AccessAction,
    reason: str = "",
) -> None:
    AccessController.assert_access(layer, category, action, reason)
