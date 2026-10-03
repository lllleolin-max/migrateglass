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

## v0.1.1 strict contract update — three additional self-review rounds

Executed on 2026-10-03, Windows/Python 3.14.3. The three original substantive
cycles and old independent review remain unchanged. These additional rounds
include the original duplicate-key correction, two edge cases found in the new
reader, and an installed integration review with no further product defect.
They are not represented as three new baseline bugs or three independent reviews.

### Round 1 — reproduce and reject ambiguous JSON contracts

Before: `7268a81ea3fbeddd6b08d74ac3e69cbafdad9d9e`, v0.1.0. The parallel remote
README change `444831585381ff42f1ce2cbca5ae892f8bd95a47` was fetched and normally
fast-forwarded before implementation; its cross-platform instructions remain.
The exact old source was separately exported using
`git -c core.autocrlf=false archive`, built as an ordinary wheel and installed
into a fresh environment. Raw Git blobs, LF archive, wheel and isolated site
bytes matched for all five old modules. **28 installed tests passed, 9.782s.**

The actual registered CLI ran identical source/migration data against two
contracts. Ordinary `checks=[positive]` returned **REJECT/2**, with the after
invariant false. `{"checks":[positive],"checks":[]}` returned **ACCEPT/0** with
no invariant. All source, migration, rollback and both contract hashes stayed
unchanged. This reproduces the old disclosed P2 file ambiguity, not a new
privacy, deployment or supported-format safety claim.

Correction: `ec04247c33bbdb5fdde050b6d990a5f7d9d85f24`. Shared SDK/CLI
`read_contract` rejects duplicate keys throughout the JSON and validates UTF-8,
BOM/constants, nesting and schema before worker invocation. Contract and SQL
file admission respects explicit budgets; aggregate embedded SQL is still
included in the original SQL budget. Invalid CLI errors omit payloads/paths.
Nine new contract tests passed; **37 source tests passed, 5.837s**. Tests verify
no worker or source hashing on invalid file contracts, exact UTF-8 boundaries,
late malformed input, file growth and ordinary fail/safe restoration behavior.

The first test helper forgot that a SQLite context manager does not close its
connection, causing Windows cleanup errors. Its tiny SQL cap also constrained
existing trusted SQLite audit statements and correctly produced DATABASE_ERROR.
Original helper/log were retained in ignored builder evidence. Corrected tests
close connections and use a realistically sized exact aggregate SQL boundary;
no product correction is attributed to either harness mistake.

### Round 2 — resource and semantic composition of the new reader

Reviewed exact round-1 code `ec04247`. An actual empty `{}` contract with an
exact two-byte budget raised ValueError because normalization added empty lists.
A tiny valid file with a positive `contract_bytes=10**100` budget raised
OverflowError when that budget was passed directly as a platform read size.
Expected-before observations and input bytes were retained separately.

Correction: `078f5ba0a6ad0387c916375ba53443a98af41330`. Empty lists are omitted
when accounting normalized SDK JSON, so the minimal contract remains `{}`.
Fixed-size reads of at most 64 KiB avoid read-size overflow and allow at most
one over-budget sentinel byte. Both exact prior cases now accept, with input
bytes unchanged. Two added regressions and all **39 source tests passed,
6.699s**. This corrects defects found in the new implementation, without raising
default budgets, weakening query validation or inventing extra old-version bugs.

### Round 3 — ordinary installed SDK, CLI and examples

At `91904b8212e3504bb2c92d463d82c36bf22aabc6` (v0.1.1), a new canonical LF
archive, ordinary wheel and fresh environment matched all six raw Git package
modules exactly through installed site bytes. **39 installed tests passed,
12.232s**, including all original 28 and eleven contract/resource tests.
Actual CLI repeat: ordinary positive check remains **REJECT/2**; duplicate
contract now **INVALID/1**. All five input hashes match the old reproduction.

The installed fixture example generated 5,000 invoices and 120 accounts.
Actual sysconfig CLI under `PYTHONUTF8=0/1` returned **ACCEPT/0, RESTORED**;
SDK `read_contract`→`rehearse` produced equal phase snapshots and restoration.
Source, safe migration, rollback and contract bytes stayed unchanged. Actual
duplicate CLI returned INVALID/1. The seven-case contrast passed: populated
**7/7**, lexical **2/7**, empty-schema **4/7**; both no-contract/no-rollback
ablations still change the corresponding rejection to acceptance. Recorded
median 0.3983s is an environment observation, not a performance improvement.

An initial integration helper assumed the fixture generator printed UTF-8 JSON;
it actually prints a human path in native encoding. The example had exited 0
before the helper's decoder failed. Original helper and generated fixture are
retained; a corrected helper checks its exit and parses only CLI JSON. This is
not a product encoding defect. No further product defect was found in this round.

The final iteration-log commit changes documentation only. Final archive/wheel
association must bind its exact SHA, rather than inherit a claim from a changed
artifact. Builder observations are not independent scores, publication, remote
CI, customer adoption or income. Old independent scores belong to the old SHA.
