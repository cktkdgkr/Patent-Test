import re
import logging
from typing import List, Optional
from production.parser.models import ClaimNode

logger = logging.getLogger(__name__)

class TreeBuilder:
    """
    Deterministically builds a dependency tree from raw patent claims using regex.
    """
    
    # Matches "1. A method..." or "Claim 1: A method..."
    CLAIM_START_REGEX = re.compile(r"^(?:Claim\s+)?(\d+)[.:]\s*(.*)", re.IGNORECASE)
    # Matches "The method of claim 1..." or "according to claim 2"
    DEPENDENCY_REGEX = re.compile(r"(?:of|to|in)\s+claim\s+(\d+)", re.IGNORECASE)
    
    @classmethod
    def parse_claims(cls, raw_text: str) -> List[ClaimNode]:
        """
        Parses a block of text containing multiple claims separated by newlines.
        Returns a list of ClaimNode objects.
        """
        nodes = []
        lines = raw_text.split('\n')
        
        current_claim_id = None
        current_claim_text = []
        
        def commit_claim():
            nonlocal current_claim_id, current_claim_text
            if current_claim_id is not None:
                full_text = " ".join(current_claim_text).strip()
                parent_id = cls._extract_parent_id(full_text)
                
                # Cannot depend on a future claim or itself
                if parent_id is not None and parent_id >= current_claim_id:
                    logger.warning(f"Invalid dependency detected for Claim {current_claim_id}. Defaulting to independent.")
                    parent_id = None
                    
                nodes.append(ClaimNode(
                    id=current_claim_id,
                    parent_id=parent_id,
                    text=full_text
                ))
            current_claim_id = None
            current_claim_text = []

        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            match = cls.CLAIM_START_REGEX.match(line)
            if match:
                commit_claim()
                current_claim_id = int(match.group(1))
                current_claim_text.append(match.group(2))
            else:
                if current_claim_id is not None:
                    current_claim_text.append(line)
                    
        commit_claim()
        return nodes
        
    @classmethod
    def _extract_parent_id(cls, text: str) -> Optional[int]:
        # Simple heuristic: Look at the first sentence or first 150 characters for dependency
        prefix = text[:150]
        match = cls.DEPENDENCY_REGEX.search(prefix)
        if match:
            return int(match.group(1))
        return None
