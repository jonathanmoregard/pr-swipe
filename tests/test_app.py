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


def test_risky_card_approves_on_one_press_once_dwell_is_met(qtbot):
    card = make_card(ci={"state": "failure", "failing": ["t"]})
    w, store, client, t = make(qtbot, [card])
    qtbot.keyClick(w, Qt.Key_Right)          # too soon: dwell < 2 s
    w.flush_pending()
    assert client.sent == []
    t["now"] += 3
    w.reload()                               # a refresh does not restart the dwell clock
    qtbot.keyClick(w, Qt.Key_Right)
    w.flush_pending()
    assert client.sent and client.sent[0]["action"] == "approve"


def test_close_against_the_ai_takes_one_press(qtbot):
    card = make_card()
    assert card["verdict"]["recommendation"] != "close"
    w, store, client, _ = make(qtbot, [card])
    qtbot.keyClick(w, Qt.Key_Left)
    w.flush_pending()
    assert client.sent and client.sent[0]["action"] == "close"


def test_undo_cancels_pending_decision(qtbot):
    w, store, client, _ = make(qtbot, [make_card()])
    w.undo_ms = 60000
    qtbot.keyClick(w, Qt.Key_Right)
    qtbot.keyClick(w, Qt.Key_U)
    w.flush_pending()
    assert client.sent == [] and store.undone == [(1, "human")]


def test_r_requests_deep_review_and_f_toggles_detail(qtbot):
    w, store, client, _ = make(qtbot, [make_card(), make_card(number=2)])
    qtbot.keyClick(w, Qt.Key_R)
    assert store.deep == [1] and w.current()["number"] == 2
    qtbot.keyClick(w, Qt.Key_F)
    assert w.detail_open


def test_up_down_scroll_the_diff_and_a_reload_keeps_the_place(qtbot):
    w, store, _, _ = make(qtbot, [long_card()])
    w.show(); w.resize(1200, 700)
    sb = w.body.verticalScrollBar()
    for _ in range(10):
        qtbot.keyClick(w, Qt.Key_Down)
    pos = sb.value()
    assert pos > 0 and w.current()["number"] == 1 and not store.deep and not w.detail_open
    w.reload()                                  # the 30 s refresh re-renders the same card
    assert sb.value() == pos
    qtbot.keyClick(w, Qt.Key_Up)
    assert sb.value() < pos


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
    assert "QUEST" in w.start_kicker.text() and w.start_display.text() == "2 encounters across 1 repo"
    qtbot.keyClick(w, Qt.Key_Right)          # begins the quest, decides nothing
    w.flush_pending()
    assert client.sent == [] and w.quest_started
    assert w.progress_label.text() == "Encounter 1 of 2"


def test_encounter_header_and_sharp_eye_toast(qtbot):
    card = make_card(ci={"state": "failure", "failing": ["t"]})
    w, store, _, _ = make(qtbot, [card])
    assert "Dragon" in w.pill.text()
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
    assert w.care_stats["finds"].text() == "1" and "1 approved" in w.tally.text()
    assert "QUEST COMPLETE" in w.complete_kicker.text()


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
    c["context"]["purpose"] = "Merges survive a transient 409. The rest is detail."
    c.update(_verified=True, _view=FakeView(), _commits=[{"sha": "c" * 40, "subject": "feat: retry"}],
             _record={"merge_base": "b" * 40, "head_sha": "a" * 40, "body": "Retry merges on 409.\n\nDetails."})
    return c


def test_review_view_orders_files_and_collapses_low_signal(qtbot):
    w, *_ = make(qtbot, [review_card()])
    rows = w.file_rows()
    assert rows[:4] == ["🚩 rule-flagged", "🚩 .github/workflows/ci.yml  +1 -1", "⚠ AI-flagged",
                        "⚠ high src/train.py  +1 -1"]
    assert rows[-1].startswith("▸ 1 low-signal") and not any("vendor/lib.go" in r for r in rows)
    assert "🚩 rule: ci-workflow" in w.body.toPlainText()
    assert w.intent_text.text() == "Merges survive a transient 409." and "unverified" in w.intent_source.text()
    assert w.intent_pr.text() == "PR says: Retry merges on 409."
    assert w.gate_label.text() == "🔒 Approve locked" and w.keys["approve"][1].text() == "approve 🔒"


def test_j_moves_through_files_and_expands_low_signal(qtbot):
    w, *_ = make(qtbot, [review_card()])
    qtbot.keyClick(w, Qt.Key_J)
    assert "retry(force=True)" in w.body.toPlainText()
    for _ in range(3):
        qtbot.keyClick(w, Qt.Key_J)
    assert "+v2" in w.body.toPlainText()
    assert any("vendor/lib.go" in r for r in w.file_rows())


def test_dragon_approve_waits_for_every_flagged_hunk(qtbot):
    w, store, client, t = make(qtbot, [review_card()])
    t["now"] += 60                            # time alone does not open the gate
    qtbot.keyClick(w, Qt.Key_Right)
    w.flush_pending()
    assert client.sent == [] and "1 flagged hunk unvisited in train.py" in w.footer.text()
    qtbot.keyClick(w, Qt.Key_N)               # the ci.yml hunk is already on screen: one press reaches train.py
    assert "⚠ AI high · unverified — retries without re-checking head" in w.body.toPlainText()
    assert w.hunks_count.text() == "2/2" and w.gate_label.text() == "✓ Approve unlocked"
    qtbot.keyClick(w, Qt.Key_Right)
    w.flush_pending()
    assert client.sent and client.sent[0]["action"] == "approve"


def test_app_icon_renders_at_small_and_large_sizes(qtbot):
    from pr_swipe.gui.style import app_icon
    icon = app_icon()
    assert not icon.isNull()
    assert {s.width() for s in icon.availableSizes()} >= {16, 256}


def test_long_flag_callout_wraps_and_focus_follows(qtbot):
    from pr_swipe.gui.app import DiffView
    v = DiffView(); qtbot.addWidget(v); v.resize(400, 300)
    why = "word " * 80
    v.show_lines([("hunk", "@@ -1 +1 @@"), ("ai", f"⚠ AI high · unverified — {why}"), ("add", "+x = 1")], focus=2)
    kinds = [k for k, _ in v.lines]
    assert kinds.count("ai") > 1 and kinds[-1] == "add"
    assert v.textCursor().blockNumber() == len(v.lines) - 1


def long_card():
    """One file, two flags on its first hunk and one flag on a hunk far below."""
    far = "@@ -1,2 +1,2 @@\n-a\n+b\n" + "".join(f" line{i}\n" for i in range(300)) + "@@ -400 +400 @@\n-c\n+d"
    class View(FakeView):
        DIFFS = {"src/long.py": far, "src/other.py": "@@ -1 +1 @@\n-x\n+y"}
    spots = [{"source": "rule", "rule": "large-deletion", "file": "src/long.py", "hunk": "@@ -1,2 +1,2 @@", "lines": "+b"},
             {"source": "ai", "file": "src/long.py", "hunk": "@@ -1,2 +1,2 @@", "lines": "+b", "why": "w1", "severity": "high"},
             {"source": "ai", "file": "src/long.py", "hunk": "@@ -400 +400 @@", "lines": "+d", "why": "w2", "severity": "high"},
             {"source": "ai", "file": "src/other.py", "hunk": "@@ -1 +1 @@", "lines": "+y", "why": "w3", "severity": "low"}]
    c = review_card()
    c.update(hotspots=spots, _view=View())
    return c


def test_hunks_on_screen_count_as_visited_and_n_always_moves(qtbot):
    w, *_ = make(qtbot, [long_card()])
    w.resize(1440, 900); w.show(); qtbot.waitExposed(w)
    w._select_row(w.review.row)               # re-render at the real size
    assert w.hunks_count.text() == "2/4"      # both flags on the first hunk were on screen
    qtbot.keyClick(w, Qt.Key_N)               # skips what is already on screen: the far hunk
    assert w.body.textCursor().block().text().startswith("@@ -400")
    assert w.hunks_count.text() == "3/4"
    qtbot.keyClick(w, Qt.Key_N)
    assert w.diff_path.text() == "src/other.py" and w.hunks_count.text() == "4/4"
    before = (w.diff_path.text(), w.body.verticalScrollBar().value())
    qtbot.keyClick(w, Qt.Key_N)               # all visited: still goes somewhere else
    assert (w.diff_path.text(), w.body.verticalScrollBar().value()) != before


def secrets_card():
    age = b"age-encryption.org/v1\n-> ssh-ed25519 tuZffA abc\nxyz\n--- mac\n\x00\x01"
    class View(FakeView):
        DIFFS = {"secrets/a.age": "Binary files differ", "secrets/rekeyed/dellan/b.age": "Binary files differ",
                 "src/util.py": "@@ -1 +1 @@\n-x\n+y"}
        def files(self, base, head):
            return [dict(f, binary=f["path"].endswith(".age")) for f in super().files(base, head)]
        def blob(self, rev, path): return age if rev == "a" * 40 and path.endswith(".age") else None
    spots = [{"source": "rule", "rule": r, "file": p, "hunk": "", "lines": ""}
             for p in ("secrets/a.age", "secrets/rekeyed/dellan/b.age") for r in ("secrets", "binary")]
    c = review_card()
    c.update(hotspots=spots, _view=View())
    return c


def test_encrypted_secrets_review_as_one_who_can_decrypt_row(qtbot):
    w, *_ = make(qtbot, [secrets_card()])
    rows = w.file_rows()
    assert rows[:2] == ["🚩 rule-flagged", "🚩 🔐 2 encrypted secrets  +0 -0"]
    assert not any(".age" in r for r in rows[2:])
    text = w.body.toPlainText()
    assert text.count("🚩 rule:") == 1 and "secrets/rekeyed/dellan/b.age" in text and "tuZffA" in text
    assert "generated by agenix-rekey" in text
    assert w.hunks_count.text() == "4/4"     # every flag on the secrets row is on that one screen


def test_warnings_are_one_meta_token_and_manual_steps_stay_off_the_card(qtbot):
    from PySide6.QtWidgets import QLabel
    hidden = [{"kind": "zero-width", "where": "diff:a.py", "text": "x\u200by"}]
    card = make_card(hidden_content=hidden)
    card["context"]["manual"] = ["Create the OAuth client secret"]
    w, *_ = make(qtbot, [card])
    assert "⚠ 1 warning · w" in w.meta_label.text()
    shown = " ".join(l.text() for l in w.findChildren(QLabel) if l.isVisibleTo(w))
    assert "OAuth client secret" not in shown and "WARNINGS" not in shown
