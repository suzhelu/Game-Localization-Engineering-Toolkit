from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from localization_toolkit.adapters.bgi import BgiAdapter
from localization_toolkit.translation import (
    Glossary,
    GlossaryEntry,
    GlossaryError,
    MockProvider,
    PromptTemplate,
    ProviderResult,
    TranslationStatus,
    TranslationStore,
    TranslationWorkflowError,
    approve_translation,
    build_tasks,
    run_batches,
    write_approved,
)
from localization_toolkit.translation.quality import validate_target


def extract(source: str, *, context=None):
    parsed = BgiAdapter().extract(source, "synthetic-script.bgi")
    if context is None:
        return parsed
    return replace(parsed, slots=(replace(parsed.slots[0], context=context),))


def default_glossary():
    return Glossary("rules-1", ())


def make_tasks(slots, glossary=None, template=None, rule_version="1"):
    return build_tasks(
        slots,
        glossary=glossary or default_glossary(),
        prompt_template=template or PromptTemplate(),
        source_language="ja",
        target_language="zh-Hans",
        rule_version=rule_version,
        special_rules="Do not add unstated facts.",
    )


def run(slots, provider, store, glossary=None, template=None, batch_size=8, rule_version="1"):
    return run_batches(
        slots,
        glossary=glossary or default_glossary(),
        prompt_template=template or PromptTemplate(),
        provider=provider,
        store=store,
        source_language="ja",
        target_language="zh-Hans",
        rule_version=rule_version,
        batch_size=batch_size,
        special_rules="Do not add unstated facts.",
    )


class TranslationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="translation workflow synthetic ")
        self.store = TranslationStore(Path(self.temp.name) / "state" / "translations.json")
        self.parsed = extract('OutputLineAll("原文", "Reference", 0);')
        self.slot = self.parsed.slots[0]

    def tearDown(self):
        self.temp.cleanup()

    def provider_for(self, text="合成译文"):
        return MockProvider(lambda request: [ProviderResult(task.slot_id, text) for task in request.tasks])

    def test_normal_batch_saves_pending_review_records_and_renders_configurable_prompt(self):
        glossary = Glossary("g1", (GlossaryEntry("原文", "指定译名", note="synthetic note"),))
        template = PromptTemplate("{source_language}>{target_language}|{source_text}|{reference_text}|{role}|{speaker}|{context}|{glossary_constraints}|{special_rules}|{slot_id}")
        provider = self.provider_for("指定译名")
        summary = run([self.slot], provider, self.store, glossary, template)
        task = make_tasks([self.slot], glossary, template)[0]
        record = self.store.get(task.task_id)
        self.assertEqual(summary["pending_review"], 1)
        self.assertEqual(record.status, TranslationStatus.PENDING_REVIEW)
        self.assertNotEqual(record.source_sha256, "")
        self.assertIn("ja>zh-Hans", provider.requests[0].prompts[self.slot.slot_id])
        self.assertIn("Reference", provider.requests[0].prompts[self.slot.slot_id])
        self.assertIn("指定译名", provider.requests[0].prompts[self.slot.slot_id])
        self.assertEqual(record.rule_version, "1")

    def test_pending_result_is_never_auto_approved(self):
        run([self.slot], self.provider_for(), self.store)
        record = self.store.records()[0]
        self.assertEqual(record.status, TranslationStatus.PENDING_REVIEW)
        self.assertIsNone(record.reviewer)

    def test_repeat_batch_deduplicates_cached_task_and_does_not_call_provider(self):
        provider = self.provider_for()
        first = run([self.slot], provider, self.store)
        second = run([self.slot], provider, self.store)
        self.assertEqual(first["provider_calls"], 1)
        self.assertEqual(second["provider_calls"], 0)
        self.assertEqual(len(provider.requests), 1)

    def test_duplicate_text_in_distinct_slots_keeps_two_task_ids(self):
        parsed = extract('OutputLineAll("同文", "same", 0);\nOutputLineAll("同文", "same", 0);')
        self.assertEqual(parsed.slots[0].original_text, parsed.slots[1].original_text)
        tasks = make_tasks(parsed.slots)
        self.assertNotEqual(tasks[0].task_id, tasks[1].task_id)
        provider = self.provider_for("一致的同文")
        summary = run(parsed.slots, provider, self.store)
        self.assertEqual(summary["task_count"], 2)
        self.assertEqual(summary["pending_review"], 2)

    def test_duplicate_slot_id_input_is_rejected(self):
        with self.assertRaises(TranslationWorkflowError):
            make_tasks([self.slot, self.slot])

    def test_store_reload_resumes_without_reprocessing_completed_batch(self):
        provider = self.provider_for()
        run([self.slot], provider, self.store)
        reopened = TranslationStore(self.store.path)
        summary = run([self.slot], provider, reopened)
        self.assertEqual(summary["provider_calls"], 0)
        self.assertEqual(len(reopened.records()), 1)

    def test_store_records_required_provenance_and_processing_fields(self):
        run([self.slot], self.provider_for(), self.store)
        data = json.loads(self.store.path.read_text(encoding="utf-8"))
        record = data["records"][0]
        for field in ("batch_id", "slot_id", "source_sha256", "target_text", "status", "issues", "rule_version", "processed_at"):
            self.assertIn(field, record)

    def test_changed_primary_source_is_marked_stale_and_not_reused(self):
        run([self.slot], self.provider_for("旧译文"), self.store)
        changed = extract('OutputLineAll("更新原文", "Reference", 0);').slots[0]
        provider = self.provider_for("新译文")
        summary = run([changed], provider, self.store)
        self.assertEqual(summary["stale_records"], 1)
        self.assertEqual(provider.requests.__len__(), 1)
        old = [record for record in self.store.records() if record.target_text == "旧译文"][0]
        self.assertEqual(old.status, TranslationStatus.STALE)

    def test_changed_context_is_stale(self):
        first = replace(self.slot, context={"previous": ({"text": "nearby A"},)})
        second = replace(self.slot, context={"previous": ({"text": "nearby B"},)})
        run([first], self.provider_for(), self.store)
        summary = run([second], self.provider_for(), self.store)
        self.assertEqual(summary["stale_records"], 1)

    def test_changed_reference_is_stale(self):
        first = replace(self.slot, reference_text="Reference A")
        second = replace(self.slot, reference_text="Reference B")
        run([first], self.provider_for(), self.store)
        summary = run([second], self.provider_for(), self.store)
        self.assertEqual(summary["stale_records"], 1)

    def test_changed_glossary_is_stale(self):
        one = Glossary("g1", (GlossaryEntry("原文", "译名甲"),))
        two = Glossary("g2", (GlossaryEntry("原文", "译名乙"),))
        run([self.slot], self.provider_for("译名甲"), self.store, one)
        summary = run([self.slot], self.provider_for("译名乙"), self.store, two)
        self.assertEqual(summary["stale_records"], 1)

    def test_changed_rule_version_is_stale(self):
        run([self.slot], self.provider_for(), self.store, rule_version="v1")
        summary = run([self.slot], self.provider_for(), self.store, rule_version="v2")
        self.assertEqual(summary["stale_records"], 1)

    def test_changed_prompt_template_is_stale(self):
        run([self.slot], self.provider_for(), self.store, template=PromptTemplate("A {source_text}"))
        summary = run([self.slot], self.provider_for(), self.store, template=PromptTemplate("B {source_text}"))
        self.assertEqual(summary["stale_records"], 1)

    def test_glossary_conflicting_source_targets_are_rejected(self):
        with self.assertRaises(GlossaryError):
            Glossary("bad", (GlossaryEntry("term", "one"), GlossaryEntry("TERM", "two")))

    def test_external_glossary_fields_are_explicitly_mappable(self):
        glossary = Glossary.from_records("fixture", [{"ja": "合成語", "zh": "译名", "memo": "note"}],
                                         source_field="ja", target_field="zh", note_field="memo")
        self.assertEqual(glossary.entries[0], GlossaryEntry("合成語", "译名", True, "note"))

    def test_missing_required_glossary_target_is_flagged(self):
        glossary = Glossary("g1", (GlossaryEntry("原文", "强制译名"),))
        summary = run([self.slot], self.provider_for("任意译文"), self.store, glossary)
        self.assertEqual(summary["needs_review"], 1)
        self.assertIn("required_term", [issue.code for issue in self.store.records()[0].issues])

    def test_placeholder_loss_is_flagged(self):
        parsed = extract(r'OutputLineAll("原文 {name}", "Reference {name}", 0);')
        summary = run(parsed.slots, self.provider_for("缺少占位符"), self.store)
        self.assertEqual(summary["needs_review"], 1)
        self.assertIn("placeholders", [issue.code for issue in self.store.records()[0].issues])

    def test_format_tag_change_is_flagged(self):
        parsed = extract('OutputLineAll("<b>原文</b>", "<b>Reference</b>", 0);')
        summary = run(parsed.slots, self.provider_for("<i>译文</i>"), self.store)
        self.assertEqual(summary["needs_review"], 1)
        self.assertIn("format_tags", [issue.code for issue in self.store.records()[0].issues])

    def test_reordered_format_tags_are_flagged(self):
        parsed = extract('OutputLineAll("<b><i>原文</i></b>", "<b><i>Reference</i></b>", 0);')
        summary = run(parsed.slots, self.provider_for("<i><b>译文</b></i>"), self.store)
        self.assertEqual(summary["needs_review"], 1)
        self.assertIn("format_tags", [issue.code for issue in self.store.records()[0].issues])

    def test_control_escape_loss_is_flagged(self):
        parsed = extract(r'OutputLineAll("原文\n续", "Reference\nnext", 0);')
        summary = run(parsed.slots, self.provider_for("缺少转义标记"), self.store)
        self.assertEqual(summary["needs_review"], 1)
        self.assertIn("control_escapes", [issue.code for issue in self.store.records()[0].issues])

    def test_returned_slot_omission_is_flagged(self):
        provider = MockProvider(lambda request: [])
        summary = run([self.slot], provider, self.store)
        self.assertEqual(summary["needs_review"], 1)
        self.assertEqual(self.store.records()[0].issues[0].code, "missing_result")

    def test_duplicate_provider_results_are_flagged(self):
        provider = MockProvider(lambda request: [ProviderResult(request.tasks[0].slot_id, "a"),
                                                 ProviderResult(request.tasks[0].slot_id, "b")])
        summary = run([self.slot], provider, self.store)
        self.assertEqual(summary["needs_review"], 1)
        self.assertIn("duplicate_result", [issue.code for issue in self.store.records()[0].issues])

    def test_unexpected_provider_slot_id_is_flagged(self):
        provider = MockProvider(lambda request: [ProviderResult("unknown-id", "extra")])
        summary = run([self.slot], provider, self.store)
        self.assertEqual(summary["needs_review"], 1)
        self.assertIn("unexpected_slot_id", [issue.code for issue in self.store.records()[0].issues])

    def test_malformed_provider_values_are_flagged_not_written(self):
        provider = MockProvider(lambda request: [ProviderResult(request.tasks[0].slot_id, 123)])
        summary = run([self.slot], provider, self.store)
        self.assertEqual(summary["needs_review"], 1)
        self.assertIsNone(self.store.records()[0].target_text)

    def test_provider_exception_is_saved_for_retry(self):
        provider = MockProvider(lambda request: RuntimeError("synthetic provider failure"))
        summary = run([self.slot], provider, self.store)
        self.assertEqual(summary["provider_error"], 1)
        self.assertIn("synthetic provider failure", self.store.records()[0].issues[0].message)

    def test_partial_batch_failure_resumes_only_failed_tasks(self):
        parsed = extract('OutputLineAll("原文一", "Reference one", 0);\nOutputLineAll("原文二", "Reference two", 0);')
        calls = 0

        def responder(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return RuntimeError("one batch failed")
            return [ProviderResult(task.slot_id, "合成译文") for task in request.tasks]

        provider = MockProvider(responder)
        first = run(parsed.slots, provider, self.store, batch_size=1)
        self.assertEqual(first["provider_error"], 1)
        self.assertEqual(first["pending_review"], 1)
        second = run(parsed.slots, provider, self.store, batch_size=1)
        self.assertEqual(second["provider_calls"], 1)
        self.assertEqual(second["pending_review"], 2)

    def test_human_approval_is_required_before_bgi_writeback(self):
        parser = BgiAdapter()
        parsed = parser.extract('OutputLineAll("原文", "Reference", 0);', "synthetic.bgi")
        glossary = default_glossary()
        template = PromptTemplate()
        run(parsed.slots, self.provider_for("合成译文"), self.store, glossary, template)
        with self.assertRaises(TranslationWorkflowError):
            write_approved(parser, parsed, glossary=glossary, prompt_template=template,
                           store=self.store, source_language="ja", target_language="zh-Hans", rule_version="1",
                           special_rules="Do not add unstated facts.")
        task = make_tasks(parsed.slots, glossary, template)[0]
        approve_translation(task, store=self.store, glossary=glossary, reviewer="human-reviewer")
        output = write_approved(parser, parsed, glossary=glossary, prompt_template=template,
                                store=self.store, source_language="ja", target_language="zh-Hans", rule_version="1",
                                special_rules="Do not add unstated facts.")
        self.assertIn('"合成译文"', output)
        self.assertEqual(parser.extract(output, "synthetic.bgi").slots[0].current_text, "合成译文")

    def test_unresolved_adapter_parse_blocks_writeback(self):
        parser = BgiAdapter()
        parsed = parser.extract('UnknownCall("unclassified text");', "synthetic.bgi")
        with self.assertRaises(TranslationWorkflowError):
            write_approved(parser, parsed, glossary=default_glossary(), prompt_template=PromptTemplate(),
                           store=self.store, source_language="ja", target_language="zh-Hans", rule_version="1")

    def test_approval_requires_reviewer_and_safe_pending_state(self):
        run([self.slot], self.provider_for(), self.store)
        task = make_tasks([self.slot])[0]
        with self.assertRaises(TranslationWorkflowError):
            approve_translation(task, store=self.store, glossary=default_glossary(), reviewer=" ")
        approved = approve_translation(task, store=self.store, glossary=default_glossary(), reviewer="reviewer", review_note="checked")
        self.assertEqual(approved.status, TranslationStatus.APPROVED)
        self.assertEqual(approved.review_note, "checked")

    def test_invalid_prompt_placeholder_is_rejected(self):
        with self.assertRaises(ValueError):
            PromptTemplate("{unknown_field}")
        with self.assertRaises(ValueError):
            PromptTemplate("{source_text:{unvalidated_nested_field}}")

    def test_cached_pending_result_is_revalidated_before_resume_skip(self):
        run([self.slot], self.provider_for("初始译文"), self.store)
        payload = json.loads(self.store.path.read_text(encoding="utf-8"))
        payload["records"][0]["target_text"] = "tampered {missing}"
        self.store.path.write_text(json.dumps(payload), encoding="utf-8")
        provider = self.provider_for("should not replace without review")
        summary = run([self.slot], provider, self.store)
        self.assertEqual(len(provider.requests), 0)
        self.assertEqual(summary["needs_review"], 1)
        self.assertEqual(self.store.records()[0].status, TranslationStatus.NEEDS_REVIEW)

    def test_explicit_rejection_is_recorded(self):
        run([self.slot], self.provider_for(), self.store)
        task = make_tasks([self.slot])[0]
        from localization_toolkit.translation import reject_translation
        rejected = reject_translation(task, store=self.store, reviewer="reviewer", reason="semantic mismatch")
        self.assertEqual(rejected.status, TranslationStatus.REJECTED)
        self.assertEqual(rejected.review_note, "semantic mismatch")

    def test_invalid_batch_size_is_rejected(self):
        with self.assertRaises(TranslationWorkflowError):
            run([self.slot], self.provider_for(), self.store, batch_size=0)


if __name__ == "__main__":
    unittest.main()
