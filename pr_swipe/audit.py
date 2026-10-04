"""Append-only, hash-chained audit log of every executor decision and GitHub write."""
import hashlib
import json
import os
import time
from pathlib import Path

GENESIS = "0" * 64


def _h(line: str) -> str:
    return hashlib.sha256(line.encode()).hexdigest()


class AuditLog:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self._last = GENESIS
        if self.path.exists():
            lines = self.path.read_text().splitlines()
            if lines:
                self._last = _h(lines[-1])

    def append(self, event, **fields):
        rec = {"ts": self.clock(), "event": event, "prev": self._last, **fields}
        line = json.dumps(rec, sort_keys=True)
        with open(self.path, "a") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._last = _h(line)


def verify(path) -> bool:
    prev = GENESIS
    for line in Path(path).read_text().splitlines():
        if json.loads(line).get("prev") != prev:
            return False
        prev = _h(line)
    return True
