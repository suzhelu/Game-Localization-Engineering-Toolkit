# Automated test report

Run on 2026-11-12 with Python 3.10.11 on Windows.

Command:

```text
python -m unittest discover -s tests -v
```

Result: **125 tests passed, 0 failed, 0 skipped**.

This retains the original 45-test suite, 23 deployment transaction tests, and 24 overlay-package tests, and adds 33 translation workflow tests; no prior tests were removed or weakened.

The suite covers known SHA-256 vectors and content changes; relative and absolute paths; Chinese and space-containing names; case-insensitive Windows comparison; traversal, device namespace, alternate data stream, trailing-dot/space and reserved-device-name rejection; symbolic-link/junction escape and reparse ancestors; whitelist changes, additions and removals; manifest creation, duplicate rejection, missing files and hash/size mismatch; synthetic BGI extraction, slot identity, categories, locations, language choices, opaque resources, escaped strings, safe writeback, malformed inputs, unresolved calls, standalone literal reporting, and structural invariants; and deployment version/hash gates, fixed-root snapshots, explicit allowlists, backup integrity, atomic replacement, rollback, unknown-state refusal, and full-tree audits.

Deployment interruption coverage starts separate Python worker processes and terminates them with `os._exit` immediately after actual file replacement. Tests verify recovery after a partially applied deployment and after a process is killed partway through rollback. All deployment tests use temporary directories and synthetic bytes.

The synthetic manifest example was also verified successfully with `python examples/verify_sample.py`. The synthetic BGI example was run successfully with `python examples/bgi/translate_synthetic.py`. The synthetic deployment example was run successfully with `python examples/deployment/transaction_demo.py`. The synthetic overlay package example was run successfully with `python examples/overlay_package/build_synthetic_package.py`.
The synthetic translation example was run successfully with `python examples/translation/mock_batch.py`.

The overlay suite checks deterministic artifact bytes; explicit-list-only collection; path traversal and case-insensitive duplicate rejection; missing or hash-mismatched sources; damaged, missing-member, duplicate-member and traversal-member ZIPs; strict manifest/checksum validation; and cleanup after an injected partial packaging failure. All packaging tests use synthetic files in temporary directories and do not install into a target tree.

The translation workflow tests use synthetic slots and a local Mock Provider. They cover prompt rendering, glossary schema mapping/conflicts, duplicate source text at distinct slot IDs, repeat-batch deduplication, persistent resume, partial provider failure/retry, source/reference/context/glossary/prompt/rule staleness, missing/duplicate/unexpected/malformed provider results, placeholder/tag-order/control-marker and required-term checks, cached-result revalidation, human approval/rejection, unresolved-structure refusal, and BGI-adapter-mediated writeback. No network model or game text is used.

The reparse tests used Windows directory junctions because this environment does not grant ordinary symbolic-link creation privileges. The code rejects both symlinks and Windows reparse points.
