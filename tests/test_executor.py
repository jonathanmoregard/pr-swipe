import json, os, socket, stat, threading, time
from pr_swipe.executor import Executor, serve
from pr_swipe.train import Train
from pr_swipe.audit import AuditLog, verify
from tests.fakes import FakeGitHub

R = "jonathanmoregard/x"


def setup(tmp_path, installed=lambda repo: True, verified=lambda repo, n, head: True):
    gh = FakeGitHub()
    (tmp_path / "returns").mkdir()
    audit = AuditLog(tmp_path / "audit.jsonl")
    train = Train(gh, audit, tmp_path / "train.json", tmp_path / "returns")
    return gh, Executor(gh, audit, train, installed, verified)


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


def test_busy_executor_refuses_instead_of_acting_after_the_client_gave_up(tmp_path, monkeypatch):
    import pr_swipe.executor as E
    monkeypatch.setattr(E, "LOCK_WAIT", 0.2, raising=False)
    gh, ex = setup(tmp_path); gh.add_pr(R, 1, "a" * 40)
    ex.lock.acquire()                      # a long train step holds the lock
    res = {}
    t = threading.Thread(target=lambda: res.update(r=ex.handle(d("approve", 1, "a" * 40))))
    t.start(); t.join(1.0)
    blocked = t.is_alive()
    ex.lock.release(); t.join(2.0)
    assert not blocked, "handler must answer before the GUI's socket timeout, not block on the lock"
    assert res["r"]["ok"] is False and "busy" in res["r"]["error"]
    assert ex.train.queued() == set()      # refused means nothing was enqueued


def test_client_waits_longer_than_the_executor_can_take():
    import pr_swipe.executor as E
    from pr_swipe.gui.store import CLIENT_TIMEOUT
    assert CLIENT_TIMEOUT > E.LOCK_WAIT + E.MAX_HANDLE_S


def test_decision_on_an_unverified_head_is_refused(tmp_path):
    gh, ex = setup(tmp_path, verified=lambda repo, n, head: head == "a" * 40)
    gh.add_pr(R, 1, "b" * 40); gh.add_pr(R, 2, "b" * 40)
    r1 = ex.handle(d("approve", 1, "b" * 40))
    r2 = ex.handle(d("close", 2, "b" * 40))
    assert not r1["ok"] and not r2["ok"] and "verified" in r1["error"]
    assert ex.train.queued() == set() and gh.calls == []


def test_network_failure_is_answered_not_dropped(tmp_path):
    import urllib.error
    def offline(repo): raise urllib.error.URLError("no route")
    gh, ex = setup(tmp_path, installed=offline)
    r = ex.handle(d("approve", 1, "a" * 40))
    assert r["ok"] is False and "network" in r["error"] and ex.train.queued() == set()


def test_tick_runs_autobump_and_its_enqueue_holds_the_decision_lock(tmp_path):
    gh, _ = setup(tmp_path)
    seen = []
    class Probe:
        lock = None
        def step(self): seen.append(self.lock is ex.lock and not ex.lock.locked())
    audit = AuditLog(tmp_path / "audit2.jsonl")
    ex = Executor(gh, audit, Train(gh, audit, tmp_path / "t2.json", tmp_path / "returns"),
                  lambda r: True, lambda r, n, h: True, autobump=Probe())
    ex.tick()
    assert seen == [True]
