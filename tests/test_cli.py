from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from localization_toolkit.cli import main
from localization_toolkit.compilation import MockCompilerAdapter


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="localize-cli-synthetic-")
        self.root = Path(self.temp.name) / "project"
        self.assertEqual(self.call("init", str(self.root), "--name", "Synthetic Project"), 0)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, *args):
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        with contextlib.redirect_stdout(self.stdout), contextlib.redirect_stderr(self.stderr):
            code = main(list(args))
        return code

    def read_config(self):
        return json.loads((self.root / "localize.json").read_text(encoding="utf-8"))

    def write_config(self, config):
        (self.root / "localize.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def configure_script(self, *, source=None, package=True):
        config = self.read_config()
        config["inputs"]["scripts"] = [{"id": "demo", "source": "input/demo.txt", "output": "scripts/demo.mg"}]
        if package:
            config["package"]["files"] = [{"build_path": "scripts/demo.mg", "archive_path": "scripts/demo.mg"}]
        self.write_config(config)
        (self.root / "input" / "demo.txt").write_text(
            source or 'OutputLineAll("synthetic source {name}", "synthetic reference {name}", 0);\n', encoding="utf-8")

    @property
    def project_arg(self):
        return ("--project", str(self.root))

    def extract_translate(self):
        self.assertEqual(self.call("extract", *self.project_arg), 0, self.stderr.getvalue())
        self.assertEqual(self.call("translate", *self.project_arg), 0, self.stderr.getvalue())

    def approve_slots(self):
        state = json.loads((self.root / ".localize" / "state.json").read_text(encoding="utf-8"))
        slots = state["stages"]["extract"]["scripts"][0]["slot_ids"]
        for slot_id in slots:
            self.assertEqual(self.call("review", *self.project_arg, "--approve-slot", slot_id,
                                       "--reviewer", "synthetic-reviewer"), 0, self.stderr.getvalue())
        return slots

    def complete_to_package(self):
        self.configure_script()
        self.extract_translate()
        slots = self.approve_slots()
        self.assertEqual(self.call("compile", *self.project_arg), 0, self.stderr.getvalue())
        self.assertEqual(self.call("verify", *self.project_arg), 0, self.stderr.getvalue())
        self.assertEqual(self.call("package", *self.project_arg), 0, self.stderr.getvalue())
        return slots

    def test_init_inspect_and_no_overwrite(self):
        self.assertEqual(self.call("inspect", *self.project_arg), 0)
        self.assertIn('"adapter": "bgi"', self.stdout.getvalue())
        original = (self.root / "localize.json").read_bytes()
        self.assertEqual(self.call("init", str(self.root), "--name", "Overwrite"), 2)
        self.assertEqual((self.root / "localize.json").read_bytes(), original)

    def test_unknown_adapter_and_illegal_path_are_rejected(self):
        config = self.read_config()
        config["general"]["adapter"] = "imaginary"
        self.write_config(config)
        self.assertEqual(self.call("inspect", *self.project_arg), 2)
        self.assertIn("Unknown text adapter", self.stderr.getvalue())
        config["general"]["adapter"] = "bgi"
        config["inputs"]["scripts"] = [{"id": "escape", "source": "../outside.txt", "output": "escape.mg"}]
        self.write_config(config)
        self.assertEqual(self.call("inspect", *self.project_arg), 2)
        self.assertIn("Unsafe project path", self.stderr.getvalue())

    def test_unsafe_script_id_and_duplicate_archive_paths_are_rejected(self):
        config = self.read_config()
        config["inputs"]["scripts"] = [{"id": "../escape", "source": "input/demo.txt", "output": "out.mg"}]
        self.write_config(config)
        self.assertEqual(self.call("inspect", *self.project_arg), 2)
        self.assertIn("safe path tokens", self.stderr.getvalue())
        config["inputs"]["scripts"] = []
        config["package"]["files"] = [
            {"build_path": "one.mg", "archive_path": "same/file.mg"},
            {"build_path": "two.mg", "archive_path": "SAME/file.mg"},
        ]
        self.write_config(config)
        self.assertEqual(self.call("inspect", *self.project_arg), 2)
        self.assertIn("Package archive paths must be unique", self.stderr.getvalue())

    def test_init_template_keeps_deployment_disabled(self):
        config = self.read_config()
        self.assertFalse(config["deployment"]["enabled"])
        self.assertIsNone(config["deployment"]["target_root"])
        self.assertEqual(self.call("deploy", *self.project_arg, "--apply", "--confirm-project", "Synthetic Project"), 0)
        self.assertIn('"status": "plan_only"', self.stdout.getvalue())

    def test_online_provider_is_not_silently_accepted(self):
        config = self.read_config()
        config["translation"]["provider"] = "remote-service"
        self.write_config(config)
        self.assertEqual(self.call("inspect", *self.project_arg), 2)
        self.assertIn("only provider='mock'", self.stderr.getvalue())

    def test_translate_deduplicates_repeat_and_never_auto_approves(self):
        self.configure_script()
        self.assertEqual(self.call("extract", *self.project_arg), 0)
        self.assertEqual(self.call("translate", *self.project_arg), 0)
        first = json.loads(self.stdout.getvalue())
        self.assertEqual(first["pending_review"], 1)
        self.assertEqual(first["approved"], 0)
        self.assertEqual(self.call("translate", *self.project_arg), 0)
        second = json.loads(self.stdout.getvalue())
        self.assertEqual(second["provider_calls"], 0)
        self.assertEqual(second["approved"], 0)

    def test_compile_blocks_unreviewed_text_then_compiles_after_explicit_approval(self):
        self.configure_script()
        self.extract_translate()
        self.assertEqual(self.call("compile", *self.project_arg), 2)
        self.assertIn("Human review gate is incomplete", self.stderr.getvalue())
        self.assertFalse((self.root / "build").exists() and any((self.root / "build").iterdir()))
        self.approve_slots()
        self.assertEqual(self.call("compile", *self.project_arg), 0, self.stderr.getvalue())
        result = json.loads(self.stdout.getvalue())
        self.assertEqual(result["artifacts"], 1)

    def test_full_pipeline_to_verified_package(self):
        self.configure_script()
        self.extract_translate()
        self.approve_slots()
        self.assertEqual(self.call("compile", *self.project_arg), 0, self.stderr.getvalue())
        self.assertEqual(self.call("verify", *self.project_arg), 0, self.stderr.getvalue())
        self.assertEqual(self.call("package", *self.project_arg), 0, self.stderr.getvalue())
        first = json.loads(self.stdout.getvalue())
        self.assertEqual(first["file_count"], 1)
        package_path = Path(first["package_directory"])
        self.assertEqual(self.call("verify", *self.project_arg), 0, self.stderr.getvalue())
        self.assertIn('"package": "passed"', self.stdout.getvalue())
        self.assertTrue((package_path / "package.zip").is_file())

    def test_stale_source_and_glossary_invalidate_upstream_stages(self):
        self.configure_script()
        self.extract_translate()
        source_path = self.root / "input" / "demo.txt"
        source_path.write_text('OutputLineAll("changed source", "changed reference", 0);\n', encoding="utf-8")
        self.assertEqual(self.call("translate", *self.project_arg), 2)
        self.assertIn("stale", self.stderr.getvalue())
        self.assertEqual(self.call("status", *self.project_arg), 0)
        self.assertIn('"extract": {\n      "completed_at":', self.stdout.getvalue())
        config = self.read_config()
        config["inputs"]["scripts"][0]["source"] = "input/demo.txt"
        self.write_config(config)
        self.assertEqual(self.call("extract", *self.project_arg), 0)
        glossary = self.root / "config" / "glossary.json"
        glossary.write_text('{"version":"v2","entries":[]}\n', encoding="utf-8")
        self.assertEqual(self.call("translate", *self.project_arg), 2)

    def test_review_requires_explicit_reviewer_and_known_slot(self):
        self.configure_script()
        self.extract_translate()
        state = json.loads((self.root / ".localize" / "state.json").read_text(encoding="utf-8"))
        slot_id = state["stages"]["extract"]["scripts"][0]["slot_ids"][0]
        self.assertEqual(self.call("review", *self.project_arg, "--approve-slot", slot_id), 2)
        self.assertIn("reviewer identifier is required", self.stderr.getvalue())
        self.assertEqual(self.call("review", *self.project_arg, "--approve-slot", "unknown", "--reviewer", "r"), 2)

    def test_config_compiler_failure_is_not_reported_as_success(self):
        self.configure_script()
        self.extract_translate()
        self.approve_slots()
        with patch("localization_toolkit.cli.workflow._compiler", return_value=MockCompilerAdapter("mock-1", lambda request: (17, "", "synthetic error"))):
            self.assertEqual(self.call("compile", *self.project_arg), 2)
        self.assertIn("Build did not publish", self.stderr.getvalue())
        state = json.loads((self.root / ".localize" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["operations"][-1]["status"], "failed")

    def test_configured_mock_compiler_can_omit_output_and_fail_closed(self):
        self.configure_script()
        self.extract_translate()
        self.approve_slots()
        with patch("localization_toolkit.cli.workflow._compiler", return_value=MockCompilerAdapter("mock-1", lambda request: (0, "", ""))):
            self.assertEqual(self.call("compile", *self.project_arg), 2)
        self.assertIn("output file is missing", self.stderr.getvalue())

    def test_invalid_package_selection_blocks_without_scanning_tree(self):
        self.configure_script(package=False)
        self.extract_translate()
        self.approve_slots()
        self.assertEqual(self.call("compile", *self.project_arg), 0)
        self.assertEqual(self.call("package", *self.project_arg), 2)
        self.assertIn("explicitly list authorized build paths", self.stderr.getvalue())

    def test_stale_compiler_configuration_requires_rebuild(self):
        self.complete_to_package()
        config = self.read_config()
        config["compiler"]["version"] = "mock-2"
        self.write_config(config)
        self.assertEqual(self.call("verify", *self.project_arg), 2)
        self.assertIn("stale", self.stderr.getvalue())
        self.assertEqual(self.call("status", *self.project_arg), 0)
        self.assertIn('"compile": {\n      "completed_at":', self.stdout.getvalue())

    def test_status_reports_failed_operation_and_recommended_next_step(self):
        self.configure_script()
        self.assertEqual(self.call("extract", *self.project_arg), 0)
        (self.root / "input" / "demo.txt").write_text("unparseable synthetic input", encoding="utf-8")
        self.assertEqual(self.call("translate", *self.project_arg), 2)
        self.assertEqual(self.call("status", *self.project_arg), 0)
        self.assertIn('"status": "failed"', self.stdout.getvalue())
        self.assertIn('"recommended_next_steps": [\n    "extract"', self.stdout.getvalue())

    def test_corrupted_package_is_reported_by_verify(self):
        self.complete_to_package()
        state = json.loads((self.root / ".localize" / "state.json").read_text(encoding="utf-8"))
        package_root = self.root / state["stages"]["package"]["directory"]
        with (package_root / "package.zip").open("ab") as stream:
            stream.write(b"corrupt")
        self.assertEqual(self.call("verify", *self.project_arg), 2)
        self.assertIn("SHA-256", self.stderr.getvalue())

    def test_interrupted_operation_is_visible_and_stage_can_resume(self):
        self.configure_script()
        self.assertEqual(self.call("extract", *self.project_arg), 0)
        state_path = self.root / ".localize" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["operations"].append({"operation_id": "interrupted", "command": "translate", "status": "running",
                                     "started_at": "synthetic", "configuration_fingerprint": "fixture", "error": None, "summary": None})
        state_path.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.call("status", *self.project_arg), 0)
        self.assertIn('"status": "running"', self.stdout.getvalue())
        self.assertEqual(self.call("translate", *self.project_arg), 0)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["operations"][-1]["status"], "succeeded")

    def test_status_recommends_review_before_compile(self):
        self.configure_script()
        self.extract_translate()
        self.assertEqual(self.call("status", *self.project_arg), 0)
        self.assertIn('"recommended_next_steps": [\n    "review"', self.stdout.getvalue())

    def test_recovery_requires_explicit_deployment_configuration(self):
        self.assertEqual(self.call("recover", *self.project_arg), 2)
        self.assertIn("explicitly configured deployment", self.stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
