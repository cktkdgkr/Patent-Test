from dataclasses import dataclass, field
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import List, Optional


@dataclass
class PairwiseAlignment:
    backend: str
    scope: str
    aligned_target: str
    aligned_reference: str
    target_start: int
    reference_start: int
    local_identity: Optional[float] = None
    bit_score: Optional[float] = None
    evalue: Optional[str] = None
    notes: List[str] = field(default_factory=list)


@dataclass
class AlignmentBackendOutcome:
    alignment: Optional[PairwiseAlignment] = None
    notes: List[str] = field(default_factory=list)


def run_external_alignment_backend(
    target_sequence: str,
    reference_sequence: str,
    preferred_backend: str = "auto",
    timeout_seconds: int = 20,
) -> AlignmentBackendOutcome:
    backend = (preferred_backend or "auto").strip().lower()
    if backend in {"needleman_wunsch", "needleman-wunsch", "nw", "global"}:
        return AlignmentBackendOutcome(notes=["Needleman-Wunsch backend requested."])

    backends = ["blastp", "mmseqs"] if backend == "auto" else [backend]
    outcome = AlignmentBackendOutcome()
    for candidate in backends:
        if candidate == "blastp":
            result = _run_blastp(target_sequence, reference_sequence, timeout_seconds)
        elif candidate == "mmseqs":
            result = _run_mmseqs(target_sequence, reference_sequence, timeout_seconds)
        else:
            result = AlignmentBackendOutcome(notes=[f"Unknown alignment backend '{candidate}'."])
        outcome.notes.extend(result.notes)
        if result.alignment:
            outcome.alignment = result.alignment
            return outcome
    return outcome


def _run_blastp(target_sequence: str, reference_sequence: str, timeout_seconds: int) -> AlignmentBackendOutcome:
    executable = shutil.which("blastp")
    if not executable:
        return AlignmentBackendOutcome(notes=["blastp executable not found; falling back."])

    with tempfile.TemporaryDirectory(prefix="patent_blastp_") as tmp:
        tmpdir = Path(tmp)
        reference_path = tmpdir / "reference.fa"
        target_path = tmpdir / "target.fa"
        _write_fasta(reference_path, "patent_reference", reference_sequence)
        _write_fasta(target_path, "product_target", target_sequence)
        command = [
            executable,
            "-query",
            str(reference_path),
            "-subject",
            str(target_path),
            "-outfmt",
            "6 qstart qend sstart send qseq sseq length nident pident gaps bitscore evalue",
            "-seg",
            "no",
            "-max_hsps",
            "20",
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            return AlignmentBackendOutcome(notes=[f"blastp could not run: {exc}"])
    if completed.returncode != 0:
        return AlignmentBackendOutcome(notes=[f"blastp failed: {completed.stderr.strip() or completed.returncode}"])
    alignment = _parse_alignment_table(completed.stdout, backend="blastp")
    return AlignmentBackendOutcome(
        alignment=alignment,
        notes=[] if alignment else ["blastp returned no pairwise alignment."],
    )


def _run_mmseqs(target_sequence: str, reference_sequence: str, timeout_seconds: int) -> AlignmentBackendOutcome:
    executable = shutil.which("mmseqs")
    if not executable:
        return AlignmentBackendOutcome(notes=["mmseqs executable not found; falling back."])

    with tempfile.TemporaryDirectory(prefix="patent_mmseqs_") as tmp:
        tmpdir = Path(tmp)
        reference_path = tmpdir / "reference.fa"
        target_path = tmpdir / "target.fa"
        result_path = tmpdir / "result.tsv"
        work_path = tmpdir / "tmp"
        _write_fasta(reference_path, "patent_reference", reference_sequence)
        _write_fasta(target_path, "product_target", target_sequence)
        command = [
            executable,
            "easy-search",
            str(reference_path),
            str(target_path),
            str(result_path),
            str(work_path),
            "--format-output",
            "qstart,qend,tstart,tend,qaln,taln,alnlen,nident,pident,gapopen,bits,evalue",
            "--threads",
            "1",
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            return AlignmentBackendOutcome(notes=[f"mmseqs could not run: {exc}"])
        output = result_path.read_text(encoding="utf-8", errors="replace") if result_path.exists() else ""
    if completed.returncode != 0:
        return AlignmentBackendOutcome(notes=[f"mmseqs failed: {completed.stderr.strip() or completed.returncode}"])
    alignment = _parse_alignment_table(output, backend="mmseqs")
    return AlignmentBackendOutcome(
        alignment=alignment,
        notes=[] if alignment else ["mmseqs returned no pairwise alignment."],
    )


def _parse_alignment_table(output: str, backend: str) -> Optional[PairwiseAlignment]:
    best: Optional[PairwiseAlignment] = None
    best_score = (-1.0, -1.0)
    for line in output.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        columns = line.rstrip("\n").split("\t")
        if len(columns) < 12:
            continue
        try:
            reference_start = int(columns[0])
            target_start = int(columns[2])
            aligned_reference = columns[4].upper()
            aligned_target = columns[5].upper()
            alignment_length = float(columns[6])
            nident = float(columns[7])
            local_identity = float(columns[8])
            bit_score = float(columns[10])
        except (TypeError, ValueError):
            continue
        evalue = columns[11]
        if not aligned_reference or not aligned_target or len(aligned_reference) != len(aligned_target):
            continue
        score = (bit_score, nident / max(alignment_length, 1.0))
        alignment = PairwiseAlignment(
            backend=backend,
            scope="local",
            aligned_target=aligned_target,
            aligned_reference=aligned_reference,
            target_start=target_start,
            reference_start=reference_start,
            local_identity=round(local_identity, 2),
            bit_score=bit_score,
            evalue=evalue,
        )
        if score > best_score:
            best = alignment
            best_score = score
    return best


def _write_fasta(path: Path, identifier: str, sequence: str) -> None:
    chunks = [sequence[index : index + 80] for index in range(0, len(sequence), 80)]
    path.write_text(f">{identifier}\n" + "\n".join(chunks) + "\n", encoding="utf-8")
