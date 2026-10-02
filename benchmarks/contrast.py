"""Executable, explicitly limited regex baseline and feature ablation.

Not an Atlas benchmark: Atlas is a richer migration analyzer.
"""
import json
from contextlib import closing
from pathlib import Path
import re
import statistics
import sqlite3
import sys
import tempfile
import time
import platform

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from make_fixture import create_fixture
from migrateglass import rehearse


def lexical_lint(sql):
    return "REJECT" if re.search(r"\b(DROP|DELETE|TRUNCATE)\b", sql, re.I) else "ACCEPT"


def main():
    contract = json.loads((Path(__file__).resolve().parents[1] / "examples" / "contract.json").read_text())
    cases = [
        ("safe_index", "CREATE INDEX invoices_by_account ON invoices(account_id);", "DROP INDEX invoices_by_account;", "ACCEPT"),
        ("duplicate_reference", "CREATE UNIQUE INDEX unique_ref ON invoices(external_ref);", None, "REJECT"),
        ("zero_values", "UPDATE invoices SET cents=0;", None, "REJECT"),
        ("rollback_memo_loss", "UPDATE invoices SET note=NULL;", "UPDATE invoices SET note='';", "REJECT"),
        ("consumer_rename", "ALTER TABLE invoices RENAME COLUMN cents TO amount;", None, "REJECT"),
        ("safe_empty_drop", "DROP TABLE obsolete_cache;", "CREATE TABLE obsolete_cache(key TEXT);", "ACCEPT"),
        ("unmodeled_note_policy", "UPDATE invoices SET note=upper(note);", None, "ACCEPT"),
    ]
    results, durations = [], []
    with tempfile.TemporaryDirectory() as temp:
        source = create_fixture(Path(temp) / "billing.db")
        schema_only = Path(temp) / "empty-schema.db"
        with closing(sqlite3.connect(source)) as original, closing(sqlite3.connect(schema_only)) as empty, empty:
            for (sql,) in original.execute("SELECT sql FROM sqlite_schema WHERE type='table' AND sql IS NOT NULL ORDER BY name"):
                empty.execute(sql)
        for name, up, down, expected in cases:
            start = time.perf_counter()
            report = rehearse(source, up, rollback=down, **contract)
            duration = time.perf_counter() - start
            durations.append(duration)
            empty_report = rehearse(schema_only, up, rollback=down, **contract)
            results.append({"case": name, "expected_declared_policy": expected,
                            "lexical": lexical_lint(up), "rehearsal": report["decision"],
                            "empty_schema_rehearsal": empty_report["decision"],
                            "findings": sorted({x["code"] for x in report["findings"]}),
                            "source_preserved": report["source_preserved"], "seconds": round(duration, 4)})
        ablation = {
            "no_contract_zero_values": rehearse(source, "UPDATE invoices SET cents=0;")["decision"],
            "no_rollback_memo_loss": rehearse(source, "UPDATE invoices SET note=NULL;", **contract)["decision"],
        }
    output = {"fixture": {"synthetic": True, "invoices": 5000, "accounts": 120},
              "environment": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version, "platform": platform.system()},
              "baseline": "regex DROP/DELETE/TRUNCATE and same-engine empty-schema rehearsal; neither is Atlas", "cases": results,
              "ablation": ablation, "median_rehearsal_seconds": round(statistics.median(durations), 4),
              "policy_matches": sum(row["rehearsal"] == row["expected_declared_policy"] for row in results),
              "lexical_matches": sum(row["lexical"] == row["expected_declared_policy"] for row in results),
              "empty_schema_matches": sum(row["empty_schema_rehearsal"] == row["expected_declared_policy"] for row in results)}
    print(json.dumps(output, indent=2))
    assert output["policy_matches"] == len(cases)
    assert all(row["source_preserved"] for row in results)
    assert ablation == {"no_contract_zero_values": "ACCEPT", "no_rollback_memo_loss": "ACCEPT"}


if __name__ == "__main__":
    main()
