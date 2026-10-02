import hashlib
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from migrateglass import Limits, rehearse
from migrateglass.engine import statements


class RehearsalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "source.db"
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executescript("CREATE TABLE parent(id INTEGER PRIMARY KEY, name TEXT); CREATE TABLE child(id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id), amount INTEGER CHECK(amount>=0)); INSERT INTO parent VALUES(1,'Alice'),(2,'Bob'); INSERT INTO child VALUES(1,1,40),(2,2,60);")
        self.original = self.path.read_bytes()

    def run_sql(self, sql, **kwargs):
        result = rehearse(self.path, sql, **kwargs)
        self.assertTrue(result["source_preserved"])
        self.assertEqual(self.original, self.path.read_bytes())
        return result

    def codes(self, report):
        return {x["code"] for x in report["findings"]}

    def test_reversible_real_data_and_consumer(self):
        result = self.run_sql("UPDATE child SET amount=amount+5;", rollback="UPDATE child SET amount=amount-5;",
                              checks=[{"name": "positive", "sql": "SELECT count(*) FROM child WHERE amount<0"}],
                              consumers=[{"name": "keys", "sql": "SELECT id FROM child", "stable": True}])
        self.assertEqual(result["decision"], "ACCEPT")
        self.assertEqual(result["rollback"], "RESTORED")

    def test_failed_second_statement_preserves_source(self):
        result = self.run_sql("UPDATE child SET amount=amount+1; INSERT INTO child VALUES(1,1,100);")
        self.assertIn("CONSTRAINT_FAILED", self.codes(result))
        self.assertNotIn("after", result["phases"])

    def test_duplicate_data_index_failure(self):
        result = self.run_sql("CREATE UNIQUE INDEX one_amount ON child(parent_id % 1);")
        self.assertIn("CONSTRAINT_FAILED", self.codes(result))

    def test_foreign_key_prevents_orphans(self):
        result = self.run_sql("INSERT INTO child VALUES(3,99,1);")
        self.assertIn("CONSTRAINT_FAILED", self.codes(result))

    def test_existing_foreign_key_failure(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("INSERT INTO child VALUES(3,99,1)")
        self.original = self.path.read_bytes()
        result = self.run_sql("SELECT 1;")
        self.assertIn("FOREIGN_KEY_FAILED", self.codes(result))

    def test_reject_corrupt_source(self):
        self.path.write_bytes(b"corrupt-not-a-database")
        self.original = self.path.read_bytes()
        self.assertEqual(self.run_sql("SELECT 1;")["decision"], "REJECT")

    def test_path_and_pragma_escape(self):
        target = Path(self.temp.name) / "escaped.db"
        commands = ["ATTACH DATABASE '" + str(target).replace("'", "''") + "' AS stolen;",
                    "VACUUM INTO '" + str(target).replace("'", "''") + "';",
                    "PRAGMA writable_schema=ON;", "PRAGMA journal_mode=WAL;",
                    "PRAGMA temp_store_directory='..';", "SELECT load_extension('fake');",
                    "BEGIN; UPDATE parent SET name='leak'; COMMIT;"]
        for sql in commands:
            with self.subTest(sql=sql):
                result = self.run_sql(sql)
                self.assertEqual(result["decision"], "REJECT")
                self.assertFalse(target.exists())

    def test_readonly_contract_rejects_cte_write_and_pragma(self):
        for sql in ["WITH x AS (SELECT 1) DELETE FROM child RETURNING id", "PRAGMA writable_schema=ON", "UPDATE child SET amount=0 RETURNING amount"]:
            result = self.run_sql("SELECT 1;", consumers=[{"name": "malicious", "sql": sql}])
            self.assertEqual(result["decision"], "REJECT")

    def test_trigger_parser_and_trigger_behavior(self):
        sql = "CREATE TABLE audit(value TEXT); CREATE TRIGGER track AFTER UPDATE ON child BEGIN INSERT INTO audit VALUES('one;two'); INSERT INTO audit VALUES('three'); END; UPDATE child SET amount=amount+1 WHERE id=1;"
        self.assertEqual(len(statements(sql, 10)), 3)
        report = self.run_sql(sql)
        self.assertEqual(report["decision"], "ACCEPT")
        self.assertEqual(report["phases"]["after"]["snapshot"]["tables"]["audit"]["rows"], 2)

    def test_incomplete_trigger_and_statement_limit(self):
        self.assertIn("INCOMPLETE_SQL", self.codes(self.run_sql("CREATE TRIGGER t AFTER UPDATE ON child BEGIN SELECT 1;")))
        self.assertIn("STATEMENT_LIMIT", self.codes(self.run_sql("SELECT 1; SELECT 2;", limits=Limits(statements=1))))

    def test_row_loss_and_budget(self):
        sql = "DELETE FROM child WHERE id=1;"
        self.assertIn("ROW_LOSS", self.codes(self.run_sql(sql)))
        self.assertEqual(self.run_sql(sql, row_loss_budget=1)["decision"], "ACCEPT")

    def test_invariant_and_consumer_changes(self):
        report = self.run_sql("UPDATE child SET amount=0;", checks=[{"name": "nonzero", "sql": "SELECT count(*) FROM child WHERE amount=0"}])
        self.assertIn("INVARIANT_FAILED", self.codes(report))
        report = self.run_sql("UPDATE child SET amount=1;", consumers=[{"name": "amounts", "sql": "SELECT amount FROM child", "stable": True}])
        self.assertIn("CONSUMER_CHANGED", self.codes(report))

    def test_rollback_schema_is_not_data_restoration(self):
        report = self.run_sql("UPDATE parent SET name='';", rollback="UPDATE parent SET name='unknown';")
        self.assertEqual(report["restoration"]["schema_equal"], True)
        self.assertEqual(report["restoration"]["data_equal"], False)
        self.assertIn("ROLLBACK_NOT_RESTORED", self.codes(report))

    def test_bound_steps_rows_and_values(self):
        sql = "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<1000000000) SELECT sum(x) FROM n;"
        self.assertIn("STEP_LIMIT", self.codes(self.run_sql(sql, limits=Limits(vm_steps=2000))))
        self.assertIn("ROW_LIMIT", self.codes(self.run_sql("SELECT 1;", limits=Limits(rows=1))))
        self.assertEqual(self.run_sql("SELECT zeroblob(2000000);", limits=Limits(value_bytes=100000))["decision"], "REJECT")

    def test_worker_wall_time(self):
        result = self.run_sql("SELECT 1;", limits=Limits(seconds=0.000001))
        self.assertIn("TIME_LIMIT", self.codes(result))

    def test_virtual_table_denied(self):
        self.assertIn("SQL_DENIED", self.codes(self.run_sql("CREATE VIRTUAL TABLE docs USING fts5(body);")))

    def test_contract_report_has_no_raw_values(self):
        report = self.run_sql("SELECT 1;", consumers=[{"name": "names", "sql": "SELECT name FROM parent"}])
        self.assertNotIn("Alice", json.dumps(report))

    def test_wal_companion_and_invalid_limits(self):
        companion = Path(str(self.path) + "-wal")
        companion.write_bytes(b"not-real-wal")
        with self.assertRaises(ValueError):
            rehearse(self.path, "SELECT 1;")
        companion.unlink()
        for limits in [Limits(seconds=float("nan")), Limits(rows=0), Limits(seconds=True)]:
            with self.assertRaises(ValueError):
                rehearse(self.path, "SELECT 1;", limits=limits)


if __name__ == "__main__":
    unittest.main()
