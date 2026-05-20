from typing import List, Optional

from pydantic import BaseModel, Field


class ClaimResidueCondition(BaseModel):
    raw_text: str
    seq_id: Optional[str] = Field(default=None, description="Referenced patent SEQ ID, when claim text states it")
    mutation_type: str = Field(description="substitution, residue_requirement, deletion, insertion, or unknown")
    reference_position: int = Field(description="1-based residue position in the patent reference sequence")
    original_residue: Optional[str] = Field(default=None, description="Expected original residue in one-letter code")
    claimed_residue: Optional[str] = Field(default=None, description="Claimed residue in one-letter code")


class ResiduePositionMapping(BaseModel):
    seq_id: str
    raw_claim: str
    mutation_type: str
    reference_position: int
    reference_residue: Optional[str] = None
    original_residue: Optional[str] = None
    original_residue_matches: Optional[bool] = None
    product_position: Optional[int] = None
    product_residue: Optional[str] = None
    claimed_residue: Optional[str] = None
    claim_match: Optional[bool] = None
    status: str = Field(description="mapped, target_gap, reference_position_not_aligned, or reference_position_out_of_range")
    confidence: str = Field(description="high, medium, or low")
    reasoning: str


class SequenceAlignmentResult(BaseModel):
    seq_id: str
    status: str = Field(description="matched, missing_reference_sequence, no_product_sequence, or no_seq_id_reference")
    identity: Optional[float] = None
    local_identity: Optional[float] = Field(default=None, description="Local backend percent identity when BLAST/MMseqs2 is used")
    coverage: Optional[float] = None
    target_length: int = 0
    reference_length: int = 0
    matches: int = 0
    substitutions: List[str] = Field(default_factory=list)
    deletions: List[str] = Field(default_factory=list)
    insertions: List[str] = Field(default_factory=list)
    residue_position_mappings: List[ResiduePositionMapping] = Field(default_factory=list)
    alignment_backend: str = "needleman_wunsch"
    alignment_scope: str = Field(default="global", description="global or local")
    alignment_notes: List[str] = Field(default_factory=list)
    threshold: Optional[float] = None
    threshold_met: Optional[bool] = None
    reasoning: str
