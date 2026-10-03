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
from .autobump import AutoBump
from .audit import AuditLog
from .config import load
from .github import AppTokens, GitHub, GitHubError
from .train import Train
from .verify import Verifier, is_verified

log = logging.getLogger("pr-swipe.executor")
CLOSE_NOTE = "Closed via pr-swipe (human decision)."
# A decision waits at most LOCK_WAIT for a running train step, then is refused as busy.
# Once it holds the lock it makes at most 6 GitHub calls (installation lookup + token,
# PR read, close, comment), each bounded by the client's 30 s HTTP timeout. The GUI's
# socket timeout must exceed both, or a decision the GUI shows as refused could still act.
LOCK_WAIT = 20.0
MAX_HANDLE_S = 6 * 30.0


class Executor:
    def __init__(self, gh, audit, train, installed, verified, verifier=None, autobump=None):
        """`verified(repo, number, head_sha)` must be true before any decision acts: the human saw
        evidence fetched by the verifier for exactly that head, not a card the user's uid wrote."""
        self.gh, self.audit, self.train, self.installed = gh, audit, train, installed
        self.verified, self.verifier, self.autobump = verified, verifier, autobump
        self.lock = threading.Lock()
        if autobump is not None:
            autobump.lock = self.lock

    def handle(self, d) -> dict:
        try:
            decisions.validate(d)
        except decisions.DecisionError as e:
            return {"ok": False, "error": str(e)}
        if not self.lock.acquire(timeout=LOCK_WAIT):
            return {"ok": False, "error": "executor busy (merge train step running), try again"}
        try:
            self.audit.append("decision", **{k: d[k] for k in ("action", "repo", "number", "head_sha", "card_sha256")})
            try:
                if not self.installed(d["repo"]):
                    return {"ok": False, "error": "repo not covered by the merge-gate App"}
                if not self.verified(d["repo"], d["number"], d["head_sha"]):
                    self.audit.append("unverified", repo=d["repo"], number=d["number"], head_sha=d["head_sha"])
                    return {"ok": False, "error": "no verified evidence for this head: card not shown from GitHub data"}
                if d["action"] == "close":
                    return self._close(d)
                return {"ok": self.train.enqueue(d)}
            except GitHubError as e:
                self.audit.append("error", repo=d["repo"], number=d["number"], status=e.status)
                return {"ok": False, "error": f"GitHub {e.status}"}
            except OSError as e:  # URLError, timeouts: answer the GUI instead of dropping the connection
                self.audit.append("error", repo=d["repo"], number=d["number"], status="network")
                return {"ok": False, "error": f"network error talking to GitHub: {e}"}
        finally:
            self.lock.release()

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
        if self.verifier is not None:  # network-bound; never under the decision lock
            self.verifier.step()
        if self.autobump is not None:  # checks are network-bound; it takes the lock only to enqueue
            self.autobump.step()
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
    verifier = Verifier(gh, tokens, git_root=cfg.git, verified_dir=cfg.verified, inbox=cfg.inbox)
    verified = lambda repo, n, head: is_verified(cfg.verified, repo, n, head)  # noqa: E731
    autobump = AutoBump(gh, audit, train, tokens.installed, verifier.targets, cfg.state / "autobump.json")
    serve(Executor(gh, audit, train, tokens.installed, verified, verifier, autobump), cfg.socket)


if __name__ == "__main__":
    main()
