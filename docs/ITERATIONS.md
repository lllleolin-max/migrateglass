# Review and correction record

Environment: Windows, Python 3.14.3, SQLite version reported per run. Builder: GPT-6.1-Sol / Ultra. Independent review is separate; this log awards no scores. The initial implementation includes package, real worker execution, tests, fixture, contrast, docs and CI. Only subsequent substantive corrections count as rounds.

Round entries record exact before/after commits and executable regression probes. Initial bring-up found unclosed fixture connections on Windows; these were corrected before the initial implementation commit and are **not** counted as a review cycle.

## Initial implementation

`8322f6ede494d8f84a7f5e698384842bf7d2963d`: 18 tests passed; seven synthetic contrast policies matched expected decisions. Initial benchmark median was 0.2510 s on this machine, with regex 2/7 and rehearsal 7/7. These timings are not a speed claim and vary with startup/hardware.

## Round 1: hidden-rowid rollback false acceptance

Before: `8322f6ede494d8f84a7f5e698384842bf7d2963d`.
After: `53025ca441a2e444a45c651cd0866703ab7c0b33`.

Self-review asked whether `SELECT *` observes the whole ordinary-table state. It does not include a hidden rowid. A messages table with two equal bodies at rowids 7 and 9 was migrated to rowids 107 and 109; a no-op rollback was falsely reported as restored. The new regression actually failed before correction: `test_hidden_rowid_must_be_restored`, `AssertionError: 'ACCEPT' != 'REJECT'`, and the report showed `data_equal: true`, `rollback: RESTORED`.

Correction uses SQLite `table_list` metadata and captures an unshadowed rowid alias in every rowid table's typed multiset. WITHOUT ROWID tables use their full declared rows. If all three aliases are shadowed, it rejects `ROWID_UNOBSERVABLE` rather than certifying unseen data. SQLite >=3.37 is now an explicit structural-metadata requirement.

Verification: `py -3 -m unittest discover -s tests -v` → **22 tests, OK**, including changed hidden rowids rejected, a shadowed primary alias, all aliases hidden, and a WITHOUT ROWID composite-key roundtrip. Source bytes remained equal.

## Round 2: ambiguous contracts could silently omit safety checks

Before: `53025ca441a2e444a45c651cd0866703ab7c0b33`.
After: `ca99975c600ab83894a815770a0b6b0c39da2922`.

Self-review inspected how user JSON maps to executed checks. `phases: ["aftre"]` silently skipped a business check. Duplicate consumer names overwrote the stable consumer result with a different query; a changed invoice amount could evade the intended stability comparison. Two new validation regressions actually failed before correction with `ValueError not raised`.

Correction validates the complete contract before launching SQLite: required/unknown fields, unique names, nonempty SQL, known nonempty unique phase lists, boolean stability, finite scalar expectations and aggregate SQL/query-count limits. CLI top-level unknown contract fields also reject. Empty wrong-type objects no longer disappear through truthiness defaults.

Verification: `py -3 -m unittest discover -s tests -v` → **25 tests, OK**. Both bypass probes now raise actionable ValueError; malformed shapes, misspelled fields, NaN and contract SQL exceeding the aggregate limit are covered. Ordinary contracts remain usable.

## Round 3: individual cell limits did not bound accumulated results

Before: `ca99975c600ab83894a815770a0b6b0c39da2922`.
After: `a86903479bf0f0f57c9ab600bedfae783252befc`.

Self-review found fetched results were retained as a list until hashing/disposal. Per-value and row bounds allowed a query to materialize 2,000 values of 20,000 bytes each (40 MB), despite a tiny source and clone. The added aggregate-budget regression actually failed before correction: `'ACCEPT' != 'REJECT'`, 38,099 sampled upper-estimate VM steps and 0.218069 s elapsed on that run. This was a real resource-accounting deficiency, not a fabricated failure.

Correction adds a default 16 MiB `Limits.result_bytes` admission budget and a shared collector for user queries and trusted schema/integrity metadata. It counts raw UTF-8/blob payload plus 32 per row, 16 per cell and 8 per numeric scalar before retaining each fetched row. Many null cells and multi-byte text are accounted for. This bounds accumulated application results; it is explicitly **not** an OS RSS cap.

Verification: `py -3 -m unittest discover -s tests -v` → **28 tests, OK**, with large result rejected `RESULT_BYTES_LIMIT`, exact 58-byte accounting boundary accepted/59 rejected, and UTF-8/null behavior. Source bytes remain unchanged. Remaining memory boundaries include SQLite internal sort/parser allocations and Python overhead; externally imposed process isolation is still needed for hostile hosts.

## Reproduce before/after evidence

The regression file in the final tree includes all rounds. To audit an old implementation without modifying the current checkout, export that commit's `src/` to a disposable directory and place it first on `PYTHONPATH`, then run the specific final regression below. The worker inherits `PYTHONPATH`, so both the parent and child exercise the old code. Use a trusted local checkout and a disposable environment; do not run the full final suite at an old commit expecting later features to exist.

```powershell
$env:PYTHONPATH = 'ABSOLUTE_PATH_TO_EXPORTED_OLD_SRC'
py -3 -m unittest discover -s tests -p test_review_regressions.py -k hidden_rowid_must_be_restored -v
py -3 -m unittest discover -s tests -p test_review_regressions.py -k contract_phase_typo -v
py -3 -m unittest discover -s tests -p test_review_regressions.py -k many_small_values -v
Remove-Item Env:PYTHONPATH
```

Expected failures respectively at the initial, round-1 and round-2 before commits. Final implementation passes all three. These are reviewed corrections after a working full initial implementation. Documentation/evidence commits are not counted as additional rounds. Independent scores, public publication and remote CI results remain separate verification tasks.
