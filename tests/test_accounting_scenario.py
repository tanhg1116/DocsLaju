import hashlib
import json
from pathlib import Path

import pypdf
from PIL import Image


SCENARIO = Path(__file__).parent / "scenarios" / "laju_office_solutions"


def test_scenario_manifest_is_complete_and_privacy_safe():
    manifest = json.loads((SCENARIO / "scenario_manifest.json").read_text(encoding="utf-8"))
    files = {item["file"] for item in manifest["documents"]}
    generated = {f"documents/{path.name}" for path in (SCENARIO / "documents").iterdir()}

    assert manifest["scenario_id"] == "laju-office-solutions-2026-08"
    assert len(files) == 27
    assert files == generated
    assert all("documents/" in name for name in files)


def test_scenario_documents_have_expected_media_and_page_counts():
    manifest = json.loads((SCENARIO / "scenario_manifest.json").read_text(encoding="utf-8"))
    for item in manifest["documents"]:
        path = SCENARIO / item["file"]
        if path.name == "42_damaged_supplier_attachment.pdf":
            try:
                pypdf.PdfReader(path)
            except Exception:
                pass
            else:
                raise AssertionError("The controlled damaged-PDF fixture must stay invalid")
        elif path.suffix.lower() == ".pdf":
            reader = pypdf.PdfReader(path)
            assert len(reader.pages) == int(item.get("pages", 1))
        else:
            with Image.open(path) as image:
                image.verify()


def test_scenario_contains_exact_and_visual_duplicate_controls():
    documents = SCENARIO / "documents"
    original = (documents / "12_receipt_parking_R-PARK-229.png").read_bytes()
    exact = (documents / "13_receipt_parking_exact_duplicate.png").read_bytes()
    recompressed = (documents / "14_receipt_parking_recompressed.jpg").read_bytes()

    assert hashlib.sha256(original).digest() == hashlib.sha256(exact).digest()
    assert hashlib.sha256(original).digest() != hashlib.sha256(recompressed).digest()
    with Image.open(documents / "12_receipt_parking_R-PARK-229.png") as first:
        with Image.open(documents / "14_receipt_parking_recompressed.jpg") as second:
            assert first.size == second.size


def test_every_expected_issue_only_references_scenario_documents():
    manifest = json.loads((SCENARIO / "scenario_manifest.json").read_text(encoding="utf-8"))
    issues = json.loads((SCENARIO / "expected_issues.json").read_text(encoding="utf-8"))
    document_names = {Path(item["file"]).name for item in manifest["documents"]}

    assert len(issues["issues"]) >= 10
    for issue in issues["issues"]:
        assert set(issue["documents"]) <= document_names
