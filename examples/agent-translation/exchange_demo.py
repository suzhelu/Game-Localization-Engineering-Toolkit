"""End-to-end synthetic offline exchange; no model or network is used."""
import json
import tempfile
from pathlib import Path

from localization_toolkit.adapters import BgiAdapter
from localization_toolkit.translation import (
    Glossary, PromptTemplate, TranslationStore, approve_translation, build_tasks, write_approved,
)
from localization_toolkit.translation.exchange import export_jsonl, import_jsonl
from localization_toolkit.translation.quality import validate_target


def main() -> None:
    source = 'OutputLineAll("Synthetic greeting {name}", "Synthetic greeting {name}", 0);\n'
    adapter = BgiAdapter()
    parsed = adapter.extract(source, "synthetic-demo")
    glossary, prompt = Glossary("synthetic-1", ()), PromptTemplate()
    tasks = build_tasks(parsed.slots, glossary=glossary, prompt_template=prompt,
                        source_language="en", target_language="zh-Hans", rule_version="demo-1")
    with tempfile.TemporaryDirectory(prefix="agent-exchange-demo-") as temporary:
        root = Path(temporary)
        task_path, result_path = root / "tasks.jsonl", root / "results.jsonl"
        store = TranslationStore(root / "state.json")
        export_jsonl(tasks, task_path)
        task_row = json.loads(task_path.read_text(encoding="utf-8").splitlines()[0])
        # Local synthetic Agent response. This is not a translation service.
        result_row = {"schema_version": 1, "task_id": task_row["task_id"],
                      "slot_id": task_row["slot_id"], "source_fingerprint": task_row["source_fingerprint"],
                      "task_fingerprint": task_row["task_fingerprint"],
                      "translated_text": "合成问候 {name}"}
        result_path.write_text(json.dumps(result_row, ensure_ascii=False) + "\n", encoding="utf-8")
        report = import_jsonl(result_path, tasks, glossary=glossary, store=store)
        record = store.get(tasks[0].task_id)
        assert record and record.status.value == "pending_review"
        assert not validate_target(tasks[0], record.target_text, glossary)
        approved = approve_translation(tasks[0], store=store, glossary=glossary,
                                       reviewer="synthetic-example-reviewer")
        assert approved.status.value == "approved"
        output = write_approved(adapter, parsed, glossary=glossary, prompt_template=prompt,
                                store=store, source_language="en", target_language="zh-Hans",
                                rule_version="demo-1")
        assert "合成问候 {name}" in output
        verified = adapter.extract(output, "synthetic-demo")
        assert verified.structure_signature == parsed.structure_signature
        print(json.dumps({"status": "passed", "import": report, "qa": "passed",
                          "review": "synthetic approval", "writeback": "passed", "verify": "passed",
                          "network_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
