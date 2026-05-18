# Product Screening Workflow

This workflow lets a user screen one structured product against one candidate
patent text file or one existing mock patent ID.

## Product Input

Create a JSON file with product assumptions:

```json
{
  "product_id": "enzyme_product_alpha",
  "identity": 88.0,
  "ph": 7.0,
  "enzyme_class": "protease",
  "variant": "A123V",
  "jurisdiction": "US"
}
```

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
connector reads claims from Google Patents and writes fetched claim text under
`data/patent_cache/web/` for later local reuse. Use this as a convenience
connector, not as the authoritative legal record.

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

Then open `http://127.0.0.1:8765`. The UI accepts product assumptions and a
candidate `.csv` or `.xlsx`, then shows patent-level risk, claim-level detail,
and design-around candidate directions. The `웹 예시 실행` button uses
`examples/patent_candidates_web.csv` and enables public-web claim fetching.

## Output

The report contains:

- overall grade and rationale
- claim-level grades
- extracted claim features
- overlap signals
- design-around candidate directions
- recommended next actions

This is still a screening aid. A patent professional should review any
`HIGH`, `MEDIUM`, or ambiguous `LOW` result before a launch decision.
