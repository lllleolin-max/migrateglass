"""Strict file admission and SDK/CLI parity before worker or staging."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from migrateglass import Limits, read_contract, rehearse
from migrateglass.cli import main
from migrateglass.inputs import CONTRACT_DEPTH, parse_contract, read_utf8


class ContractInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "source.db"
        with contextlib.closing(sqlite3.connect(self.source)) as conn, conn:
            conn.executescript("CREATE TABLE t(value INTEGER); INSERT INTO t VALUES(1);")
        self.migration, self.rollback, self.contract = [self.base / x for x in ("migration.sql", "rollback.sql", "contract.json")]
        self.migration.write_bytes(b"UPDATE t SET value=0;")
        self.rollback.write_bytes(b"UPDATE t SET value=1;")
        self.check = {"name": "positive", "sql": "SELECT count(*) FROM t WHERE value<=0"}
        self.before = self.source.read_bytes()

    def invoke(self, data, **options):
        self.contract.write_bytes(data)
        before = {p: p.read_bytes() for p in (self.source, self.migration, self.rollback, self.contract)}
        stdout, stderr = io.StringIO(), io.StringIO()
        args = [str(self.source), str(self.migration), "--contract", str(self.contract)]
        for key, value in options.items():
            args.extend(["--" + key.replace("_", "-"), str(value)])
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = main(args)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        return status, stdout.getvalue(), stderr.getvalue()

    def test_duplicate_top_and_nested_keys_reject_without_worker(self):
        cases = (
            '{"checks":' + json.dumps([self.check]) + ',"checks":[]}',
            '{"consumers":[],"consumers":[]}',
            '{"checks":[],"c\\u0068ecks":[]}',
            '{"checks":[{"name":"x","sql":"SELECT 1","sql":"SELECT 0"}]}',
            '{"consumers":[{"name":"x","sql":"SELECT 1","stable":true,"stable":false}]}',
            '{"checks":[{"name":"x","sql":"SELECT 1","expected":{"a":1,"a":2}}]}',
        )
        with patch("migrateglass.api.subprocess.run") as worker, patch("migrateglass.api.file_digest") as digest:
            for text in cases:
                with self.subTest(text=text):
                    status, out, err = self.invoke(text.encode())
                    self.assertEqual(status, 1)
                    self.assertFalse(out)
                    self.assertEqual(json.loads(err)["decision"], "INVALID")
                    self.assertNotIn("SELECT", err)
                    with self.assertRaises(ValueError):
                        read_contract(self.contract)
            worker.assert_not_called()
            digest.assert_not_called()

    def test_ordinary_positive_still_rejects_and_safe_roundtrip_restores(self):
        status, out, _ = self.invoke(json.dumps({"checks": [self.check]}).encode())
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(out)["decision"], "REJECT")
        self.migration.write_bytes(b"CREATE INDEX by_value ON t(value);")
        self.rollback.write_bytes(b"DROP INDEX by_value;")
        contract = {"checks": [self.check], "consumers": [{"name": "values", "sql": "SELECT value FROM t", "stable": True}]}
        self.contract.write_text(json.dumps(contract), encoding="utf-8")
        loaded = read_contract(self.contract)
        report = rehearse(self.source, self.migration.read_text(), rollback=self.rollback.read_text(), **loaded)
        self.assertEqual((report["decision"], report["rollback"]), ("ACCEPT", "RESTORED"))
        self.assertEqual(self.source.read_bytes(), self.before)
        status, out, _ = self.invoke(json.dumps(contract).encode(), rollback=self.rollback)
        self.assertEqual((status, json.loads(out)["rollback"]), (0, "RESTORED"))

    def test_controlled_utf8_bom_constants_depth_shapes_and_fields(self):
        cases = [b'\xef\xbb\xbf{}', b'{"checks":[]}\xff', b'{"checks":[', b'[]', b'null',
                 b'{"checks":null}', b'{"consumers":{}}', b'{"unknown":[]}',
                 b'{"checks":[{"name":"x","sql":"SELECT 1","expected":NaN}]}',
                 b'{"checks":[{"name":"x","sql":"SELECT 1","expected":Infinity}]}',
                 b'{"checks":[{"name":"x","sql":"SELECT 1","expected":-Infinity}]}',
                 b'{"checks":[{"name":"x","sql":"SELECT 1","expected":1e999}]}',
                 b'{"consumers":[{"name":"x","sql":"SELECT 1","stable":1}]}',
                 ('{"checks":' + '[' * 17 + '0' + ']' * 17 + '}').encode(),
                 b'{"checks":[{"name":"x","sql":"SELECT 1","expected":"\\ud800"}]}']
        with patch("migrateglass.api.subprocess.run") as worker:
            for data in cases:
                with self.subTest(data=data):
                    status, out, err = self.invoke(data)
                    self.assertEqual(status, 1)
                    self.assertFalse(out)
                    self.assertNotIn("SELECT", err)
            worker.assert_not_called()

    def test_contract_byte_boundary_multibyte_and_late_invalid(self):
        data = json.dumps({"consumers": [{"name": "中", "sql": "SELECT 1"}]}, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
        # The normalized SDK object adds the empty checks list. Include that explicit
        # list here so raw and normalized byte lengths have the same exact boundary.
        data = b'{"checks":[],' + data[1:]
        self.contract.write_bytes(data)
        for limit in (len(data), len(data) + 1):
            self.assertEqual(read_contract(self.contract, limits=Limits(contract_bytes=limit))["consumers"][0]["name"], "中")
        with self.assertRaises(ValueError):
            read_contract(self.contract, limits=Limits(contract_bytes=len(data) - 1))
        self.contract.write_bytes(data + b'\xff')
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            read_contract(self.contract, limits=Limits(contract_bytes=len(data) + 1))

    def test_oversize_refuses_read_and_parse(self):
        self.contract.write_bytes(b" " * 101)
        with patch("pathlib.Path.open") as opened, patch("migrateglass.api.parse_contract") as parsed:
            with self.assertRaisesRegex(ValueError, "byte limit"):
                read_contract(self.contract, limits=Limits(contract_bytes=100))
            opened.assert_not_called()
            parsed.assert_not_called()

    def test_read_remains_bounded_when_file_grows_after_stat(self):
        self.contract.write_bytes(b'{}')
        stream = io.BytesIO(b'{}' + b' ' * 100)
        with patch("pathlib.Path.open", return_value=stream):
            with self.assertRaisesRegex(ValueError, "byte limit"):
                read_utf8(self.contract, 10)
            self.assertEqual(stream.tell() if not stream.closed else 11, 11)

    def test_depth_bound_ignores_braces_escapes_and_counts_real_containers(self):
        for depth in (CONTRACT_DEPTH - 1, CONTRACT_DEPTH):
            text = '{"checks":' + '[' * (depth - 1) + '0' + ']' * (depth - 1) + '}'
            self.assertIn("checks", parse_contract(text))  # Syntax stage only; schema validation is separate.
        with self.assertRaisesRegex(ValueError, "nesting"):
            parse_contract('{"checks":' + '[' * CONTRACT_DEPTH + '0' + ']' * CONTRACT_DEPTH + '}')
        text = json.dumps({"checks": [{"name": "x", "sql": 'SELECT "' + '{' * 30 + '\\\"' + '}' * 30 + '"'}]})
        self.assertEqual(parse_contract(text)["checks"][0]["name"], "x")

    def test_combined_sql_exact_utf8_boundary_and_cli_preflight(self):
        # Keep the aggregate boundary above trusted SQLite metadata statement
        # lengths; a tiny SQL_LENGTH cap also limits those existing audit queries.
        self.migration.write_bytes(b"/*" + ("中" * 400).encode() + b"*/SELECT 1;")
        self.rollback.write_bytes(b"SELECT 2;")
        check = {"name": "x", "sql": "SELECT 0"}
        data = json.dumps({"checks": [check]}).encode()
        total = len(self.migration.read_bytes()) + len(self.rollback.read_bytes()) + len(check["sql"].encode())
        with patch("migrateglass.api.subprocess.run") as worker:
            status, out, _ = self.invoke(data, rollback=self.rollback, sql_bytes=total - 1)
            self.assertEqual(status, 1)
            self.assertFalse(out)
            worker.assert_not_called()
        status, out, _ = self.invoke(data, rollback=self.rollback, sql_bytes=total)
        self.assertEqual(status, 0, out)
        loaded = read_contract(self.contract)
        with self.assertRaisesRegex(ValueError, "sql_bytes"):
            rehearse(self.source, self.migration.read_text(encoding="utf-8"), rollback=self.rollback.read_text(),
                     limits=Limits(sql_bytes=total - 1), **loaded)

    def test_sdk_metadata_budget_and_invalid_limits_before_worker(self):
        checks = [{"name": "x", "sql": "SELECT 0", "expected": "x" * 1000}]
        with patch("migrateglass.api.subprocess.run") as worker:
            with self.assertRaisesRegex(ValueError, "contract_bytes"):
                rehearse(self.source, "SELECT 1", checks=checks, limits=Limits(contract_bytes=100))
            self.contract.write_bytes(b'{}')
            for cap in (0, -1, True):
                with self.assertRaises(ValueError):
                    read_contract(self.contract, limits=Limits(contract_bytes=cap))
            worker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
