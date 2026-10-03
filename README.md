# MigrateGlass

Rehearse a SQLite migration on the data that will actually encounter it. Receive a bounded JSON decision that combines integrity, foreign keys, row-loss policy, application queries and an executed rollback. The source is opened immutable and read-only, then backed up into memory inside a disposable worker. There is no deploy command.

**面向 SQLite 嵌入式应用维护者。** 不只检查 SQL 中有没有 `DROP`：把静止数据库克隆到内存，真实运行迁移、数据不变量、应用查询与回滚，再核对数据是否恢复。报告不会输出原始行数据。`ACCEPT` 仅表示所提供数据与契约通过本次演练，不代表所有业务语义安全。

## Install and try / 安装和演示

Requires SQLite >=3.37 in addition to Python 3.11+.

Install from a source checkout with Python 3.11+:

```sh
git clone https://github.com/lllleolin-max/migrateglass.git
cd migrateglass
```

Linux/macOS:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python examples/make_fixture.py demo-output/billing.db
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\python.exe examples/make_fixture.py demo-output/billing.db
```

In the remaining examples, `python` means this environment's interpreter:
`.venv/bin/python` on Linux/macOS or `.venv\Scripts\python.exe` on Windows.
The runtime uses the standard library; source installation may download build
dependencies. No PyPI release is required for these instructions.

Rehearse the safe migration and its rollback on that synthetic database:

```sh
python -m migrateglass.cli demo-output/billing.db examples/safe.sql --rollback examples/safe_rollback.sql --contract examples/contract.json
```

The installed `migrateglass` command runs the same CLI. The fixture generator
refuses to overwrite an existing database; use a new path for another run and
pass that same path to the CLI. Optional development checks:
`python -m unittest discover -s tests -v` and `python benchmarks/contrast.py`.

The synthetic fixture contains 5,000 invoices and 120 accounts. The safe index migration returns `decision: ACCEPT`, `source_preserved: true`, `rollback: RESTORED`. CLI exit codes: **0** accepted, **2** rejected rehearsal, **1** invalid invocation. JSON goes to stdout; invocation errors go to stderr.

**使用方式：** 将待审阅 SQL、回滚 SQL 与 `contract.json` 作为代码审查附件；先对已关闭、无 WAL/journal 附属文件的数据库副本演练，再把拒绝原因交给维护者修正。演示命令创建的是合成账单，不能当成真实客户或生产验证。

## SDK

Run after generating the fixture above. The CLI inputs are a quiescent SQLite
file, UTF-8 migration/rollback SQL files and an optional local JSON contract.
The SDK instead receives SQL strings and contract lists; both return the same
bounded report. Neither applies the migration to the source database.

```python
from migrateglass import Limits, rehearse

report = rehearse(
    "demo-output/billing.db",
    "CREATE INDEX by_account ON invoices(account_id);",
    rollback="DROP INDEX by_account;",
    checks=[{"name": "positive", "sql": "SELECT count(*) FROM invoices WHERE cents<=0", "expected": 0}],
    consumers=[{"name": "totals", "sql": "SELECT account_id,sum(cents) FROM invoices GROUP BY account_id", "stable": True}],
    limits=Limits(seconds=8, vm_steps=5_000_000),
)
assert report["source_preserved"]
print(report["decision"])  # ACCEPT for the generated synthetic fixture
```

Checks must return one scalar equal to `expected` (default zero violations). `phases` defaults to `before`, `after`, `rollback`; consumers run in each available phase. Names must be unique across the contract, unknown fields/phases reject, and expectations must be finite JSON scalars. A consumer marked `stable` must retain its column names and result multiset after migration. Unmarked consumers only need to run. Row-loss budget defaults to zero and counts reductions under the same main-table name; table replacement may need an explicit budget and stronger business queries. Snapshots include hidden rowids; tables shadowing all rowid aliases reject rather than certify unobservable state.

In v0.1.1, contract files are strict UTF-8 JSON objects containing optional
`checks` and `consumers` lists. A UTF-8 BOM, duplicate object keys at any nesting
level (including escaped spellings of the same key), NaN/Infinity, excessive
nesting, unknown fields and wrong field types are invalid. Explicit `null` lists
are invalid; an absent list means empty. Invalid contracts return CLI
`INVALID`/exit **1** before a worker starts or a clone is staged. A valid check
that fails still returns `REJECT`/exit **2**. Errors omit SQL, row contents and
filesystem paths.

The SDK can use the exact CLI file reader rather than another JSON decoder:

```python
from migrateglass import Limits, read_contract, rehearse

limits = Limits(sql_bytes=1024 * 1024, contract_bytes=1024 * 1024)
contract = read_contract("examples/contract.json", limits=limits)
report = rehearse("demo-output/billing.db", "CREATE INDEX by_account ON invoices(account_id);",
                  rollback="DROP INDEX by_account;", limits=limits, **contract)
```

`Limits.contract_bytes` defaults to **1 MiB** and limits both raw contract-file
bytes (including whitespace) and the compact UTF-8 JSON representation of SDK
contract lists. Empty lists are omitted in this normalized representation, so
an empty contract is `{}`. The raw file size is checked before reading/parsing;
reads use at most 64 KiB per chunk and retain at most one extra sentinel byte.
JSON nesting is checked before parsing and permits at most **16 containers**;
braces inside strings do not count. The supported contract schema is shallower.

The existing **1 MiB** `Limits.sql_bytes` remains a separate aggregate budget for
the UTF-8 bytes of migration SQL, rollback SQL and every check/consumer query.
Contract syntax/metadata counts toward `contract_bytes`; embedded SQL counts
toward both budgets. CLI `--contract-bytes` and `--sql-bytes` override these
defaults. Each SQL file is admitted against the remaining SQL budget before
reading. SQLite also uses `sql_bytes` as its statement-length limit, including
trusted audit queries, so an impractically small value can reject a rehearsal.
These limits bound admission, not total process memory or input validation time.
SDK objects already exist in caller memory; JSON decoding, snapshots and SQLite
allocations still need operational isolation for hostile resource exposure.

## Evidence and boundaries / 证据与边界

`benchmarks/contrast.py` executes seven policies against disclosed synthetic data. Its regex baseline flags only DROP/DELETE/TRUNCATE; a second baseline executes the same engine, contracts and rollback against the same schema with no rows. Neither is **Atlas**. Populated rehearsal matches all seven declared policies; regex matches two and empty-schema rehearsal four. The data-dependent index failure, business violation and rollback loss disappear on empty data. Removing the contract or rollback respectively changes the corresponding rejection to acceptance. The empty-table drop is a lexical false alarm. An unmodeled note transformation is deliberately accepted: no tool can infer a missing business contract from this fixture. See [recorded local output](docs/contrast-results.json) and [verification](docs/VERIFICATION.md); this small designed corpus is not a population accuracy estimate.

This is a complementary local workflow. [Atlas migration lint](https://atlasgo.io/versioned/lint) already analyzes destructive/incompatible changes, uses a development database, and supports policies. We do not claim features absent in Atlas or benchmark its product. MigrateGlass's demonstrated focus is actual SQLite fixture data, consumer result contracts and executed typed-row rollback equality. Primary-source comparison verified 2026-10-03; see [commercial rationale](docs/COMMERCIAL.md).

Supported: SQLite ordinary tables/indexes/views/triggers and a single quiescent standalone source file. Explicit transactions/savepoints, ATTACH/DETACH, virtual tables, extension/file functions and most PRAGMA are denied. SQL chunks use SQLite's completeness parser, including trigger bodies; SQLite performs actual preparation. Runtime SQLite version is recorded. No PostgreSQL lock model, OS sandbox, live/WAL source support or universal migration-safety claim. See [architecture](docs/ARCHITECTURE.md), [security](SECURITY.md), [iterations](docs/ITERATIONS.md), and [contributing](CONTRIBUTING.md).

MIT · Copyright 2026 lllleolin-max. Adoption, revenue and willingness to pay are unknown. Automated multi-round code review assists development; maintainers should independently review suitability for their data.
