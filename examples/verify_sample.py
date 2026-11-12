"""Verify the synthetic example manifest from the repository root."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from localization_toolkit.manifest import load_manifest, validate_manifest  # noqa: E402

manifest = load_manifest(ROOT / "examples" / "sample_manifest.json")
issues = validate_manifest(manifest, ROOT / "examples")
if issues:
    raise SystemExit("Manifest verification failed: " + "; ".join(issues))
print(f"Verified {len(manifest['files'])} synthetic manifest entry.")
