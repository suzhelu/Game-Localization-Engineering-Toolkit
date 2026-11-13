from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from localization_toolkit.cli import main, parser
from localization_toolkit.game_analysis import validate_game_analysis_report
from localization_toolkit.compilation import MockCompilerAdapter

REPO = Path(__file__).parents[1]
_DEMO_PATH = Path(__file__).parents[1] / "examples/agent-workflow/end_to_end.py"
_SPEC = importlib.util.spec_from_file_location("synthetic_agent_workflow", _DEMO_PATH)
assert _SPEC and _SPEC.loader
_DEMO = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_DEMO)
run = _DEMO.run


class AgentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="localize-agent-e2e-test-")
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.call("init", str(self.project), "--name", "Synthetic Agent Test")
        self.source = self.project / "input" / "sample.txt"
        self.source.write_text('OutputLineAll("Hello {user}", "Reference {user}", 0);\n', encoding="utf-8")
        config_path = self.project / "localize.json"
        self.config = json.loads(config_path.read_text(encoding="utf-8"))
        self.config["inputs"]["scripts"] = [{"id": "sample", "source": "input/sample.txt", "output": "out/sample.mg"}]
        self.config["package"]["files"] = [{"build_path": "out/sample.mg", "archive_path": "out/sample.mg"}]
        config_path.write_text(json.dumps(self.config, indent=2) + "\n", encoding="utf-8")
        self.project_args = ["--project", str(self.project)]

    def tearDown(self):
        self.temp.cleanup()

    def call(self, *args, expect=0):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(args))
        raw = out.getvalue() if code == 0 else err.getvalue()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"CLI returned non-JSON: {raw}") from exc
        self.assertEqual(code, expect, value)
        return value

    def prepare_imported(self):
        self.call("extract", *self.project_args)
        self.call("translate-export", *self.project_args, "exchange/tasks.jsonl")
        task = json.loads((self.project / "exchange/tasks.jsonl").read_text(encoding="utf-8").splitlines()[0])
        result = {"schema_version": 1, "task_id": task["task_id"], "slot_id": task["slot_id"],
                  "source_fingerprint": task["source_fingerprint"], "task_fingerprint": task["task_fingerprint"],
                  "translated_text": "你好 {user}"}
        (self.project / "exchange/results.jsonl").write_text(json.dumps(result) + "\n", encoding="utf-8")
        return task, self.call("translate-import", *self.project_args, "exchange/results.jsonl")

    def test_full_synthetic_workflow_runs_end_to_end(self):
        result = run()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["stage_9"], "runtime_unverified")
        self.assertFalse(result["deployment_enabled"])

    def test_unreviewed_translation_is_blocked_from_compile(self):
        _, imported = self.prepare_imported()
        self.assertEqual(imported["pending_review"], 1)
        error = self.call("compile", *self.project_args, expect=2)
        self.assertEqual(error["schema_version"], 1)
        self.assertIn("error", error)

    def test_unsupported_adapter_is_rejected(self):
        self.config["general"]["adapter"] = "unknown-format"
        (self.project / "localize.json").write_text(json.dumps(self.config), encoding="utf-8")
        result = self.call("inspect", *self.project_args, expect=2)
        self.assertEqual(result["error"]["code"], "LOCALIZE_ERROR")

    def test_analysis_report_fixture_passes_schema(self):
        report = json.loads((REPO / "examples/game-analysis/reports/game-analysis.json").read_text(encoding="utf-8"))
        validate_game_analysis_report(report)
        self.assertEqual(report["engine"]["name"], "unknown")

    def test_changed_source_makes_exchange_import_stale(self):
        self.call("extract", *self.project_args)
        self.call("translate-export", *self.project_args, "exchange/tasks.jsonl")
        task = json.loads((self.project / "exchange/tasks.jsonl").read_text(encoding="utf-8").splitlines()[0])
        (self.project / "exchange/results.jsonl").write_text(json.dumps({
            "schema_version": 1, "task_id": task["task_id"], "slot_id": task["slot_id"],
            "source_fingerprint": task["source_fingerprint"], "task_fingerprint": task["task_fingerprint"],
            "translated_text": "你好 {user}"}) + "\n", encoding="utf-8")
        self.source.write_text('OutputLineAll("Changed {user}", "Reference {user}", 0);\n', encoding="utf-8")
        error = self.call("translate-import", *self.project_args, "exchange/results.jsonl", expect=2)
        self.assertIn("stale", error["error"]["message"].lower())

    def test_compile_failure_prevents_package_publication(self):
        _, _ = self.prepare_imported()
        state = json.loads((self.project / ".localize/state.json").read_text(encoding="utf-8"))
        slot = state["stages"]["extract"]["scripts"][0]["slot_ids"][0]
        self.call("review", *self.project_args, "--approve-slot", slot, "--reviewer", "TEST_ONLY")
        with patch("localization_toolkit.cli.workflow._compiler",
                   return_value=MockCompilerAdapter("broken", lambda request: (9, "", "synthetic failure"))):
            self.call("compile", *self.project_args, expect=2)
        self.call("package", *self.project_args, expect=2)
        self.assertFalse((self.project / "releases").exists() and any((self.project / "releases").iterdir()))

    def test_static_failure_stops_and_deployment_remains_disabled(self):
        _, _ = self.prepare_imported()
        state = json.loads((self.project / ".localize/state.json").read_text(encoding="utf-8"))
        slot = state["stages"]["extract"]["scripts"][0]["slot_ids"][0]
        self.call("review", *self.project_args, "--approve-slot", slot, "--reviewer", "TEST_ONLY")
        self.call("compile", *self.project_args)
        state = json.loads((self.project / ".localize/state.json").read_text(encoding="utf-8"))
        compile_stage = state["stages"]["compile"]
        output_rel = compile_stage["artifacts"][0]["build_path"]
        artifact = self.project / compile_stage["build_directory"] / "artifacts" / output_rel
        artifact.write_bytes(b"corrupted synthetic build")
        self.call("verify", *self.project_args, expect=2)
        self.call("package", *self.project_args, expect=2)
        self.assertFalse((self.project / "releases").exists() and any((self.project / "releases").iterdir()))
        self.assertFalse(self.config["deployment"]["enabled"])
        self.assertFalse((self.project / "target").exists())

    def test_overlay_manifest_matches_zip_contents(self):
        workspace = self.base / "persistent-demo"
        report = run(workspace)
        project = workspace / "synthetic-project"
        package_dir = project / report["artifacts"]["package_directory"]
        manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
        with zipfile.ZipFile(package_dir / "package.zip") as archive:
            self.assertEqual(sorted(archive.namelist()), sorted(row["path"] for row in manifest["files"]))
        self.assertEqual(report["package_files"], len(manifest["files"]))
        self.assertEqual(len(report["package_sha256"]), 64)

    def test_stage_nine_stays_runtime_unverified(self):
        report = run()
        self.assertEqual(report["stage_9"], "runtime_unverified")

    def test_original_synthetic_input_is_unchanged(self):
        before = self.source.read_bytes()
        _, _ = self.prepare_imported()
        self.assertEqual(self.source.read_bytes(), before)

    def test_failed_workflow_operation_is_diagnosable(self):
        _, _ = self.prepare_imported()
        self.call("compile", *self.project_args, expect=2)
        state = json.loads((self.project / ".localize/state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["operations"][-1]["status"], "failed")
        self.assertTrue(state["operations"][-1]["error"])

    def test_no_implicit_deployment_target_or_write(self):
        config = json.loads((self.project / "localize.json").read_text(encoding="utf-8"))
        self.assertFalse(config["deployment"]["enabled"])
        self.assertIsNone(config["deployment"]["target_root"])
        result = self.call("deploy", *self.project_args)
        self.assertIn(result["status"], {"disabled", "plan_only", "precheck_failed"})
        self.assertFalse((self.project / "target").exists())

    def test_demo_rejects_workspace_outside_controlled_temp_root(self):
        with tempfile.TemporaryDirectory(prefix="localize-controlled-temp-") as temporary_root:
            temp_root = Path(temporary_root).resolve()
            outside = temp_root.parent / f"{temp_root.name}-outside"
            self.assertFalse(outside.exists())
            with patch.object(_DEMO.tempfile, "gettempdir", return_value=str(temp_root)):
                with self.assertRaisesRegex(ValueError, "temporary directory"):
                    run(outside)
            self.assertFalse(outside.exists())

    def test_demo_accepts_workspace_nested_under_controlled_temp_root(self):
        with tempfile.TemporaryDirectory(prefix="localize-controlled-temp-") as temporary_root:
            temp_root = Path(temporary_root).resolve()
            nested = temp_root / "nested" / "demo"
            with patch.object(_DEMO.tempfile, "gettempdir", return_value=str(temp_root)):
                report = run(nested)
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["stage_9"], "runtime_unverified")
            self.assertFalse(report["deployment_enabled"])
            self.assertTrue((nested / "synthetic-project" / "reports" / "final-report.json").is_file())

    def test_every_registered_cli_command_has_working_help(self):
        commands = parser()._subparsers._group_actions[0].choices
        for command_name in commands:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit) as raised:
                    main([command_name, "--help"])
            self.assertEqual(raised.exception.code, 0, command_name)
            self.assertTrue(out.getvalue(), command_name)

    def test_user_docs_cover_real_limits_and_workflow_entrypoints(self):
        docs = (REPO / "docs/quickstart-for-ai-users.md").read_text(encoding="utf-8")
        compatibility = (REPO / "docs/compatibility.md").read_text(encoding="utf-8")
        faq = (REPO / "docs/faq.md").read_text(encoding="utf-8")
        self.assertIn("py` launcher is optional", docs)
        self.assertIn("examples/agent-workflow/end_to_end.py", docs)
        self.assertIn("runtime acceptance", compatibility.lower())
        self.assertIn("Mock Compiler", compatibility)
        for question in ("Python", "OpenAI", "Codex", "human", "Steam", "another AI Agent"):
            self.assertIn(question.lower(), faq.lower())


if __name__ == "__main__":
    unittest.main()
