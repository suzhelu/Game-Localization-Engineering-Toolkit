import json
import subprocess
import sys
import tempfile
import time
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from localization_toolkit.models import TextCategory, TextSlot
from localization_toolkit.translation import Glossary, PromptTemplate, TranslationStore, TranslationStoreError, build_tasks
from localization_toolkit.translation.exchange import ExchangeError, export_jsonl, import_jsonl


def task_set(source="hello {name}"):
    slot = TextSlot("slot-1", "synthetic", TextCategory.DIALOGUE, "body", source, source,
                    None, 0, len(source), 0, 1, 1, "TEXT", 0, 0)
    return build_tasks((slot,), glossary=Glossary("g1", ()), prompt_template=PromptTemplate(),
                       source_language="en", target_language="zh-Hans", rule_version="r1")


def result(task, target="你好 {name}"):
    return {"schema_version": 1, "task_id": task.task_id, "slot_id": task.slot_id,
            "source_fingerprint": task.source_sha256, "task_fingerprint": task.task_id,
            "translated_text": target}


class TranslationExchangeTests(unittest.TestCase):
    def test_sorted_offset_batches_are_disjoint(self):
        tasks = task_set() + task_set("other")
        with tempfile.TemporaryDirectory() as temp:
            first, second = Path(temp) / "first.jsonl", Path(temp) / "second.jsonl"
            self.assertEqual(export_jsonl(tasks, first, batch_size=1, offset=0), 1)
            self.assertEqual(export_jsonl(tasks, second, batch_size=1, offset=1), 1)
            left = json.loads(first.read_text(encoding="utf-8"))
            right = json.loads(second.read_text(encoding="utf-8"))
            self.assertNotEqual(left["task_id"], right["task_id"])

    def test_export_import_pending_only_and_no_overwrite(self):
        tasks = task_set()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "tasks.jsonl"
            self.assertEqual(export_jsonl(tasks, path), 1)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], 1)
            with self.assertRaises(ExchangeError): export_jsonl(tasks, path)
            returned = Path(temp) / "results.jsonl"
            returned.write_text(json.dumps(result(tasks[0]), ensure_ascii=False) + "\n", encoding="utf-8")
            store = TranslationStore(Path(temp) / "state.json")
            self.assertEqual(import_jsonl(returned, tasks, glossary=Glossary("g1", ()), store=store)["pending_review"], 1)
            self.assertEqual(store.get(tasks[0].task_id).status.value, "pending_review")
            self.assertEqual(import_jsonl(returned, tasks, glossary=Glossary("g1", ()), store=store)["imported"], 1)

    def test_whole_batch_rejects_invalid_record_without_partial_write(self):
        tasks = task_set()
        second = task_set("other")
        tasks = (tasks[0], second[0])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "results.jsonl"
            rows = [result(tasks[0]), result(tasks[1], "")]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            store = TranslationStore(Path(temp) / "state.json")
            with self.assertRaises(ExchangeError): import_jsonl(path, tasks, glossary=Glossary("g1", ()), store=store)
            self.assertEqual(store.records(), ())

    def test_unknown_duplicate_stale_and_placeholder_rejected(self):
        task = task_set()[0]
        with tempfile.TemporaryDirectory() as temp:
            store = TranslationStore(Path(temp) / "state.json")
            path = Path(temp) / "result.jsonl"
            for row in (dict(result(task), task_id="unknown"), dict(result(task), source_fingerprint="bad"),
                        dict(result(task), task_fingerprint="bad"), result(task, "你好")):
                path.write_text(json.dumps(row) + "\n", encoding="utf-8")
                with self.assertRaises(ExchangeError): import_jsonl(path, (task,), glossary=Glossary("g1", ()), store=store)
                self.assertEqual(store.records(), ())
            row = result(task)
            path.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")
            with self.assertRaises(ExchangeError): import_jsonl(path, (task,), glossary=Glossary("g1", ()), store=store)

    def test_control_markers_and_schema_cannot_be_changed_or_self_approved(self):
        task = task_set("<b>hello {name}</b>\n")[0]
        with tempfile.TemporaryDirectory() as temp:
            store = TranslationStore(Path(temp) / "state.json")
            path = Path(temp) / "result.jsonl"
            for row in (result(task, "你好 {name}"), result(task, "<i>你好 {name}</i>\n"),
                        dict(result(task), schema_version=True), dict(result(task), status="approved")):
                path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
                with self.assertRaises(ExchangeError): import_jsonl(path, (task,), glossary=Glossary("g1", ()), store=store)
                self.assertEqual(store.records(), ())

    def test_store_optimistic_conflict_and_atomic_replace_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            store = TranslationStore(Path(temp) / "state.json")
            before = store.snapshot_fingerprint()
            with self.assertRaises(TranslationStoreError):
                store.save_many((), expected_fingerprint="stale")
            self.assertEqual(store.snapshot_fingerprint(), before)
            self.assertEqual(store.records(), ())
            with patch("localization_toolkit.translation.store.os.replace", side_effect=OSError("injected")):
                with self.assertRaises(OSError): store._write(())
            self.assertEqual(store.snapshot_fingerprint(), before)

    def test_two_processes_from_same_snapshot_cannot_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            store = TranslationStore(path)
            gate = Path(temp) / "ready"
            gate.mkdir()
            task = task_set()[0]
            results = []
            for index, target in enumerate(("left {name}", "right {name}")):
                row = result(task, target)
                result_path = Path(temp) / f"result-{index}.jsonl"
                result_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
                results.append(result_path)
            code = """import sys,time
from pathlib import Path
from localization_toolkit.models import TextCategory,TextSlot
from localization_toolkit.translation import Glossary,PromptTemplate,TranslationStore,build_tasks
from localization_toolkit.translation.exchange import import_jsonl,ExchangeError
p,result_path,gate=sys.argv[1:]
s=TranslationStore(p)
original=s.snapshot_fingerprint
def synchronized_snapshot():
 value=original()
 Path(gate, str(__import__('os').getpid())).touch()
 while len(list(Path(gate).iterdir())) < 2: time.sleep(.01)
 return value
s.snapshot_fingerprint=synchronized_snapshot
text='hello {name}'
slot=TextSlot('slot-1','synthetic',TextCategory.DIALOGUE,'body',text,text,None,0,len(text),0,1,1,'TEXT',0,0)
tasks=build_tasks((slot,),glossary=Glossary('g1',()),prompt_template=PromptTemplate(),source_language='en',target_language='zh-Hans',rule_version='r1')
try:
 import_jsonl(result_path,tasks,glossary=Glossary('g1',()),store=s)
 print('committed')
except ExchangeError:
 print('conflict')
"""
            env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / "src"))
            procs = [subprocess.Popen([sys.executable, "-c", code, str(path), str(result_path), str(gate)],
                                      env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for result_path in results]
            outputs = [proc.communicate(timeout=10)[0].strip() for proc in procs]
            self.assertCountEqual(outputs, ["committed", "conflict"])
            saved = store.get(task.task_id)
            self.assertEqual(saved.status.value, "pending_review")
            self.assertIn(saved.target_text, {"left {name}", "right {name}"})

    def test_lock_is_released_after_writer_process_terminates(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            marker = Path(temp) / "locked"
            code = ("import sys,time; from localization_toolkit.translation.store import TranslationStore; "
                    "from pathlib import Path; s=TranslationStore(sys.argv[1]); "
                    "ctx=s._exclusive(); ctx.__enter__(); Path(sys.argv[2]).write_text('yes'); time.sleep(30)")
            env = dict(__import__('os').environ, PYTHONPATH=str(Path(__file__).parents[1] / "src"))
            proc = subprocess.Popen([sys.executable, "-c", code, str(path), str(marker)], env=env)
            try:
                deadline = time.time() + 5
                while not marker.exists() and time.time() < deadline: time.sleep(.05)
                self.assertTrue(marker.exists())
                store = TranslationStore(path); store.lock_timeout_seconds = .15
                with self.assertRaises(TranslationStoreError):
                    with store._exclusive(): pass
            finally:
                proc.kill(); proc.wait(timeout=5)
            store.lock_timeout_seconds = 1
            with store._exclusive(): pass


if __name__ == "__main__": unittest.main()
