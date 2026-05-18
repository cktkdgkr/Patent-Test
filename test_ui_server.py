import base64
from pathlib import Path

from ui.server import ROOT, run_example_payload, run_screening_payload


def main():
    candidates_path = ROOT / "examples" / "patent_candidates.csv"
    payload = {
        "product": {
            "product_id": "ui_smoke_product",
            "enzyme_name": "UI smoke enzyme",
            "identity": 80,
            "ph": 7,
            "temperature_c": 45,
            "enzyme_class": "protease",
            "substrate": "protein substrate",
        },
        "file": {
            "name": "patent_candidates.csv",
            "content_base64": base64.b64encode(candidates_path.read_bytes()).decode("ascii"),
        },
        "enable_web_fetch": False,
    }
    response = run_screening_payload(payload)
    report = response["report"]
    print(response["report_path"])
    print(report["summary_by_grade"])
    assert report["total_candidates"] == 4
    assert report["screened_count"] == 4
    assert report["failed_candidates"] == []
    assert Path(ROOT / response["report_path"]).exists()

    example = run_example_payload()
    example_report = example["report"]
    print(example_report["summary_by_grade"])
    assert example_report["screened_count"] == 1
    assert example_report["reports"][0]["claim_results"][0]["sequence_comparisons"][0]["threshold_met"] is True


if __name__ == "__main__":
    main()
