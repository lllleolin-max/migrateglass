from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from migrateglass import Limits, rehearse


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "probe.db"

    def create(self, sql):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.executescript(sql)

    def test_hidden_rowid_must_be_restored(self):
        self.create("CREATE TABLE messages(body TEXT); INSERT INTO messages(rowid,body) VALUES(7,'same'),(9,'same');")
        report = rehearse(self.path, "UPDATE messages SET rowid=rowid+100;", rollback="SELECT 1;")
        self.assertEqual(report["decision"], "REJECT", report)
        self.assertEqual(report["restoration"]["data_equal"], False)
        self.assertEqual(report["restoration"]["different_tables"], ["messages"])

    def test_shadowed_rowid_uses_other_alias(self):
        self.create("CREATE TABLE messages(rowid TEXT, body TEXT); INSERT INTO messages(_rowid_,rowid,body) VALUES(7,'visible','same');")
        report = rehearse(self.path, "UPDATE messages SET _rowid_=8;", rollback="SELECT 1;")
        self.assertEqual(report["decision"], "REJECT")

    def test_all_rowid_aliases_shadowed_rejects_unknown(self):
        self.create("CREATE TABLE hidden(rowid TEXT, _rowid_ TEXT, oid TEXT); INSERT INTO hidden VALUES('a','b','c');")
        report = rehearse(self.path, "SELECT 1;")
        self.assertEqual(report["findings"][0]["code"], "ROWID_UNOBSERVABLE")

    def test_without_rowid_composite_key_roundtrip(self):
        self.create("CREATE TABLE messages(a TEXT,b INTEGER,PRIMARY KEY(a,b)) WITHOUT ROWID; INSERT INTO messages VALUES('a',1);")
        report = rehearse(self.path, "UPDATE messages SET b=2;", rollback="UPDATE messages SET b=1;")
        self.assertEqual(report["decision"], "ACCEPT")
        self.assertFalse(report["phases"]["before"]["snapshot"]["tables"]["messages"]["rowid_tracked"])

    def test_contract_phase_typo_does_not_silently_skip(self):
        self.create("CREATE TABLE invoices(amount INTEGER); INSERT INTO invoices VALUES(20);")
        with self.assertRaisesRegex(ValueError, "phases"):
            rehearse(self.path, "UPDATE invoices SET amount=0;", checks=[{"name": "positive", "sql": "SELECT count(*) FROM invoices WHERE amount<=0", "phases": ["aftre"]}])

    def test_duplicate_consumer_names_cannot_overwrite_stable_result(self):
        self.create("CREATE TABLE invoices(amount INTEGER); INSERT INTO invoices VALUES(20);")
        with self.assertRaisesRegex(ValueError, "unique"):
            rehearse(self.path, "UPDATE invoices SET amount=0;", consumers=[{"name": "critical", "sql": "SELECT amount FROM invoices", "stable": True}, {"name": "critical", "sql": "SELECT count(*) FROM invoices"}])

    def test_contract_shapes_unknown_fields_and_aggregate_sql_limit(self):
        self.create("CREATE TABLE x(value INTEGER);")
        for kwargs in [
            {"checks": {}}, {"consumers": [{"name": "x", "sql": "SELECT 1", "stabl": True}]},
            {"checks": [{"name": "x", "sql": "SELECT 1", "phases": []}]},
            {"checks": [{"name": "x", "sql": "SELECT 1", "expected": float("nan")}]},
            {"consumers": [{"name": "x", "sql": "SELECT 1"}], "limits": Limits(sql_bytes=12)},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                rehearse(self.path, "SELECT 1;", **kwargs)


if __name__ == "__main__":
    unittest.main()
