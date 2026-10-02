"""Deterministic synthetic embedded billing data; no customer dataset."""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3


def create_fixture(path, rows=5000):
    path = Path(path)
    if path.exists():
        raise ValueError("refusing to overwrite existing fixture")
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript("CREATE TABLE accounts(id INTEGER PRIMARY KEY, name TEXT NOT NULL); CREATE TABLE invoices(id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id), external_ref TEXT NOT NULL, cents INTEGER NOT NULL CHECK(cents>=0), note TEXT); CREATE TABLE obsolete_cache(key TEXT);")
        connection.executemany("INSERT INTO accounts VALUES(?,?)", [(n, f"Synthetic account {n}") for n in range(1, 121)])
        connection.executemany("INSERT INTO invoices VALUES(?,?,?,?,?)", [(n, n % 120 + 1, f"ref-{n % 4999}", n % 1000 + 100, f"Memo {n}") for n in range(1, rows + 1)])
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    args = parser.parse_args()
    print(create_fixture(args.path))
