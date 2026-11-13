# Synthetic offline Agent exchange

Run from the repository root:

```powershell
$env:PYTHONPATH = 'src'
python examples/agent-translation/exchange_demo.py
```

The example uses generated synthetic text and a local mock Agent step. It demonstrates extract → JSONL export → mock result → import → structural QA → explicit synthetic human approval → adapter write. It makes no network request and is not a real translation or game compatibility test. The dummy result schema file is documentation only; its fingerprints are intentionally invalid.

For a configured project, run `localize extract`, then `localize translate-export --project PROJECT exchange/tasks.jsonl`, fill a result file according to the version 1 schema, then `localize translate-import --project PROJECT exchange/results.jsonl`. Inspect and approve with `localize review`; only after approval should compilation be considered. See `docs/agent-translation-exchange.md` for fields, conflicts and filesystem limits.
