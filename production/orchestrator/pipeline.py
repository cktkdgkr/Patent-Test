import logging
import asyncio
from typing import List

from production.retriever import retrieve_patent
from production.parser import TreeBuilder, LLMExtractor, ClaimFeatures
from production.analyzer import RiskAnalyzer, RiskReport

logger = logging.getLogger(__name__)

async def run_screening_pipeline(patent_id: str, target_spec: dict) -> RiskReport:
    """
    Executes the full Layer 1 screening pipeline asynchronously.
    """
    logger.info(f"--- Starting Pipeline for Patent: {patent_id} ---")
    
    # 1. Retriever (Includes Sanitizer)
    try:
        raw_text = retrieve_patent(patent_id)
        logger.info("Retriever & Sanitizer: OK")
    except Exception as e:
        logger.error(f"Pipeline failed at Retriever stage: {e}")
        raise
        
    # 2. Parser - Tree Builder
    try:
        claim_nodes = TreeBuilder.parse_claims(raw_text)
        logger.info(f"TreeBuilder: Parsed {len(claim_nodes)} claims.")
    except Exception as e:
        logger.error(f"Pipeline failed at TreeBuilder stage: {e}")
        raise
        
    # 3. Parser - LLM Extractor (Parallel extraction)
    try:
        # For MVP, we will extract all claims concurrently
        tasks = [LLMExtractor.extract_features(node) for node in claim_nodes]
        features_list: List[ClaimFeatures] = await asyncio.gather(*tasks)
        logger.info("LLMExtractor: Extracted features successfully.")
    except Exception as e:
        logger.error(f"Pipeline failed at LLMExtractor stage: {e}")
        raise
        
    # 4. Risk Analyzer
    try:
        report = await RiskAnalyzer.analyze_risk(features_list, target_spec)
        logger.info(f"RiskAnalyzer: Evaluation complete. Grade: {report.grade.value}")
    except Exception as e:
        logger.error(f"Pipeline failed at RiskAnalyzer stage: {e}")
        raise
        
    logger.info("--- Pipeline Completed Successfully ---")
    return report
