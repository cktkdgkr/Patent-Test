# Product Screening Workflow

This workflow lets a user screen one structured product against one candidate
patent text file or one existing mock patent ID.

## Product Input

Create a JSON file with product assumptions and, when available, the product
enzyme sequence:

```json
{
  "product_id": "enzyme_product_alpha",
  "amino_acid_sequence": "MKTAYIAKQRQISFVKSHFSRQEILDLIC",
  "reference_sequence_id": "SEQ ID NO:1",
  "ph": 7.0,
  "enzyme_class": "protease",
  "variant": "A10V",
  "jurisdiction": "US"
}
```

The `identity` field is now an optional override. If a claim references a
recoverable `SEQ ID NO` sequence, the screening workflow computes percent
identity, coverage, and mutation/deletion/insertion differences from the
submitted amino acid sequence.

Claims that recite a specific residue position, such as `A125Y`, `position 125
is tyrosine`, or `lysine at position 125 of SEQ ID NO:1`, are also mapped through
the sequence alignment. The report records which product residue corresponds to
the patent reference position, whether the product residue matches the claimed
residue, and a confidence label based on alignment identity, coverage, gaps, and
whether the recovered reference residue agrees with the claim wording.

The alignment backend can be set to `auto`, `needleman_wunsch`, `blastp`, or
`mmseqs`. In `auto` mode the workflow tries local BLASTP first, then local
MMseqs2, and falls back to the built-in Needleman-Wunsch global aligner when
external executables are unavailable. BLASTP/MMseqs2 hits are treated as local
alignments: their local identity is shown for context, while the risk workflow
uses a full-length-normalized identity and reference coverage to avoid
overstating short high-identity matches.

When public web fetching is enabled, missing `SEQ ID NO` reference sequences are
resolved from public sequence sources when possible:

- WIPO published PCT sequence-listing directories for WO publications, including
  linked ZIP/XML/TXT listing documents.
- ST.26 XML sequence listings, including XML files packaged inside ZIP archives.
- EPO public patent-document pages as a best-effort route for sequence-listing
  document links.
- USPTO PSIPS (`seqdata.uspto.gov`) for lengthy issued/published US sequence
  listings.
- NCBI Protein E-utilities as a secondary patent-sequence lookup path for US,
  WO, EP, and other country-code patent identifiers when records are indexed.
- Google Patents full-text pages and linked sequence documents as a convenience
  fallback when sequence text is embedded in the page.

Fetched sequences are cached under `data/sequence_cache/web/` for local reuse.
If no source can provide the sequence, the result remains
`missing_reference_sequence` rather than being treated as safe. Public coverage
depends on what each patent office or database exposes without credentials; for
restricted office systems, the UI should show the missing sequence instead of
guessing.

## Candidate Patent Input

For the local-upload path, save candidate claims as a text file under the repo.
Claims should be numbered, for example `1.`, `2.`, and so on.

## Run

```powershell
python scripts\screen_product.py --product examples\product_alpha.json --patent-file examples\candidate_patent_ph_overlap.txt --candidate-id candidate_patent_ph_overlap --output build_log\product_screening_report.json
```

You can also screen against an existing mock patent:

```powershell
python scripts\screen_product.py --product examples\product_alpha.json --patent-id mock_enzyme_004
```

## Batch Screening From Excel Or CSV

For multiple candidate patents, prepare a `.xlsx` or `.csv` file with columns
such as:

- `candidate_id`
- `patent_id`
- `publication_number`
- `title`
- `claim_text`
- `patent_file`
- `keywords`

The current local workflow can screen rows that contain `claim_text`, a local
`patent_file`, a local mock `patent_id`, or a `publication_number` /
`application_number` that has a matching cached text file under
`data/patent_cache`. Rows with real-world publication numbers but no cache entry
are reported as failed candidates until an external patent database connector is
configured.

For a quick public-web preview, pass `--enable-web-fetch`. The current preview
connector reads claims from Google Patents, writes fetched claim text under
`data/patent_cache/web/`, and attempts to resolve missing `SEQ ID NO` reference
sequences from public web sequence sources. Use this as a convenience connector,
not as the authoritative legal record.

```powershell
python scripts\screen_excel.py --product examples\product_alpha.json --excel examples\patent_candidates.csv --output build_log\batch_screening_report.json --summary-csv build_log\batch_screening_summary.csv
```

```powershell
python scripts\screen_excel.py --product examples\product_alpha.json --excel examples\patent_candidates_web.csv --enable-web-fetch
```

## Browser UI

Run the local UI:

```powershell
python scripts\run_ui.py --port 8765
```

Then open `http://127.0.0.1:8765`. The UI accepts FASTA text, a FASTA file, or a
direct amino acid sequence along with operating conditions and a candidate
`.csv` or `.xlsx`. It shows patent-level risk, claim-level detail, sequence
alignment, and design-around candidate directions. The `서열 예시 실행` button
uses `examples/patent_candidates_sequence.csv`.

## Output

The report contains:

- overall grade and rationale
- claim-level grades
- extracted claim features
- SEQ ID references and sequence alignment when available
- overlap signals
- design-around candidate directions
- recommended next actions

This is still a screening aid. A patent professional should review any
`HIGH`, `MEDIUM`, or ambiguous `LOW` result before a launch decision.
