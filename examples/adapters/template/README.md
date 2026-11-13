# Synthetic adapter template

This is a small training fixture, not support for any real game or engine. It demonstrates the registry contract with the invented `SYNTHETIC-TEXT-V1` line format in `sample.synthetic`.

## Run the template tests

From the repository root, with the toolkit source available:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest examples.adapters.template.test_adapter -v
```

## Turn a copy into an installed plugin

Copy `adapter.py` into your own reviewed Python package, adjust the parser, safe writer, metadata and synthetic tests, then declare an entry point in that adapter package's `pyproject.toml`:

```toml
[project.entry-points."localization_toolkit.adapters"]
synthetic-text-v1 = "my_adapter:register"
```

The entry point name must equal the adapter ID and point to a callable accepting a registry. The callable should register exactly one matching descriptor and factory. Install the package into the toolkit's Python environment. `localize adapters` lists the discovered ID; configure `general.adapter` with that ID. Selecting it imports and executes its installed Python code in the CLI process. Review and trust the package first.

The template writer demonstrates slot mapping and structural checks only. Real adapters must preserve non-text bytes, reject unknown forms, validate exact slot mappings and test round trips with synthetic inputs. Translation approval remains enforced by the toolkit's `write_approved()` workflow; calling an adapter's low-level `write()` directly is not human review.
