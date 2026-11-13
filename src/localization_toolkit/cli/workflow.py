"""Project-oriented orchestration built only from the toolkit's public APIs."""
from __future__ import annotations

import hashlib
import json
import struct
import uuid
from pathlib import Path
from typing import Any

from ..adapters.registry import RegisteredTextAdapter, default_registry
from ..compilation import (
    BuildMode, BuildPlan, BuildTarget, CommandLineCompilerAdapter, MGSCV1Validator,
    MockCompilerAdapter, build, capture_original_source, prepare_reviewed_source,
)
from ..deployment import DeploymentPlan, DeploymentTarget, capture_fixed_snapshot, deploy, inspect_transaction, recover_transaction
from ..hashing import hash_file
from ..manifest import load_manifest, validate_manifest
from ..models import ExtractionResult
from ..packages import OverlayPackagePlan, PackageFile, audit_overlay_package, build_overlay_package
from ..path_safety import compare_snapshots, normalize_target, snapshot_tree
from ..translation import (
    Glossary, MockProvider, PromptTemplate, ProviderResult, TranslationStore,
    approve_translation, build_tasks, reject_translation, run_batches,
)
from ..translation.exchange import export_jsonl, import_jsonl
from ..translation.models import TranslationStatus
from .config import Project, ProjectConfigError
from .state import now, operation, read_state, require_fresh, write_state


class WorkflowError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _adapter(project: Project) -> RegisteredTextAdapter:
    try:
        return default_registry().create(
            project.config["general"]["adapter"], project.config["game_specific"]["adapter_options"])
    except Exception as exc:
        raise WorkflowError(f"Could not create configured adapter: {exc}") from exc


def _glossary(project: Project) -> Glossary:
    return Glossary.load(project.path(project.config["paths"]["glossary"]))


def _rules(project: Project) -> str:
    return project.path(project.config["paths"]["rules"]).read_text(encoding="utf-8")


def _prompt(project: Project) -> PromptTemplate:
    return PromptTemplate(project.config["general"]["prompt_template"])


def _store(project: Project) -> TranslationStore:
    return TranslationStore(project.path(project.config["paths"]["state_dir"]) / "translations.json")


def _extract_all(project: Project) -> dict[str, ExtractionResult]:
    adapter = _adapter(project)
    results: dict[str, ExtractionResult] = {}
    for row in project.config["inputs"]["scripts"]:
        try:
            source = project.script_path(row, "source").read_text(encoding="utf-8")
            reference = project.script_path(row, "reference").read_text(encoding="utf-8") if row.get("reference") else None
            parsed = adapter.extract(source, row["id"], reference)
        except (OSError, UnicodeError, ValueError) as exc:
            raise WorkflowError(f"Could not extract script {row['id']}: {exc}") from exc
        results[row["id"]] = parsed
    if not results:
        raise WorkflowError("No input scripts are configured")
    return results


def _stage(state: dict[str, Any], name: str, fingerprint: str, **details: Any) -> None:
    state["stages"][name] = {"fingerprint": fingerprint, "completed_at": now(), **details}


def inspect_project(project: Project) -> dict[str, Any]:
    sources = []
    for row in project.config["inputs"]["scripts"]:
        source = project.path(row["source"])
        reference = project.path(row["reference"]) if row.get("reference") else None
        sources.append({"id": row["id"], "source": row["source"], "source_exists": source.is_file(),
                        "reference": row.get("reference"), "reference_exists": reference.is_file() if reference else None,
                        "output": row["output"]})
    try:
        fingerprint = project.fingerprint()
        fingerprint_issue = None
    except Exception as exc:
        fingerprint = None
        fingerprint_issue = str(exc)
    return {"project": project.config["project"]["name"], "adapter": project.config["general"]["adapter"],
            "provider": project.config["translation"]["provider"], "compiler": project.config["compiler"]["kind"],
            "game_specific_settings": project.config["game_specific"], "scripts": sources,
            "configuration_fingerprint": fingerprint, "input_issue": fingerprint_issue}


def extract(project: Project) -> dict[str, Any]:
    with operation(project, "extract") as (state, op):
        fingerprint = project.fingerprint()
        results = _extract_all(project)
        scripts = [{"id": script_id, "source_sha256": parsed.source_sha256, "slots": len(parsed.slots),
                    "unresolved": parsed.unresolved_count, "slot_ids": [slot.slot_id for slot in parsed.slots],
                    "structure_sha256": hashlib.sha256(_canonical(parsed.structure_signature)).hexdigest().upper()}
                   for script_id, parsed in results.items()]
        _stage(state, "extract", fingerprint, scripts=scripts, total_slots=sum(row["slots"] for row in scripts))
        op["summary"] = {"scripts": len(scripts), "slots": sum(row["slots"] for row in scripts),
                          "unresolved": sum(row["unresolved"] for row in scripts)}
        return op["summary"]


def _fresh_extractions(project: Project, state: dict[str, Any], fingerprint: str) -> dict[str, ExtractionResult]:
    require_fresh(project, state, "extract", fingerprint)
    results = _extract_all(project)
    recorded = {row["id"]: row for row in state["stages"]["extract"]["scripts"]}
    for script_id, parsed in results.items():
        row = recorded.get(script_id)
        if row is None or row["source_sha256"] != parsed.source_sha256 or row["slot_ids"] != [slot.slot_id for slot in parsed.slots]:
            raise WorkflowError(f"Extraction result for {script_id} is stale; rerun extract")
    return results


def _tasks(project: Project, extractions: dict[str, ExtractionResult]) -> tuple[Any, ...]:
    general = project.config["general"]
    glossary = _glossary(project)
    prompt = _prompt(project)
    rules = _rules(project)
    all_tasks = []
    for parsed in extractions.values():
        all_tasks.extend(build_tasks(parsed.slots, glossary=glossary, prompt_template=prompt,
                                     source_language=general["source_language"], target_language=general["target_language"],
                                     rule_version=general["rule_version"], special_rules=rules))
    return tuple(all_tasks)


def translate(project: Project) -> dict[str, Any]:
    with operation(project, "translate") as (state, op):
        fingerprint = project.fingerprint()
        extractions = _fresh_extractions(project, state, fingerprint)
        prefix = project.config["translation"]["mock_prefix"]
        provider = MockProvider(lambda request: [ProviderResult(task.slot_id, prefix + task.source_text) for task in request.tasks])
        slots = [slot for parsed in extractions.values() for slot in parsed.slots]
        general = project.config["general"]
        result = run_batches(slots, glossary=_glossary(project), prompt_template=_prompt(project), provider=provider,
                             store=_store(project), source_language=general["source_language"],
                             target_language=general["target_language"], rule_version=general["rule_version"],
                             batch_size=general["batch_size"], special_rules=_rules(project))
        _stage(state, "translate", fingerprint, **result)
        op["summary"] = result
        return result


def translate_export(project: Project, output: str, *, limit: int = 0, offset: int = 0) -> dict[str, Any]:
    with operation(project, "translate-export") as (state, op):
        fingerprint = project.fingerprint()
        tasks = _tasks(project, _fresh_extractions(project, state, fingerprint))
        store = _store(project)
        pending = [task for task in tasks
                   if (store.get(task.task_id) is None or store.get(task.task_id).status not in
                       {TranslationStatus.PENDING_REVIEW, TranslationStatus.APPROVED})]
        count = export_jsonl(pending, project.path(output), batch_size=limit, offset=offset)
        result = {"schema_version": 1, "exported": count, "total_pending": len(pending),
                  "offset": offset, "path": output}
        op["summary"] = result
        return result


def translate_import(project: Project, input_path: str) -> dict[str, Any]:
    with operation(project, "translate-import") as (state, op):
        fingerprint = project.fingerprint()
        tasks = _tasks(project, _fresh_extractions(project, state, fingerprint))
        result = import_jsonl(project.path(input_path), tasks,
                              glossary=_glossary(project), store=_store(project))
        _stage(state, "translate", fingerprint, imported=result["imported"], pending_review=result["pending_review"])
        op["summary"] = result
        return result


def review(project: Project, *, approve_slot: str | None = None, reject_slot: str | None = None,
           reviewer: str | None = None, note: str = "") -> dict[str, Any]:
    with operation(project, "review") as (state, op):
        fingerprint = project.fingerprint()
        require_fresh(project, state, "translate", fingerprint)
        tasks = _tasks(project, _fresh_extractions(project, state, fingerprint))
        store, glossary = _store(project), _glossary(project)
        if approve_slot or reject_slot:
            if not reviewer:
                raise WorkflowError("A reviewer identifier is required for approve/reject")
            task = next((item for item in tasks if item.slot_id == (approve_slot or reject_slot)), None)
            if task is None:
                raise WorkflowError("Unknown slot ID; no translation record was changed")
            if approve_slot:
                record = approve_translation(task, store=store, glossary=glossary, reviewer=reviewer, review_note=note)
            else:
                record = reject_translation(task, store=store, reviewer=reviewer, review_note=note)
            op["summary"] = {"slot_id": task.slot_id, "status": record.status.value, "reviewer": reviewer}
        rows = []
        approved = 0
        for task in tasks:
            record = store.get(task.task_id)
            status = record.status.value if record else "missing"
            approved += status == "approved"
            rows.append({"slot_id": task.slot_id, "script": task.script, "source": task.source_text,
                         "target": record.target_text if record else None, "status": status,
                         "issues": [{"code": issue.code, "message": issue.message} for issue in (record.issues if record else ())],
                         "reviewer": record.reviewer if record else None})
        _stage(state, "review", fingerprint, approved=approved, total=len(tasks))
        summary = {"approved": approved, "total": len(tasks), "records": rows}
        if op["summary"] is None:
            op["summary"] = {"approved": approved, "total": len(tasks), "action": "list"}
        return summary


def _compiler(project: Project):
    config = project.config["compiler"]
    if config["kind"] == "mock":
        def compile_synthetic(request):
            source = request.input_path.read_bytes()
            payload = hashlib.sha256(source).digest()
            header = b"MGSC" + struct.pack("<iiii", 1, 1, 1, len(payload))
            block_name = b"main"
            output = header + bytes([len(block_name)]) + block_name + struct.pack("<ii", 0, 0) + payload
            request.output_path.write_bytes(output)
            return 0, "synthetic compilation completed", ""
        return MockCompilerAdapter(config["version"], compile_synthetic)
    cwd = project.path(config["cwd"]) if config["cwd"] else None
    return CommandLineCompilerAdapter(config["executable"], config["arguments"],
                                      version_arguments=config["version_arguments"],
                                      compiler_id=config["id"], cwd=cwd)


def compile_project(project: Project) -> dict[str, Any]:
    with operation(project, "compile") as (state, op):
        fingerprint = project.fingerprint()
        extractions = _fresh_extractions(project, state, fingerprint)
        require_fresh(project, state, "translate", fingerprint)
        if project.config["validation"]["original_roundtrip_required"]:
            missing_baselines = [row["id"] for row in project.config["inputs"]["scripts"]
                                 if not row.get("baseline_mg")]
            if missing_baselines:
                raise WorkflowError("Original byte roundtrip is required but baselines are missing for: " + ", ".join(missing_baselines))
        tasks = _tasks(project, extractions)
        store = _store(project)
        missing = [task.slot_id for task in tasks if (record := store.get(task.task_id)) is None or record.status.value != "approved" or not record.reviewer]
        if missing:
            raise WorkflowError(f"Human review gate is incomplete for {len(missing)} slot(s); run review explicitly. First: {missing[0]}")
        run_id = uuid.uuid4().hex
        source_root = project.root
        state_base = Path(project.config["paths"]["state_dir"])
        targets: list[BuildTarget] = []
        for row in project.config["inputs"]["scripts"]:
            parsed = extractions[row["id"]]
            if not parsed.slots:
                continue
            translated = prepare_reviewed_source(
                _adapter(project), parsed, input_root=source_root,
                relative_path=(state_base / "staged" / run_id / f"{row['id']}.txt").as_posix(),
                glossary=_glossary(project), prompt_template=_prompt(project), store=store,
                source_language=project.config["general"]["source_language"],
                target_language=project.config["general"]["target_language"],
                rule_version=project.config["general"]["rule_version"], special_rules=_rules(project),
                reference_source=project.script_path(row, "reference").read_text(encoding="utf-8") if row.get("reference") else None,
            )
            targets.append(BuildTarget(translated, row["output"], BuildMode.TRANSLATED))
            if row.get("baseline_mg"):
                original = capture_original_source(_adapter(project), parsed.source, script_id=row["id"],
                                                  input_root=source_root,
                                                  relative_path=(state_base / "control" / run_id / f"{row['id']}.txt").as_posix())
                targets.append(BuildTarget(original, f"control/{row['output']}", BuildMode.ORIGINAL_ROUNDTRIP,
                                           hash_file(project.path(row["baseline_mg"]))))
        if not targets:
            raise WorkflowError("No compilable translated slots are configured")
        build_root = project.path(project.config["paths"]["build_dir"])
        build_root.mkdir(parents=True, exist_ok=True)
        output_directory = build_root / f"build-{run_id}"
        report_path = project.path(project.config["paths"]["state_dir"]) / f"build-report-{run_id}.json"
        report = build(BuildPlan(source_root, output_directory, tuple(targets), project.config["compiler"]["version"],
                                 timeout_seconds=project.config["compiler"]["timeout_seconds"],
                                 report_path=report_path), compiler=_compiler(project), validators=(MGSCV1Validator(),))
        if report.status != "succeeded":
            raise WorkflowError(f"Build did not publish: {report.status}: {report.error}")
        artifacts_root = output_directory / "artifacts"
        artifact_rows = [item for item in report.items if item.mode == BuildMode.TRANSLATED.value]
        compiled = [{"build_path": item.output_relative_path, "sha256": item.output_sha256,
                     "size_bytes": (artifacts_root / item.output_relative_path).stat().st_size} for item in artifact_rows]
        state["stages"]["compile"] = {"fingerprint": fingerprint, "completed_at": now(),
                                       "build_id": report.build_id, "build_directory": output_directory.relative_to(project.root).as_posix(),
                                       "artifacts": compiled, "report": (output_directory / "build_report.json").relative_to(project.root).as_posix()}
        op["summary"] = {"build_id": report.build_id, "artifacts": len(compiled), "directory": state["stages"]["compile"]["build_directory"]}
        return op["summary"]


def verify(project: Project) -> dict[str, Any]:
    with operation(project, "verify") as (state, op):
        fingerprint = project.fingerprint()
        compiled = require_fresh(project, state, "compile", fingerprint)
        build_directory = project.path(compiled["build_directory"])
        artifacts_root = build_directory / "artifacts"
        manifest_path = build_directory / "artifact_manifest.json"
        manifest = load_manifest(manifest_path)
        issues = validate_manifest(manifest, artifacts_root, verify_files=True)
        if issues:
            raise WorkflowError("Compiled artifact manifest failed: " + "; ".join(issues))
        for row in compiled["artifacts"]:
            if hash_file(artifacts_root / row["build_path"]) != row["sha256"]:
                raise WorkflowError(f"Compiled artifact hash changed: {row['build_path']}")
        result: dict[str, Any] = {"compiled_artifacts": len(compiled["artifacts"]), "compiled_manifest": "passed"}
        package_stage = state["stages"].get("package")
        if package_stage:
            if package_stage.get("fingerprint") != fingerprint or package_stage.get("build_id") != compiled["build_id"]:
                result["package"] = "stale"
            else:
                package_path = project.path(package_stage["directory"])
                result["package"] = audit_overlay_package(package_path)["status"]
        op["summary"] = result
        return result


def package(project: Project) -> dict[str, Any]:
    with operation(project, "package") as (state, op):
        fingerprint = project.fingerprint()
        compiled = require_fresh(project, state, "compile", fingerprint)
        config = project.config["package"]
        if not config["files"]:
            raise WorkflowError("package.files is empty; explicitly list authorized build paths before packaging")
        output_parent = project.path(project.config["paths"]["package_dir"])
        output_parent.mkdir(parents=True, exist_ok=True)
        output = output_parent / f"{config['name']}-{config['version']}-{uuid.uuid4().hex[:8]}"
        by_path = {row["build_path"].casefold(): row for row in compiled["artifacts"]}
        files = []
        for row in config["files"]:
            artifact = by_path.get(row["build_path"].replace("\\", "/").casefold())
            if artifact is None:
                raise WorkflowError(f"Package requested an artifact outside the latest explicit build: {row['build_path']}")
            files.append(PackageFile(artifact["build_path"], row["archive_path"], artifact["sha256"]))
        result = build_overlay_package(OverlayPackagePlan(project.path(compiled["build_directory"]) / "artifacts",
                                                          output, config["name"], config["version"], tuple(files)))
        _stage(state, "package", fingerprint, build_id=compiled["build_id"], directory=output.relative_to(project.root).as_posix(),
               archive_sha256=result["archive_sha256"], file_count=result["file_count"])
        op["summary"] = result
        return result


def status(project: Project) -> dict[str, Any]:
    state = read_state(project)
    try:
        fingerprint = project.fingerprint()
    except Exception as exc:
        fingerprint = None
        stale_reason = str(exc)
    else:
        stale_reason = None
    stages = {}
    for name in ("extract", "translate", "review", "compile", "package"):
        record = state["stages"].get(name)
        current = bool(record and fingerprint is not None and record.get("fingerprint") == fingerprint)
        if name == "package" and current:
            current = record.get("build_id") == state.get("stages", {}).get("compile", {}).get("build_id")
        stages[name] = {"state": "missing" if not record else ("current" if current else "stale"),
                        "completed_at": record.get("completed_at") if record else None,
                        "summary": {key: record.get(key) for key in ("total_slots", "approved", "total", "build_id", "file_count") if record and key in record}}
    next_steps = []
    if stages["extract"]["state"] != "current": next_steps.append("extract")
    elif stages["translate"]["state"] != "current": next_steps.append("translate")
    elif stages["review"]["state"] != "current" or state["stages"]["review"].get("approved", 0) < state["stages"]["review"].get("total", 0): next_steps.append("review")
    elif stages["compile"]["state"] != "current": next_steps.append("compile")
    elif stages["package"]["state"] != "current": next_steps.append("package")
    else: next_steps.append("verify")
    return {"project": project.config["project"]["name"], "stages": stages,
            "latest_operation": state["operations"][-1] if state["operations"] else None,
            "recent_operations": state["operations"][-10:], "transactions": state.get("transactions", []),
            "recommended_next_steps": next_steps, "stale_reason": stale_reason}


def deploy_command(project: Project, *, apply: bool, confirm_project: str | None, observed_version: str | None) -> dict[str, Any]:
    config = project.config["deployment"]
    if not config["enabled"]:
        return {"status": "plan_only", "message": "Deployment is disabled; configure an explicit target and fixed snapshot to enable it."}
    if not apply:
        state = read_state(project)
        fingerprint = project.fingerprint()
        compiled = require_fresh(project, state, "compile", fingerprint)
        source_root = project.path(compiled["build_directory"]) / "artifacts"
        target_root = Path(config["target_root"])
        snapshot = json.loads(project.path(config["fixed_snapshot"]).read_text(encoding="utf-8"))
        live_snapshot = capture_fixed_snapshot(target_root)
        current = snapshot_tree(target_root)
        diff = compare_snapshots(snapshot["files"], current, root=target_root, authorized_paths=())
        checks = [{"root_matches": snapshot.get("root_fingerprint") == live_snapshot.get("root_fingerprint"),
                   "fixed_snapshot_matches": not bool(diff["added"] or diff["removed"] or diff["changed"]),
                   "added": len(diff["added"]), "removed": len(diff["removed"]), "changed": len(diff["changed"])}]
        for row in config["targets"]:
            source = source_root / row["build_path"]
            target = normalize_target(target_root, row["target_path"]).full_path
            source_hash = hash_file(source) if source.is_file() else None
            target_hash = hash_file(target) if target.is_file() else None
            checks.append({"build_path": row["build_path"], "target_path": row["target_path"],
                           "candidate_hash_matches": source_hash == row["candidate_sha256"].upper(),
                           "before_hash_matches": target_hash == row["before_sha256"].upper()})
        passed = all(item.get("root_matches", True) and item.get("fixed_snapshot_matches", True) and item.get("candidate_hash_matches", True)
                     and item.get("before_hash_matches", True) for item in checks)
        return {"status": "precheck_passed" if passed else "precheck_failed",
                "message": "No files were written. Apply requires --apply and exact project confirmation." if passed else "Precheck failed; deployment is blocked.",
                "target_root": config["target_root"], "target_count": len(config["targets"]),
                "expected_version": config["expected_version"], "checks": checks}
    expected_name = project.config["project"]["name"]
    if confirm_project != expected_name:
        raise WorkflowError("Deployment requires --apply and --confirm-project with the exact configured project name")
    if not observed_version or observed_version != config["expected_version"]:
        raise WorkflowError("Observed version is missing or does not match the configured expected_version")
    state = read_state(project)
    fingerprint = project.fingerprint()
    compiled = require_fresh(project, state, "compile", fingerprint)
    source_root = project.path(compiled["build_directory"]) / "artifacts"
    fixed_snapshot = json.loads(project.path(config["fixed_snapshot"]).read_text(encoding="utf-8"))
    targets = [DeploymentTarget(row["build_path"], row["target_path"], row["before_sha256"], row["candidate_sha256"])
               for row in config["targets"]]
    plan = DeploymentPlan(source_root, config["target_root"], config["transaction_root"],
                          config["expected_version"], targets, fixed_snapshot)
    with operation(project, "deploy") as (state, op):
        result = deploy(plan, observed_version=observed_version)
        state["transactions"].append({"transaction_id": result["transaction_id"], "state": result["state"],
                                      "transaction_root": config["transaction_root"], "recorded_at": now()})
        op["summary"] = result
        return result


def recover_command(project: Project, *, apply: bool, confirm_transaction: str | None) -> dict[str, Any]:
    config = project.config["deployment"]
    if not config["enabled"]:
        raise WorkflowError("Recovery requires an explicitly configured deployment target and transaction_root")
    inspect = inspect_transaction(config["target_root"], config["transaction_root"])
    if not apply:
        return {"status": "inspection_only", "transaction_id": inspect["transaction_id"], "state": inspect["state"],
                "error": inspect.get("error"), "message": "No recovery was applied; pass --apply and the exact transaction ID after reviewing the state."}
    if confirm_transaction != inspect["transaction_id"]:
        raise WorkflowError("Recovery requires --apply and --confirm-transaction with the exact inspected transaction ID")
    with operation(project, "recover") as (state, op):
        result = recover_transaction(config["target_root"], config["transaction_root"])
        state["transactions"].append({"transaction_id": result["transaction_id"], "state": result["state"],
                                      "transaction_root": config["transaction_root"], "recorded_at": now()})
        op["summary"] = result
        return result


def initialize(root: str | Path, name: str) -> Path:
    project_root = Path(root).absolute()
    project_root.mkdir(parents=True, exist_ok=True)
    config_path = project_root / Project.CONFIG_NAME
    if config_path.exists():
        raise ProjectConfigError(f"Refusing to overwrite existing project config: {config_path}")
    directories = ["config", "input", ".localize", "build", "releases"]
    for directory in directories:
        (project_root / directory).mkdir(parents=True, exist_ok=True)
    (project_root / "config" / "glossary.json").write_text('{"version":"v1","entries":[]}\n', encoding="utf-8")
    (project_root / "config" / "rules.txt").write_text("Preserve ambiguity. Do not add facts absent from the source.\n", encoding="utf-8")
    config = {
        "schema_version": 1,
        "project": {"name": name},
        "general": {"adapter": "bgi", "source_language": "ja", "target_language": "zh-CN", "batch_size": 8,
                    "rule_version": "rules-v1", "prompt_template": "Translate {source_text} from {source_language} to {target_language}. Preserve tags and placeholders; do not add unstated facts."},
        "game_specific": {"adapter_options": {}},
        "inputs": {"scripts": []},
        "translation": {"provider": "mock", "mock_prefix": "[mock] "},
        "compiler": {"kind": "mock", "id": "synthetic-mock-compiler", "version": "mock-1", "executable": None,
                     "arguments": [], "version_arguments": [], "cwd": None, "timeout_seconds": 120},
        "validation": {"compiled_format": "mgsc-v1", "require_human_review": True,
                       "require_resolved_structure": True, "original_roundtrip_required": False},
        "paths": {"glossary": "config/glossary.json", "rules": "config/rules.txt", "state_dir": ".localize",
                  "build_dir": "build", "package_dir": "releases"},
        "package": {"name": "localization-overlay", "version": "0.1.0", "files": []},
        "deployment": {"enabled": False, "target_root": None, "transaction_root": None, "expected_version": None,
                       "fixed_snapshot": None, "targets": []},
    }
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return project_root
