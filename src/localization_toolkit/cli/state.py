"""Private local project state, stage fingerprints, and operation history."""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import Project


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def state_path(project: Project) -> Path:
    path = project.path(project.config["paths"]["state_dir"])
    path.mkdir(parents=True, exist_ok=True)
    return path / "state.json"


def read_state(project: Project) -> dict[str, Any]:
    path = state_path(project)
    if not path.exists():
        return {"schema_version": 1, "stages": {}, "operations": [], "transactions": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read project state: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("stages"), dict) or not isinstance(value.get("operations"), list):
        raise RuntimeError("Project state has an unsupported or malformed structure")
    value.setdefault("transactions", [])
    return value


def write_state(project: Project, state: dict[str, Any]) -> None:
    destination = state_path(project)
    payload = (json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temp_name = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, destination)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def require_fresh(project: Project, state: dict[str, Any], stage: str, fingerprint: str) -> dict[str, Any]:
    record = state["stages"].get(stage)
    if not isinstance(record, dict) or record.get("fingerprint") != fingerprint:
        raise RuntimeError(f"Stage {stage!r} is missing or stale; rerun the upstream command first")
    return record


@contextmanager
def operation(project: Project, command: str) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    state = read_state(project)
    row = {"operation_id": uuid.uuid4().hex, "command": command, "status": "running", "started_at": now(),
           "configuration_fingerprint": project.fingerprint(), "error": None, "summary": None}
    state["operations"].append(row)
    write_state(project, state)
    try:
        yield state, row
    except Exception as exc:
        row["status"] = "failed"
        row["finished_at"] = now()
        row["error"] = f"{type(exc).__name__}: {exc}"
        write_state(project, state)
        raise
    else:
        row["status"] = "succeeded"
        row["finished_at"] = now()
        write_state(project, state)
