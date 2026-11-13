"""Validate a wholly synthetic unknown-game analysis report."""
import json
from pathlib import Path

from localization_toolkit.adapters import default_registry
from localization_toolkit.game_analysis import validate_game_analysis_report

ROOT = Path(__file__).parent


def main() -> None:
    report = json.loads((ROOT / "reports" / "game-analysis.json").read_text(encoding="utf-8"))
    validate_game_analysis_report(report)
    sample = (ROOT / "fixture" / "data" / "sample.fixture").read_bytes()
    # Inspect bytes as inert data. The extension is not used as compatibility evidence.
    probe = default_registry().probe(sample.decode("utf-8-sig"))
    assert probe["status"] == "unsupported"
    assert report["engine"]["evidence_level"] == "unknown"
    assert report["stages"]["stage_9"] == "runtime_unverified"
    print(json.dumps({"status": "passed", "schema_version": report["schema_version"],
                      "candidate_count": len(report["resource_candidates"]),
                      "registered_adapter_probe": probe["status"],
                      "format_support_claim": "none", "stage_9": report["stages"]["stage_9"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
