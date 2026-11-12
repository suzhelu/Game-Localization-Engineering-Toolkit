"""Worker for a real process-termination test; writes synthetic data only."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from localization_toolkit.adapters.bgi import BgiAdapter
from localization_toolkit.compilation import BuildMode, BuildPlan, BuildTarget, MockCompilerAdapter, build, capture_original_source
from localization_toolkit.compilation.mgsc_v1 import MGSCV1Validator


input_root = Path(sys.argv[1])
output_parent = Path(sys.argv[2])
source_path = input_root / "source.txt"
source = source_path.read_text(encoding="utf-8")
captured = capture_original_source(
    BgiAdapter(), source, script_id="crash-fixture.txt", input_root=input_root,
    relative_path="staged.txt",
)
plan = BuildPlan(
    input_root, output_parent / "crash-output",
    (BuildTarget(captured, "output.mg", BuildMode.ORIGINAL_ROUNDTRIP, "0" * 64),),
    "mock-crash-v1", timeout_seconds=2,
)
compiler = MockCompilerAdapter("mock-crash-v1", lambda request: os._exit(73))
build(plan, compiler=compiler, validators=(MGSCV1Validator(),))
raise SystemExit("os._exit unexpectedly returned")
