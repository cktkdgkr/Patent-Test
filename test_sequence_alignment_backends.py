from production.sequence.alignment_backends import _parse_alignment_table, run_external_alignment_backend
from production.sequence.analysis import _alignment_result_from_backend
from production.sequence import extract_claim_residue_conditions


def main():
    output = "1\t10\t1\t11\tMKTAYI-AKQR\tMKTAYISSKQR\t11\t9\t81.82\t1\t50.0\t1e-20\n"
    alignment = _parse_alignment_table(output, backend="blastp")
    assert alignment is not None
    assert alignment.backend == "blastp"
    assert alignment.scope == "local"
    assert alignment.reference_start == 1
    assert alignment.target_start == 1
    assert alignment.local_identity == 81.82

    conditions = extract_claim_residue_conditions("An enzyme comprising lysine at position 8 of SEQ ID NO:1.")
    result = _alignment_result_from_backend(
        target="MKTAYISSKQR",
        reference="MKTAYIAKQR",
        seq_id="SEQ ID NO:1",
        threshold=None,
        residue_conditions=conditions,
        backend_alignment=alignment,
        backend_notes=[],
    )
    print(result.model_dump_json(indent=2))
    mapping = result.residue_position_mappings[0]
    assert result.alignment_backend == "blastp"
    assert result.alignment_scope == "local"
    assert result.local_identity == 81.82
    assert mapping.reference_position == 8
    assert mapping.product_position == 9
    assert mapping.claim_match is True

    needleman_outcome = run_external_alignment_backend(
        "MKTAYISSKQR",
        "MKTAYIAKQR",
        preferred_backend="needleman_wunsch",
    )
    assert needleman_outcome.alignment is None


if __name__ == "__main__":
    main()
