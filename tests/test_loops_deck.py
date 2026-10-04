from PySide6.QtCore import Qt

from tests.test_app import make
from tests.cards import make_card


def loop(n, title, **over):
    x = {"number": n, "title": title, "kind": "followup", "owner": "human", "source": "o/r", "state": "open",
         "reason": "", "due": "", "until": "", "key": "k" * 16, "url": f"https://github.com/me/.claude/issues/{n}",
         "created_at": "2026-10-01T00:00:00Z", "body": "From o/r#1"}
    x.update(over)
    return x


def with_loops(qtbot, items):
    w, store, client, t = make(qtbot, [make_card()])
    store.snapshot = {"today": "2026-10-04", "repo": "me/.claude", "loops": items}
    store.loops = lambda: store.snapshot
    store.loop_requests = []
    store.request_loop = lambda action, n: store.loop_requests.append((action, n))
    return w, store


def test_l_opens_the_deck_and_keys_send_requests_and_advance(qtbot):
    w, store = with_loops(qtbot, [loop(1, "Create the App key", due="2026-10-02"), loop(2, "Run e2e"),
                                  loop(3, "Rotate"), loop(4, "Decide")])
    qtbot.keyClick(w, Qt.Key_L)
    assert w.screens.currentWidget() is w.loops_screen
    assert w.loops_title.text() == "Create the App key" and "OVERDUE 2026-10-02" in w.loops_meta.text()
    qtbot.keyClick(w, Qt.Key_Right)
    assert w.loops_title.text() == "Run e2e"
    qtbot.keyClick(w, Qt.Key_S, Qt.ShiftModifier)
    qtbot.keyClick(w, Qt.Key_R)
    qtbot.keyClick(w, Qt.Key_Left)
    assert store.loop_requests == [("close", 1), ("snooze-week", 2), ("agent", 3), ("drop", 4)]
    assert "Every ball is handled" in w.loops_title.text()
    w.reload()                                   # the 30 s reload keeps the deck up
    assert w.screens.currentWidget() is w.loops_screen
    qtbot.keyClick(w, Qt.Key_L)
    assert w.screens.currentWidget() is not w.loops_screen and store.marked == []


def test_empty_snapshot_says_so(qtbot):
    w, store = with_loops(qtbot, [])
    store.snapshot = {}
    qtbot.keyClick(w, Qt.Key_L)
    assert "has not run" in w.loops_meta.text()
