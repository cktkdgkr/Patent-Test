import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional
from uuid import uuid4


project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.access_control import (  # noqa: E402
    AccessAction,
    AccessController,
    AccessDenied,
    DataCategory,
    Layer,
    assert_access,
)


logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


DEFAULT_REPORT_PATH = project_root / "meta_harness_workspace" / "eval_report.json"
PROPOSAL_DIR = project_root / "build_log" / "layer3-proposals"


@dataclass
class MetaHarnessProposal:
    proposal_id: str
    created_at: str
    status: str
    target_files: List[str]
    rationale: str
    expected_effects: List[str]
    required_gates: List[str]
    forbidden_actions: List[str] = field(default_factory=list)


class MetaHarnessAgent:
    """
    Layer 3 proposer.
    It may inspect allowed reports and propose scoped changes. It does not
    overwrite code, run the deployment gate, or modify protected artifacts.
    """

    @classmethod
    def generate_proposal(
        cls,
        report_path: Optional[str | Path] = None,
    ) -> MetaHarnessProposal:
        report_file = Path(report_path) if report_path else DEFAULT_REPORT_PATH
        assert_access(
            Layer.META_HARNESS,
            DataCategory.MODEL_OUTPUTS,
            AccessAction.READ,
            reason=f"read_eval_report:{report_file.name}",
        )
        report_data = cls._read_report(report_file)
        metrics = report_data.get("metrics", {})
        failed_cases = report_data.get("failed_cases", [])

        target_files = cls._choose_target_files(metrics, failed_cases)
        cls.validate_change_scope(target_files)

        proposal = MetaHarnessProposal(
            proposal_id=f"proposal_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}",
            created_at=datetime.now(timezone.utc).isoformat(),
            status="proposal_only_requires_plan_review",
            target_files=target_files,
            rationale=cls._build_rationale(metrics, failed_cases),
            expected_effects=[
                "Preserve or improve recall floor.",
                "Reduce critical misses without changing evaluation code.",
                "Keep ECE no worse than the current baseline.",
                "Leave golden set, metric definitions, sanitizer, and gate code untouched.",
            ],
            required_gates=[
                "Incubation document exists for the proposed build task.",
                "Atomic plan document exists and is approved.",
                "Lightweight validation passes.",
                "Full evaluation passes recall, critical miss, category, ECE, Pareto, and human-review gates.",
            ],
            forbidden_actions=[
                "Do not modify protected golden set or labels.",
                "Do not modify evaluator metric calculations.",
                "Do not modify sanitizer or data protection rules.",
                "Do not overwrite production or harness code directly from Layer 3.",
            ],
        )
        return proposal

    @classmethod
    def save_proposal(cls, proposal: MetaHarnessProposal) -> Path:
        assert_access(
            Layer.META_HARNESS,
            DataCategory.BUILD_LOGS,
            AccessAction.WRITE,
            reason=f"save_proposal:{proposal.proposal_id}",
        )
        PROPOSAL_DIR.mkdir(parents=True, exist_ok=True)
        path = PROPOSAL_DIR / f"{proposal.proposal_id}.json"
        with path.open("w", encoding="utf-8") as handle:
            json.dump(asdict(proposal), handle, ensure_ascii=True, indent=2, sort_keys=True)
        return path

    @staticmethod
    def validate_change_scope(paths: Iterable[str]) -> None:
        for path in paths:
            normalized = path.replace("\\", "/").lstrip("./")
            AccessController.assert_layer3_mutable_path(normalized)
            if normalized.startswith("production/"):
                assert_access(
                    Layer.META_HARNESS,
                    DataCategory.PRODUCTION_CODE,
                    AccessAction.PROPOSE,
                    reason=normalized,
                )
            elif normalized.startswith("harness/"):
                assert_access(
                    Layer.META_HARNESS,
                    DataCategory.HARNESS_CODE,
                    AccessAction.PROPOSE,
                    reason=normalized,
                )
            else:
                raise AccessDenied(
                    "Layer 3 proposals must target production/ or mutable harness/ files"
                )

    @staticmethod
    def _read_report(report_file: Path) -> dict:
        if not report_file.exists():
            return {
                "metrics": {
                    "recall": 1.0,
                    "critical_misses": 0,
                    "ece": 0.0,
                    "data_protection_violations": 0,
                },
                "failed_cases": [],
            }
        with report_file.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _choose_target_files(metrics: dict, failed_cases: list) -> List[str]:
        if metrics.get("data_protection_violations", 0) > 0:
            return ["production/retriever/local_reader.py"]
        if metrics.get("critical_misses", 0) > 0 or metrics.get("recall", 1.0) < 1.0:
            return ["production/analyzer/evaluator.py"]
        if failed_cases:
            return ["production/parser/llm_extractor.py"]
        return ["production/orchestrator/pipeline.py"]

    @staticmethod
    def _build_rationale(metrics: dict, failed_cases: list) -> str:
        if metrics.get("data_protection_violations", 0) > 0:
            return "Data protection violations were observed; propose tightening ingestion and pre-LLM checks without changing sanitizer rules."
        if metrics.get("critical_misses", 0) > 0:
            return "Critical misses were observed; propose recall-first production analyzer changes, not evaluator changes."
        if metrics.get("recall", 1.0) < 1.0:
            return "Recall is below the floor; propose production parser or analyzer changes that broaden risk detection."
        if failed_cases:
            return "Non-critical disagreements remain; propose parser or reporting improvements after preserving recall."
        return "No regression signal was found; propose no-op orchestration cleanup only if a human approves the build task."


def main() -> int:
    try:
        proposal = MetaHarnessAgent.generate_proposal()
        path = MetaHarnessAgent.save_proposal(proposal)
        logger.info("Saved proposal-only Layer 3 artifact: %s", path)
        return 0
    except AccessDenied as exc:
        logger.error("Layer 3 proposal blocked: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
