# Security and reporting

Supply only quiescent standalone SQLite databases owned by your review workflow. SQL is untrusted; the host, Python/SQLite installation, package import path, process environment and configuration are trusted. This is not an OS sandbox and cannot defend against malicious local users, SQLite/Python vulnerabilities, source-path races or native extensions installed by a compromised host. Work on an exported test fixture when the original database is live.

User SQL cannot ATTACH/DETACH another file, use VACUUM INTO, load extensions, call file-I/O functions, enable writable_schema, change journal/temp settings, create virtual tables or manage transactions. The authorizer denies every PRAGMA except metadata introspection. Queries use a read-action whitelist, including CTE/RETURNING attempts. Extension loading is explicitly disabled. SQLite temporary storage is memory. Trusted schema is disabled; unsupported schema/functions reject rather than bypass the policy.

Byte-for-byte source preservation is checked at the parent boundary, including timeout/failure; source/WAL companions are rejected before SQLite opens the file. There is no restoration write to a changed source. Concurrent modification is unsupported and triggers rejection if hashes differ. File hashes, table/column/check names and row counts are metadata and can reveal information; digests are not encryption or anonymization and can be guessed for small domains. Never publish reports from sensitive data without reviewing metadata. Raw SQL/error payloads and rows are omitted by the engine.

Limits are operational guardrails, not hard process-memory isolation. Reduce source/value/row/step limits and run the process in your own OS/container isolation when adversarial resource exposure matters. Interrupted workers lose the disposable clone. An accepted result is no guarantee about untested data or missing business requirements.

The v0.1.1 contract-file boundary rejects duplicate keys throughout the JSON,
nonfinite constants/expectations, invalid UTF-8, a UTF-8 BOM, wrong schema/types
and nesting deeper than 16 containers before launching a worker or staging a
clone. The SDK's `read_contract` is the same reader used by the CLI. A size check
precedes parsing; capped chunk reads still reject growth beyond the configured
budget. These are admission guards, not protection against concurrent hostile
path replacement or an untrusted Python host.

The default 1 MiB contract budget accounts for raw file bytes and normalized
SDK JSON separately; empty lists are omitted from normalized JSON. The existing
1 MiB SQL budget still aggregates migration, rollback and embedded query UTF-8
bytes. Embedded SQL therefore consumes both relevant budgets. CLI SQL files are
bounded before reading, and invalid-input errors omit SQL, row contents and
paths. Validation, input reading and parent hashes remain outside the worker's
wall timeout. Caller-created SDK objects, JSON representations and SQLite
internal memory are not bounded by an OS RSS limit.

Report a suspected bypass or false acceptance using a minimal synthetic database, SQL, Python/SQLite versions and JSON decision. Use a private GitHub security report when the repository is published; do not include production rows, secrets or customer files. Public issues are suitable for non-sensitive reproducible limitations. Security handling capacity and response SLAs are not promised.
