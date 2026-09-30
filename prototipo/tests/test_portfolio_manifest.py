import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_manifest_v2_and_public_links():
    manifest = json.loads((ROOT / "portfolio.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["classification"] == "applied-academic-project"
    assert {link["type"] for link in manifest["links"]} >= {
        "live_demo", "case_study", "architecture", "github"
    }
    for item in manifest["evidence"]:
        assert (ROOT / item["path"]).exists(), item["path"]

