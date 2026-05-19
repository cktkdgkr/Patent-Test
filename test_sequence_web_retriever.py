from production.sequence import sequence_listing_content_to_sequence
from production.sequence.web_retriever import (
    _parse_ncbi_fasta_records,
    _psips_document_id_candidates,
)


def main():
    psips_text = """
      SEQ ID NO 18
      LENGTH: 6
      TYPE: PRT
      SEQUENCE: 18
    Met Lys Asn Pro Leu Arg
    1               5
    """
    sequence = sequence_listing_content_to_sequence(psips_text)
    print(sequence)
    assert sequence == "MKNPLR"

    fasta = """>ABC123 Sequence 18 from patent US 11723967
MKNPLR
>ABC124 unrelated sequence
AAAA
"""
    records = _parse_ncbi_fasta_records(fasta, "11723967")
    print(records)
    assert records[18] == "MKNPLR"

    candidates = _psips_document_id_candidates("US11723967B2")
    print(candidates)
    assert "US11723967B2" in candidates
    assert "11723967" in candidates


if __name__ == "__main__":
    main()
