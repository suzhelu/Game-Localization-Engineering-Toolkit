"""Deploy and restore one synthetic file entirely inside a temporary folder."""
from __future__ import annotations

import tempfile
from pathlib import Path

from localization_toolkit.deployment import (
    DeploymentPlan,
    DeploymentTarget,
    capture_fixed_snapshot,
    deploy,
    rollback_transaction,
)
from localization_toolkit.hashing import hash_file


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="localization-deployment-demo-") as temporary:
        root = Path(temporary)
        payload_root = root / "payload"
        target_root = root / "synthetic-target"
        transaction_root = root / "transactions" / "demo-001"
        (payload_root / "Data").mkdir(parents=True)
        (target_root / "Data").mkdir(parents=True)
        transaction_root.parent.mkdir()
        payload = payload_root / "Data" / "sample.bin"
        target = target_root / "Data" / "sample.bin"
        payload.write_bytes(b"synthetic candidate bytes\n")
        target.write_bytes(b"synthetic original bytes\n")
        plan = DeploymentPlan(
            source_root=payload_root,
            target_root=target_root,
            transaction_root=transaction_root,
            expected_version="demo-1",
            targets=[DeploymentTarget(
                source_path="Data/sample.bin",
                target_path="Data/sample.bin",
                before_sha256=hash_file(target),
                candidate_sha256=hash_file(payload),
            )],
            fixed_snapshot=capture_fixed_snapshot(target_root),
        )
        result = deploy(plan, observed_version="demo-1")
        assert result["state"] == "deployed"
        assert hash_file(target) == plan.targets[0].candidate_sha256
        restored = rollback_transaction(target_root, transaction_root)
        assert restored["state"] == "rolled_back"
        assert target.read_bytes() == b"synthetic original bytes\n"
        print("Synthetic deployment and verified rollback passed.")


if __name__ == "__main__":
    main()
