from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from localization_toolkit.deployment import (
    DeploymentPlan,
    DeploymentTarget,
    ManualRecoveryRequired,
    PreflightError,
    TransactionStateError,
    capture_fixed_snapshot,
    deploy,
    inspect_transaction,
    recover_transaction,
    rollback_transaction,
)
from localization_toolkit.hashing import hash_file
from localization_toolkit.path_safety import PathSafetyError


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="loc deploy 中文 ")
        self.base = Path(self.temp.name)
        self.source = self.base / "payload with spaces"
        self.target = self.base / "target 游戏目录"
        self.tx_parent = self.base / "transactions"
        self.source.mkdir()
        self.target.mkdir()
        self.tx_parent.mkdir()
        (self.target / "Data").mkdir()
        (self.source / "new.txt").write_bytes("候选-新文本".encode())
        (self.target / "Data" / "existing.txt").write_bytes(b"original")
        (self.target / "keep.bin").write_bytes(b"keep")
        self.tx = self.tx_parent / "tx-001"
        self.plan = self.make_plan()

    def tearDown(self):
        self.temp.cleanup()

    def make_plan(self, *, fixed=None, target_rel="Data/existing.txt", source_rel="new.txt"):
        destination = self.target / Path(*target_rel.split("/"))
        source = self.source / Path(*source_rel.split("/"))
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"original")
        if not source.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"candidate")
        return DeploymentPlan(
            source_root=self.source,
            target_root=self.target,
            transaction_root=self.tx,
            expected_version="synthetic-v1",
            targets=[DeploymentTarget(
                source_path=source_rel,
                target_path=target_rel,
                before_sha256=hash_file(destination),
                candidate_sha256=hash_file(source),
            )],
            fixed_snapshot=fixed if fixed is not None else capture_fixed_snapshot(self.target),
        )

    def deploy_ok(self):
        return deploy(self.plan, observed_version="synthetic-v1")

    def test_deploy_creates_verified_backup_and_whitelist_diff(self):
        result = self.deploy_ok()
        self.assertEqual(result["state"], "deployed")
        self.assertEqual(hash_file(self.target / "Data/existing.txt"), self.plan.targets[0].candidate_sha256)
        self.assertTrue((self.tx / "backup_manifest.json").is_file())
        self.assertEqual(result["directory_diff"]["outside_whitelist"], [])
        self.assertEqual(inspect_transaction(self.target, self.tx)["state"], "deployed")

    def test_target_mode_is_preserved_for_deploy_and_restore(self):
        target = self.target / "Data/existing.txt"
        target.chmod(0o640)
        self.plan = self.make_plan()
        before_mode = target.stat().st_mode & 0o777
        self.deploy_ok()
        self.assertEqual(target.stat().st_mode & 0o777, before_mode)
        rollback_transaction(self.target, self.tx)
        self.assertEqual(target.stat().st_mode & 0o777, before_mode)

    def test_explicit_rollback_restores_exact_original_tree(self):
        self.deploy_ok()
        result = rollback_transaction(self.target, self.tx)
        self.assertEqual(result["state"], "rolled_back")
        self.assertEqual((self.target / "Data/existing.txt").read_bytes(), b"original")
        self.assertEqual(inspect_transaction(self.target, self.tx)["state"], "rolled_back")

    def test_repeated_apply_cannot_reuse_transaction_or_overwrite_candidate(self):
        self.deploy_ok()
        with self.assertRaises(PreflightError):
            self.deploy_ok()
        self.assertEqual((self.target / "Data/existing.txt").read_bytes(), "候选-新文本".encode())

    def test_version_mismatch_stops_before_transaction_creation(self):
        with self.assertRaises(PreflightError):
            deploy(self.plan, observed_version="other-build")
        self.assertFalse(self.tx.exists())

    def test_original_hash_mismatch_stops_before_transaction_creation(self):
        (self.target / "Data/existing.txt").write_bytes(b"changed elsewhere")
        with self.assertRaises(PreflightError):
            deploy(self.plan, observed_version="synthetic-v1")
        self.assertFalse(self.tx.exists())

    def test_candidate_hash_mismatch_stops_before_transaction_creation(self):
        (self.source / "new.txt").write_bytes(b"tampered")
        with self.assertRaises(PreflightError):
            deploy(self.plan, observed_version="synthetic-v1")
        self.assertFalse(self.tx.exists())

    def test_fixed_snapshot_bound_to_different_root_is_rejected(self):
        other = self.base / "other-root"
        other.mkdir()
        wrong = capture_fixed_snapshot(other)
        plan = self.make_plan(fixed=wrong)
        with self.assertRaises(PreflightError):
            deploy(plan, observed_version="synthetic-v1")
        self.assertFalse(self.tx.exists())

    def test_snapshot_absolute_and_relative_path_root_mismatch_is_rejected(self):
        snapshot = capture_fixed_snapshot(self.target)
        snapshot["files"][0]["path"] = str(self.target / snapshot["files"][0]["path"])
        plan = self.make_plan(fixed=snapshot)
        with self.assertRaises(PreflightError):
            deploy(plan, observed_version="synthetic-v1")
        self.assertFalse(self.tx.exists())

    def test_preexisting_outside_change_fails_closed(self):
        (self.target / "keep.bin").write_bytes(b"outside edit")
        with self.assertRaises(PreflightError):
            deploy(self.plan, observed_version="synthetic-v1")
        self.assertFalse(self.tx.exists())

    def test_absolute_target_inside_root_is_normalized(self):
        absolute = self.target / "Data" / "existing.txt"
        plan = self.make_plan(target_rel=str(absolute))
        result = deploy(plan, observed_version="synthetic-v1")
        self.assertEqual(result["state"], "deployed")
        self.assertEqual(result["directory_diff"]["changed"][0]["path"], "Data/existing.txt")

    def test_parent_traversal_is_rejected(self):
        plan = self.make_plan(target_rel="../outside.txt")
        with self.assertRaises(PreflightError):
            deploy(plan, observed_version="synthetic-v1")

    def test_duplicate_normalized_targets_are_rejected(self):
        one = self.plan.targets[0]
        duplicate = DeploymentTarget("new.txt", "data/EXISTING.txt", one.before_sha256, one.candidate_sha256)
        plan = DeploymentPlan(self.source, self.target, self.tx, "synthetic-v1", [one, duplicate], self.plan.fixed_snapshot)
        with self.assertRaises(PreflightError):
            deploy(plan, observed_version="synthetic-v1")

    def test_existing_transaction_directory_is_never_reused(self):
        self.tx.mkdir()
        with self.assertRaises(PreflightError):
            deploy(self.plan, observed_version="synthetic-v1")

    def test_exception_after_replace_triggers_automatic_rollback(self):
        original = __import__("localization_toolkit.deployment.transaction", fromlist=["_replace_atomically"])._replace_atomically
        calls = 0

        def replace_then_fail(source, destination):
            nonlocal calls
            original(source, destination)
            calls += 1
            if calls == 1:
                raise OSError("injected failure after replacement")

        with patch("localization_toolkit.deployment.transaction._replace_atomically", replace_then_fail):
            with self.assertRaises(Exception):
                self.deploy_ok()
        self.assertEqual((self.target / "Data/existing.txt").read_bytes(), b"original")
        self.assertEqual(inspect_transaction(self.target, self.tx)["state"], "rolled_back")

    def test_backup_corruption_blocks_rollback_without_target_write(self):
        self.deploy_ok()
        (self.tx / "backup/000000.bin").write_bytes(b"corrupt")
        current = (self.target / "Data/existing.txt").read_bytes()
        with self.assertRaises(ManualRecoveryRequired):
            rollback_transaction(self.target, self.tx)
        self.assertEqual((self.target / "Data/existing.txt").read_bytes(), current)
        self.assertEqual(inspect_transaction(self.target, self.tx)["state"], "manual_recovery_required")

    def test_unknown_target_hash_blocks_rollback_without_overwrite(self):
        self.deploy_ok()
        (self.target / "Data/existing.txt").write_bytes(b"unknown third-party bytes")
        with self.assertRaises(ManualRecoveryRequired):
            rollback_transaction(self.target, self.tx)
        self.assertEqual((self.target / "Data/existing.txt").read_bytes(), b"unknown third-party bytes")

    def test_outside_addition_after_deploy_failure_requires_manual_recovery(self):
        original = __import__("localization_toolkit.deployment.transaction", fromlist=["_replace_atomically"])._replace_atomically

        def replace_and_add(source, destination):
            original(source, destination)
            (self.target / "unlisted.txt").write_bytes(b"outside")
            raise OSError("fault after replacement and outside addition")

        with patch("localization_toolkit.deployment.transaction._replace_atomically", replace_and_add):
            with self.assertRaises(Exception):
                self.deploy_ok()
        self.assertEqual((self.target / "Data/existing.txt").read_bytes(), "候选-新文本".encode())
        self.assertEqual(inspect_transaction(self.target, self.tx)["state"], "manual_recovery_required")

    def test_outside_deletion_after_deploy_failure_requires_manual_recovery(self):
        original = __import__("localization_toolkit.deployment.transaction", fromlist=["_replace_atomically"])._replace_atomically

        def replace_and_delete(source, destination):
            original(source, destination)
            (self.target / "keep.bin").unlink()
            raise OSError("fault after replacement and outside deletion")

        with patch("localization_toolkit.deployment.transaction._replace_atomically", replace_and_delete):
            with self.assertRaises(Exception):
                self.deploy_ok()
        self.assertEqual((self.target / "Data/existing.txt").read_bytes(), "候选-新文本".encode())
        self.assertEqual(inspect_transaction(self.target, self.tx)["state"], "manual_recovery_required")

    def test_corrupt_journal_prevents_recovery_without_target_write(self):
        child_config = self.base / "crash-journal.json"
        child_config.write_text(json.dumps(self._process_config(self.tx, self.plan.targets)), encoding="utf-8")
        child = self._run_worker("deploy", child_config)
        self.assertEqual(child.returncode, 91, child.stderr)
        before = (self.target / "Data/existing.txt").read_bytes()
        (self.tx / "transaction.json").write_text("{truncated", encoding="utf-8")
        with self.assertRaises(TransactionStateError):
            recover_transaction(self.target, self.tx)
        self.assertEqual((self.target / "Data/existing.txt").read_bytes(), before)

    def _write_crash_config(self, *, transaction_root=None):
        return self.base / "worker-config.json"

    def _run_worker(self, operation, config_path, after=1):
        worker = Path(__file__).parent / "fixtures" / "deployment" / "crash_worker.py"
        result = subprocess.run(
            [sys.executable, str(worker), operation, str(config_path), "--exit-after-replace", str(after)],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result

    def _process_config(self, tx_path, targets, fixed=None):
        return {
            "source_root": str(self.source),
            "target_root": str(self.target),
            "transaction_root": str(tx_path),
            "expected_version": "synthetic-v1",
            "observed_version": "synthetic-v1",
            "targets": [target.__dict__ for target in targets],
            "fixed_snapshot": fixed if fixed is not None else self.plan.fixed_snapshot,
        }

    def test_real_process_exit_after_replace_is_recovered(self):
        config_path = self.base / "crash-deploy.json"
        config_path.write_text(json.dumps(self._process_config(self.tx, self.plan.targets)), encoding="utf-8")
        child = self._run_worker("deploy", config_path)
        self.assertEqual(child.returncode, 91, child.stderr)
        self.assertEqual((self.target / "Data/existing.txt").read_bytes(), "候选-新文本".encode())
        self.assertEqual(inspect_transaction(self.target, self.tx)["state"], "applying")
        recovered = recover_transaction(self.target, self.tx)
        self.assertEqual(recovered["state"], "rolled_back")
        self.assertEqual((self.target / "Data/existing.txt").read_bytes(), b"original")

    def test_real_process_exit_during_recovery_is_restartable(self):
        # Two explicit files make the worker die after restoring the first one.
        (self.source / "second.txt").write_bytes(b"candidate-two")
        (self.target / "Data/second.txt").write_bytes(b"original-two")
        fixed = capture_fixed_snapshot(self.target)
        targets = [
            DeploymentTarget("new.txt", "Data/existing.txt", hash_file(self.target / "Data/existing.txt"), hash_file(self.source / "new.txt")),
            DeploymentTarget("second.txt", "Data/second.txt", hash_file(self.target / "Data/second.txt"), hash_file(self.source / "second.txt")),
        ]
        tx = self.tx_parent / "tx-crash-recovery"
        plan = DeploymentPlan(self.source, self.target, tx, "synthetic-v1", targets, fixed)
        config_path = self.base / "crash-recovery.json"
        config_path.write_text(json.dumps(self._process_config(tx, targets, fixed)), encoding="utf-8")
        deploy(plan, observed_version="synthetic-v1")
        recovery_child = self._run_worker("rollback", config_path)
        self.assertEqual(recovery_child.returncode, 91, recovery_child.stderr)
        self.assertEqual(inspect_transaction(self.target, tx)["state"], "rollback_in_progress")
        final = recover_transaction(self.target, tx)
        self.assertEqual(final["state"], "rolled_back")
        self.assertEqual((self.target / "Data/existing.txt").read_bytes(), b"original")
        self.assertEqual((self.target / "Data/second.txt").read_bytes(), b"original-two")

    def test_injected_partial_deploy_process_exit_then_recovery(self):
        (self.source / "second.txt").write_bytes(b"candidate-two")
        (self.target / "Data/second.txt").write_bytes(b"original-two")
        fixed = capture_fixed_snapshot(self.target)
        targets = [
            DeploymentTarget("new.txt", "Data/existing.txt", hash_file(self.target / "Data/existing.txt"), hash_file(self.source / "new.txt")),
            DeploymentTarget("second.txt", "Data/second.txt", hash_file(self.target / "Data/second.txt"), hash_file(self.source / "second.txt")),
        ]
        tx = self.tx_parent / "tx-partial-crash"
        config = self.base / "crash-partial.json"
        config.write_text(json.dumps(self._process_config(tx, targets, fixed)), encoding="utf-8")
        child = self._run_worker("deploy", config, after=1)
        self.assertEqual(child.returncode, 91, child.stderr)
        self.assertEqual((self.target / "Data/existing.txt").read_bytes(), "候选-新文本".encode())
        self.assertEqual((self.target / "Data/second.txt").read_bytes(), b"original-two")
        recover_transaction(self.target, tx)
        self.assertEqual((self.target / "Data/existing.txt").read_bytes(), b"original")
        self.assertEqual((self.target / "Data/second.txt").read_bytes(), b"original-two")


if __name__ == "__main__":
    unittest.main()
