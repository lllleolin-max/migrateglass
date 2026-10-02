"""SQLite state machine and guarded execution; no deployment path exists."""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import hashlib
import json
import sqlite3
import time
from pathlib import Path


class RehearsalError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def statements(sql: str, maximum: int):
    """Delegate statement completeness (including trigger bodies) to SQLite.

    Not a dialect parser. Every chunk still passes SQLite prepare + authorizer.
    Semicolons inside quoted strings/comments/triggers do not split statements.
    """
    chunks, start = [], 0
    for end, char in enumerate(sql):
        if char == ";" and sqlite3.complete_statement(sql[start:end + 1]):
            chunks.append(sql[start:end + 1])
            start = end + 1
            if len(chunks) > maximum:
                raise RehearsalError("STATEMENT_LIMIT")
    tail = sql[start:]
    if tail.strip():
        if not sqlite3.complete_statement(tail + ";"):
            raise RehearsalError("INCOMPLETE_SQL")
        chunks.append(tail)
    if len(chunks) > maximum:
        raise RehearsalError("STATEMENT_LIMIT")
    return chunks


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def typed(value):
    if value is None:
        return ["null"]
    if isinstance(value, bytes):
        return ["blob", value.hex()]
    if isinstance(value, int):
        return ["integer", str(value)]
    if isinstance(value, float):
        return ["real", value.hex()]
    return ["text", value]


def digest_rows(rows):
    # Multisets retain duplicates and storage types; sorting removes scan-order noise.
    hashes = Counter(hashlib.sha256(json.dumps([typed(v) for v in row],
                              ensure_ascii=True, separators=(",", ":")).encode()).hexdigest() for row in rows)
    return hashlib.sha256(json.dumps(sorted(hashes.items()), separators=(",", ":")).encode()).hexdigest()


class Engine:
    def __init__(self, config):
        self.config = config
        self.limits = config["limits"]
        self.deadline = time.monotonic() + self.limits["seconds"]
        self.steps = 0
        self.reason = None
        self.denial = None
        self.readonly = False
        self.findings = []
        self.conn = sqlite3.connect(":memory:", isolation_level=None, cached_statements=0)
        self.conn.enable_load_extension(False)
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA trusted_schema=OFF")
        for setting, value in [(sqlite3.SQLITE_LIMIT_LENGTH, self.limits["value_bytes"]),
                               (sqlite3.SQLITE_LIMIT_SQL_LENGTH, self.limits["sql_bytes"]),
                               (sqlite3.SQLITE_LIMIT_COLUMN, 1000),
                               (sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 100),
                               (sqlite3.SQLITE_LIMIT_TRIGGER_DEPTH, 50),
                               (sqlite3.SQLITE_LIMIT_ATTACHED, 0)]:
            self.conn.setlimit(setting, value)

    def progress(self):
        self.steps += 100
        if self.steps > self.limits["vm_steps"]:
            self.reason = "STEP_LIMIT"
        if time.monotonic() > self.deadline:
            self.reason = "TIME_LIMIT"
        return bool(self.reason)

    def authorizer(self, action, one, two, database, trigger):
        forbidden = {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH,
                     sqlite3.SQLITE_CREATE_VTABLE, sqlite3.SQLITE_DROP_VTABLE,
                     sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT}
        allowed_pragmas = {"table_info", "table_xinfo", "foreign_key_list", "index_list", "index_info"}
        if action in forbidden or (database not in (None, "main", "temp")):
            self.denial = "SQL_DENIED"
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA and (one or "").lower() not in allowed_pragmas:
            self.denial = "PRAGMA_DENIED"
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and (two or "").lower() in {"load_extension", "readfile", "writefile", "fts3_tokenizer"}:
            self.denial = "FUNCTION_DENIED"
            return sqlite3.SQLITE_DENY
        # Whitelist read-only authorizer actions: WITH UPDATE/DELETE ... RETURNING
        # cannot evade this by beginning with WITH/SELECT.
        if self.readonly and action not in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ,
                                           sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE,
                                           sqlite3.SQLITE_PRAGMA}:
            self.denial = "QUERY_NOT_READONLY"
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def guarded(self, sql, *, readonly=False):
        self.readonly = readonly
        self.denial = None
        self.conn.set_authorizer(self.authorizer)
        try:
            cursor = self.conn.execute(sql)
            return self.collect(cursor), [x[0] for x in cursor.description or []]
        except sqlite3.Error as error:
            code = self.reason or self.denial or ("CONSTRAINT_FAILED" if isinstance(error, sqlite3.IntegrityError) else "SQL_ERROR")
            raise RehearsalError(code) from error
        finally:
            self.conn.set_authorizer(None)
            self.readonly = False

    def collect(self, cursor):
        """Bound aggregate fetched payload, not just each individual SQLite cell.

        Accounting includes 32 bytes per row, 16 per cell, raw UTF-8/blob bytes,
        and 8 bytes per numeric value. This is an admission budget, not RSS.
        """
        rows, size = [], 0
        for row in cursor:
            if len(rows) >= self.limits["rows"]:
                raise RehearsalError("ROW_LIMIT")
            size += 32 + 16 * len(row)
            for value in row:
                if isinstance(value, str):
                    size += len(value.encode("utf-8"))
                elif isinstance(value, bytes):
                    size += len(value)
                elif value is not None:
                    size += 8
            if size > self.limits["result_bytes"]:
                raise RehearsalError("RESULT_BYTES_LIMIT")
            rows.append(row)
        return rows

    def audit(self):
        integrity = self.collect(self.conn.execute("PRAGMA integrity_check"))
        if integrity != [("ok",)]:
            raise RehearsalError("INTEGRITY_FAILED")
        if self.conn.execute("PRAGMA foreign_key_check").fetchone():
            raise RehearsalError("FOREIGN_KEY_FAILED")
        size = self.conn.execute("PRAGMA page_count").fetchone()[0] * self.conn.execute("PRAGMA page_size").fetchone()[0]
        if size > self.limits["clone_bytes"]:
            raise RehearsalError("CLONE_SIZE_LIMIT")

    def snapshot(self):
        self.audit()
        schema = self.collect(self.conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"))
        # Structural metadata avoids guessing WITHOUT ROWID from SQL strings.
        if sqlite3.sqlite_version_info < (3, 37, 0):
            raise RehearsalError("SQLITE_VERSION_UNSUPPORTED")
        rowid_tables = {row[1]: not row[4] for row in self.collect(self.conn.execute("PRAGMA main.table_list")) if row[0] == "main"}
        tables = {}
        total = 0
        for kind, name, _, sql in schema:
            if kind != "table":
                continue
            if sql and "CREATE VIRTUAL TABLE" in sql.upper():
                raise RehearsalError("VIRTUAL_TABLE_UNSUPPORTED")
            rowid_tracked = rowid_tables.get(name, False)
            projection = "*"
            if rowid_tracked:
                declared = {row[1].casefold() for row in self.collect(self.conn.execute("PRAGMA table_xinfo(" + quote(name) + ")"))}
                alias = next((x for x in ("rowid", "_rowid_", "oid") if x not in declared), None)
                if alias is None:
                    raise RehearsalError("ROWID_UNOBSERVABLE")
                projection = quote(alias) + ",*"
            rows, columns = self.guarded("SELECT " + projection + " FROM " + quote(name), readonly=True)
            total += len(rows)
            if total > self.limits["rows"]:
                raise RehearsalError("ROW_LIMIT")
            tables[name] = {"rows": len(rows), "columns": columns[1:] if rowid_tracked else columns,
                            "rowid_tracked": rowid_tracked, "data_sha256": digest_rows(rows)}
        return {"schema_sha256": digest_rows(schema), "tables": tables,
                "user_version": self.conn.execute("PRAGMA user_version").fetchone()[0]}

    def script(self, sql):
        parts = statements(sql, self.limits["statements"])
        self.conn.execute("BEGIN")
        try:
            for number, part in enumerate(parts, 1):
                self.guarded(part)
                self.audit()
            self.conn.execute("COMMIT")
        except (RehearsalError, sqlite3.Error):
            self.conn.execute("ROLLBACK")
            raise
        return len(parts)

    def queries(self, phase):
        outcomes = []
        for check in self.config["checks"]:
            if phase not in check.get("phases", ["before", "after", "rollback"]):
                continue
            name = check["name"]
            try:
                rows, _ = self.guarded(check["sql"], readonly=True)
                passed = len(rows) == 1 and len(rows[0]) == 1 and rows[0][0] == check.get("expected", 0)
                if not passed:
                    self.findings.append({"code": "INVARIANT_FAILED", "phase": phase, "name": name})
                outcomes.append({"name": name, "passed": passed})
            except RehearsalError as error:
                self.findings.append({"code": error.code, "phase": phase, "name": name})
        consumers = {}
        for consumer in self.config["consumers"]:
            try:
                rows, columns = self.guarded(consumer["sql"], readonly=True)
                consumers[consumer["name"]] = {"rows": len(rows), "columns": columns, "data_sha256": digest_rows(rows)}
            except RehearsalError as error:
                self.findings.append({"code": error.code, "phase": phase, "name": consumer["name"]})
        return {"invariants": outcomes, "consumers": consumers}

    def run(self):
        result = {"decision": "REJECT", "findings": self.findings, "phases": {}, "rollback": "NOT_REQUESTED"}
        phase = "source"
        try:
            uri = Path(self.config["source"]).as_uri() + "?mode=ro&immutable=1"
            with closing(sqlite3.connect(uri, uri=True)) as source:
                source.backup(self.conn, pages=128, progress=lambda *args: self.backup_progress())
            self.conn.set_progress_handler(self.progress, 100)
            self.conn.execute("PRAGMA max_page_count=" + str(max(1, self.limits["clone_bytes"] // self.conn.execute("PRAGMA page_size").fetchone()[0])))
            before = self.snapshot()
            result["phases"]["before"] = {"snapshot": before, **self.queries("before")}
            if self.findings:
                return result
            phase = "migration"
            result["migration_statements"] = self.script(self.config["migration"])
            phase = "after"
            after = self.snapshot()
            result["phases"]["after"] = {"snapshot": after, **self.queries("after")}
            lost = sum(max(0, table["rows"] - after["tables"].get(name, {"rows": 0})["rows"]) for name, table in before["tables"].items())
            result["rows_lost"] = lost
            if lost > self.config["row_loss_budget"]:
                self.findings.append({"code": "ROW_LOSS", "phase": phase, "rows": lost})
            for consumer in self.config["consumers"]:
                name = consumer["name"]
                if consumer.get("stable") and result["phases"]["before"]["consumers"].get(name) != result["phases"]["after"]["consumers"].get(name):
                    self.findings.append({"code": "CONSUMER_CHANGED", "phase": phase, "name": name})
            if self.config["rollback"] is not None:
                phase = "rollback"
                result["rollback"] = "FAILED"
                result["rollback_statements"] = self.script(self.config["rollback"])
                restored = self.snapshot()
                result["phases"]["rollback"] = {"snapshot": restored, **self.queries("rollback")}
                differences = [name for name in sorted(before["tables"].keys() | restored["tables"].keys()) if before["tables"].get(name) != restored["tables"].get(name)]
                schema_equal = before["schema_sha256"] == restored["schema_sha256"] and before["user_version"] == restored["user_version"]
                result["restoration"] = {"data_equal": not differences, "schema_equal": schema_equal, "different_tables": differences}
                result["rollback"] = "RESTORED" if not differences and schema_equal else "LOSS"
                if differences or not schema_equal:
                    self.findings.append({"code": "ROLLBACK_NOT_RESTORED", "phase": phase})
            result["decision"] = "ACCEPT" if not self.findings else "REJECT"
        except RehearsalError as error:
            self.findings.append({"code": error.code, "phase": phase})
        except sqlite3.Error:
            self.findings.append({"code": self.reason or "DATABASE_ERROR", "phase": phase})
        finally:
            result["vm_steps_upper_estimate"] = self.steps + 99
            result["sqlite_version"] = sqlite3.sqlite_version
            self.conn.close()
        return result

    def backup_progress(self):
        if time.monotonic() > self.deadline:
            raise RehearsalError("TIME_LIMIT")
