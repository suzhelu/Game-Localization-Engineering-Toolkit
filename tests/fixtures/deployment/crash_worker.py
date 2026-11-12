"""Subprocess worker used to prove recovery after an actual process exit."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))

import localization_toolkit.deployment.transaction as transaction
from localization_toolkit.deployment import DeploymentPlan, DeploymentTarget, deploy, recover_transaction, rollback_transaction


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("deploy", "recover", "rollback"))
    parser.add_argument("config")
    parser.add_argument("--exit-after-replace", type=int, default=1)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    original_replace = transaction._replace_atomically
    count = 0

    def replace_then_exit(source: Path, destination: Path) -> None:
        nonlocal count
        original_replace(source, destination)
        count += 1
        if count == args.exit_after_replace:
            os._exit(91)

    transaction._replace_atomically = replace_then_exit
    if args.operation == "deploy":
        targets = [DeploymentTarget(**row) for row in config["targets"]]
        plan = DeploymentPlan(
            source_root=config["source_root"],
            target_root=config["target_root"],
            transaction_root=config["transaction_root"],
            expected_version=config["expected_version"],
            targets=targets,
            fixed_snapshot=config["fixed_snapshot"],
        )
        deploy(plan, observed_version=config["observed_version"])
    elif args.operation == "recover":
        recover_transaction(config["target_root"], config["transaction_root"])
    else:
        rollback_transaction(config["target_root"], config["transaction_root"])


if __name__ == "__main__":
    main()
