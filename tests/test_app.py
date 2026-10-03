from PySide6.QtCore import Qt
from pr_swipe.gui.app import Window
from tests.cards import make_card


class FakeStore:
    def __init__(self, cards):
        self.cards, self.marked, self.undone, self.opened, self.deep = cards, [], [], [], []
        self.recorded, self.one_offs = [], []
    def decided(self): return {}
    def returns(self): return []
    def in_train(self): return set()
    def metrics(self, days=7): return []
    def mark_decided(self, c, a, dwell, detail, one_off=False):
        self.marked.append((c["number"], a)); self.one_offs.append(one_off)
    def undo(self, c, reason="human"): self.undone.append((c["number"], reason))
    def record(self, c, action, note=None, hotspot=None, one_off=False):
        self.recorded.append((c["number"], action, note, hotspot and hotspot["file"], one_off))
    def request_open(self, url): self.opened.append(url)
    def request_deep_review(self, c): self.deep.append(c["number"])


class FakeClient:
    def __init__(self): self.sent = []
    def send(self, d): self.sent.append(d); return {"ok": True}


def make(qtbot, cards, clock=None):
    store, client = FakeStore(cards), FakeClient()
    t = {"now": 100.0}
    w = Window(store, client, load_cards=lambda: store.cards, clock=clock or (lambda: t["now"]), undo_ms=0)
    qtbot.addWidget(w)
    return w, store, client, t


def test_right_approves_calm_card_and_sends_pinned_decision(qtbot):
    w, store, client, _ = make(qtbot, [make_card()])
    qtbot.keyClick(w, Qt.Key_Right)
    w.flush_pending()
    assert client.sent[0]["action"] == "approve" and client.sent[0]["head_sha"] == "a" * 40
    assert store.marked == [(1, "approve")]


def test_risky_card_needs_second_press_after_dwell(qtbot):
    card = make_card(ci={"state": "failure", "failing": ["t"]})
    w, store, client, t = make(qtbot, [card])
    qtbot.keyClick(w, Qt.Key_Right)
    qtbot.keyClick(w, Qt.Key_Right)          # too soon: dwell < 2 s
    w.flush_pending()
    assert client.sent == []
    t["now"] += 3
    qtbot.keyClick(w, Qt.Key_Right)
    w.flush_pending()
    assert client.sent and client.sent[0]["action"] == "approve"


def test_undo_cancels_pending_decision(qtbot):
    w, store, client, _ = make(qtbot, [make_card()])
    w.undo_ms = 60000
    qtbot.keyClick(w, Qt.Key_Right)
    qtbot.keyClick(w, Qt.Key_U)
    w.flush_pending()
    assert client.sent == [] and store.undone == [(1, "human")]


def test_up_requests_deep_review_and_down_toggles_detail(qtbot):
    w, store, client, _ = make(qtbot, [make_card(), make_card(number=2)])
    qtbot.keyClick(w, Qt.Key_Up)
    assert store.deep == [1] and w.current()["number"] == 2
    qtbot.keyClick(w, Qt.Key_Down)
    assert w.detail_open


def test_external_repo_card_opens_browser_instead_of_deciding(qtbot):
    w, store, client, _ = make(qtbot, [make_card(can_merge=False)])
    qtbot.keyClick(w, Qt.Key_Right)
    w.flush_pending()
    assert client.sent == [] and store.opened == ["https://github.com/o/r/pull/1"]


def test_card_text_is_rendered_as_plain_text(qtbot):
    w, *_ = make(qtbot, [make_card(title="<b>bold</b><img src=x>")])
    assert w.title_label.textFormat() == Qt.PlainText


class RefusingClient(FakeClient):
    def send(self, d): self.sent.append(d); return {"ok": False, "error": "nope"}


def test_executor_refusal_is_logged_as_refused(qtbot):
    w, store, _, _ = make(qtbot, [make_card()])
    w.client = RefusingClient()
    qtbot.keyClick(w, Qt.Key_Right)
    w.flush_pending()
    assert store.undone == [(1, "refused")]


def hot(file="f.py"):
    return {"source": "ai", "file": file, "hunk": "@@ -1 +1 @@", "lines": "+x", "why": "w", "severity": "high"}


def test_note_attaches_to_picked_hotspot(qtbot):
    w, store, _, _ = make(qtbot, [make_card(hotspots=[hot("a.py"), hot("b.py")])])
    w.ask_item = lambda *a: 2
    w.ask_text = lambda *a: "b.py swallows the error"
    qtbot.keyClick(w, Qt.Key_N)
    assert store.recorded == [(1, "note", "b.py swallows the error", "b.py", False)]


def test_missed_and_cancelled_feedback(qtbot):
    w, store, _, _ = make(qtbot, [make_card(hotspots=[hot()])])
    w.ask_text = lambda *a: None
    qtbot.keyClick(w, Qt.Key_M)
    assert store.recorded == []
    w.ask_text = lambda *a: "race in train.py:40"
    qtbot.keyClick(w, Qt.Key_M)
    assert store.recorded == [(1, "missed", "race in train.py:40", None, False)]


def test_one_off_flag_rides_on_decision_and_resets(qtbot):
    w, store, _, _ = make(qtbot, [make_card(), make_card(number=2)])
    qtbot.keyClick(w, Qt.Key_X)
    qtbot.keyClick(w, Qt.Key_Right)
    qtbot.keyClick(w, Qt.Key_S)
    assert store.one_offs == [True] and store.recorded == [(2, "skip", None, None, False)]
