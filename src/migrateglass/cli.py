import argparse
import json
from pathlib import Path
import sys
from .api import Limits, rehearse


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rehearse SQLite migrations without deploying them")
    parser.add_argument("source", type=Path)
    parser.add_argument("migration", type=Path)
    parser.add_argument("--rollback", type=Path)
    parser.add_argument("--contract", type=Path, help="JSON with checks and consumers")
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--vm-steps", type=int, default=5_000_000)
    parser.add_argument("--row-loss-budget", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        contract = json.loads(args.contract.read_text(encoding="utf-8")) if args.contract else {}
        result = rehearse(args.source, args.migration.read_text(encoding="utf-8"),
                          rollback=args.rollback.read_text(encoding="utf-8") if args.rollback else None,
                          checks=contract.get("checks"), consumers=contract.get("consumers"),
                          limits=Limits(seconds=args.seconds, vm_steps=args.vm_steps), row_loss_budget=args.row_loss_budget)
        print(json.dumps(result, indent=2, ensure_ascii=True, sort_keys=True))
        return 0 if result["decision"] == "ACCEPT" else 2
    except (OSError, ValueError, TypeError, AttributeError) as error:
        print(json.dumps({"decision": "INVALID", "error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
