# Changelog

## v0.2.0-rc1 — Preview candidate

This candidate consolidates the bounded AI-agent workflow delivered in batches A–E:

- Agent entry guidance, game-analysis evidence reports, and an 11-stage task contract.
- Explicit adapter registry and conservative BGI text-slot adapter.
- Offline JSONL translation exchange with freshness checks, structural QA, pending-review state, and cooperating-process store locking.
- Reviewed-source compilation workflow, mock compiler, and limited read-only MGSC v1 envelope validation.
- Auditable deployment/recovery transactions and explicit-file overlay package generation.
- Unified CLI and synthetic end-to-end workflow.

The package has no online AI provider and no bundled real game compiler or game assets. Synthetic tests and static validation do not establish runtime compatibility. See [release readiness](docs/release-readiness-v0.2.0.md), [compatibility](docs/compatibility.md), and [the FAQ](docs/faq.md).
