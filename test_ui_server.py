import base64
import json
from pathlib import Path

from ui.server import (
    ROOT,
    get_report_payload,
    list_reports_payload,
    run_example_payload,
    run_screening_payload,
)


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
        "user_id": "smoke_user_a",
    }
    response = run_screening_payload(payload)
    report = response["report"]
    print(response["report_path"])
    print(report["summary_by_grade"])
    assert report["total_candidates"] == 4
    assert report["screened_count"] == 4
    assert report["failed_candidates"] == []
    saved_path = Path(ROOT / response["report_path"])
    assert saved_path.exists()
    assert response["user_id"] == "smoke_user_a"
    saved = json.loads(saved_path.read_text(encoding="utf-8"))
    assert saved["version"] == 2
    assert saved["user_id"] == "smoke_user_a"
    assert saved["product_id"] == "ui_smoke_product"
    assert saved["report"]["screened_count"] == 4

    example = run_example_payload(user_id="smoke_user_b")
    example_report = example["report"]
    print(example_report["summary_by_grade"])
    assert example_report["screened_count"] == 1
    assert example_report["reports"][0]["claim_results"][0]["sequence_comparisons"][0]["threshold_met"] is True
    assert example["user_id"] == "smoke_user_b"

    # GET /api/reports — list endpoint
    listing = list_reports_payload({})
    assert listing["total"] >= 2
    run_ids = {item["run_id"] for item in listing["reports"]}
    assert response["run_id"] in run_ids
    assert example["run_id"] in run_ids

    # Filter by user_id
    user_a = list_reports_payload({"user_id": "smoke_user_a"})
    assert any(item["run_id"] == response["run_id"] for item in user_a["reports"])
    assert all(item.get("user_id") == "smoke_user_a" for item in user_a["reports"])

    # Filter by product_id substring (case insensitive)
    by_product = list_reports_payload({"product_id": "SMOKE_PRODUCT"})
    assert any(item["run_id"] == response["run_id"] for item in by_product["reports"])

    # Filter by grade — example produced a HIGH grade
    by_grade = list_reports_payload({"grade": "HIGH"})
    assert any(item["run_id"] == example["run_id"] for item in by_grade["reports"])

    # GET /api/reports/<run_id> — retrieval
    fetched = get_report_payload(example["run_id"])
    assert fetched["run_id"] == example["run_id"]
    assert fetched["user_id"] == "smoke_user_b"
    assert fetched["report"]["screened_count"] == 1

    # Invalid run_id is rejected
    try:
        get_report_payload("../etc/passwd")
        raise AssertionError("path traversal should be rejected")
    except Exception as exc:
        assert "Invalid" in str(exc) or "not found" in str(exc).lower()

    print("ok:", len(listing["reports"]), "reports indexed")


if __name__ == "__main__":
    main()
