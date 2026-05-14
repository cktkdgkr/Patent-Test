import logging
import os
from typing import Optional

from core.access_control import AccessAction, DataCategory, Layer, assert_access
from core.sanitizer import Sanitizer
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
    def append_to_golden_set(cls, record: GoldenRecord, master_file: Optional[str] = None) -> None:
        """
        Appends a GoldenRecord as a JSON string to the master.jsonl file.
        """
        assert_access(
            Layer.REVIEWER,
            DataCategory.GOLDEN_SET,
            AccessAction.WRITE,
            reason=f"append:{record.patent_id}",
        )
        target_file = master_file or MASTER_FILE
        target_dir = os.path.dirname(target_file)
        if target_dir and not os.path.exists(target_dir):
            os.makedirs(target_dir, exist_ok=True)
            
        Sanitizer.sanitize_payload(record.model_dump())
        json_str = record.model_dump_json()
        
        # In MVP, we just use append mode. In production, fcntl locks would be needed for concurrency.
        try:
            with open(target_file, "a", encoding="utf-8") as f:
                f.write(json_str + "\n")
            logger.info(f"Successfully appended record for {record.patent_id} to golden set.")
        except Exception as e:
            logger.error(f"Failed to write to golden set: {e}")
            raise
