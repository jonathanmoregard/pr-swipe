import json, os, socket, stat, threading, time
from pr_swipe.executor import Executor, serve
from pr_swipe.train import Train
from pr_swipe.audit import AuditLog, verify
from tests.fakes import FakeGitHub

R = "jonathanmoregard/x"


def setup(tmp_path, installed=lambda repo: True):
    gh = FakeGitHub()
    (tmp_path / "returns").mkdir()
    audit = AuditLog(tmp_path / "audit.jsonl")
    train = Train(gh, audit, tmp_path / "train.json", tmp_path / "returns")
    return gh, Executor(gh, audit, train, installed)


def d(action, n, sha):
    return {"action": action, "repo": R, "number": n, "head_sha": sha, "card_sha256": "f" * 64, "ts": 1.0}


def test_close_checks_head_then_closes_and_comments(tmp_path):
    gh, ex = setup(tmp_path); gh.add_pr(R, 1, "a" * 40)
    assert ex.handle(d("close", 1, "a" * 40))["ok"]
    assert [c[0] for c in gh.calls] == ["close", "comment"]
    assert verify(tmp_path / "audit.jsonl")


def test_close_aborts_if_head_moved(tmp_path):
    gh, ex = setup(tmp_path); gh.add_pr(R, 1, "b" * 40)
    r = ex.handle(d("close", 1, "a" * 40))
    assert not r["ok"] and gh.calls == []


def test_uninstalled_repo_is_refused(tmp_path):
    gh, ex = setup(tmp_path, installed=lambda repo: False); gh.add_pr(R, 1, "a" * 40)
    assert not ex.handle(d("approve", 1, "a" * 40))["ok"]


def test_malformed_decision_is_refused(tmp_path):
    gh, ex = setup(tmp_path)
    assert not ex.handle({"action": "merge"})["ok"]


def test_socket_is_owner_only_and_round_trips(tmp_path):
    gh, ex = setup(tmp_path); gh.add_pr(R, 1, "a" * 40)
    sock = tmp_path / "ex.sock"
    threading.Thread(target=serve, args=(ex, sock), kwargs={"interval": 0.05}, daemon=True).start()
    for _ in range(100):
        if sock.exists(): break
        time.sleep(0.02)
    assert stat.S_IMODE(os.stat(sock).st_mode) == 0o600
    s = socket.socket(socket.AF_UNIX); s.connect(str(sock))
    s.sendall((json.dumps(d("approve", 1, "a" * 40)) + "\n").encode())
    assert json.loads(s.makefile().readline())["ok"]
    s.sendall(b"not json\n")
    assert not json.loads(s.makefile().readline())["ok"]
