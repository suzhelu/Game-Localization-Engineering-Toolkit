"""Run a provider-neutral synthetic batch without network access."""
from __future__ import annotations

import tempfile
from pathlib import Path

from localization_toolkit.adapters.bgi import BgiAdapter
from localization_toolkit.translation import (
    Glossary,
    MockProvider,
    ProviderResult,
    PromptTemplate,
    TranslationStore,
    run_batches,
)


def main() -> None:
    source = (
        'OutputLineAll("Synthetic source A", "Synthetic reference A", 0);\n'
        'OutputLineAll("Synthetic source B", "Synthetic reference B", 0);\n'
    )
    extracted = BgiAdapter().extract(source, "synthetic-example.bgi")
    provider = MockProvider(lambda request: [
        ProviderResult(task.slot_id, f"合成译文 {index + 1}")
        for index, task in enumerate(request.tasks)
    ])
    with tempfile.TemporaryDirectory(prefix="localization-translation-demo-") as temporary:
        store = TranslationStore(Path(temporary) / "translation-state.json")
        summary = run_batches(
            extracted.slots,
            glossary=Glossary("synthetic-rules-v1", ()),
            prompt_template=PromptTemplate(),
            provider=provider,
            store=store,
            source_language="en",
            target_language="zh-Hans",
            rule_version="demo-rules-v1",
            batch_size=2,
            special_rules="Use only information in the primary source.",
        )
        print(f"Tasks: {summary['task_count']}; pending human review: {summary['pending_review']}")
        print("No provider network call or script writeback was performed.")


if __name__ == "__main__":
    main()
