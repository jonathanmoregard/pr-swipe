"""GUI-side state: decided cards, gate metrics, outbox requests, returned-card notes."""
import json
import socket
import time
import uuid
from pathlib import Path

from ..card import write_json_atomic
from .deck import card_key

RETURN_TTL = 7 * 86400


class Store:
    def __init__(self, cfg, clock=time.time):
        self.cfg, self.clock = cfg, clock
        self.state_file = cfg.state / "gui-state.json"
        self.metrics_file = cfg.state / "metrics.jsonl"

    def _load(self):
        return json.loads(self.state_file.read_text()) if self.state_file.exists() else {"decided": {}}

    def _save(self, st):
        write_json_atomic(self.cfg.state, self.state_file.name, st, mode=0o600)

    def decided(self) -> dict:
        return self._load()["decided"]

    def mark_decided(self, card, action, dwell, detail):
        st = self._load()
        st["decided"][card_key(card)] = self.clock()
        self._save(st)
        with open(self.metrics_file, "a") as f:
            f.write(json.dumps({"ts": self.clock(), "key": card_key(card), "action": action,
                                "dwell": round(dwell, 2), "detail": detail}) + "\n")

    def undo(self, card):
        st = self._load()
        st["decided"].pop(card_key(card), None)
        self._save(st)
        with open(self.metrics_file, "a") as f:
            f.write(json.dumps({"ts": self.clock(), "key": card_key(card), "action": "undo",
                                "dwell": 0, "detail": False}) + "\n")

    def metrics(self, days=7) -> list:
        if not self.metrics_file.exists():
            return []
        cutoff = self.clock() - days * 86400
        rows = [json.loads(l) for l in self.metrics_file.read_text().splitlines() if l.strip()]
        kept, latest = [], {}
        for r in rows:  # an undo row (human undo or executor refusal) cancels that key's latest decision
            if r["action"] == "undo":
                if r["key"] in latest:
                    kept[latest.pop(r["key"])] = None
            elif r["action"] in ("approve", "close"):
                latest[r["key"]] = len(kept)
                kept.append(r)
        return [r for r in kept if r is not None and r["ts"] >= cutoff]

    def _request(self, obj):
        write_json_atomic(self.cfg.outbox, f"{uuid.uuid4().hex}.json", obj, mode=0o660)

    def request_open(self, url):
        self._request({"kind": "open", "url": url})

    def request_deep_review(self, card):
        self._request({"kind": "deep-review", "repo": card["repo"], "number": card["number"],
                       "head_sha": card["head_sha"]})

    def returns(self) -> list:
        out = []
        for p in Path(self.cfg.returns).glob("*.json"):
            try:
                r = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            if self.clock() - r.get("ts", 0) > RETURN_TTL:
                p.unlink(missing_ok=True)
                continue
            out.append(r)
        return out

    def in_train(self) -> set:
        p = self.cfg.state / "train.json"
        if not p.exists():
            return set()
        return {f"{it['repo']}#{it['number']}" for q in json.loads(p.read_text())["queues"].values() for it in q}


class ExecutorClient:
    def __init__(self, path):
        self.path = Path(path)

    def send(self, decision) -> dict:
        try:
            with socket.socket(socket.AF_UNIX) as s:
                s.settimeout(30)
                s.connect(str(self.path))
                f = s.makefile("rw")
                f.write(json.dumps(decision) + "\n")
                f.flush()
                return json.loads(f.readline())
        except (OSError, ValueError) as e:
            return {"ok": False, "error": f"executor unreachable: {e}"}
