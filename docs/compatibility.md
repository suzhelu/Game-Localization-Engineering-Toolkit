# Compatibility and support boundaries

This document separates code-level checks from claims that require a real game and legally usable tools. Static validation is not runtime acceptance.

| Area | Current evidence | Boundary |
|---|---|---|
| General Python utilities | Automated tests with synthetic files | Python 3.10+; tests do not cover every filesystem or host |
| BGI text slots | Synthetic tests and the conservative registered BGI adapter | Handles only declared, recognized call patterns; unknown structures block safe writeback |
| Other formats | Synthetic adapter template and registry contract | No additional real-game format adapter is included |
| Translation | Local Mock Provider and offline Agent JSONL exchange are tested | Mock is not translation; online AI providers are not wired into the CLI |
| Review | Explicit state transition and human-review gate tests | Tooling cannot judge semantic quality or impersonate a reviewer |
| Compilation | Mock Compiler and configured external-process adapter tests | Real compiler must be legally held and supplied by the operator |
| MGSC v1 | Limited read-only envelope/table/bounds checks | Payload is opaque; this does not prove the game will load the output |
| Packaging | Explicit-file deterministic overlay checks on synthetic payloads | Does not install files or grant rights to distribute game content |
| Deployment/recovery | Transaction tests against temporary synthetic trees | Not protection against every power loss, filesystem defect, external writer, or network filesystem behavior |
| Runtime | Synthetic workflow leaves Stage 9 `runtime_unverified` | Only observed testing in the intended game build/environment can establish runtime behavior |

Use `localize adapters`, an explicit read-only probe, and the [game analysis workflow](game-analysis-workflow.md) before choosing a format. A file extension, filename, or one recognized text call is not proof that a whole game is supported. Unknown formats require a separately developed and tested adapter. See also the [AI quick start](quickstart-for-ai-users.md) and [FAQ](faq.md).
