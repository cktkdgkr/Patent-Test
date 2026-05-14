import logging
import asyncio
from typing import List, Optional

from core.access_control import Layer
from core.trace_logger import TraceLogger
from production.retriever import retrieve_patent
from production.parser import TreeBuilder, LLMExtractor, ClaimFeatures
from production.analyzer import RiskAnalyzer, RiskReport

logger = logging.getLogger(__name__)

async def run_screening_pipeline(
    patent_id: str,
    target_spec: dict,
    run_id: Optional[str] = None,
) -> RiskReport:
    """
    Executes the full Layer 1 screening pipeline asynchronously.
    """
    run_id = run_id or TraceLogger.start_run("production")
    logger.info(f"--- Starting Pipeline for Patent: {patent_id} ---")
    
    # 1. Retriever (Includes Sanitizer)
    try:
        raw_text = retrieve_patent(patent_id, run_id=run_id)
        logger.info("Retriever & Sanitizer: OK")
    except Exception as e:
        logger.error(f"Pipeline failed at Retriever stage: {e}")
        raise
        
    # 2. Parser - Tree Builder
    try:
        claim_nodes = TreeBuilder.parse_claims(raw_text)
        logger.info(f"TreeBuilder: Parsed {len(claim_nodes)} claims.")
        TraceLogger.write_event(
            run_id=run_id,
            patent_id=patent_id,
            agent_name="claim_parser",
            event_name="claim_tree",
            payload={"claims": [node.model_dump() for node in claim_nodes]},
            layer=Layer.PRODUCTION,
        )
    except Exception as e:
        logger.error(f"Pipeline failed at TreeBuilder stage: {e}")
        raise
        
    # 3. Parser - LLM Extractor (Parallel extraction)
    try:
        # For MVP, we will extract all claims concurrently
        tasks = [
            LLMExtractor.extract_features(node, run_id=run_id, patent_id=patent_id)
            for node in claim_nodes
        ]
        features_list: List[ClaimFeatures] = await asyncio.gather(*tasks)
        logger.info("LLMExtractor: Extracted features successfully.")
    except Exception as e:
        logger.error(f"Pipeline failed at LLMExtractor stage: {e}")
        raise
        
    # 4. Risk Analyzer
    try:
        report = await RiskAnalyzer.analyze_risk(
            features_list,
            target_spec,
            run_id=run_id,
            patent_id=patent_id,
        )
        logger.info(f"RiskAnalyzer: Evaluation complete. Grade: {report.grade.value}")
    except Exception as e:
        logger.error(f"Pipeline failed at RiskAnalyzer stage: {e}")
        raise
        
    logger.info("--- Pipeline Completed Successfully ---")
    return report
