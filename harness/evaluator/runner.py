import json
import logging
import os
from collections import defaultdict
from typing import Dict, List, Optional

from core.access_control import AccessAction, DataCategory, Layer, assert_access
from core.sanitizer import DataProtectionViolation, Sanitizer
from core.trace_logger import TraceLogger
from harness.curator.models import GoldenRecord
from harness.evaluator.models import EvalFailedCase, EvalMetrics, EvalReport
from production.orchestrator import run_screening_pipeline


logger = logging.getLogger(__name__)

GOLDEN_SET_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "meta_harness_workspace",
    "protected",
    "golden_set",
    "master.jsonl",
)

RISKY_GRADES = {"HIGH", "MEDIUM", "LOW"}


class EvalRunner:
    """
    Layer 2 evaluator.
    It owns measurement, never lets Layer 1 self-score, and treats recall and
    critical misses as primary constraints.
    """

    @classmethod
    async def evaluate_golden_set(
        cls,
        target_spec: Optional[dict] = None,
        run_id: Optional[str] = None,
    ) -> EvalReport:
        assert_access(
            Layer.REVIEWER,
            DataCategory.GOLDEN_SET,
            AccessAction.READ,
            reason="evaluate_golden_set",
        )
        if not os.path.exists(GOLDEN_SET_FILE):
            raise FileNotFoundError(f"Golden set not found at {GOLDEN_SET_FILE}")

        run_id = run_id or TraceLogger.start_run("eval")
        records = cls._load_records()
        counters = {
            "total": 0,
            "correct": 0,
            "positive": 0,
            "true_positive": 0,
            "predicted_risky": 0,
            "false_positive": 0,
            "false_negative": 0,
            "critical_misses": 0,
            "data_protection_violations": 0,
        }
        category_positive: Dict[str, int] = defaultdict(int)
        category_true_positive: Dict[str, int] = defaultdict(int)
        category_total: Dict[str, int] = defaultdict(int)
        category_correct: Dict[str, int] = defaultdict(int)
        calibration_errors: List[float] = []
        reasoning_scores: List[float] = []
        failed_cases: List[EvalFailedCase] = []

        for record in records:
            counters["total"] += 1
            patent_id = record.patent_id
            human_grade = record.human_grade.upper()
            category = record.category or "uncategorized"
            case_target_spec = record.target_spec or target_spec or {}
            Sanitizer.sanitize_payload(case_target_spec)
            category_total[category] += 1

            human_positive = human_grade in RISKY_GRADES
            if human_positive:
                counters["positive"] += 1
                category_positive[category] += 1

            try:
                logger.info("Evaluating %s...", patent_id)
                report = await run_screening_pipeline(
                    patent_id,
                    case_target_spec,
                    run_id=run_id,
                )
                machine_grade = report.grade.value
                confidence = report.confidence
                machine_reasoning = report.reasoning
            except DataProtectionViolation as e:
                counters["data_protection_violations"] += 1
                machine_grade = "BLOCKED"
                confidence = 1.0
                machine_reasoning = str(e)
            except Exception as e:
                machine_grade = "ERROR"
                confidence = 0.0
                machine_reasoning = str(e)

            predicted_risky = machine_grade in RISKY_GRADES
            if predicted_risky:
                counters["predicted_risky"] += 1
            if human_positive and predicted_risky:
                counters["true_positive"] += 1
                category_true_positive[category] += 1
            if not human_positive and predicted_risky:
                counters["false_positive"] += 1

            exact_match = human_grade == machine_grade
            if exact_match:
                counters["correct"] += 1
                category_correct[category] += 1

            false_negative = human_positive and not predicted_risky
            critical = human_grade == "HIGH" and machine_grade == "SAFE"
            if false_negative:
                counters["false_negative"] += 1
            if critical:
                counters["critical_misses"] += 1

            correctness = 1.0 if exact_match else 0.0
            calibration_errors.append(abs(confidence - correctness))
            reasoning_scores.append(1.0 if machine_reasoning.strip() else 0.0)

            TraceLogger.write_event(
                run_id=run_id,
                patent_id=patent_id,
                agent_name="eval_runner",
                event_name="case_result",
                payload={
                    "human_grade": human_grade,
                    "machine_grade": machine_grade,
                    "confidence": confidence,
                    "category": category,
                    "exact_match": exact_match,
                    "false_negative": false_negative,
                    "critical": critical,
                },
                layer=Layer.REVIEWER,
            )

            if not exact_match:
                failed_cases.append(
                    EvalFailedCase(
                        patent_id=patent_id,
                        category=category,
                        human_grade=human_grade,
                        machine_grade=machine_grade,
                        machine_confidence=confidence,
                        machine_reasoning=machine_reasoning,
                        is_critical=critical,
                        is_false_negative=false_negative,
                    )
                )

        metrics = EvalMetrics(
            total_cases=counters["total"],
            positive_cases=counters["positive"],
            true_positives=counters["true_positive"],
            false_positives=counters["false_positive"],
            false_negatives=counters["false_negative"],
            accuracy=cls._safe_div(counters["correct"], counters["total"]),
            recall=cls._safe_div(counters["true_positive"], counters["positive"]),
            precision=cls._safe_div(counters["true_positive"], counters["predicted_risky"]),
            critical_misses=counters["critical_misses"],
            ece=sum(calibration_errors) / len(calibration_errors) if calibration_errors else 0.0,
            per_category_recall={
                category: cls._safe_div(category_true_positive[category], count)
                for category, count in category_positive.items()
            },
            per_category_accuracy={
                category: cls._safe_div(category_correct[category], count)
                for category, count in category_total.items()
            },
            data_protection_violations=counters["data_protection_violations"],
            reasoning_quality=sum(reasoning_scores) / len(reasoning_scores) if reasoning_scores else 0.0,
        )

        TraceLogger.write_event(
            run_id=run_id,
            patent_id="_aggregate",
            agent_name="eval_runner",
            event_name="scores",
            payload=metrics.model_dump(),
            layer=Layer.REVIEWER,
        )
        return EvalReport(metrics=metrics, failed_cases=failed_cases)

    @staticmethod
    def _safe_div(numerator: int, denominator: int) -> float:
        return numerator / denominator if denominator else 0.0

    @staticmethod
    def _load_records() -> List[GoldenRecord]:
        records: List[GoldenRecord] = []
        with open(GOLDEN_SET_FILE, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                records.append(GoldenRecord(**json.loads(line)))
        return records
