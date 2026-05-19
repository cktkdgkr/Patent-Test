import io
import zipfile

from production.sequence import sequence_listing_content_to_sequence
from production.sequence.web_retriever import (
    _extract_sequences_from_payload,
    _ncbi_patent_search_tokens,
    _parse_ncbi_fasta_records,
    _psips_document_id_candidates,
    _wipo_publication_folder,
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

    global_fasta = """>XYZ123 Sequence 12 from patent WO 2020123456
MKTAYIAKQR
>XYZ124 SEQ ID NO: 7 from patent EP 3456789
MNKLLPTAA
"""
    wo_records = _parse_ncbi_fasta_records(global_fasta, "WO2020123456A1")
    ep_records = _parse_ncbi_fasta_records(global_fasta, "EP3456789A1")
    print(wo_records)
    print(ep_records)
    assert wo_records[12] == "MKTAYIAKQR"
    assert ep_records[7] == "MNKLLPTAA"

    candidates = _psips_document_id_candidates("US11723967B2")
    print(candidates)
    assert "US11723967B2" in candidates
    assert "11723967" in candidates

    assert "WO2020123456" in _ncbi_patent_search_tokens("WO2020123456A1")
    assert _wipo_publication_folder("WO2020081987A1") == "WO20_081987"

    st26_xml = """<?xml version="1.0"?>
    <ST26SequenceListing>
      <SequenceData sequenceIDNumber="3">
        <INSDSeq>
          <INSDSeq_moltype>AA</INSDSeq_moltype>
          <INSDSeq_sequence>mknplrstyv</INSDSeq_sequence>
        </INSDSeq>
      </SequenceData>
    </ST26SequenceListing>
    """
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("WO20_081987/sequence_listing.xml", st26_xml)
    sequences, sources = _extract_sequences_from_payload(archive_bytes.getvalue(), "unit-test zip")
    print(sequences)
    print(sources)
    assert sequences["SEQ ID NO:3"] == "MKNPLRSTYV"
    assert "sequence_listing.xml" in sources["SEQ ID NO:3"]


if __name__ == "__main__":
    main()
