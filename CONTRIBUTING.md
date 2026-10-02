# Contributing

Use Python 3.11+ and a virtual environment. Install with `python -m pip install -e .`, run `python -m unittest discover -s tests -v` and `python benchmarks/contrast.py`. CI checks Ubuntu/Windows with 3.11/3.14; checked-in YAML is not evidence that remote runs already passed.

Every change to SQL authorization, snapshots, transaction handling or budgets needs a failing-before synthetic probe and passing-after verification. Keep the source-byte comparison in adversarial tests. Explain supported semantics rather than adding an allowlist bypass to satisfy one migration. Fixtures must not contain personal data. Benchmark claims must retain baseline disclosure, adverse examples and actual outputs.

SDK configuration/report changes should preserve documented exit codes and report-version semantics or bump the version deliberately. Open an issue describing a concrete workflow and reproducible fixture before a major dialect/live-database feature. Contributions remain under MIT. No production support promise, adoption claim or payment claim should be inferred from this repository.
