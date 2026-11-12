# Conservative deployment transactions

`localization_toolkit.deployment` applies an explicit set of already-built files to an existing directory tree. It reuses the toolkit's path normalization, reparse-point rejection, SHA-256, manifest and tree-snapshot helpers. It does not translate, compile, discover targets, infer versions, or create new target files.

## Audited provenance

The implementation is a standard-library Python refactor of behavior audited in the localization project's path guard and batch deployment/backup/rollback scripts. The reviewed implementation used an explicit target allowlist, a fixed whole-tree snapshot, per-file source and original hashes, an independent verified backup, a per-file journal, staged replacements, a final tree comparison, and fail-closed rollback. The refactor does not copy those PowerShell scripts or any project configuration/data.

Two real failure reviews directly informed regression coverage:

1. A replacement call supplied an empty backup argument to `.NET File.Replace`, so it failed before any target changed. This module uses Python's `os.replace` and keeps the independent backup as a separately verified copy; it does not pass an optional empty backup path.
2. A whole-tree comparison used paths relative to different roots (game root versus a nested asset root). A fixed snapshot now carries a fingerprint of its canonical root, and snapshot entries must be canonical root-relative POSIX paths. Whitelist comparison always uses that same target root.
3. A partial multi-file deployment was rolled back only after each current target was classified by its recorded original/candidate digest. Unknown hashes, corrupted backups, and outside-whitelist tree changes stop recovery and preserve the journal for manual review.

## Minimal synthetic example

Run from the repository root:

```text
python examples/deployment/transaction_demo.py
```

The example creates a temporary source tree, target tree and transaction directory, deploys one synthetic payload, verifies it, then restores the original. It does not access a game installation.

## API outline

1. Keep the payload root, target root, and transaction root separate. The transaction directory must be new, and its parent must already exist outside both payload and target roots.
2. Capture a fixed snapshot with `capture_fixed_snapshot(target_root)` before deployment. It is bound to that exact canonical root; do not recapture it at apply time to hide changes.
3. Build a `DeploymentPlan` with a caller-supplied expected version, observed version, and explicit `DeploymentTarget` entries. Every target must already exist. Each entry carries a payload-relative source path, target-relative path, original SHA-256, and candidate SHA-256.
4. Call `deploy(plan, observed_version=...)`. It checks version, path safety, duplicate destinations, all hashes, the fixed snapshot, and same-filesystem staging before target writes. It then verifies the independent backup and candidate staging manifests, records intent before each replacement, checks each resulting hash, and audits the full target tree against the original snapshot and exact allowlist.
5. Use `rollback_transaction` only for an explicit rollback of a committed transaction. Use `recover_transaction` after an interrupted apply or rollback. `inspect_transaction` reads the durable journal without changing the target.

All roots and paths must be accessible to the process. The caller is responsible for providing a trustworthy version probe and for preventing unrelated writers during the transaction. The module rejects observed changes; it cannot prevent an arbitrary concurrent process from racing in the narrow interval between a final hash check and an atomic replace.

## Durable state definitions

| State | Meaning | Recovery behavior |
|---|---|---|
| `preparing` | Transaction journal exists; backup/staging may be incomplete; no target replacement is allowed yet. | Verify the original snapshot and mark `aborted`; no target writes. |
| `prepared` | Backup and staged payload manifests verified; apply has not begun. | Verify original snapshot and mark `aborted`. |
| `applying` | Apply began; the journal may have a per-target `replace_intent`. | Validate root, full-tree changes, every target digest and every backup before restoring known candidate files. |
| `rollback_in_progress` | Automatic or explicit restoration began; an item may have `restore_intent`. | Reclassify current bytes and resume idempotently. |
| `deployed` | All candidates and the final full-tree audit passed. | Verify only; explicit restore requires `rollback_transaction`. |
| `rolled_back` | Every target and the full tree match the fixed original snapshot. | Verify only. |
| `aborted` | An interrupted preparation was proved to have made no target changes. | Verify only. |
| `preparation_failed` | Preparation failed before target writes. | Verify original tree; retained transaction is not reused. |
| `manual_recovery_required` | A hash, backup, journal, root, or outside-tree condition could not be proved safe. | Automatic overwrite is disabled. Preserve evidence and require human inspection. |

Journal event records are append-only in intent, and the JSON file is atomically replaced and flushed before a target replacement. Each target's intent is durable before replacement; after a process kill, recovery infers whether that operation reached disk from the actual original/candidate hashes, not only from the last journal event.

## Failure and crash behavior

- Ordinary exceptions after a replacement trigger automatic rollback when every target is still either its original or candidate bytes, every independent backup validates, and no out-of-whitelist tree changes exist.
- The tests start a separate Python process and terminate it with `os._exit` immediately after a real `os.replace`, leaving the OS-level disk state and journal as they were at process death. A second test kills a process during rollback and resumes from the durable journal in another process.
- These tests demonstrate recovery from the injected process exits on the tested local filesystem. They do not prove survival of power loss, controller/device failure, remote/network filesystems, filesystem corruption, or every Windows storage stack. The library refuses cross-device staging, but atomic rename durability varies by filesystem.
- A crash while creating the transaction journal itself can leave an incomplete transaction directory before target writes. It must not be reused; inspect and preserve it for manual cleanup.
- Unknown target bytes, invalid/missing journal data, invalid/missing backups, reparse points, root mismatch, or outside-whitelist changes stop before restoration. The library does not claim automatic recovery in those cases.
- A committed deployment can be explicitly rolled back only while target files remain at known original/candidate hashes and the backup remains valid.

## Scope and limitations

- Existing regular files only: additions, deletions, directory creation, registry changes, process shutdown, application-specific cache handling, and game dictionary generation are outside this API.
- The module compares a caller-supplied version token with an expected token; it does not identify software versions itself.
- `os.replace` is used only after checking that staging and destination are on the same device. The module records and reapplies basic permission mode bits, but does not preserve Windows ACLs, owners, alternate data streams, extended attributes, timestamps, hard-link relationships, or security labels. Review these requirements before using it on protected or multi-user installations.
- No interprocess lock can stop arbitrary writers. Quiesce the application and other tools; the module detects pre-existing and post-operation drift, and fails closed when it can prove a conflict.
- Backups and journals are not cryptographically signed. Store the transaction directory with suitable access controls and retain it until the deployment is accepted.
- This is a conservative primitive, not a turnkey installer. Integrators must build a reviewed manifest, verify provenance and authorization, close the target application, choose safe backup storage, and define an operator recovery procedure.
