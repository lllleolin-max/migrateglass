# Changelog

## 0.1.1

- Strict, bounded contract-file parsing rejects duplicate JSON keys at every
  nesting level, BOM/invalid UTF-8, nonfinite expectations and malformed schema.
  Invalid contracts fail before worker invocation and clone staging.
- Exported `read_contract` shares CLI parsing and contract validation with SDK
  callers. Added a 1 MiB `contract_bytes` admission budget and CLI overrides.
- Bounded migration/rollback file reads consume the unchanged aggregate
  `sql_bytes` budget, including all embedded check/consumer SQL.
- Fixed empty-contract normalization at the exact byte boundary and chunked
  reads that avoid platform read-size overflow for large positive budgets.
- Invalid-input CLI errors omit SQL, row content and filesystem paths.

The source database and caller input files are never rewritten. Valid failing
checks still reject; safe migration/consumer/rollback workflows still restore.
SQLite ordinary-table, quiescent-file, no-live-WAL and trusted-host boundaries
remain unchanged. No production performance, customer or revenue claim is added.
