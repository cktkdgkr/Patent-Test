import os
import logging
from harness.curator.models import GoldenRecord

logger = logging.getLogger(__name__)

# Base path relative to this script
BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "meta_harness_workspace", "protected", "golden_set")
MASTER_FILE = os.path.join(BASE_DIR, "master.jsonl")

class GoldenSetManager:
    """
    Manages the appending of golden set records to the IMMUTABLE (for Layer 3) file system.
    """
    
    @classmethod
    def append_to_golden_set(cls, record: GoldenRecord) -> None:
        """
        Appends a GoldenRecord as a JSON string to the master.jsonl file.
        """
        if not os.path.exists(BASE_DIR):
            os.makedirs(BASE_DIR, exist_ok=True)
            
        json_str = record.model_dump_json()
        
        # In MVP, we just use append mode. In production, fcntl locks would be needed for concurrency.
        try:
            with open(MASTER_FILE, "a", encoding="utf-8") as f:
                f.write(json_str + "\n")
            logger.info(f"Successfully appended record for {record.patent_id} to golden set.")
        except Exception as e:
            logger.error(f"Failed to write to golden set: {e}")
            raise
