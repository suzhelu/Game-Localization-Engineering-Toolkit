import unittest

from localization_toolkit.adapters.bgi import BgiAdapter, OpaqueCallSchema
from localization_toolkit.models import TextCategory, UnsupportedStructureError


def adapter():
    return BgiAdapter(opaque_commands={"LoadAsset": OpaqueCallSchema(1, frozenset({0}))})


class BgiAdapterTests(unittest.TestCase):
    def test_extracts_body_and_name_with_categories_and_positions(self):
        source = 'OutputLine("JP_NAME", "JP_BODY", "EN_NAME", "EN_BODY", 0);\r\n'
        parsed = adapter().extract(source, "synthetic.bgi")
        self.assertEqual(len(parsed.slots), 2)
        self.assertEqual([s.category for s in parsed.slots],
                         [TextCategory.CHARACTER_NAME, TextCategory.DIALOGUE])
        name, body = parsed.slots
        self.assertEqual((name.original_text, name.current_text, name.line), ("JP_NAME", "EN_NAME", 1))
        self.assertEqual((body.original_text, body.current_text, body.char_start), ("JP_BODY", "EN_BODY", 44))
        self.assertEqual(body.byte_offset, len(source[:body.char_start].encode("utf-8")))

    def test_duplicate_text_gets_distinct_stable_ids(self):
        source = ('OutputLineAll("jp", "same", 0);\n'
                  'OutputLineAll("jp", "same", 0);\n')
        parser = adapter()
        first = parser.extract(source, "duplicate.bgi")
        second = parser.extract(source, "duplicate.bgi")
        self.assertEqual(len(first.slots), 2)
        self.assertNotEqual(first.slots[0].slot_id, first.slots[1].slot_id)
        self.assertEqual([x.slot_id for x in first.slots], [x.slot_id for x in second.slots])

    def test_extracts_ui_save_labels_and_choice_menu(self):
        source = '''
char Menu[2];
if(GetGlobalFlag(GLanguage)==1) {
Menu[0]="Accept"; Menu[1]="Decline";
} else {
Menu[0]="JP_A"; Menu[1]="JP_B";
}
Select(2,Menu);
SavePoint("JP_SAVE", "Save Slot");
ModAddArtset("Settings", "art-settings", 0);
'''
        parsed = adapter().extract(source, "choices.bgi")
        self.assertEqual([slot.category for slot in parsed.slots], [
            TextCategory.MENU, TextCategory.MENU, TextCategory.OTHER_VISIBLE, TextCategory.UI
        ])
        self.assertEqual([slot.current_text for slot in parsed.slots],
                         ["Accept", "Decline", "Save Slot", "Settings"])
        self.assertEqual([slot.original_text for slot in parsed.slots[:2]], ["JP_A", "JP_B"])

    def test_mixed_markup_and_opaque_resource_preserved(self):
        source = 'LoadAsset("ui/background");\nOutputLineAll("jp", "<color=red>EN</color>", 0);'
        parsed = adapter().extract(source, "mixed.bgi")
        self.assertEqual(parsed.unresolved_count, 0)
        slot = parsed.slots[0]
        output = adapter().write(parsed, {slot.slot_id: "<color=red>中</color>"})
        self.assertIn('LoadAsset("ui/background")', output)
        self.assertIn('"<color=red>中</color>"', output)

    def test_escape_and_newline_round_trip(self):
        source = r'OutputLineAll("jp", "line1\nline2\t\\\\\"quoted\\\\\"", 0);'
        parsed = adapter().extract(source, "escapes.bgi")
        slot = parsed.slots[0]
        translated = '第一行\n第二行\t\\"引号\\"'
        output = adapter().write(parsed, {slot.slot_id: translated})
        reparsed = adapter().extract(output, "escapes.bgi")
        self.assertEqual(reparsed.slots[0].current_text, translated)

    def test_translation_cannot_inject_a_call(self):
        source = 'OutputLineAll("jp", "EN", 0);'
        parsed = adapter().extract(source, "injection.bgi")
        slot = parsed.slots[0]
        output = adapter().write(parsed, {slot.slot_id: '中文"); DeleteAll(); OutputLineAll("x'})
        self.assertEqual(len(adapter().extract(output, "injection.bgi").slots), 1)

    def test_control_tag_and_placeholder_sequence_must_be_preserved(self):
        source = 'OutputLineAll("jp", "Hello {0} <wait>", 0);'
        parsed = adapter().extract(source, "controls.bgi")
        slot = parsed.slots[0]
        with self.assertRaises(ValueError):
            adapter().write(parsed, {slot.slot_id: "你好<wait> {0}"})
        self.assertEqual(adapter().write(parsed, {slot.slot_id: "你好 {0} <wait>"}).count("OutputLineAll"), 1)

    def test_unknown_string_call_is_reported_and_blocks_writeback(self):
        parsed = BgiAdapter().extract('Mystery("maybe visible");', "unknown.bgi")
        self.assertEqual(parsed.unresolved_count, 1)
        with self.assertRaises(UnsupportedStructureError):
            BgiAdapter().write(parsed, {})

    def test_unknown_call_without_literals_is_nonblocking_but_reported(self):
        parsed = BgiAdapter().extract("Advance(3);", "opaque-call.bgi")
        self.assertEqual(parsed.unresolved_count, 0)
        self.assertEqual(len(parsed.issues), 1)

    def test_known_dynamic_format_call_blocks_writeback(self):
        parsed = adapter().extract("SetNameFormat(1);", "dynamic-format.bgi")
        self.assertEqual(parsed.unresolved_count, 1)

    def test_standalone_string_literal_is_reported(self):
        parsed = adapter().extract('const Message = "unclassified";\n', "standalone.bgi")
        self.assertEqual(parsed.unresolved_count, 1)
        self.assertIn("outside a recognized", parsed.issues[0].reason)

    def test_character_literals_do_not_confuse_call_delimiters(self):
        parsed = adapter().extract("Unknown('(', \"resource-id\");", "char-literal.bgi")
        self.assertEqual(parsed.issues[0].argument_indices, (1,))

    def test_arity_and_nonliteral_target_fail_closed(self):
        parser = adapter()
        wrong_arity = parser.extract('OutputLineAll("jp", "en");', "arity.bgi")
        self.assertGreater(wrong_arity.unresolved_count, 0)
        nonliteral = parser.extract('OutputLineAll("jp", RuntimeText, 0);', "dynamic.bgi")
        self.assertGreater(nonliteral.unresolved_count, 0)

    def test_unproven_select_is_reported(self):
        parsed = adapter().extract('Select(2, RuntimeMenu);', "dynamic-menu.bgi")
        self.assertEqual(parsed.unresolved_count, 1)

    def test_reference_call_mismatch_is_rejected(self):
        with self.assertRaises(UnsupportedStructureError):
            adapter().extract('OutputLineAll("jp", "en", 0);', "ref.bgi",
                              'OutputLineAll("jp", "en", 0, 1);')

    def test_reference_source_literal_mismatch_is_rejected(self):
        with self.assertRaises(UnsupportedStructureError):
            adapter().extract('OutputLineAll("jp", "en", 0);', "ref.bgi",
                              'OutputLineAll("different", "en", 0);')

    def test_translation_keys_must_cover_exactly_all_slots(self):
        parsed = adapter().extract('OutputLineAll("jp", "en", 0);', "keys.bgi")
        with self.assertRaises(ValueError):
            adapter().write(parsed, {})
        with self.assertRaises(ValueError):
            adapter().write(parsed, {parsed.slots[0].slot_id: "中文", "extra": "多余"})

    def test_unbalanced_input_fails(self):
        for source in ('OutputLineAll("jp", "en", 0;', 'OutputLineAll("jp", "en", 0); /*',
                       'OutputLineAll("unterminated, "en", 0);', '([)]'):
            with self.subTest(source=source), self.assertRaises(UnsupportedStructureError):
                adapter().extract(source, "broken.bgi")

    def test_comment_calls_are_not_extracted(self):
        parsed = adapter().extract('// OutputLineAll("fake", "fake", 0);\n'
                                   '/* Select(2, Hidden); */\n'
                                   'OutputLineAll("jp", "real", 0);', "comments.bgi")
        self.assertEqual(len(parsed.slots), 1)
        self.assertEqual(parsed.slots[0].current_text, "real")

    def test_speaker_hint_tracks_name_and_message_clear(self):
        source = ('OutputLine("JP_NAME", "JP_BODY", "<color=red>Guide</color>", "Body", 0);\n'
                  'ClearMessage();\n'
                  'OutputLineAll("jp", "Next body", 0);')
        parsed = adapter().extract(source, "speaker.bgi")
        body_slots = [slot for slot in parsed.slots if slot.role == "body"]
        self.assertEqual(body_slots[0].speaker_hint, "JP_NAME")
        self.assertIsNone(body_slots[1].speaker_hint)

    def test_slot_id_survives_replacement_text_changes(self):
        parser = adapter()
        parsed = parser.extract('OutputLineAll("jp", "English", 0);', "stable.bgi")
        slot = parsed.slots[0]
        output = parser.write(parsed, {slot.slot_id: "简体中文"})
        reparsed = parser.extract(output, "stable.bgi")
        self.assertEqual(slot.slot_id, reparsed.slots[0].slot_id)
        self.assertEqual(reparsed.slots[0].current_text, "简体中文")

    def test_writeback_preserves_all_non_target_source_bytes_and_slot_count(self):
        source = ('// Keep whitespace\r\nLoadAsset("sprite:fake");\r\n'
                  'OutputLine("JP", "JP body", "Name", "English body", 7);\r\n')
        parser = adapter()
        parsed = parser.extract(source, "roundtrip.bgi")
        translations = {slot.slot_id: "中文" for slot in parsed.slots}
        output = parser.write(parsed, translations)
        reparsed = parser.extract(output, "roundtrip.bgi")
        self.assertEqual(len(parsed.slots), len(reparsed.slots))
        self.assertEqual([s.current_text for s in reparsed.slots], ["中文", "中文"])
        self.assertEqual([s.slot_id for s in parsed.slots], [s.slot_id for s in reparsed.slots])


if __name__ == "__main__":
    unittest.main()
