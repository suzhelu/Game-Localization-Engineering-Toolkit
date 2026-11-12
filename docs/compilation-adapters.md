# Compiler adapters and auditable builds

This module separates source review, local compiler invocation, compiler-version checks, output mapping, format validation, build manifests, and publication. It is an adapter framework, not a universal game compiler. No third-party compiler, game assembly, script, or compiled asset is included.

## Migration basis and limits

The source audit identified actual PowerShell wrappers that load a locally installed game compiler assembly, resolve the public `BGICompiler.Compiler.BGItoMG(System.String,System.String)` constructor, invoke it with explicitly cast input/output strings, and compile control and translated sources to isolated output locations. The project-side validator checked the `MGSC` magic, version `1`, declared block/line/data counts, .NET 7-bit encoded block-name lengths, in-range block and row offsets, presence of the `main` block, and exact remaining payload length. The original-source validation compared a control compilation's SHA-256 to the caller's corresponding original MG hash; translated output was separately checked for structural table equality and expected strings.

The inventory classifies the native compiler and game assemblies as non-redistributable or permission-uncertain. They are not copied. `CommandLineCompilerAdapter` accepts a caller-provided executable and explicit argument vector. It uses `subprocess` with `shell=False`, exact `{input}`/`{output}` argument substitutions, a separate version probe, captured output, and a timeout. A PowerShell wrapper can be configured by its owner where needed; no PowerShell wrapper or vendor compiler is bundled.

The `MGSCV1Validator` ports only the audited read-only envelope checks above. Block names use the source validator's UTF-8 replacement behavior; this module does not add a stricter decoding rule. It treats the data payload as opaque. It does not decode instructions, validate game semantics, prove runtime compatibility, or implement a compiler. Unknown format revisions fail closed.

The source project also held a 96-record compiled-script metadata index and a separate 104-entry translation glossary. They are different data sets with different purposes. This module neither reads nor generates either game-specific file. `expected_artifact_index` is only a caller-supplied mapping of this build's output-relative paths to expected SHA-256 values; it is not the game's compiler index and must not be built from a glossary.

## Translation and review gate

Translated inputs should be produced with `prepare_reviewed_source()`. It delegates to the translation workflow's `write_approved()`, which requires current, explicitly human-approved records and calls the selected text adapter's safe `write()`. The candidate is staged exclusively under a caller-owned input root, re-extracted, and checked for unresolved structures, slot ID/order/count drift, and structure-signature changes. Changed staged bytes are detected again before compilation. Original control inputs use `capture_original_source()` and must have resolved structure.

These records are workflow controls, not cryptographic proof of the quality of a human review. A project integrating another adapter must ensure its `extract()`/`write()` contract accurately protects non-target strings and structure.

## Evidence levels

Each item reports evidence independently:

| Evidence | What it establishes | What it does not establish |
|---|---|---|
| `input_structure` | The staged source was created/rechecked through an adapter gate and has no unresolved issues. | Correct game behavior. |
| `human_approval` | A current translation-store approval with reviewer identity was required for translated input. | Linguistic accuracy; reviewer attestation is not machine-verifiable. |
| `compiler_exit` | The configured process returned zero before timeout. | Valid output. |
| `output_format` | Each configured validator accepted the artifact. | Full decoding or runtime compatibility. |
| `output_hash` | The produced file was hashed and compared where pinned. | Authenticity unless the expected hash has a trusted origin. |
| `original_roundtrip` | Recompiling an original control input matched a caller-supplied baseline artifact SHA-256 byte-for-byte. | Equivalence when a valid baseline was not supplied. |
| `translated_structure` | The translated text re-extraction retained the adapter's structure signature and slot map. | Binary/runtime semantic equivalence. |
| `runtime` | Always `not_verified` here. | In-game acceptance. |

No zero exit code alone is treated as build success. Builds also require at least one explicit output validator, complete output presence, pinned version, target/index hashes where supplied, and an exact output-file whitelist. Publication renames a fully validated sibling temporary directory only when the destination does not already exist.

## Example

Run `python examples/compilation/mock_build.py`. It uses a BGI-shaped synthetic source, local Mock Provider, explicit reviewer approval, a Mock Compiler, and a hand-built synthetic MGSC envelope. It makes no network calls and does not invoke a real game compiler.

For an installed command-line compiler, configure an argument list such as `("--source", "{input}", "--output", "{output}")` and a separate version argument list. Do not pass a shell command string. Input and output paths are substituted inside individual argv elements; shell metacharacters are passed as data. The compiler executable and license must be supplied and managed by the caller.

## Failure, interruption, and cleanup

Nonzero exit, timeout, missing output, format failure, hash/index mismatch, compiler-version drift, or extra/unlisted output files fail the build and prevent publication. Ordinary failures remove the temporary directory and return a report; set `BuildPlan.report_path` to persist a failure report without overwriting an existing one.

A hard process termination may bypass cleanup. The temporary directory contains a build ID marker and is not published. `cleanup_abandoned_build()` removes it only when explicitly called with the exact parent and matching ID after operator inspection. This is not automatic crash recovery; it does not prove that child compiler processes or operating-system writes have stopped. Preserve the directory and require manual investigation if its marker, contents, or state is unexpected.

## Limitations

- No real BGI compiler is bundled, invoked in the tests, or claimed as supported out of the box.
- MGSC v1 checking is envelope-only and based on the audited project checks; its payload remains opaque.
- Roundtrip byte equality requires legally held original input and a separately preserved matching baseline artifact hash.
- A caller-supplied artifact index validates output paths/hashes only; this module does not update game compiler indexes.
- One build process must own its destination. Concurrent builds to the same output path are not coordinated.
- Timeout handling targets the direct child process; compiler-created grandchildren may require external process supervision.
- Static validation does not replace game runtime testing or human review.

## Regression coverage

Synthetic tests cover success, compiler errors/timeouts, no output, malformed MGSC fields, pinned version and hashes, roundtrip mismatch, duplicate/escaping outputs, artifact-index mismatch, output whitelist additions, stale temp cleanup, repeated publication, changed reviewed bytes, explicit review requirements, safe argv with Unicode/space/shell metacharacters, and a real separate-process `os._exit` interruption. The .NET reflection regression uses a synthetic `Add-Type` class only; it tests exact public `string,string` constructor selection and `[object[]]` arguments with explicit string conversion. It never loads a game assembly.
