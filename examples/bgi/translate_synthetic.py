"""Run the BGI adapter against the adjacent synthetic-only sample."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from localization_toolkit.adapters.bgi import BgiAdapter, OpaqueCallSchema


HERE = Path(__file__).resolve().parent
source = (HERE / "synthetic_script.txt").read_text(encoding="utf-8")
adapter = BgiAdapter(opaque_commands={"LoadAsset": OpaqueCallSchema(1, frozenset({0}))})
result = adapter.extract(source, "synthetic_script.txt")

if result.unresolved_count:
    raise SystemExit(f"Unresolved parser structures: {result.issues}")

translations = {
    slot.slot_id: f"[示例译文] {slot.current_text}"
    for slot in result.slots
}
translated = adapter.write(result, translations)
with (HERE / "translated_output.txt").open("w", encoding="utf-8", newline="") as output:
    output.write(translated)
print(f"Wrote {len(result.slots)} synthetic slots to translated_output.txt")
