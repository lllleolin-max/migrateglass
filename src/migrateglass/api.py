"""Parent controller. Only the worker ever opens SQLite."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import time


@dataclass(frozen=True)
class Limits:
    seconds: float = 8.0
    vm_steps: int = 5_000_000
    source_bytes: int = 128 * 1024 * 1024
    clone_bytes: int = 128 * 1024 * 1024
    sql_bytes: int = 1024 * 1024
    rows: int = 200_000
    statements: int = 1000
    value_bytes: int = 1024 * 1024
    result_bytes: int = 16 * 1024 * 1024

    def validate(self):
        if isinstance(self.seconds, bool) or not isinstance(self.seconds, (int, float)) or not math.isfinite(self.seconds) or not 0 < self.seconds <= 600:
            raise ValueError("seconds must be finite in (0, 600]")
        for key, value in asdict(self).items():
            if key != "seconds" and (type(value) is not int or value <= 0):
                raise ValueError(f"{key} must be a positive integer")


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_contract(checks, consumers, limits):
    """Reject ambiguity before launching the worker, never silently omit a check."""
    names = set()
    sql_bytes = 0
    if type(checks) is not list or type(consumers) is not list:
        raise ValueError("checks and consumers must be lists")
    if len(checks) + len(consumers) > limits.statements:
        raise ValueError("contract query count exceeds statements limit")
    for kind, entries in (("check", checks), ("consumer", consumers)):
        fields = {"name", "sql", "expected", "phases"} if kind == "check" else {"name", "sql", "stable"}
        for entry in entries:
            if type(entry) is not dict or entry.keys() - fields or not {"name", "sql"} <= entry.keys():
                raise ValueError(f"invalid {kind} fields")
            name, sql = entry["name"], entry["sql"]
            if not isinstance(name, str) or not name.strip() or len(name.encode("utf-8")) > 128:
                raise ValueError("contract name must be nonempty and <=128 bytes")
            if name in names:
                raise ValueError("contract names must be unique")
            names.add(name)
            if not isinstance(sql, str) or not sql.strip():
                raise ValueError("contract SQL must be nonempty text")
            sql_bytes += len(sql.encode("utf-8"))
            if kind == "check":
                phases = entry.get("phases", ["before", "after", "rollback"])
                if type(phases) is not list or not phases or any(x not in ("before", "after", "rollback") for x in phases) or len(set(phases)) != len(phases):
                    raise ValueError("check phases must be unique known phases")
                expected = entry.get("expected", 0)
                if expected is not None and type(expected) not in (str, int, float, bool):
                    raise ValueError("check expected must be a JSON scalar")
                if type(expected) is float and not math.isfinite(expected):
                    raise ValueError("check expected must be finite")
            elif "stable" in entry and type(entry["stable"]) is not bool:
                raise ValueError("consumer stable must be boolean")
    return sql_bytes


def rehearse(source: str | Path, migration: str, *, rollback: str | None = None,
             checks: list[dict] | None = None, consumers: list[dict] | None = None,
             limits: Limits | None = None, row_loss_budget: int = 0) -> dict:
    """Return JSON-compatible decision. ValueError/OSError mean invalid invocation.

    Checks: {name, sql, expected=0, phases=[before,after,rollback]} scalar queries.
    Consumers: {name, sql, stable=False}; always executed before and after.
    Source must be quiescent, standalone, and contain no journal/WAL companions.
    """
    limits = limits or Limits()
    limits.validate()
    if type(row_loss_budget) is not int or row_loss_budget < 0:
        raise ValueError("row_loss_budget must be a nonnegative integer")
    path = Path(source).absolute()
    if path.is_symlink() or not path.is_file():
        raise ValueError("source must be an existing regular file, not a symlink")
    if path.stat().st_size > limits.source_bytes:
        raise ValueError("source_bytes limit exceeded")
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(path) + suffix).exists():
            raise ValueError("source has a journal/WAL companion; close and checkpoint it first")
    if not isinstance(migration, str) or (rollback is not None and not isinstance(rollback, str)):
        raise ValueError("SQL must be text")
    checks = [] if checks is None else checks
    consumers = [] if consumers is None else consumers
    contract_bytes = validate_contract(checks, consumers, limits)
    if sum(len(x.encode("utf-8")) for x in (migration, rollback or "")) + contract_bytes > limits.sql_bytes:
        raise ValueError("sql_bytes limit exceeded")
    payload = {"source": str(path), "migration": migration, "rollback": rollback,
               "checks": checks, "consumers": consumers,
               "limits": asdict(limits), "row_loss_budget": row_loss_budget}
    before = file_digest(path)
    started = time.monotonic()
    try:
        process = subprocess.run([sys.executable, "-m", "migrateglass.worker"],
                                 input=json.dumps(payload), text=True, encoding="utf-8",
                                 capture_output=True, timeout=limits.seconds)
        if process.returncode:
            result = {"decision": "REJECT", "findings": [{"code": "WORKER_FAILURE", "phase": "worker"}]}
        else:
            result = json.loads(process.stdout)
    except subprocess.TimeoutExpired:
        result = {"decision": "REJECT", "findings": [{"code": "TIME_LIMIT", "phase": "worker"}]}
    after = file_digest(path)
    preserved = before == after
    result.update({"report_version": 1, "source_preserved": preserved,
                   "source_sha256": before, "elapsed_seconds": round(time.monotonic() - started, 6)})
    if not preserved:
        result["decision"] = "REJECT"
        result["findings"].append({"code": "SOURCE_CHANGED", "phase": "source"})
    return result
