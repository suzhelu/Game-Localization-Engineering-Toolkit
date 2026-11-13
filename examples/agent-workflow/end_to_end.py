"""Run a complete, synthetic localization workflow through the existing CLI."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from localization_toolkit.cli import main
from localization_toolkit.game_analysis import validate_game_analysis_report

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_SOURCE = 'OutputLineAll("Welcome, {player}!", "Reference greeting for {player}.", 0);\n'
SAMPLE_TRANSLATION = "欢迎，{player}！"


def _call(args: list[str], *, expect: int = 0) -> dict[str, Any]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(args)
    raw = stdout.getvalue() if code == 0 else stderr.getvalue()
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"CLI did not return JSON for {args[0]}: {raw or exc}") from exc
    if code != expect:
        raise RuntimeError(f"CLI {args[0]} returned {code}, expected {expect}: {result}")
    return result


def _analysis_report() -> dict[str, Any]:
    report = json.loads((ROOT / "examples/game-analysis/reports/game-analysis.json").read_text(encoding="utf-8"))
    report["game"] = "Synthetic Lantern Project"
    report["platform"] = "SyntheticFixture"
    report["engine"] = {"name": "unknown", "evidence_level": "unknown"}
    report["evidence"] = [
        {"claim": "A synthetic UTF-8 script fixture is present.", "level": "confirmed", "source": "generated fixture bytes"},
        {"claim": "The registered BGI adapter reports the synthetic call as supported.", "level": "inferred", "source": "read-only adapter probe output"},
    ]
    report["resource_candidates"] = [
        {"path": "input/demo.txt", "kind": "synthetic script fixture", "evidence_level": "confirmed"}
    ]
    report["encoding"] = {"value": "UTF-8", "evidence_level": "confirmed"}
    report["reusable_components"] = ["registered BGI adapter", "existing CLI and Mock Compiler"]
    report["risks"] = ["This fixture proves no compatibility with any real game."]
    report["unknowns"] = ["Real engine, runtime rendering, font coverage, and compiler compatibility remain unknown."]
    report["next_actions"] = ["Use only synthetic inputs in this demonstration.", "Keep Stage 9 runtime_unverified."]
    report["stages"] = {f"stage_{n}": "not_started" for n in range(11)}
    report["stages"]["stage_0"] = "passed"
    report["stages"]["stage_1"] = "passed"
    report["stages"]["stage_9"] = "runtime_unverified"
    report["runtime_evidence"] = []
    validate_game_analysis_report(report)
    return report


def run(workspace: str | Path | None = None) -> dict[str, Any]:
    owned_temp: tempfile.TemporaryDirectory[str] | None = None
    if workspace is None:
        owned_temp = tempfile.TemporaryDirectory(prefix="localize-agent-workflow-")
        base = Path(owned_temp.name)
    else:
        base = Path(workspace).resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
        if base != temp_root and temp_root not in base.parents:
            raise ValueError("The demonstration workspace must resolve inside the operating system temporary directory")
        if base.exists() and any(base.iterdir()):
            raise ValueError(f"Refusing to use a non-empty demonstration workspace: {base}")
        base.mkdir(parents=True, exist_ok=True)

    project = base / "synthetic-project"
    project_arg = ["--project", str(project)]
    try:
        _call(["init", str(project), "--name", "Synthetic Lantern Project"])
        source_path = project / "input" / "demo.txt"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(SAMPLE_SOURCE.encode("utf-8"))
        original = source_path.read_bytes()

        config_path = project / "localize.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["inputs"]["scripts"] = [{"id": "demo", "source": "input/demo.txt", "output": "scripts/demo.mg"}]
        config["package"]["files"] = [{"build_path": "scripts/demo.mg", "archive_path": "scripts/demo.mg"}]
        config["deployment"]["enabled"] = False
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        _call(["inspect", *project_arg])
        adapters = _call(["adapters"])
        if not any(row["adapter_id"] == "bgi" for row in adapters["registered"]):
            raise RuntimeError("Expected registered BGI adapter is unavailable")
        probe = _call(["probe", str(source_path), "--adapter", "bgi"])
        if probe.get("status") != "supported":
            raise RuntimeError(f"Synthetic probe did not support the fixture: {probe}")

        report = _analysis_report()
        report["evidence"][1] = {
            "claim": "The registered BGI adapter reports the synthetic call as supported.",
            "level": "confirmed",
            "source": "actual localize probe output for this generated fixture",
        }
        validate_game_analysis_report(report)
        reports = project / "reports"
        reports.mkdir()
        (reports / "game-analysis.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (reports / "game-analysis.md").write_text(
            "# Synthetic game analysis\n\n"
            "- Engine: unknown; no real-game compatibility claim.\n"
            "- Fixture: generated UTF-8 BGI-like text call.\n"
            "- Adapter selection: explicit registered `bgi`, based on the read-only probe.\n"
            "- Runtime: untested; Stage 9 remains `runtime_unverified`.\n",
            encoding="utf-8",
        )

        extracted = _call(["extract", *project_arg])
        exported_path = "exchange/tasks.jsonl"
        _call(["translate-export", *project_arg, exported_path])
        task_lines = (project / exported_path).read_text(encoding="utf-8").splitlines()
        if len(task_lines) != extracted["slots"]:
            raise RuntimeError("Exported task count does not match extracted slot count")
        tasks = [json.loads(line) for line in task_lines]
        results_path = project / "exchange" / "results.jsonl"
        results_path.parent.mkdir(exist_ok=True)
        results = [{"schema_version": 1, "task_id": row["task_id"], "slot_id": row["slot_id"],
                    "source_fingerprint": row["source_fingerprint"], "task_fingerprint": row["task_fingerprint"],
                    "translated_text": SAMPLE_TRANSLATION} for row in tasks]
        results_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results), encoding="utf-8")
        imported = _call(["translate-import", *project_arg, "exchange/results.jsonl"])
        if imported.get("pending_review") != len(tasks):
            raise RuntimeError("Imported translations did not remain pending human review")

        blocked = _call(["compile", *project_arg], expect=2)
        if blocked.get("error", {}).get("code") is None:
            raise RuntimeError("Unreviewed compile failure did not contain a structured error")

        # Test-only action: this synthetic fixture approval is not a real human review.
        review_results = []
        for task in tasks:
            review_results.append(_call(["review", *project_arg, "--approve-slot", task["slot_id"],
                                         "--reviewer", "SYNTHETIC_TEST_ONLY", "--note", "Synthetic demo gate only"]))

        compiled = _call(["compile", *project_arg])
        _call(["verify", *project_arg])
        packaged = _call(["package", *project_arg])
        final_verify = _call(["verify", *project_arg])
        status = _call(["status", *project_arg])
        if source_path.read_bytes() != original:
            raise RuntimeError("Original synthetic input changed during workflow")
        if report["stages"]["stage_9"] != "runtime_unverified" or config["deployment"]["enabled"]:
            raise RuntimeError("Runtime or deployment safety state is incorrect")

        package_dir = Path(packaged["package_directory"])
        manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
        with zipfile.ZipFile(package_dir / "package.zip") as archive:
            archive_names = sorted(archive.namelist())
        manifest_names = sorted(row["path"] for row in manifest["files"])
        if archive_names != manifest_names:
            raise RuntimeError("ZIP entries do not match the package manifest")

        final = {
            "schema_version": 1,
            "status": "passed",
            "synthetic_only": True,
            "engine_claim": "unknown",
            "adapter": "bgi",
            "provider": "prewritten synthetic translation; no model/network call",
            "compiler": "Mock Compiler; no real game compiler used",
            "slots": extracted["slots"],
            "pending_review_before_test_approval": imported["pending_review"],
            "unreviewed_compile_blocked": blocked["error"]["code"],
            "synthetic_approval_count": len(review_results),
            "compiled_artifacts": compiled["artifacts"],
            "static_verify": final_verify["compiled_manifest"],
            "package_files": len(manifest_names),
            "package_sha256": packaged["archive_sha256"],
            "input_sha256_unchanged": hashlib.sha256(original).hexdigest().upper() == hashlib.sha256(source_path.read_bytes()).hexdigest().upper(),
            "stage_9": "runtime_unverified",
            "deployment_enabled": False,
            "operation_count": len(status["recent_operations"]),
            "artifacts": {"analysis_json": "reports/game-analysis.json", "analysis_markdown": "reports/game-analysis.md",
                          "package_directory": str(package_dir.relative_to(project)),
                          "operation_log": ".localize/state.json", "recovery_notes": "reports/recovery-notes.md"},
        }
        (reports / "final-report.json").write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (reports / "recovery-notes.md").write_text(
            "# Synthetic recovery notes\n\n"
            "No deployment was requested or performed; deployment remains disabled and no target directory was configured. "
            "The operation log is `.localize/state.json`. For a future explicitly authorized synthetic deployment, inspect "
            "the transaction with `localize recover` first and follow the transaction guide. Stop for unknown hashes, "
            "damaged backups, or ambiguous state; do not force recovery. This demo has no game files to restore.\n",
            encoding="utf-8",
        )
        return final
    finally:
        if owned_temp is not None:
            owned_temp.cleanup()


def main_cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", help="Optional new/empty directory for persistent synthetic outputs")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.workspace), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"schema_version": 1, "status": "failed", "error": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False), file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main_cli())
