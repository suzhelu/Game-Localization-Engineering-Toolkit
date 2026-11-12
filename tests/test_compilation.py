from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from localization_toolkit.adapters.bgi import BgiAdapter
from localization_toolkit.compilation import (
    BuildMode,
    BuildPlan,
    BuildTarget,
    CommandLineCompilerAdapter,
    MGSCV1Validator,
    MGSCValidationError,
    MockCompilerAdapter,
    ReviewedSourceError,
    build,
    capture_original_source,
    cleanup_abandoned_build,
    prepare_reviewed_source,
)
from localization_toolkit.translation import (
    Glossary,
    MockProvider,
    PromptTemplate,
    ProviderResult,
    TranslationStore,
    approve_translation,
    build_tasks,
    run_batches,
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def synthetic_mgsc(*, version: int = 1, block: str = "main", block_offset: int = 0,
                   line_offset: int = 0, payload: bytes = b"\x00") -> bytes:
    name = block.encode("utf-8")
    header = b"MGSC" + struct.pack("<iiii", version, 1, 1, len(payload))
    table = bytes([len(name)]) + name + struct.pack("<ii", block_offset, line_offset)
    return header + table + payload


class CompilationTestCase(unittest.TestCase):
    SOURCE = 'OutputLineAll("synthetic source", "synthetic reference", 0);\n'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="compile synthetic ")
        self.root = Path(self.temp.name)
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()
        self.output_parent = self.root / "outputs"
        self.output_parent.mkdir()
        self.report_dir = self.root / "reports"
        self.report_dir.mkdir()
        self.adapter = BgiAdapter()
        self.extraction = self.adapter.extract(self.SOURCE, "synthetic-script.txt")
        self.glossary = Glossary("synthetic-glossary-v1", ())
        self.template = PromptTemplate()
        self.store = TranslationStore(self.root / "state.json")
        self.valid_artifact = synthetic_mgsc()
        self.valid_hash = sha(self.valid_artifact)

    def tearDown(self):
        self.temp.cleanup()

    def translated_input(self, output_name: str = "translated.txt"):
        tasks = build_tasks(
            self.extraction.slots, glossary=self.glossary, prompt_template=self.template,
            source_language="synthetic-source", target_language="synthetic-target",
            rule_version="rules-v1", special_rules="Do not add facts.",
        )
        provider = MockProvider(lambda request: [
            ProviderResult(task.slot_id, "synthetic translation") for task in request.tasks
        ])
        batch = run_batches(
            self.extraction.slots, glossary=self.glossary, prompt_template=self.template,
            provider=provider, store=self.store,
            source_language="synthetic-source", target_language="synthetic-target",
            rule_version="rules-v1", special_rules="Do not add facts.",
        )
        self.assertEqual(batch["pending_review"], len(tasks))
        for task in tasks:
            approve_translation(task, store=self.store, glossary=self.glossary, reviewer="synthetic-reviewer")
        return prepare_reviewed_source(
            self.adapter, self.extraction,
            input_root=self.inputs, relative_path=output_name,
            glossary=self.glossary, prompt_template=self.template, store=self.store,
            source_language="synthetic-source", target_language="synthetic-target",
            rule_version="rules-v1", special_rules="Do not add facts.",
        )

    def control_input(self, output_name: str = "original.txt"):
        return capture_original_source(
            self.adapter, self.SOURCE, script_id="synthetic-script.txt",
            input_root=self.inputs, relative_path=output_name,
        )

    def compiler(self, *, data: bytes | None = None, code: int = 0, stdout: str = "ok", stderr: str = ""):
        artifact = self.valid_artifact if data is None else data

        def run(request):
            if code == 0 and artifact is not None:
                request.output_path.write_bytes(artifact)
            return code, stdout, stderr

        return MockCompilerAdapter("mock-1", run)

    def plan(self, source, *, name="build-out", expected_version="mock-1", expected_hash=None,
             index=None, output_path="script.mg", report=None, mode=None):
        mode = mode or source.purpose
        target = BuildTarget(source, output_path, mode, expected_hash)
        return BuildPlan(self.inputs, self.output_parent / name, (target,), expected_version,
                         timeout_seconds=2, expected_artifact_index=index,
                         report_path=report)

    def test_translated_source_requires_review_and_uses_safe_bgi_writeback(self):
        staged = self.translated_input()
        self.assertEqual(staged.purpose, BuildMode.TRANSLATED)
        self.assertEqual(len(staged.reviewer_ids), 1)
        self.assertEqual(staged.slot_ids, tuple(slot.slot_id for slot in self.extraction.slots))
        candidate = (self.inputs / staged.relative_path).read_text(encoding="utf-8")
        parsed = self.adapter.extract(candidate, "synthetic-script.txt")
        self.assertEqual(parsed.unresolved_count, 0)
        self.assertEqual(parsed.structure_signature, self.extraction.structure_signature)
        self.assertIn("synthetic translation", candidate)

    def test_missing_human_approval_blocks_source_preparation(self):
        with self.assertRaises(ReviewedSourceError):
            prepare_reviewed_source(
                self.adapter, self.extraction, input_root=self.inputs, relative_path="no-review.txt",
                glossary=self.glossary, prompt_template=self.template, store=self.store,
                source_language="x", target_language="y", rule_version="r1",
            )

    def test_control_source_must_be_structurally_resolved(self):
        bad = BgiAdapter().extract('UnknownCall("unclassified");', "unknown.txt")
        with self.assertRaises(ReviewedSourceError):
            capture_original_source(BgiAdapter(), bad.source, script_id=bad.script,
                                    input_root=self.inputs, relative_path="bad.txt")

    def test_success_report_separates_evidence_and_publishes_new_directory(self):
        source = self.translated_input()
        plan = self.plan(source, expected_hash=self.valid_hash)
        report = build(plan, compiler=self.compiler(), validators=(MGSCV1Validator(),))
        self.assertEqual(report.status, "succeeded")
        self.assertTrue((plan.output_directory / "artifacts" / "script.mg").is_file())
        item = report.items[0]
        self.assertEqual(item.evidence["human_approval"], "passed")
        self.assertEqual(item.evidence["compiler_exit"], "passed")
        self.assertEqual(item.evidence["output_format"], "passed")
        self.assertEqual(item.evidence["runtime"], "not_verified")
        self.assertTrue((plan.output_directory / "artifact_manifest.json").is_file())
        self.assertTrue((plan.output_directory / "build_report.json").is_file())
        marker = json.loads((plan.output_directory / ".localization-build-temp.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["status"], "validated")

    def test_original_roundtrip_requires_and_matches_pinned_baseline_hash(self):
        source = self.control_input()
        report = build(self.plan(source, expected_hash=self.valid_hash), compiler=self.compiler(),
                       validators=(MGSCV1Validator(),))
        self.assertEqual(report.status, "succeeded")
        self.assertEqual(report.items[0].evidence["original_roundtrip"], "passed")

    def test_original_roundtrip_mismatch_fails_and_does_not_publish(self):
        source = self.control_input()
        report = build(self.plan(source, expected_hash="0" * 64), compiler=self.compiler(),
                       validators=(MGSCV1Validator(),))
        self.assertEqual(report.status, "failed")
        self.assertIn("byte-identical", report.error)
        self.assertFalse((self.output_parent / "build-out").exists())

    def test_compiler_nonzero_exit_stops_and_persists_logs(self):
        source = self.translated_input()
        report_path = self.report_dir / "failed.json"
        report = build(self.plan(source, report=report_path), compiler=self.compiler(code=9, stderr="synthetic error"),
                       validators=(MGSCV1Validator(),))
        self.assertEqual(report.status, "failed")
        self.assertEqual(report.items[0].execution.exit_code, 9)
        self.assertIn("synthetic error", json.loads(report_path.read_text(encoding="utf-8"))["items"][0]["execution"]["stderr"])
        self.assertFalse(any(p.name.startswith(".locbuild-") for p in self.output_parent.iterdir()))

    def test_timeout_stops_build(self):
        source = self.translated_input()
        compiler = MockCompilerAdapter("mock-1", lambda request: (_ for _ in ()).throw(TimeoutError("timed out")))
        report = build(self.plan(source), compiler=compiler, validators=(MGSCV1Validator(),))
        self.assertEqual(report.items[0].execution.timed_out, True)
        self.assertEqual(report.status, "failed")

    def test_success_exit_without_output_is_failure(self):
        source = self.translated_input()
        compiler = MockCompilerAdapter("mock-1", lambda request: (0, "claimed success", ""))
        report = build(self.plan(source), compiler=compiler, validators=(MGSCV1Validator(),))
        self.assertIn("output file is missing", report.error)

    def test_compiler_extra_output_is_rejected_by_whitelist(self):
        source = self.translated_input()

        def compiler_with_extra(request):
            request.output_path.write_bytes(self.valid_artifact)
            (request.output_path.parent / "undeclared.bin").write_bytes(b"extra")
            return 0, "ok", ""

        report = build(self.plan(source), compiler=MockCompilerAdapter("mock-1", compiler_with_extra),
                       validators=(MGSCV1Validator(),))
        self.assertEqual(report.status, "failed")
        self.assertIn("output whitelist mismatch", report.error)

    def test_compiler_extra_empty_directory_is_rejected_by_whitelist(self):
        source = self.translated_input()

        def compiler_with_empty_dir(request):
            request.output_path.write_bytes(self.valid_artifact)
            (request.output_path.parent / "unlisted").mkdir()
            return 0, "ok", ""

        report = build(self.plan(source), compiler=MockCompilerAdapter("mock-1", compiler_with_empty_dir),
                       validators=(MGSCV1Validator(),))
        self.assertEqual(report.status, "failed")
        self.assertIn("directory whitelist mismatch", report.error)

    def test_invalid_output_format_is_failure_and_temp_is_cleaned(self):
        source = self.translated_input()
        report = build(self.plan(source), compiler=self.compiler(data=b"synthetic bad bytes"),
                       validators=(MGSCV1Validator(),))
        self.assertEqual(report.status, "failed")
        self.assertIn("artifact validation failed", report.error)
        self.assertFalse(any(p.name.startswith(".locbuild-") for p in self.output_parent.iterdir()))

    def test_compiled_hash_mismatch_is_failure(self):
        source = self.translated_input()
        report = build(self.plan(source, expected_hash="A" * 64), compiler=self.compiler(),
                       validators=(MGSCV1Validator(),))
        self.assertIn("hash differs", report.error)

    def test_compiler_version_change_blocks_before_compile(self):
        source = self.translated_input()
        calls = []
        compiler = MockCompilerAdapter("mock-2", lambda request: (calls.append(request), (0, "", ""))[1])
        report = build(self.plan(source, expected_version="mock-1"), compiler=compiler,
                       validators=(MGSCV1Validator(),))
        self.assertEqual(report.status, "blocked")
        self.assertEqual(calls, [])
        self.assertIn("version changed", report.error)

    def test_existing_output_directory_is_never_overwritten(self):
        source = self.translated_input()
        output = self.output_parent / "build-out"
        output.mkdir()
        sentinel = output / "keep.txt"
        sentinel.write_text("untouched", encoding="utf-8")
        report = build(self.plan(source), compiler=self.compiler(), validators=(MGSCV1Validator(),))
        self.assertEqual(report.status, "blocked")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "untouched")

    def test_output_index_path_mismatch_blocks_before_compiler(self):
        source = self.translated_input()
        calls = []
        compiler = MockCompilerAdapter("mock-1", lambda request: (calls.append(request), (0, "", ""))[1])
        report = build(self.plan(source, index={"different.mg": self.valid_hash}), compiler=compiler,
                       validators=(MGSCV1Validator(),))
        self.assertEqual(report.status, "blocked")
        self.assertEqual(calls, [])
        self.assertIn("index paths", report.error)

    def test_output_path_escape_is_blocked(self):
        source = self.translated_input()
        plan = self.plan(source, output_path="../outside.mg")
        report = build(plan, compiler=self.compiler(), validators=(MGSCV1Validator(),))
        self.assertEqual(report.status, "blocked")
        self.assertIn("Unsafe output path", report.error)

    def test_invalid_windows_output_paths_block_before_compiler(self):
        source = self.translated_input()
        for invalid in ("nested/CON.txt", "nested/trailing.", "nested/data:stream"):
            with self.subTest(path=invalid):
                calls = []
                compiler = MockCompilerAdapter("mock-1", lambda request: (calls.append(request), (0, "", ""))[1])
                report = build(self.plan(source, name=f"invalid-{len(calls)}", output_path=invalid),
                               compiler=compiler, validators=(MGSCV1Validator(),))
                self.assertEqual(report.status, "blocked")
                self.assertEqual(calls, [])

    def test_output_and_index_path_separators_share_normalized_key(self):
        source = self.translated_input()
        plan = self.plan(source, output_path="nested\\file.mg", index={"nested/file.mg": self.valid_hash})
        report = build(plan, compiler=self.compiler(), validators=(MGSCV1Validator(),))
        self.assertEqual(report.status, "succeeded")
        self.assertEqual(report.items[0].output_relative_path, "nested/file.mg")

    def test_output_index_hash_mismatch_fails_after_build_and_cleans_temp(self):
        source = self.translated_input()
        report = build(self.plan(source, index={"script.mg": "0" * 64}), compiler=self.compiler(),
                       validators=(MGSCV1Validator(),))
        self.assertEqual(report.status, "failed")
        self.assertIn("artifact index", report.error)
        self.assertFalse((self.output_parent / "build-out").exists())
        self.assertFalse(any(p.name.startswith(".locbuild-") for p in self.output_parent.iterdir()))

    def test_duplicate_output_paths_are_blocked(self):
        source_a = self.translated_input("a.txt")
        # A second reviewed source at a distinct input path is valid; output aliases are not.
        source_b = self.control_input("b.txt")
        plan = BuildPlan(self.inputs, self.output_parent / "dup", (
            BuildTarget(source_a, "script.mg", BuildMode.TRANSLATED),
            BuildTarget(source_b, "SCRIPT.MG", BuildMode.ORIGINAL_ROUNDTRIP, self.valid_hash),
        ), "mock-1", timeout_seconds=2)
        report = build(plan, compiler=self.compiler(), validators=(MGSCV1Validator(),))
        self.assertEqual(report.status, "blocked")
        self.assertIn("Duplicate output path", report.error)

    def test_source_changed_after_review_is_blocked(self):
        source = self.translated_input()
        (self.inputs / source.relative_path).write_text("tampered", encoding="utf-8")
        report = build(self.plan(source), compiler=self.compiler(), validators=(MGSCV1Validator(),))
        self.assertEqual(report.status, "blocked")
        self.assertIn("changed after review", report.error)

    def test_unsupported_unresolved_source_cannot_be_forged_through_helpers(self):
        unresolved_adapter = BgiAdapter()
        parsed = unresolved_adapter.extract('Mystery("opaque");', "unresolved.txt")
        with self.assertRaises(ReviewedSourceError):
            prepare_reviewed_source(
                unresolved_adapter, parsed, input_root=self.inputs, relative_path="unsafe.txt",
                glossary=self.glossary, prompt_template=self.template, store=self.store,
                source_language="x", target_language="y", rule_version="r1",
            )

    def test_stale_temp_cleanup_requires_matching_marker_and_explicit_id(self):
        stale = self.output_parent / ".locbuild-deadbeef-test"
        stale.mkdir()
        (stale / ".localization-build-temp.json").write_text(
            json.dumps({"schema_version": 1, "build_id": "deadbeef", "status": "building"}),
            encoding="utf-8")
        (stale / "partial.bin").write_bytes(b"partial")
        with self.assertRaises(Exception):
            cleanup_abandoned_build(self.output_parent, stale, "wrong-id")
        self.assertTrue(stale.exists())
        cleanup_abandoned_build(self.output_parent, stale, "deadbeef")
        self.assertFalse(stale.exists())

    def test_hard_process_termination_leaves_auditable_temp_for_explicit_cleanup(self):
        source_path = self.inputs / "source.txt"
        source_path.write_text(self.SOURCE, encoding="utf-8")
        fixture = Path(__file__).parent / "fixtures" / "compilation" / "build_crash_worker.py"
        environment = os.environ.copy()
        package_root = str(Path(__file__).parents[1] / "src")
        environment["PYTHONPATH"] = package_root + os.pathsep + environment.get("PYTHONPATH", "")
        completed = subprocess.run(
            [sys.executable, str(fixture), str(self.inputs), str(self.output_parent)],
            env=environment, shell=False, capture_output=True, timeout=20, check=False,
        )
        self.assertEqual(completed.returncode, 73, completed.stderr.decode(errors="replace"))
        abandoned = [path for path in self.output_parent.iterdir() if path.name.startswith(".locbuild-")]
        self.assertEqual(len(abandoned), 1)
        marker = json.loads((abandoned[0] / ".localization-build-temp.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["status"], "building")
        cleanup_abandoned_build(self.output_parent, abandoned[0], marker["build_id"])
        self.assertFalse(abandoned[0].exists())

    def test_repeated_build_does_not_replace_published_directory(self):
        source = self.translated_input()
        plan = self.plan(source)
        first = build(plan, compiler=self.compiler(), validators=(MGSCV1Validator(),))
        content = (plan.output_directory / "artifacts" / "script.mg").read_bytes()
        second = build(plan, compiler=self.compiler(data=b"replacement"), validators=(MGSCV1Validator(),))
        self.assertEqual(first.status, "succeeded")
        self.assertEqual(second.status, "blocked")
        self.assertEqual((plan.output_directory / "artifacts" / "script.mg").read_bytes(), content)


class MGSCValidationTests(unittest.TestCase):
    def test_valid_synthetic_mgsc_v1(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.mg"
            path.write_bytes(synthetic_mgsc())
            result = MGSCV1Validator().validate(path)
            self.assertEqual(result["format"], "MGSC")
            self.assertEqual(result["version"], 1)
            self.assertEqual(result["block_table"], [{"name": "main", "offset": 0}])
            self.assertIn("opaque", result["evidence_scope"])

    def test_rejects_wrong_magic_and_truncated_header(self):
        for data in (b"NOPE" + synthetic_mgsc()[4:], b"MGSC"):
            with self.subTest(data=data[:4]), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "bad.mg"
                path.write_bytes(data)
                with self.assertRaises(MGSCValidationError):
                    MGSCV1Validator().validate(path)

    def test_rejects_unsupported_version_and_table_offsets(self):
        wrong_version = synthetic_mgsc(version=2)
        wrong_offset = synthetic_mgsc(block_offset=1)
        for data in (wrong_version, wrong_offset):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "bad.mg"
                path.write_bytes(data)
                with self.assertRaises(MGSCValidationError):
                    MGSCV1Validator().validate(path)

    def test_rejects_missing_main_and_payload_length_mismatch(self):
        missing_main = synthetic_mgsc(block="other")
        bad_length = synthetic_mgsc() + b"trailing"
        for data in (missing_main, bad_length):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "bad.mg"
                path.write_bytes(data)
                with self.assertRaises(MGSCValidationError):
                    MGSCV1Validator().validate(path)

    def test_preserves_audited_utf8_replacement_and_rejects_truncated_name(self):
        invalid_utf8 = (b"MGSC" + struct.pack("<iiii", 1, 2, 1, 1)
                        + b"\x01\xff" + struct.pack("<i", 0)
                        + b"\x04main" + struct.pack("<i", 0)
                        + struct.pack("<i", 0) + b"\x00")
        truncated_length = b"MGSC" + struct.pack("<iiii", 1, 1, 1, 1) + b"\x80"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "malformed-name.mg"
            path.write_bytes(invalid_utf8)
            result = MGSCV1Validator().validate(path)
            self.assertEqual(result["block_table"][0]["name"], "�")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "truncated-name.mg"
            path.write_bytes(truncated_length)
            with self.assertRaises(MGSCValidationError):
                MGSCV1Validator().validate(path)


class CompilerAdapterTests(unittest.TestCase):
    def test_command_adapter_passes_safe_argv_with_unicode_and_shell_metacharacters(self):
        with tempfile.TemporaryDirectory(prefix="compiler args ") as directory:
            root = Path(directory)
            source = root / "source 中文 ; &.txt"
            output = root / "output 中文 ; &.bin"
            source.write_bytes(b"synthetic payload")
            code = "import pathlib,sys; pathlib.Path(sys.argv[2]).write_bytes(pathlib.Path(sys.argv[1]).read_bytes())"
            adapter = CommandLineCompilerAdapter(
                sys.executable, ("-c", code, "{input}", "{output}"),
                version_arguments=("-c", "print('fixture-1')"), compiler_id="python-test-fixture",
            )
            self.assertEqual(adapter.get_version(5).version, "fixture-1")
            execution = adapter.compile(type("Request", (), {"input_path": source, "output_path": output})(), 5)
            self.assertEqual(execution.exit_code, 0)
            self.assertEqual(output.read_bytes(), b"synthetic payload")

    def test_command_adapter_reports_real_child_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.txt"
            output = root / "output.bin"
            source.write_text("synthetic", encoding="utf-8")
            adapter = CommandLineCompilerAdapter(
                sys.executable, ("-c", "import time; time.sleep(5); open(r'{output}','wb').write(b'x')", "{input}"),
                version_arguments=("-c", "print('fixture-1')"), compiler_id="timeout-test",
            )
            execution = adapter.compile(type("Request", (), {"input_path": source, "output_path": output})(), 0.1)
            self.assertTrue(execution.timed_out)
            self.assertFalse(output.exists())

    def test_rejects_unknown_placeholders_and_shell_strings_are_not_split(self):
        with self.assertRaises(ValueError):
            CommandLineCompilerAdapter("tool", ("{input}", "{oops}"),
                                       version_arguments=("--version",), compiler_id="tool")
        with self.assertRaises(ValueError):
            CommandLineCompilerAdapter("tool", "--input {input}",
                                       version_arguments=("--version",), compiler_id="tool")

    @unittest.skipUnless(shutil.which("powershell") or shutil.which("pwsh"),
                         "PowerShell is required for the synthetic .NET reflection regression fixture")
    def test_dotnet_reflection_string_string_binding_and_explicit_cast_regression(self):
        shell = shutil.which("powershell") or shutil.which("pwsh")
        fixture = Path(__file__).parent / "fixtures" / "compilation" / "reflection_binding_probe.ps1"
        with tempfile.TemporaryDirectory(prefix="reflect bind 中文 ") as directory:
            root = Path(directory)
            source = root / "input with spaces.txt"
            output = root / "output with spaces.txt"
            source.write_text("synthetic reflection payload", encoding="utf-8")
            environment = os.environ.copy()
            environment["LOCALIZATION_SYNTHETIC_INPUT"] = str(source)
            environment["LOCALIZATION_SYNTHETIC_OUTPUT"] = str(output)
            completed = subprocess.run(
                [shell, "-NoProfile", "-NonInteractive", "-Command", fixture.read_text(encoding="utf-8")],
                env=environment,
                shell=False, capture_output=True, text=True, timeout=30, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertEqual(output.read_text(encoding="utf-8"), "synthetic reflection payload")


if __name__ == "__main__":
    unittest.main()
