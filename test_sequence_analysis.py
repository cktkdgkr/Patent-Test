from production.sequence import (
    align_sequences,
    extract_mutation_terms,
    extract_reference_sequences,
    extract_seq_id_references,
    extract_st26_sequence_listing_entries,
    normalize_amino_acid_sequence,
)


def main():
    fasta = ">product enzyme\nMKTAYI\nAKQR\n"
    assert normalize_amino_acid_sequence(fasta) == "MKTAYIAKQR"

    patent_text = """
    1. A variant having at least 80% identity to SEQ ID NO:1.
    SEQ ID NO:1: MKTAYIAKQRQISFVKSHFSRQDILDLIC
    """
    refs = extract_seq_id_references(patent_text)
    sequences = extract_reference_sequences(patent_text)
    print(refs)
    print(sequences)
    assert refs == ["SEQ ID NO:1"]
    assert sequences["SEQ ID NO:1"] == "MKTAYIAKQRQISFVKSHFSRQDILDLIC"

    alignment = align_sequences(
        "MKTAYIAKQRQISFVKSHFSRQEILDLIC",
        sequences["SEQ ID NO:1"],
        seq_id="SEQ ID NO:1",
        threshold=80.0,
    )
    print(alignment.model_dump_json(indent=2))
    assert alignment.status == "matched"
    assert alignment.identity > 95.0
    assert alignment.coverage == 100.0
    assert alignment.threshold_met is True
    assert "D23E" in alignment.substitutions

    mutations = extract_mutation_terms("wherein the variant comprises A10V substitution")
    assert "A10V" in mutations

    st26_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <ST26SequenceListing>
      <SequenceData sequenceIDNumber="7">
        <INSDSeq>
          <INSDSeq_moltype>AA</INSDSeq_moltype>
          <INSDSeq_sequence>mktayiakqrqisfvk</INSDSeq_sequence>
        </INSDSeq>
      </SequenceData>
      <SequenceData sequenceIDNumber="8">
        <INSDSeq>
          <INSDSeq_moltype>DNA</INSDSeq_moltype>
          <INSDSeq_sequence>atggccattgtaatgggc</INSDSeq_sequence>
          <INSDSeq_feature-table>
            <INSDFeature>
              <INSDFeature_quals>
                <INSDQualifier>
                  <INSDQualifier_name>translation</INSDQualifier_name>
                  <INSDQualifier_value>MAIVMG</INSDQualifier_value>
                </INSDQualifier>
              </INSDFeature_quals>
            </INSDFeature>
          </INSDSeq_feature-table>
        </INSDSeq>
      </SequenceData>
    </ST26SequenceListing>
    """
    st26_sequences = extract_st26_sequence_listing_entries(st26_xml)
    print(st26_sequences)
    assert st26_sequences["SEQ ID NO:7"] == "MKTAYIAKQRQISFVK"
    assert st26_sequences["SEQ ID NO:8"] == "MAIVMG"


if __name__ == "__main__":
    main()
