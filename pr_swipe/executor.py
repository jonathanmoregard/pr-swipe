"""Executor: the only component holding a credential that can merge or close.

Runs as the prswipe system user. Accepts fixed-shape decisions on a 0600 unix socket.
"""
import json
import logging
import os
import socketserver
import threading
import time
from pathlib import Path

from . import decisions
from .audit import AuditLog
from .config import load
from .github import AppTokens, GitHub, GitHubError
from .train import Train

log = logging.getLogger("pr-swipe.executor")
CLOSE_NOTE = "Closed via pr-swipe (human decision)."


class Executor:
    def __init__(self, gh, audit, train, installed):
        self.gh, self.audit, self.train, self.installed = gh, audit, train, installed
        self.lock = threading.Lock()

    def handle(self, d) -> dict:
        try:
            decisions.validate(d)
        except decisions.DecisionError as e:
            return {"ok": False, "error": str(e)}
        with self.lock:
            self.audit.append("decision", **{k: d[k] for k in ("action", "repo", "number", "head_sha", "card_sha256")})
            try:
                if not self.installed(d["repo"]):
                    return {"ok": False, "error": "repo not covered by the merge-gate App"}
                if d["action"] == "close":
                    return self._close(d)
                return {"ok": self.train.enqueue(d)}
            except GitHubError as e:
                self.audit.append("error", repo=d["repo"], number=d["number"], status=e.status)
                return {"ok": False, "error": f"GitHub {e.status}"}

    def _close(self, d):
        pr = self.gh.pr(d["repo"], d["number"])
        if pr["state"] != "open" or pr["head"]["sha"] != d["head_sha"]:
            self.audit.append("close-aborted", repo=d["repo"], number=d["number"], head_sha=pr["head"]["sha"])
            return {"ok": False, "error": "PR changed since you saw it"}
        self.gh.close(d["repo"], d["number"])
        self.gh.comment(d["repo"], d["number"], CLOSE_NOTE)
        self.audit.append("closed", repo=d["repo"], number=d["number"], head_sha=d["head_sha"])
        return {"ok": True}

    def tick(self):
        with self.lock:
            self.train.step()


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        for raw in self.rfile:
            try:
                resp = self.server.executor.handle(json.loads(raw))
            except ValueError as e:
                resp = {"ok": False, "error": f"bad json: {e}"}
            self.wfile.write((json.dumps(resp) + "\n").encode())
            self.wfile.flush()


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def serve(executor, sock_path, interval=30):
    sock_path = Path(sock_path)
    sock_path.unlink(missing_ok=True)
    old = os.umask(0o177)
    try:
        srv = _Server(str(sock_path), _Handler)
    finally:
        os.umask(old)
    os.chmod(sock_path, 0o600)
    srv.executor = executor

    def loop():
        while True:
            try:
                executor.tick()
            except Exception:
                log.exception("train step failed")
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True).start()
    srv.serve_forever()


def main():
    logging.basicConfig(level=logging.INFO)
    cfg = load()
    cred_dir = Path(os.environ["CREDENTIALS_DIRECTORY"])
    tokens = AppTokens(os.environ["PR_SWIPE_APP_ID"], (cred_dir / "merge-gate.pem").read_bytes())
    gh = GitHub(tokens)
    audit = AuditLog(cfg.state / "audit.jsonl")
    train = Train(gh, audit, cfg.state / "train.json", cfg.returns)
    serve(Executor(gh, audit, train, tokens.installed), cfg.socket)


if __name__ == "__main__":
    main()
