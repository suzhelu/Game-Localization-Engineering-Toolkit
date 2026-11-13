import unittest

from examples.adapters.template.adapter import SyntheticTextAdapter, register
from localization_toolkit.adapters.registry import AdapterDescriptor, AdapterRegistry, AdapterRegistryError
from localization_toolkit.models import UnsupportedStructureError


class SyntheticAdapterTemplateTests(unittest.TestCase):
    def setUp(self):
        self.adapter = SyntheticTextAdapter()
        self.source = 'SYNTHETIC-TEXT-V1\r\nTEXT\tgreeting\t"hello\\nworld"\r\n'

    def test_extracts_fixture_text(self):
        parsed = self.adapter.extract(self.source, "sample")
        self.assertEqual(len(parsed.slots), 1)
        self.assertEqual(parsed.slots[0].current_text, "hello\nworld")

    def test_unchanged_roundtrip_is_byte_identical(self):
        parsed = self.adapter.extract(self.source, "sample")
        self.assertEqual(self.adapter.write(parsed, {parsed.slots[0].slot_id: parsed.slots[0].current_text}), self.source)

    def test_safe_writeback_changes_text_and_keeps_structure(self):
        parsed = self.adapter.extract(self.source, "sample")
        output = self.adapter.write(parsed, {parsed.slots[0].slot_id: "合成文本"})
        self.assertEqual(self.adapter.extract(output, "sample").slots[0].current_text, "合成文本")
        self.assertTrue(output.startswith("SYNTHETIC-TEXT-V1\r\nTEXT\tgreeting\t"))

    def test_malformed_rows_fail_closed(self):
        for source in ('SYNTHETIC-TEXT-V1\nUNKNOWN x\n', 'SYNTHETIC-TEXT-V1\nTEXT\tx\t[bad]\n'):
            with self.subTest(source=source), self.assertRaises(UnsupportedStructureError):
                self.adapter.extract(source, "bad")

    def test_duplicate_registration_and_false_capability_claim_fail(self):
        registry = AdapterRegistry(discover_plugins=False)
        register(registry)
        with self.assertRaisesRegex(AdapterRegistryError, "Duplicate adapter ID"):
            register(registry)
        class FalseValidatorClaim(SyntheticTextAdapter):
            capabilities = SyntheticTextAdapter.capabilities | {"format_validate"}

        adapter = FalseValidatorClaim()
        descriptor = AdapterDescriptor(adapter.adapter_id, adapter.version, adapter.formats, adapter.capabilities)
        other = AdapterRegistry(discover_plugins=False)
        other.register(descriptor, lambda options: adapter)
        with self.assertRaisesRegex(AdapterRegistryError, "validate_format"):
            other.create(adapter.adapter_id)


if __name__ == "__main__":
    unittest.main()
