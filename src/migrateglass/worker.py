"""Private child process protocol; invoked by the public parent controller."""
import json
import sys
from .engine import Engine


def main():
    try:
        result = Engine(json.load(sys.stdin)).run()
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    except (ValueError, TypeError, KeyError):
        print(json.dumps({"decision": "REJECT", "findings": [{"code": "INVALID_CONFIG", "phase": "config"}]}))


if __name__ == "__main__":
    main()
