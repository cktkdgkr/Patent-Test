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
- `country_code` (optional — see "Country codes" below)
- `publication_number`
- `application_number`
- `title`
- `claim_text`
- `patent_file`
- `keywords`

### Providing reference sequences (`reference_sequences` column)

When a patent recites `SEQ ID NO:1` but the actual amino acid string for
that SEQ ID is not in the fetched claim text and not in any public
sequence-listing mirror (common for very recent CN/KR/JP filings), the
screening workflow cannot align the product sequence against the patent
reference and falls back to a hedge MEDIUM grade with a "no sequence
comparison was performed" note.

To force a real comparison, add a `reference_sequences` column (Korean
header `참조 서열` / `참조서열` / `서열` also recognised) and paste the
patent's SEQ ID NO sequences directly. Three input formats are accepted:

1. FASTA blocks (Excel cells allow multi-line content with Alt+Enter)::

       >SEQ ID NO:1
       MKTAYIAKQRQISFVKSHFSRQEILDLIC
       >SEQ ID NO:2
       MKDPLNKAAVFGTHK

2. Inline `key=value` separated by `;`::

       SEQ ID NO:1=MKTAYIAKQRQISFVK; SEQ ID NO:2=MKDPLN

3. Plain `SEQ ID NO:N: SEQUENCE` lines.

User-supplied sequences override what we extract from the patent text or
fetch from the web. They appear in the report's `sequence_reference_sources`
map as `"user_csv_reference_sequences"` so reviewers can see where the
sequence came from.

When the column is left blank the workflow continues as before
(automatic extraction from the patent text + web fetch when enabled).
See `examples/patent_candidates_with_reference_sequences.csv` for a
ready-to-edit template.

### Country codes

When a row's `publication_number` or `application_number` lacks the leading
2-letter office prefix (for example `10-2020-0012345` instead of
`KR1020200012345`), populate `country_code` to disambiguate. The ingestion
layer accepts ISO 2-letter codes (`US`, `KR`, `EP`, `WO`, `JP`, `CN`, `GB`,
`DE`, `FR`, `CA`, `AU`, `IN`, `TW`) and common spellings in English or
Korean (`USA`, `미국`, `대한민국`, `한국`, `Japan`, `일본`, `China`, `중국`,
`europe`, `유럽`, `wipo`, `pct`, ...). Column header aliases are equally
permissive: `country_code`, `country code`, `country`, `cc`, `jurisdiction`,
`patent office`, `국가`, `국가코드`, `출원국`, `공개국가`.

When both fields are present, the screening workflow combines them as
`<CC><cleaned_number>` (for example `KR` + `10-2020-0012345` becomes
`KR1020200012345`) before resolving the local cache or calling the
public-web fetcher. Rows whose publication number already begins with a
2-letter prefix are preserved as-is and the column is ignored.

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

### HTTPS on corporate networks (SSL inspection)

On a corporate network that performs SSL inspection (Zscaler / Bluecoat /
Cisco Umbrella / similar), outbound `https://` requests reach Python with a
re-signed certificate from the company's internal CA rather than the upstream
provider's real certificate. A bare Python install does not trust that CA and
fails with `SSL: CERTIFICATE_VERIFY_FAILED` (often noting
`Missing Authority Key Identifier`).

Three resolution paths, in order of preference:

1. **Use the OS trust store via `truststore`** (recommended; automatic).
   `pip install -r requirements.txt` pulls in `truststore`. On Windows it
   exposes the certificates Windows already trusts — including any corporate
   root CA installed via group policy — so the harness fetches Google Patents
   without further configuration.
2. **Skip verification temporarily**. Set
   `PATENT_HARNESS_INSECURE_SSL=1` in the shell that launches
   `scripts/run_ui.py`. Outbound HTTPS will then skip certificate
   verification, with a warning logged on every fetch. Use only while a proper
   trust path is being arranged. PowerShell example:
   `$env:PATENT_HARNESS_INSECURE_SSL = "1"; python scripts\run_ui.py --port 8765`.
3. **Install the corporate root CA into the trust store**. Ask IT for the
   internal proxy/inspection root certificate (PEM/CRT) and import it into
   the Windows trust store (Manage user certificates → Trusted Root
   Certification Authorities). Re-run the harness; `truststore` will pick
   it up automatically.

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
