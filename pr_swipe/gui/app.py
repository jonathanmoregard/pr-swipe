"""Swipe deck window. All card text is untrusted and rendered as plain text."""
import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (QApplication, QInputDialog, QLabel, QMainWindow, QPlainTextEdit, QVBoxLayout,
                               QWidget)

from .. import card as C
from ..config import load
from . import deck as D
from . import quest as Q
from .store import ExecutorClient, Store

DWELL_S = 2.0


class DiffHighlighter(QSyntaxHighlighter):
    def highlightBlock(self, text):
        fmt = QTextCharFormat()
        if text.startswith("+"):
            fmt.setForeground(QColor("#1a7f37"))
        elif text.startswith("-"):
            fmt.setForeground(QColor("#cf222e"))
        elif text.startswith("@@") or text.startswith("▶"):
            fmt.setForeground(QColor("#8250df"))
        else:
            return
        self.setFormat(0, len(text), fmt)


def _plain_label(wrap=True, bold=False):
    l = QLabel()
    l.setTextFormat(Qt.PlainText)
    l.setWordWrap(wrap)
    l.setTextInteractionFlags(Qt.TextSelectableByMouse)
    if bold:
        f = l.font(); f.setBold(True); f.setPointSize(f.pointSize() + 3); l.setFont(f)
    return l


class Window(QMainWindow):
    def __init__(self, store, client, load_cards, clock=time.time, undo_ms=5000):
        super().__init__()
        self.store, self.client, self.load_cards, self.clock, self.undo_ms = store, client, load_cards, clock, undo_ms
        self.deck, self.idx, self.armed, self.shown_at = [], 0, None, clock()
        self.detail_open, self.detail_seen, self.pending, self.one_off = False, False, None, False
        self.quest_started, self.quest_total, self.session_start, self.noted = False, 0, clock(), set()
        self.quest_complete = False
        self.setWindowTitle("pr-swipe")
        self.encounter_label = _plain_label()
        self.title_label = _plain_label(bold=True)
        self.meta_label = _plain_label()
        self.banner = _plain_label()
        self.banner.setStyleSheet("background:#cf222e;color:white;padding:6px;")
        self.context_label = _plain_label()
        self.body = QPlainTextEdit(readOnly=True)
        self.body.setFont(QFont("monospace"))
        self.body.setFocusPolicy(Qt.NoFocus)  # arrow keys belong to the deck, not the text pane
        self.setFocusPolicy(Qt.StrongFocus)
        self.highlighter = DiffHighlighter(self.body.document())  # keep a reference or Qt drops it
        self.footer = _plain_label()
        lay = QVBoxLayout()
        for w in (self.encounter_label, self.title_label, self.meta_label, self.banner, self.context_label,
                  self.body, self.footer):
            lay.addWidget(w)
        root = QWidget(); root.setLayout(lay); self.setCentralWidget(root)
        self.resize(1100, 850)
        self.reload()
        self.timer = QTimer(self); self.timer.timeout.connect(self.reload); self.timer.start(30000)

    # --- data ---
    def reload(self):
        cur = self.current()
        self.deck = D.build_deck(self.load_cards(), self.store.in_train(), self.store.decided(), self.store.returns())
        if self.pending:
            self.deck = [c for c in self.deck if D.card_key(c) != D.card_key(self.pending[0])]
        keys = [D.card_key(c) for c in self.deck]
        self.idx = keys.index(D.card_key(cur)) if cur and D.card_key(cur) in keys else 0
        if self.quest_started:
            self.quest_total = max(self.quest_total, len(self.deck) + self.done_count)
        self.render()

    def current(self):
        return self.deck[self.idx] if 0 <= self.idx < len(self.deck) else None

    # --- quest framing (rewards care, never throughput; see gui/quest.py) ---
    @property
    def done_count(self):
        return max(0, self.quest_total - len(self.deck)) if self.quest_started else 0

    def begin_quest(self):
        self.quest_started, self.quest_complete, self.quest_total = True, False, len(self.deck)
        self.session_start, self.noted = self.clock(), set()
        self.render()

    def _start_screen(self):
        s = Q.quest_summary(self.deck)
        parts = [f"{Q.ENCOUNTERS[k]['emoji']} {n} {Q.ENCOUNTERS[k]['name'].lower()}{'s' if n != 1 else ''}"
                 for k, n in s["counts"].items() if n]
        self.encounter_label.setText("🗺️  A new quest")
        self.encounter_label.setStyleSheet("background:#6e40c9;color:white;padding:6px;font-weight:bold;")
        self.title_label.setText("Today's quest awaits.")
        self.meta_label.setText("")
        self.banner.hide()
        self.context_label.setText(f"{s['total']} encounters across {s['repos']} repos:  " + "  ·  ".join(parts)
                                   + "\n\nRewards here are for care, not speed: catching what the AI missed, "
                                   "writing lore, overruling with reasons, laying ghosts to rest.")
        self.body.setPlainText("Press any key to begin.")
        self._footer()

    def _complete_screen(self):
        r = Q.recap(self.store.rows(), since_ts=self.session_start)
        plural = lambda n, w: f"{n} {w}{'' if n == 1 else 's'}"  # noqa: E731
        self.encounter_label.setText("🏆  Quest complete")
        self.encounter_label.setStyleSheet("background:#bf8700;color:white;padding:6px;font-weight:bold;")
        self.title_label.setText(Q.celebration())
        self.meta_label.setText("")
        self.banner.hide()
        self.context_label.setText(
            f"🔍 {plural(r['finds'], 'sharp-eye find')}   📜 {r['lore']} lore   "
            f"⚖️ {plural(r['overrules'], 'overrule')}   👻 {r['laid_to_rest']} laid to rest\n"
            f"({r['approved']} approved, {r['closed']} closed: counted, never scored)")
        self.body.setPlainText("New encounters will appear here when the collector finds them.")
        self._footer()

    def _encounter_header(self, c):
        e = Q.ENCOUNTERS[Q.encounter(c)]
        n = min(self.done_count + 1, max(self.quest_total, 1))
        self.encounter_label.setText(f"{e['emoji']}  {e['name']} · encounter {n}/{self.quest_total}  —  {Q.flavour(c)}")
        self.encounter_label.setStyleSheet(f"background:{e['colour']};color:white;padding:6px;font-weight:bold;")

    # --- rendering ---
    def render(self):
        c = self.current()
        self.armed, self.shown_at, self.detail_seen, self.one_off = None, self.clock(), False, False
        if c is not None and not self.quest_started:
            return self._start_screen()
        if c is None:
            if self.quest_started or self.quest_complete:
                self.quest_started, self.quest_complete = False, True
                return self._complete_screen()
            self.encounter_label.setText("")
            self.encounter_label.setStyleSheet("")
            self.title_label.setText("Inbox zero.")
            for w in (self.meta_label, self.context_label):
                w.setText("")
            self.banner.hide(); self.body.setPlainText("")
            self._footer(); return
        self._encounter_header(c)
        age = c["first_commit_at"][:10]
        ds = c["diffstat"]
        self.title_label.setText(f"{c['repo']}#{c['number']}  {c['title']}")
        self.meta_label.setText(
            f"by {c['author']} ({c['author_class']}) · started {age} · idle {c['signals']['days_since_update']}d · "
            f"CI {c['ci']['state']}{' ' + ','.join(c['ci']['failing']) if c['ci']['failing'] else ''} · "
            f"{c['mergeable']} · {ds['files']} files +{ds['additions']} -{ds['deletions']}"
            + (" · overlaps #" + ", #".join(map(str, c["signals"]["overlapping_open"])) if c["signals"]["overlapping_open"] else ""))
        warn = []
        if c.get("_returned"):
            warn.append(f"RETURNED: {c['_returned']}")
        for h in c["hidden_content"]:
            warn.append(f"HIDDEN {h['kind']} in {h['where']}: {h['text'][:200]}")
        self.banner.setText("\n".join(warn)); self.banner.setVisible(bool(warn))
        v = c["verdict"]
        ctx = c["context"]
        lines = [f"AI: {v['recommendation'].upper()} ({v['confidence']})"
                 + (" · STALE" if v["stale"] else "")
                 + (f" · superseded by {', '.join(v['superseded_by'])}" if v["superseded_by"] else "")
                 + f" — {v['reason']}",
                 f"Purpose (from PR text): {ctx['purpose']}", f"Solution (from PR text): {ctx['solution']}"]
        if ctx["notes"]:
            lines.append(f"Notes: {ctx['notes']}")
        if c.get("deep_review"):
            lines.append("Deep review: " + c["deep_review"]["summary"])
            lines += [f"  • {f}" for f in c["deep_review"]["findings"]]
        self.context_label.setText("\n".join(lines))
        self._render_body()
        self._footer()

    def _render_body(self):
        c = self.current()
        if self.detail_open:
            text = c["detail"]["diff"] + ("\n[diff truncated]" if c["detail"]["truncated"] else "")
            text += "".join(f"\n\n▶ comment by {m['author']}:\n{m['body']}" for m in c["detail"]["comments"])
        else:
            parts = []
            for h in c["hotspots"]:
                tag = f"rule:{h['rule']}" if h["source"] == "rule" else f"AI {h.get('severity')}: {h.get('why', '')}"
                parts.append(f"▶ {h['file']}  [{tag}]\n{h['hunk']}\n{h['lines']}")
            text = "\n\n".join(parts) or "No hotspots. ↓ for the full diff."
        self.body.setPlainText(text)

    def _footer(self, msg=""):
        s = D.stats(self.store.metrics(days=7))
        keys = ("← close  → approve  ↑ deep AI review  ↓ detail  o browser  s skip  u undo\n"
                "n note  m AI missed something  x one-off (don't learn)" + ("  [ONE-OFF]" if self.one_off else ""))
        ch = Q.chronicle(self.store.rows(), now=self.clock())
        gate = (f"This week: 🔍 {ch['finds']} finds · 📜 {ch['lore']} lore   |   gate: {s['n']} decisions · "
                f"median approve {s['median_approve_s']:.0f}s · close {s['close_rate']:.0%} · "
                f"detail {s['detail_rate']:.0%}")
        pend = f" · pending: {self.pending[1]} {self.pending[0]['repo']}#{self.pending[0]['number']}" if self.pending else ""
        self.footer.setText(f"{len(self.deck)} left · {keys}\n{gate}{pend}" + (f"\n{msg}" if msg else ""))

    # --- actions ---
    def keyPressEvent(self, ev):
        c, k = self.current(), ev.key()
        if c is not None and not self.quest_started:
            return self.begin_quest()
        if k == Qt.Key_U:
            return self.undo()
        if c is None:
            return
        if k == Qt.Key_Down:
            self.detail_open = not self.detail_open
            self.detail_seen = self.detail_seen or self.detail_open
            return self._render_body()
        if k == Qt.Key_Escape and self.detail_open:
            self.detail_open = False
            return self._render_body()
        if k == Qt.Key_O:
            return self.store.request_open(c["url"])
        if k == Qt.Key_X:
            self.one_off = not self.one_off
            return self._footer()
        if k == Qt.Key_N:
            return self.note(c, "note")
        if k == Qt.Key_M:
            return self.note(c, "missed")
        if k == Qt.Key_S:
            self.store.record(c, "skip", one_off=self.one_off)
            return self._advance(skip=True)
        if k == Qt.Key_Up:
            self.store.request_deep_review(c)
            self.store.record(c, "deep-review", one_off=self.one_off)
            return self._advance(skip=True)
        if k in (Qt.Key_Left, Qt.Key_Right):
            return self.decide(c, "close" if k == Qt.Key_Left else "approve")

    def decide(self, c, action):
        if not c["can_merge"]:
            self.store.request_open(c["url"])
            return self._advance(skip=True)
        dwell = self.clock() - self.shown_at
        if D.needs_confirm(c, action):
            if self.armed != action or (action == "approve" and dwell < DWELL_S):
                self.armed = action
                why = "🐉 hold your ground: hunks must be on screen 2 s, then press again" if action == "approve" \
                    else "AI did not suggest closing: press ← again to confirm"
                return self._footer(why)
        self.flush_pending()
        decision = {"action": action, "repo": c["repo"], "number": c["number"], "head_sha": c["head_sha"],
                    "card_sha256": C.digest({k: v for k, v in c.items() if not k.startswith("_")}),
                    "ts": self.clock()}
        self.store.mark_decided(c, action, dwell=dwell, detail=self.detail_seen, one_off=self.one_off)
        self.pending = (c, action, decision)
        if self.undo_ms > 0:
            QTimer.singleShot(self.undo_ms, self.flush_pending)
        rec = c["verdict"]["recommendation"]
        toast = ""
        if action == "close" and Q.encounter(c) == "ghost":
            toast = Q.TOASTS["laid_to_rest"]
        elif (action, rec) in (("approve", "close"), ("close", "approve")) and D.card_key(c) in self.noted:
            toast = Q.TOASTS["overrules"]
        self._advance(skip=False)
        if toast:
            self._footer(toast)

    # --- feedback capture (logged locally for the future learning loop; never sent anywhere) ---
    def ask_text(self, title, label):
        text, ok = QInputDialog.getMultiLineText(self, title, label)
        return text.strip() if ok and text.strip() else None

    def ask_item(self, title, label, items):
        item, ok = QInputDialog.getItem(self, title, label, items, 0, False)
        return items.index(item) if ok else None

    def note(self, c, kind):
        hotspot = None
        if kind == "note" and c["hotspots"] and not self.detail_open:
            items = ["whole PR"] + [f"{i}: {h['file']}  {h['hunk'][:80]}" for i, h in enumerate(c["hotspots"], 1)]
            pick = self.ask_item("Note on…", "Attach the note to:", items)
            if pick is None:
                return
            hotspot = c["hotspots"][pick - 1] if pick else None
        label = ("What did the AI miss? (file/line, what is wrong, why it matters)" if kind == "missed"
                 else "Your note: what is right or wrong here, and why")
        text = self.ask_text("Feedback", label)
        if text is None:
            return self._footer("feedback discarded")
        self.store.record(c, kind, note=text, hotspot=hotspot, one_off=self.one_off)
        if kind == "note":
            self.noted.add(D.card_key(c))
        self._footer(Q.TOASTS["finds" if kind == "missed" else "lore"])

    def flush_pending(self):
        if not self.pending:
            return
        c, action, decision = self.pending
        self.pending = None
        r = self.client.send(decision)
        if not r.get("ok"):
            self.store.undo(c, reason="refused")
            self.reload()
            keys = [D.card_key(x) for x in self.deck]
            if D.card_key(c) in keys:  # put the refused card back in front of the human
                self.idx = keys.index(D.card_key(c))
                self.render()
            self._footer(f"executor refused {action} on {c['repo']}#{c['number']}: {r.get('error')}")
        else:
            self._footer()

    def undo(self):
        if not self.pending:
            return self._footer("nothing to undo (already sent)")
        c = self.pending[0]
        self.pending = None
        self.store.undo(c)
        self.deck.insert(self.idx, c)
        self.render()

    def _advance(self, skip):
        c = self.current()
        if skip and c is not None:
            self.deck.append(self.deck.pop(self.idx))
        elif c is not None:
            self.deck.pop(self.idx)
        if self.idx >= len(self.deck):
            self.idx = 0
        self.detail_open = False
        self.render()


def main():
    cfg = load()
    app = QApplication(sys.argv)
    w = Window(Store(cfg), ExecutorClient(cfg.socket), load_cards=lambda: C.load_cards(cfg.inbox))
    w.show()
    app.aboutToQuit.connect(w.flush_pending)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
