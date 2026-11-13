# Synthetic analysis report — Fictional Lantern Demo

## Confirmed

- The synthetic inventory fixture lists `data/sample.fixture`.

## Inferred

- A marker suggests that the fixture might contain text records. No format parser was run, so this is only a lead.

## Unknown

- Engine, container structure, encoding, localization settings, fonts, compiler and runtime rendering are unknown.
- No compatibility claim is made. Stage 9 is `runtime_unverified` because no game was executed.

## Next actions

Inspect only a copied synthetic sample as data, identify boundaries with a documented test, and stop if the structure remains ambiguous. Do not infer support from `.dat`.
