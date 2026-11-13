# Synthetic unknown-game analysis

Run from the repository root with `PYTHONPATH=src`:

```powershell
python examples/game-analysis/analysis_demo.py
```

This fixture is fictional. The script reads the sample as bytes, decodes it only to pass inert text to the built-in read-only probe, validates the JSON report, and expects current adapters to return `unsupported`. That means “no registered adapter recognized this sample,” not a claim about every possible parser or about a real engine. The `.dat` suffix is deliberately not treated as evidence. No executable is run and no game compatibility is claimed.

The Markdown and JSON report files are synthetic templates. Copy them into a private project report directory before recording real observations; do not commit proprietary inventories, absolute paths, extracted strings or game resources.
