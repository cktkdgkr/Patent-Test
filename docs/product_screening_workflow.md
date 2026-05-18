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
`patent_file`, or a local mock `patent_id`. Rows that only contain a real-world
publication number are preserved as candidates, but external patent fetching is
the next connector step.

```powershell
python scripts\screen_excel.py --product examples\product_alpha.json --excel examples\patent_candidates.csv --output build_log\batch_screening_report.json --summary-csv build_log\batch_screening_summary.csv
```

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
