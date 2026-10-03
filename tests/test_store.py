import json, socket, threading
from pr_swipe.gui.store import Store, ExecutorClient
from pr_swipe.config import load
from tests.cards import make_card


def cfg(tmp_path):
    c = load({"PR_SWIPE_ROOT": str(tmp_path), "PR_SWIPE_SOCKET": str(tmp_path / "s.sock")})
    for d in (c.inbox, c.outbox, c.returns, c.state):
        d.mkdir(parents=True)
    return c


def test_decided_and_metrics_persist(tmp_path):
    c = cfg(tmp_path)
    s = Store(c, clock=lambda: 100.0)
    s.mark_decided(make_card(), "approve", dwell=3.0, detail=False)
    s2 = Store(c, clock=lambda: 200.0)
    assert s2.decided() == {"o/r#1@" + "a" * 40: 100.0}
    assert s2.metrics(days=7)[0]["action"] == "approve"


def test_undo_removes_decision(tmp_path):
    s = Store(cfg(tmp_path), clock=lambda: 1.0)
    s.mark_decided(make_card(), "close", dwell=1.0, detail=False)
    s.undo(make_card())
    assert s.decided() == {}


def test_requests_written_to_outbox(tmp_path):
    c = cfg(tmp_path)
    Store(c).request_open("https://github.com/o/r/pull/1")
    Store(c).request_deep_review(make_card())
    kinds = sorted(json.loads(p.read_text())["kind"] for p in c.outbox.glob("*.json"))
    assert kinds == ["deep-review", "open"]


def test_returns_are_read_and_old_ones_pruned(tmp_path):
    c = cfg(tmp_path)
    (c.returns / "a.json").write_text(json.dumps({"repo": "o/r", "number": 1, "head_sha": "a" * 40, "reason": "x", "ts": 1.0}))
    (c.returns / "b.json").write_text(json.dumps({"repo": "o/r", "number": 2, "head_sha": "a" * 40, "reason": "y", "ts": 10 * 86400.0}))
    rs = Store(c, clock=lambda: 10 * 86400.0 + 5).returns()
    assert [r["number"] for r in rs] == [2] and not (c.returns / "a.json").exists()


def test_executor_client_round_trip(tmp_path):
    path = tmp_path / "s.sock"
    srv = socket.socket(socket.AF_UNIX); srv.bind(str(path)); srv.listen(1)
    def serve():
        conn, _ = srv.accept(); f = conn.makefile("rw")
        req = json.loads(f.readline()); f.write(json.dumps({"ok": True, "echo": req["action"]}) + "\n"); f.flush()
    threading.Thread(target=serve, daemon=True).start()
    assert ExecutorClient(path).send({"action": "close"}) == {"ok": True, "echo": "close"}


def test_executor_client_reports_unreachable(tmp_path):
    r = ExecutorClient(tmp_path / "missing.sock").send({"action": "close"})
    assert r["ok"] is False and "executor" in r["error"]


def test_metrics_exclude_undone_decisions(tmp_path):
    t = {"now": 1.0}
    s = Store(cfg(tmp_path), clock=lambda: t["now"])
    a, b = make_card(number=1), make_card(number=2)
    s.mark_decided(a, "approve", dwell=2.0, detail=False)
    s.mark_decided(b, "close", dwell=2.0, detail=False)
    t["now"] = 2.0
    s.undo(a)  # undone by the human, or refused by the executor
    t["now"] = 3.0
    s.mark_decided(a, "close", dwell=4.0, detail=True)
    assert [(r["key"].split("@")[0], r["action"]) for r in s.metrics()] == [("o/r#2", "close"), ("o/r#1", "close")]


def test_feedback_rows_carry_ai_verdict_override_and_are_private(tmp_path):
    import stat
    from pr_swipe.gui.store import NOTE_MAX
    c = cfg(tmp_path)
    s = Store(c, clock=lambda: 5.0)
    card = make_card()
    card["verdict"]["recommendation"] = "close"
    s.mark_decided(card, "approve", dwell=1.0, detail=True, one_off=True)
    s.record(card, "note", note="x" * (NOTE_MAX + 10),
             hotspot={"file": "a.py", "hunk": "@@", "lines": "+secret", "source": "ai"})
    rows = [json.loads(l) for l in (c.state / "feedback.jsonl").read_text().splitlines()]
    assert rows[0]["override"] is True and rows[0]["one_off"] is True and rows[0]["ai"]["recommendation"] == "close"
    assert rows[1]["override"] is False and len(rows[1]["note"]) == NOTE_MAX
    assert rows[1]["hotspot"] == {"file": "a.py", "hunk": "@@", "source": "ai"}
    assert stat.S_IMODE((c.state / "feedback.jsonl").stat().st_mode) == 0o600
