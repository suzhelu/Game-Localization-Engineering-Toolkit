"""Build two synthetic BGI inputs with an offline compiler and envelope validator."""
from __future__ import annotations

import hashlib
import json
import struct
import tempfile
from pathlib import Path

from localization_toolkit.adapters.bgi import BgiAdapter
from localization_toolkit.compilation import (
    BuildMode,
    BuildPlan,
    BuildTarget,
    MGSCV1Validator,
    MockCompilerAdapter,
    build,
    capture_original_source,
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


def make_synthetic_mgsc() -> bytes:
    name = b"main"
    return (b"MGSC" + struct.pack("<iiii", 1, 1, 1, 1)
            + bytes([len(name)]) + name + struct.pack("<ii", 0, 0) + b"\x00")


def main() -> None:
    source = 'OutputLineAll("Synthetic source", "Synthetic reference", 0);\n'
    adapter = BgiAdapter()
    extracted = adapter.extract(source, "synthetic-example.txt")
    glossary = Glossary("synthetic-glossary-v1", ())
    template = PromptTemplate()

    with tempfile.TemporaryDirectory(prefix="localization-build-example-") as temporary:
        root = Path(temporary)
        inputs = root / "inputs"
        inputs.mkdir()
        store = TranslationStore(root / "translation-state.json")
        tasks = build_tasks(extracted.slots, glossary=glossary, prompt_template=template,
                            source_language="synthetic", target_language="synthetic-target",
                            rule_version="demo-v1", special_rules="Do not add facts.")
        provider = MockProvider(lambda request: [
            ProviderResult(task.slot_id, "Synthetic reviewed text") for task in request.tasks
        ])
        run_batches(extracted.slots, glossary=glossary, prompt_template=template,
                    provider=provider, store=store, source_language="synthetic",
                    target_language="synthetic-target", rule_version="demo-v1",
                    special_rules="Do not add facts.")
        for task in tasks:
            approve_translation(task, store=store, glossary=glossary, reviewer="synthetic-reviewer")

        translated = prepare_reviewed_source(
            adapter, extracted, input_root=inputs, relative_path="translated.txt",
            glossary=glossary, prompt_template=template, store=store,
            source_language="synthetic", target_language="synthetic-target",
            rule_version="demo-v1", special_rules="Do not add facts.",
        )
        control = capture_original_source(
            adapter, source, script_id="synthetic-example.txt", input_root=inputs,
            relative_path="control.txt",
        )

        artifact = make_synthetic_mgsc()
        artifact_hash = hashlib.sha256(artifact).hexdigest().upper()

        def mock_compile(request):
            request.output_path.write_bytes(artifact)
            return 0, "synthetic compile complete", ""

        plan = BuildPlan(
            inputs, root / "published-build",
            (
                BuildTarget(control, "control/script.mg", BuildMode.ORIGINAL_ROUNDTRIP, artifact_hash),
                BuildTarget(translated, "translated/script.mg", BuildMode.TRANSLATED, artifact_hash),
            ),
            expected_compiler_version="offline-mock-v1",
            expected_artifact_index={
                "control/script.mg": artifact_hash,
                "translated/script.mg": artifact_hash,
            },
        )
        report = build(plan, compiler=MockCompilerAdapter("offline-mock-v1", mock_compile),
                       validators=(MGSCV1Validator(),))
        print(json.dumps({"status": report.status, "build_id": report.build_id,
                          "items": len(report.items), "output_directory": report.output_directory,
                          "runtime_verified": False}, indent=2))
        if report.status != "succeeded":
            raise SystemExit(report.error or "synthetic build failed")


if __name__ == "__main__":
    main()
