import os
import json
import logging
from typing import List
from harness.evaluator.models import EvalMetrics, EvalFailedCase, EvalReport
from production.orchestrator import run_screening_pipeline

logger = logging.getLogger(__name__)

GOLDEN_SET_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "meta_harness_workspace", "protected", "golden_set", "master.jsonl")

class EvalRunner:
    """
    Evaluates the Layer 1 Orchestrator against the Golden Set.
    """
    
    @classmethod
    async def evaluate_golden_set(cls, target_spec: dict) -> EvalReport:
        if not os.path.exists(GOLDEN_SET_FILE):
            raise FileNotFoundError(f"Golden set not found at {GOLDEN_SET_FILE}")
            
        total_cases = 0
        correct_cases = 0
        critical_misses = 0
        failed_cases: List[EvalFailedCase] = []
        
        with open(GOLDEN_SET_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                    
                record = json.loads(line)
                patent_id = record['patent_id']
                human_grade = record['human_grade']
                
                try:
                    logger.info(f"Evaluating {patent_id}...")
                    report = await run_screening_pipeline(patent_id, target_spec)
                    machine_grade = report.grade.value
                    
                    total_cases += 1
                    
                    if human_grade == machine_grade:
                        correct_cases += 1
                    else:
                        is_critical = (human_grade == 'HIGH' and machine_grade == 'SAFE')
                        if is_critical:
                            critical_misses += 1
                            
                        failed_cases.append(EvalFailedCase(
                            patent_id=patent_id,
                            human_grade=human_grade,
                            machine_grade=machine_grade,
                            machine_reasoning=report.reasoning,
                            is_critical=is_critical
                        ))
                except Exception as e:
                    logger.error(f"Failed to evaluate {patent_id}: {e}")
                    # Skip or fail depending on strictness
                    continue
                    
        accuracy = correct_cases / total_cases if total_cases > 0 else 0.0
        
        metrics = EvalMetrics(
            total_cases=total_cases,
            accuracy=accuracy,
            critical_misses=critical_misses
        )
        
        return EvalReport(metrics=metrics, failed_cases=failed_cases)
