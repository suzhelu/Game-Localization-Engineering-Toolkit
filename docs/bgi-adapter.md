# BGI text-slot adapter

This module separates game-independent slot data from BGI-specific source rules:

- `localization_toolkit.models` defines `TextSlot`, `ExtractionResult`, issue/control records, categories, and the adapter protocol.
- `localization_toolkit.adapters.bgi.BgiAdapter` tokenizes a supported BGI/C-like source form, recognizes only explicit call schemas, extracts visible target literals, and writes translations back into those literal spans.

## Supported extraction rules

The default schemas are based on the project's audited, actually used parser:

| Call | Text fields |
| --- | --- |
| `OutputLine` | source/target speaker name and source/target body pairs |
| `OutputLineAll` | source/target body pair |
| `SavePoint` | source/target save label |
| `ModAddArtset`, `ModAddBGMset`, `ModAddSEset` | UI name pair |
| `ModAddAudioset` | UI name and description pairs |

The adapter also resolves a deliberately narrow language-choice pattern: a declared fixed-size `char` array, a literal `GetGlobalFlag(GLanguage)==1` branch with English and Japanese literal assignments, followed by `Select(count, array)`. Other `Select` forms are unresolved and cannot be written back. Project-specific dynamic/format commands such as `SetNameFormat`, `ChapterPreview`, and `DrawFragment` are always reported for review and block writeback.

Callers may declare an `opaque_commands` schema for commands whose string arguments are known non-text resource/control data. This is an explicit assertion by the caller; the adapter does not infer it from names or values. Unknown calls with string arguments are reported as unresolved and block all writeback. Calls with no string arguments are reported but do not block writing.

## Slot identity and safe writeback

Each slot carries a stable ID derived from its script key, call/argument address, role, and source-language literal (and choice index for menu entries). The ID does not depend on replaceable target text. Character offsets, UTF-8 byte offset, line/column, command, call index, role/category, speaker hint, and neighboring text context are retained.

`write()` requires an exact translation-key set. It patches only identified string tokens, then checks token count, call order and arity, all non-target tokens, control signature, and a source mask proving comments, whitespace, resources, and other source bytes are unchanged. Formatting tags, control escapes, and placeholders must remain in the same order. A failed or ambiguous parse raises an error rather than silently skipping content.

## Format limits

This is a text-slot adapter, not a full BGI parser, compiler, or MGSC validator. It supports quoted string literals, character literals as opaque syntax, identifiers, numbers, punctuation, line/block comments, balanced `()[]{}`, and the escape forms implemented in the lexer. It does not evaluate macros, variables, concatenations, computed dialogue, arbitrary preprocessor syntax, or dynamic menu construction. String-bearing call types outside the explicit schemas must be configured before writeback. It does not validate translation language, glossary conformance, semantic accuracy, or runtime font/layout behavior, and cannot replace compiling and in-game testing with the original compiler/runtime.

Offsets are Python character indices and UTF-8 byte offsets for the supplied decoded string. The parser does not read files or guess their encoding; callers must decode strictly and preserve their chosen newline convention. The synthetic example is intentionally not real game content.

## Run the synthetic example

From the repository root:

```text
python examples/bgi/translate_synthetic.py
```

The script parses `examples/bgi/synthetic_script.txt`, creates illustrative translations, and writes an ignored local `translated_output.txt` beside the example. It uses no compiler, game data, network, or third-party dependency.
