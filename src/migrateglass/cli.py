import argparse
import json
from pathlib import Path
import sys
from .api import Limits, read_contract, rehearse, validate_contract
from .inputs import read_utf8


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rehearse SQLite migrations without deploying them")
    parser.add_argument("source", type=Path)
    parser.add_argument("migration", type=Path)
    parser.add_argument("--rollback", type=Path)
    parser.add_argument("--contract", type=Path, help="JSON with checks and consumers")
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--vm-steps", type=int, default=5_000_000)
    parser.add_argument("--sql-bytes", type=int, default=1024 * 1024)
    parser.add_argument("--contract-bytes", type=int, default=1024 * 1024)
    parser.add_argument("--row-loss-budget", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        limits = Limits(seconds=args.seconds, vm_steps=args.vm_steps,
                        sql_bytes=args.sql_bytes, contract_bytes=args.contract_bytes)
        limits.validate()
        contract = read_contract(args.contract, limits=limits) if args.contract else {"checks": [], "consumers": []}
        remaining = limits.sql_bytes - validate_contract(contract["checks"], contract["consumers"], limits)
        migration = read_utf8(args.migration, remaining)
        remaining -= len(migration.encode("utf-8"))
        rollback = read_utf8(args.rollback, remaining) if args.rollback else None
        result = rehearse(args.source, migration, rollback=rollback,
                          checks=contract["checks"], consumers=contract["consumers"],
                          limits=limits, row_loss_budget=args.row_loss_budget)
        print(json.dumps(result, indent=2, ensure_ascii=True, sort_keys=True))
        return 0 if result["decision"] == "ACCEPT" else 2
    except (OSError, ValueError, TypeError, AttributeError) as error:
        print(json.dumps({"decision": "INVALID", "error": "invalid input; check files, encoding, limits and contract"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
