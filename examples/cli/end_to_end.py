"""Run the CLI from init through packaging using synthetic text in a temp tree."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from localization_toolkit.cli import main


def run() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="localize-cli-e2e-") as temporary:
        root = Path(temporary) / "synthetic-project"
        if main(["init", str(root), "--name", "Synthetic CLI Project"]) != 0:
            raise RuntimeError("init failed")
        config_path = root / "localize.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["inputs"]["scripts"] = [{"id": "demo", "source": "input/demo.txt", "output": "scripts/demo.mg"}]
        config["package"]["files"] = [{"build_path": "scripts/demo.mg", "archive_path": "scripts/demo.mg"}]
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (root / "input" / "demo.txt").write_text(
            'OutputLineAll("synthetic source with {name}", "synthetic reference with {name}", 0);\n',
            encoding="utf-8")

        project_arg = ["--project", str(root)]
        results: dict[str, object] = {}
        for command in ("inspect", "extract", "translate"):
            results[command] = main([command, *project_arg])
            if results[command] != 0:
                raise RuntimeError(f"{command} failed")

        state = json.loads((root / ".localize" / "state.json").read_text(encoding="utf-8"))
        slot_ids = state["stages"]["extract"]["scripts"][0]["slot_ids"]
        # Each approval is a separate explicit review action; the provider never approves itself.
        for slot_id in slot_ids:
            if main(["review", *project_arg, "--approve-slot", slot_id, "--reviewer", "synthetic-demo-reviewer"]) != 0:
                raise RuntimeError(f"review failed for {slot_id}")

        for command in ("compile", "verify", "package", "verify", "status"):
            results[command] = main([command, *project_arg])
            if results[command] != 0:
                raise RuntimeError(f"{command} failed")
        state = json.loads((root / ".localize" / "state.json").read_text(encoding="utf-8"))
        return {"status": "passed", "slots": state["stages"]["extract"]["total_slots"],
                "reviewed_slots": state["stages"]["review"]["approved"],
                "package_sha256": state["stages"]["package"]["archive_sha256"],
                "steps": list(results), "workspace": "temporary and removed on exit"}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
