from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from examples.adapters.template.adapter import SyntheticTextAdapter, register as register_synthetic
from localization_toolkit.adapters import AdapterDescriptor, AdapterRegistry, AdapterRegistryError, BgiAdapter, FormatValidation
from localization_toolkit.adapters.registry import FormatProbe, ProbeStatus
from localization_toolkit.cli import main
from localization_toolkit.translation import (
    Glossary, MockProvider, PromptTemplate, ProviderResult, TranslationStore,
    TranslationWorkflowError, build_tasks, run_batches, write_approved,
)


class _EntryPoint:
    name = "synthetic-text-v1"
    def load(self):
        return register_synthetic


def _register_other(registry):
    adapter = _OtherAdapter()
    registry.register(AdapterDescriptor(adapter.adapter_id, adapter.version, adapter.formats,
                                        adapter.capabilities, "Competing synthetic adapter"),
                      lambda options: _OtherAdapter())


class _OtherEntryPoint:
    name = "synthetic-text-v2"
    def load(self):
        return _register_other


class _EntryPoints(list):
    def select(self, *, group):
        return self if group == AdapterRegistry.ENTRY_POINT_GROUP else []


class _OtherAdapter(SyntheticTextAdapter):
    adapter_id, version = "synthetic-text-v2", "2"
    def probe(self, source):
        return FormatProbe(ProbeStatus.SUPPORTED, ("overlap fixture evidence",))


class AdapterRegistryTests(unittest.TestCase):
    def test_bgi_factory_preserves_existing_configuration(self):
        registry = AdapterRegistry(discover_plugins=False)
        registry.register(AdapterDescriptor("bgi", BgiAdapter.version, BgiAdapter.formats, BgiAdapter.capabilities),
                          lambda options: BgiAdapter.from_options(options))
        self.assertIsInstance(registry.create("bgi", {}), BgiAdapter)

    def test_unknown_and_duplicate_adapter_ids_fail(self):
        registry = AdapterRegistry(discover_plugins=False)
        register_synthetic(registry)
        with self.assertRaisesRegex(AdapterRegistryError, "Unknown text adapter"):
            registry.create("unknown")
        with self.assertRaisesRegex(AdapterRegistryError, "Duplicate adapter ID"):
            register_synthetic(registry)

    def test_probe_reports_support_unknown_and_uncertainty_without_writes(self):
        registry = AdapterRegistry(discover_plugins=False)
        register_synthetic(registry)
        source = 'SYNTHETIC-TEXT-V1\nTEXT\tx\t"hello"\n'
        before = source.encode()
        self.assertEqual(registry.probe(source)["status"], "supported")
        self.assertEqual(registry.probe("plain bytes")["status"], "unsupported")
        self.assertEqual(registry.probe("SYNTHETIC-TEXT-V9")["status"], "needs_confirmation")
        self.assertEqual(before, source.encode())

    def test_multiple_matching_adapters_are_ambiguous(self):
        registry = AdapterRegistry(discover_plugins=False)
        register_synthetic(registry)
        other = _OtherAdapter()
        registry.register(AdapterDescriptor(other.adapter_id, other.version, other.formats, other.capabilities),
                          lambda options: _OtherAdapter())
        self.assertEqual(registry.probe("SYNTHETIC-TEXT-V1")["status"], "ambiguous")

    def test_bgi_probe_checks_syntax_not_filename(self):
        registry = AdapterRegistry(discover_plugins=False)
        adapter = BgiAdapter()
        registry.register(AdapterDescriptor("bgi", adapter.version, adapter.formats, adapter.capabilities),
                          lambda options: BgiAdapter.from_options(options))
        self.assertEqual(registry.probe('OutputLineAll("ja", "en", 0);')["status"], "supported")
        self.assertEqual(registry.probe("OpaqueCall(7);")["status"], "needs_confirmation")

    def test_cli_plugin_selection_drives_existing_extract_workflow(self):
        with patch("localization_toolkit.adapters.registry.metadata.entry_points",
                   return_value=_EntryPoints([_EntryPoint()])), tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["init", str(root)]), 0)
            config_path = root / "localize.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["general"]["adapter"] = "synthetic-text-v1"
            config["inputs"]["scripts"] = [{"id": "demo", "source": "input/demo.txt", "output": "out/demo"}]
            config_path.write_text(json.dumps(config), encoding="utf-8")
            (root / "input" / "demo.txt").write_text('SYNTHETIC-TEXT-V1\nTEXT\tx\t"hello"\n', encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = main(["extract", "--project", str(root)])
            self.assertEqual(result, 0, stderr.getvalue())
            self.assertEqual(json.loads(stdout.getvalue())["slots"], 1)

    def test_adapters_and_probe_cli_return_versioned_json(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["adapters"]), 0)
        self.assertEqual(json.loads(output.getvalue())["schema_version"], 1)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "script.txt"
            path.write_text('OutputLineAll("ja", "en", 0);', encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["probe", str(path), "--adapter", "bgi"]), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "supported")

    def test_adapters_cli_loads_only_the_explicit_plugin(self):
        with patch("localization_toolkit.adapters.registry.metadata.entry_points",
                   return_value=_EntryPoints([_EntryPoint()])):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["adapters", "--load", "synthetic-text-v1"]), 0)
            payload = json.loads(output.getvalue())
        ids = {item["adapter_id"] for item in payload["registered"]}
        self.assertIn("synthetic-text-v1", ids)
        self.assertEqual(payload["available_entry_points"], [])

    def test_cli_explicit_plugin_probe_reports_ambiguity(self):
        with patch("localization_toolkit.adapters.registry.metadata.entry_points",
                   return_value=_EntryPoints([_EntryPoint(), _OtherEntryPoint()])), tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.txt"
            path.write_text("SYNTHETIC-TEXT-V1\n", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["probe", str(path), "--include-plugins"]), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "ambiguous")

    def test_optional_format_validator_is_invoked_through_registry(self):
        class ValidatingAdapter(SyntheticTextAdapter):
            capabilities = SyntheticTextAdapter.capabilities | {"format_validate"}
            def validate_format(self, source):
                return FormatValidation(source.startswith("SYNTHETIC-TEXT-V1"), ("synthetic header check",))

        adapter = ValidatingAdapter()
        registry = AdapterRegistry(discover_plugins=False)
        registry.register(AdapterDescriptor(adapter.adapter_id, adapter.version, adapter.formats,
                                            adapter.capabilities), lambda options: ValidatingAdapter())
        result = registry.validate_format(adapter.adapter_id, "SYNTHETIC-TEXT-V1\n")
        self.assertTrue(result.valid)
        self.assertFalse(registry.validate_format(adapter.adapter_id, "other").valid)

    def test_unreviewed_plugin_translation_cannot_use_safe_writeback(self):
        adapter = SyntheticTextAdapter()
        parsed = adapter.extract('SYNTHETIC-TEXT-V1\nTEXT\tx\t"source"\n', "review-gate")
        glossary = Glossary("synthetic-v1", ())
        prompt = PromptTemplate()
        with tempfile.TemporaryDirectory() as temp:
            store = TranslationStore(Path(temp) / "translations.json")
            run_batches(parsed.slots, glossary=glossary, prompt_template=prompt,
                        provider=MockProvider(lambda request: [ProviderResult(task.slot_id, "synthetic")
                                                               for task in request.tasks]),
                        store=store, source_language="en", target_language="zh-Hans",
                        rule_version="rules-v1")
            with self.assertRaises(TranslationWorkflowError):
                write_approved(adapter, parsed, glossary=glossary, prompt_template=prompt,
                               store=store, source_language="en", target_language="zh-Hans",
                               rule_version="rules-v1")


if __name__ == "__main__":
    unittest.main()
