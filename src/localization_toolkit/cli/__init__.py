"""A thin command line over the toolkit's tested public APIs."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence
from pathlib import Path

from ..adapters.registry import default_registry
from .config import Project
from .workflow import (
    WorkflowError, compile_project, deploy_command, extract, initialize, inspect_project,
    package, recover_command, review, status, translate, verify,
)


def _project_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", default=".", help="Project directory containing localize.json (default: current directory)")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="localize", description="Project workflow CLI using the toolkit's existing adapters")
    commands = root.add_subparsers(dest="command", required=True)

    command = commands.add_parser("adapters", help="List registered adapters and discovered plugin IDs")
    command.add_argument("--load", help="Explicitly load one trusted installed adapter plugin and show its metadata")
    command = commands.add_parser("probe", help="Read-only probe of decoded UTF-8 script text")
    command.add_argument("file")
    command.add_argument("--adapter", help="Probe only this explicit adapter ID")
    command.add_argument("--project", help="Use adapter ID/options from localize.json")
    command.add_argument("--include-plugins", action="store_true",
                         help="Load all installed adapter plugins to check for ambiguous matches")

    command = commands.add_parser("init", help="Create a portable project skeleton without game-specific defaults")
    command.add_argument("path", nargs="?", default=".")
    command.add_argument("--name", default="Localization Project")

    command = commands.add_parser("inspect", help="Validate project configuration and list configured inputs")
    _project_option(command)
    command = commands.add_parser("extract", help="Extract supported text slots and record source fingerprints")
    _project_option(command)
    command = commands.add_parser("translate", help="Run configured local Mock Provider batches")
    _project_option(command)
    command = commands.add_parser("review", help="List results or explicitly approve/reject one slot")
    _project_option(command)
    command.add_argument("--approve-slot")
    command.add_argument("--reject-slot")
    command.add_argument("--reviewer")
    command.add_argument("--note", default="")
    command = commands.add_parser("compile", help="Compile only current, explicitly reviewed source")
    _project_option(command)
    command = commands.add_parser("verify", help="Verify current compiled artifacts and optional package")
    _project_option(command)
    command = commands.add_parser("package", help="Build an overlay from the explicit package.files list")
    _project_option(command)
    command = commands.add_parser("deploy", help="Precheck deployment; writes require two explicit confirmations")
    _project_option(command)
    command.add_argument("--apply", action="store_true")
    command.add_argument("--confirm-project")
    command.add_argument("--observed-version")
    command = commands.add_parser("status", help="Show stage freshness, recent operations, and next steps")
    _project_option(command)
    command = commands.add_parser("recover", help="Inspect a configured deployment transaction; recovery is separately confirmed")
    _project_option(command)
    command.add_argument("--apply", action="store_true")
    command.add_argument("--confirm-transaction")
    return root


def _dump(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        if args.command == "init":
            path = initialize(args.path, args.name)
            _dump({"status": "initialized", "project_directory": str(path), "config": "localize.json",
                   "next_steps": ["add explicit entries to inputs.scripts", "localize inspect"]})
            return 0
        if args.command == "adapters":
            registry = default_registry()
            if args.load:
                registry.create(args.load)
            _dump({"schema_version": 1,
                   "registered": [{"adapter_id": item.adapter_id, "version": item.version,
                                   "formats": list(item.formats), "capabilities": sorted(item.capabilities),
                                   "description": item.description} for item in registry.descriptors()],
                   "available_entry_points": sorted(set(registry.adapter_ids) -
                                                     {item.adapter_id for item in registry.descriptors()})})
            return 0
        if args.command == "probe":
            registry = default_registry()
            options = {}
            adapter_id = args.adapter
            if args.project:
                project = Project.load(args.project)
                configured = project.config["general"]["adapter"]
                if adapter_id and adapter_id != configured:
                    raise WorkflowError("--adapter conflicts with the project's configured adapter")
                adapter_id = configured
                options = project.config["game_specific"]["adapter_options"]
            if args.include_plugins and adapter_id:
                raise WorkflowError("--include-plugins cannot be combined with --adapter or --project")
            if args.include_plugins:
                registry.load_all_plugins()
            source = Path(args.file).read_text(encoding="utf-8")
            if adapter_id:
                registry.create(adapter_id, options)
                result = registry.probe(source, adapter_id=adapter_id, options=options)
            else:
                result = registry.probe(source)
            _dump(result)
            return 0
        project = Project.load(args.project)
        if args.command == "inspect":
            _dump(inspect_project(project))
        elif args.command == "extract":
            _dump(extract(project))
        elif args.command == "translate":
            _dump(translate(project))
        elif args.command == "review":
            if args.approve_slot and args.reject_slot:
                raise WorkflowError("Choose either --approve-slot or --reject-slot")
            _dump(review(project, approve_slot=args.approve_slot, reject_slot=args.reject_slot,
                         reviewer=args.reviewer, note=args.note))
        elif args.command == "compile":
            _dump(compile_project(project))
        elif args.command == "verify":
            _dump(verify(project))
        elif args.command == "package":
            _dump(package(project))
        elif args.command == "deploy":
            result = deploy_command(project, apply=args.apply, confirm_project=args.confirm_project,
                                    observed_version=args.observed_version)
            _dump(result)
            return 2 if result.get("status") == "precheck_failed" else 0
        elif args.command == "status":
            _dump(status(project))
        elif args.command == "recover":
            _dump(recover_command(project, apply=args.apply, confirm_transaction=args.confirm_transaction))
        else:  # pragma: no cover - argparse enforces the command set
            raise WorkflowError(f"Unsupported command: {args.command}")
        return 0
    except Exception as exc:
        print(f"localize: error [LOCALIZE_ERROR]: {exc}\nNext: check the command help, project configuration, and reported adapter evidence.", file=sys.stderr)
        return 2


__all__ = ["main", "parser"]
