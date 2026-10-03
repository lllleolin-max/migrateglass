"""Bounded UTF-8 inputs. Validation never includes caller payloads in errors."""
import json
from pathlib import Path
import stat

CONTRACT_DEPTH = 16


def read_utf8(path, max_bytes):
    """Check size before reading, then cap the read even if the file grows."""
    path = Path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("input must be a regular file")
    if info.st_size > max_bytes:
        raise ValueError("input byte limit exceeded")
    with path.open("rb") as stream:
        data = bytearray()
        while True:
            # Fixed chunks avoid a platform read-size overflow even when a caller
            # supplies a large positive budget. At most one sentinel byte exceeds it.
            chunk = stream.read(min(64 * 1024, max_bytes - len(data) + 1))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > max_bytes:
                raise ValueError("input byte limit exceeded")
    try:
        return data.decode("utf-8")
    except UnicodeError:
        raise ValueError("input must be valid UTF-8") from None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("contract JSON has duplicate object keys")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("contract JSON must use finite numbers")


def parse_contract(text):
    if text.startswith("\ufeff"):
        raise ValueError("contract UTF-8 BOM is not supported")
    # Bound nesting before the JSON parser allocates a nested object graph.
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > CONTRACT_DEPTH:
                raise ValueError("contract JSON nesting exceeds 16 levels")
        elif char in "]}":
            depth -= 1
    try:
        result = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
    except json.JSONDecodeError:
        raise ValueError("contract must be valid JSON") from None
    if type(result) is not dict or result.keys() - {"checks", "consumers"}:
        raise ValueError("contract must contain only checks and consumers")
    return result
