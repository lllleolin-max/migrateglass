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
    if sum(len(x.encode("utf-8")) for x in (migration, rollback or "")) > limits.sql_bytes:
        raise ValueError("sql_bytes limit exceeded")
    payload = {"source": str(path), "migration": migration, "rollback": rollback,
               "checks": checks or [], "consumers": consumers or [],
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
