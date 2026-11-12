"""Conservative, journaled deployment primitives for localization payloads."""

from .transaction import (
    DeploymentError,
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

__all__ = [
    "DeploymentError",
    "DeploymentPlan",
    "DeploymentTarget",
    "ManualRecoveryRequired",
    "PreflightError",
    "TransactionStateError",
    "capture_fixed_snapshot",
    "deploy",
    "inspect_transaction",
    "recover_transaction",
    "rollback_transaction",
]
