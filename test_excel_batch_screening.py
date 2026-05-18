import asyncio
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from production.ingestion import read_patent_candidates
from production.orchestrator import ProductSpec, screen_product_candidate_batch


ROOT = Path(__file__).resolve().parent


async def main():
    product = ProductSpec.model_validate_json(
        (ROOT / "examples" / "product_alpha.json").read_text(encoding="utf-8")
    )

    csv_path = ROOT / "examples" / "patent_candidates.csv"
    csv_candidates = read_patent_candidates(str(csv_path))
    assert len(csv_candidates) == 3

    csv_report = await screen_product_candidate_batch(product, csv_candidates, str(csv_path))
    print(csv_report.model_dump_json(indent=2))
    assert csv_report.total_candidates == 3
    assert csv_report.screened_count == 3
    assert csv_report.summary_by_grade["LOW"] >= 1
    assert any(report.design_around_options for report in csv_report.reports)

    xlsx_path = ROOT / "build_log" / "test_patent_candidates.xlsx"
    _write_minimal_xlsx(
        xlsx_path,
        [
            ["candidate_id", "title", "claim_text", "keywords"],
            [
                "xlsx_candidate_ph",
                "XLSX pH case",
                "1. A method comprising contacting a substrate with an enzyme.\n"
                "2. The method of claim 1, wherein the enzyme is active at pH 6-8.",
                "pH;enzyme",
            ],
        ],
    )
    try:
        xlsx_candidates = read_patent_candidates(str(xlsx_path))
        assert len(xlsx_candidates) == 1
        xlsx_report = await screen_product_candidate_batch(product, xlsx_candidates, str(xlsx_path))
        assert xlsx_report.screened_count == 1
        assert xlsx_report.reports[0].overall_grade.value == "LOW"
    finally:
        xlsx_path.unlink(missing_ok=True)


def _write_minimal_xlsx(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row, start=1):
            ref = f"{_column_name(column_index)}{row_index}"
            cells.append(
                f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
            )
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheets><sheet name="Sheet1" sheetId="1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>',
        )


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


if __name__ == "__main__":
    asyncio.run(main())
