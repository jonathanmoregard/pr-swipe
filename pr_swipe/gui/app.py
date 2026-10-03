"""Swipe deck window. All card text is untrusted: it goes into Qt.PlainText labels, the plain-text diff
pane, or through html.escape() into rich text that is otherwise built only from our own strings.

Layout follows the Claude Design handoff (2026-10-03): encounter header, PR block, warnings, intent and
AI panels, files + diff review area, coverage gate, key hints, and a status strip.
"""
import html
import sys
import textwrap
import time

from PySide6.QtCore import QPoint, QSize, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QSyntaxHighlighter, QTextCharFormat, QTextCursor,
                           QTextFormat)
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                               QListWidget, QListWidgetItem, QMainWindow, QPlainTextEdit, QProgressBar,
                               QStackedWidget, QSizePolicy, QTextEdit, QVBoxLayout, QWidget)

from .. import card as C
from ..config import load
from . import deck as D
from . import evidence as EV
from . import quest as Q
from . import review as RV
from . import style as S
from . import summary as SU
from . import syntax as SX
from .store import ExecutorClient, Store

DWELL_S = 2.0
FILE_DIFF_MAX = 200_000
ROW_TEXT = Qt.UserRole + 1  # plain description of a file-list row (the visible row is an item widget)
STATUS_WORD = {"A": "added", "M": "modified", "D": "deleted", "T": "type changed"}
TILE_TEXT = {"dragon": "risky; approve unlocks after every flag is seen",
             "sprout": "calm changes; still worth a real look",
             "ghost": "stale or superseded; the AI suggests closing",
             "traveler": "someone else's repo; opens in the browser"}
CARE = [("finds", "🔍 finds the AI missed"), ("lore", "📜 lore written"),
        ("overrules", "⚖️ overrules, with reason"), ("laid_to_rest", "👻 ghosts laid to rest")]


class ReviewState:
    """Per-card review view: ordered files from the verified mirror, coverage, selection."""

    def __init__(self, c, clock):
        rec, view = c["_record"], c["_view"]
        self.base, self.head, self.view = rec["merge_base"], rec["head_sha"], view
        ordered = RV.order_files(view.files(self.base, self.head), c["hotspots"])
        secret = [f for f in ordered if RV.is_secret(f["path"])]
        self.raw_spots = c["hotspots"]
        hotspots = c["hotspots"]
        if secret:  # encrypted files review as one row; their flags move onto it
            grp = RV.secrets_row(secret)
            ordered = sorted([f for f in ordered if not RV.is_secret(f["path"])] + [grp], key=RV.file_sort_key)
            hotspots = [dict(h, file=grp["path"], hunk="") if RV.is_secret(h["file"]) else h for h in hotspots]
        self.ordered = ordered
        pos = {f["path"]: i for i, f in enumerate(self.ordered)}
        spots = sorted((h for h in hotspots if h["file"] in pos),
                       key=lambda h: (pos[h["file"]], h["source"] != "rule"))
        self.cov = RV.Coverage(list(pos), spots, clock=clock)
        self.spots, self.low_open, self.row, self.flag_i, self.diffs = spots, False, 0, -1, {}

    def rows(self):
        normal = [f for f in self.ordered if not f["low_signal"]]
        low = [f for f in self.ordered if f["low_signal"]]
        return normal + low if self.low_open or not low else normal + [{"group": len(low)}]

    def file_lines(self, f):
        if "secrets" in f:
            return RV.secret_lines([(p, self.view.blob(self.base, p), self.view.blob(self.head, p)) for p in f["secrets"]],
                                   self.raw_spots)
        if f["path"] not in self.diffs:
            d = self.view.file_diff(self.base, self.head, f["path"])
            self.diffs[f["path"]] = d[:FILE_DIFF_MAX] + ("\n[file diff truncated]" if len(d) > FILE_DIFF_MAX else "")
        d = self.diffs[f["path"]]
        # git calls age files text (ASCII header), and undecodable bytes arrive as U+FFFD: neither is readable as a diff
        if f["binary"] or f["path"].endswith(".age") or "age-encryption.org/v1" in d[:2000] or "�" in d:
            return RV.binary_lines(f["path"], self.view.blob(self.base, f["path"]), self.view.blob(self.head, f["path"]),
                                   self.spots)
        return RV.render_diff(d, self.spots, path=f["path"])

    def file_spots(self, path):
        return [(i, h) for i, h in enumerate(self.spots) if h["file"] == path]


# --- diff pane ---------------------------------------------------------------------------------------------
_KIND_FMT = {  # kind -> (fg, bg, bold, line-number colour)
    "file": (S.C["text"], S.C["chrome"], True, None),
    "hunk": (S.TONES["ghost"][0], None, False, None),
    "rule": (S.TONES["dragon"][0], S.TONES["dragon"][1], True, None),
    "ai": (S.TONES["ai"][0], S.TONES["ai"][1], True, None),
    "add": (S.C["add_fg"], S.C["add_bg"], False, "#8FB89A"),
    "del": (S.C["del_fg"], S.C["del_bg"], False, "#D4A39C"),
    "ctx": (S.C["text2"], None, False, S.C["text4"]),
    "meta": (S.C["text3"], None, False, None),
}


def _u16(text):
    return len(text.encode("utf-16-le")) // 2  # Qt positions count UTF-16 units; emoji are 2


SYNTAX = {  # category -> (colour, bold, italic)
    "keyword": (S.TONES["ghost"][0], True, False), "builtin": (S.TONES["ghost"][0], False, False),
    "string": (S.TONES["traveler"][0], False, False), "comment": (S.C["text3"], False, True),
    "number": (S.TONES["ai"][0], False, False), "function": (S.C["text"], True, False),
    "attr": ("#8A3B6F", False, False),
}


class DiffHighlighter(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)
        self.kinds, self.spans = [], {}

    def highlightBlock(self, text):  # noqa: N802 (Qt override)
        n = self.currentBlock().blockNumber()
        if n >= len(self.kinds):
            return
        kind = self.kinds[n]
        fg, _, bold, lineno = _KIND_FMT[kind]
        code = n in self.spans or (self.spans and kind in ("add", "del", "ctx"))
        base = QTextCharFormat()
        base.setForeground(QColor(S.C["text"] if code and kind != "ctx" else fg))
        if bold:
            base.setFontWeight(QFont.DemiBold)
        self.setFormat(0, _u16(text), base)
        if lineno:
            ln = QTextCharFormat(); ln.setForeground(QColor(lineno))
            self.setFormat(0, 5, ln)
            sign = QTextCharFormat(); sign.setForeground(QColor(fg))
            self.setFormat(5, 1, sign)
        for col, length, cat in self.spans.get(n, ()):
            colour, b, it = SYNTAX[cat]
            f = QTextCharFormat(); f.setForeground(QColor(colour))
            if b:
                f.setFontWeight(QFont.DemiBold)
            f.setFontItalic(it)
            start = _u16(text[:col])
            self.setFormat(start, _u16(text[col:col + length]), f)


class DiffView(QPlainTextEdit):
    def __init__(self):
        super().__init__(readOnly=True)
        self.setObjectName("diffView")
        self.setFont(S.mono_font())
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setFocusPolicy(Qt.NoFocus)  # arrow keys belong to the deck, not the text pane
        self.highlighter = DiffHighlighter(self.document())  # keep a reference or Qt drops it
        self.lines, self.raw, self.raw_cols = [], None, 0

    def _cols(self):
        bold = QFont(self.font()); bold.setWeight(QFont.DemiBold)  # callouts render bold, which runs wider
        per_char = max(1, QFontMetrics(bold).horizontalAdvance("M" * 100)) / 100  # one "M" rounds to whole pixels
        return max(40, int((self.viewport().width() - 2 * self.document().documentMargin()) / per_char) - 2)

    def resizeEvent(self, e):  # noqa: N802 (Qt override): re-wrap callouts to the new width
        super().resizeEvent(e)
        if self.raw and self._cols() != self.raw_cols:
            pos = self.verticalScrollBar().value()
            self.show_lines(*self.raw)
            self.verticalScrollBar().setValue(pos)

    def show_lines(self, lines, focus=None, path=None):
        """lines: [(kind, text)]. focus: index of a line to centre on. path: file name for syntax colours
        (whole-diff mode takes it from the `file` lines). Flag callouts wrap to the pane width; code stays unwrapped."""
        self.raw = (lines, focus, path)
        cols = self._cols()
        wrapped = []
        for i, (kind, text) in enumerate(lines):
            if i == focus:
                focus = len(wrapped)
            if kind in ("ai", "rule") and len(text) > cols:
                wrapped += [(kind, t) for t in textwrap.wrap(text, cols, subsequent_indent="   ")]
            else:
                wrapped.append((kind, text))
        lines, self.lines, self.raw_cols = wrapped, wrapped, cols
        self.highlighter.kinds = [k for k, _ in lines]
        self.highlighter.spans = SX.spans(lines, path)
        self.setPlainText("\n".join(t for _, t in lines))
        sels, block = [], self.document().firstBlock()
        for kind, _ in lines:
            bg = _KIND_FMT[kind][1]
            if bg:
                s = QTextEdit.ExtraSelection()
                s.format.setBackground(QColor(bg)); s.format.setProperty(QTextFormat.FullWidthSelection, True)
                s.cursor = QTextCursor(block)
                sels.append(s)
            block = block.next()
        self.setExtraSelections(sels)
        self.setTextCursor(QTextCursor(self.document().findBlockByNumber(focus or 0)))
        if focus:
            self.centerCursor()
        else:
            self.verticalScrollBar().setValue(0)


# --- small widget helpers ----------------------------------------------------------------------------------
def label(text="", role=None, name=None, tone=None, rich=False, wrap=False, bold=False):
    w = QLabel(text)
    w.setTextFormat(Qt.RichText if rich else Qt.PlainText)
    w.setWordWrap(wrap)
    if role:
        w.setProperty("role", role)
    if name:
        w.setObjectName(name)
    if tone:
        w.setProperty("tone", tone)
    if bold:
        w.setProperty("weight", "bold")
    return w


def frame(name, kind=QVBoxLayout, margins=(12, 10, 12, 10), spacing=5):
    f = QFrame(); f.setObjectName(name)
    lay = kind(f); lay.setContentsMargins(*margins); lay.setSpacing(spacing)
    return f, lay


def row(*items, spacing=8, margins=(0, 0, 0, 0)):
    """HBox widget; the string "stretch" inserts a stretch."""
    w = QWidget(); lay = QHBoxLayout(w); lay.setContentsMargins(*margins); lay.setSpacing(spacing)
    for it in items:
        if isinstance(it, str):
            lay.addStretch(1)
        else:
            lay.addWidget(it)
    return w


def set_props(w, **props):
    changed = False
    for k, v in props.items():
        if w.property(k) != v:
            w.setProperty(k, v); changed = True
    if changed:
        S.repolish(w)


def clear(layout):
    while layout.count():
        it = layout.takeAt(0)
        if it.widget():
            it.widget().hide()  # deleteLater alone leaves it painted until the event loop runs
            it.widget().deleteLater()


def esc(s):
    return html.escape(str(s))


def span(text, colour, mono=False):
    fam = f"font-family:'{S.mono_family()}';" if mono else ""
    return f'<span style="color:{colour};{fam}">{text}</span>'


class Window(QMainWindow):
    def __init__(self, store, client, load_cards, clock=time.time, undo_ms=5000):
        super().__init__()
        self.store, self.client, self.load_cards, self.clock, self.undo_ms = store, client, load_cards, clock, undo_ms
        self.deck, self.idx, self.shown_at = [], 0, clock()
        self.detail_open, self.detail_seen, self.pending, self.one_off = False, False, None, False
        self.quest_started, self.quest_total, self.session_start, self.noted = False, 0, clock(), set()
        self.quest_complete, self.celebration = False, Q.celebration()
        self.reviews, self.review, self.flavours, self.list_rows = {}, None, {}, []
        self.rendered_key = None
        self.setWindowTitle("pr-swipe")
        self.setStyleSheet(S.stylesheet())
        self.setFont(S.ui_font())
        self.setFocusPolicy(Qt.StrongFocus)
        self.screens = QStackedWidget()
        self.encounter_screen = self._build_encounter()
        self.start_screen = self._build_start()
        self.complete_screen = self._build_complete()
        for s in (self.encounter_screen, self.start_screen, self.complete_screen):
            self.screens.addWidget(s)
        root = QWidget(); lay = QVBoxLayout(root); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        lay.addWidget(self.screens, 1); lay.addWidget(self._build_status())
        self.setCentralWidget(root)
        self.setMinimumSize(960, 700)
        self.resize(1440, 900)
        self.reload()
        self.timer = QTimer(self); self.timer.timeout.connect(self.reload); self.timer.start(30000)
        self.cov_timer = QTimer(self); self.cov_timer.timeout.connect(self._coverage); self.cov_timer.start(500)

    # --- construction ---
    def _build_encounter(self):
        screen = QWidget(); lay = QVBoxLayout(screen); lay.setContentsMargins(20, 16, 20, 12); lay.setSpacing(12)
        self.pill = label(name="encounterPill")
        self.progress_label = label(role="meta")
        self.dots = QWidget(); self.dots_lay = QHBoxLayout(self.dots)
        self.dots_lay.setContentsMargins(0, 0, 0, 0); self.dots_lay.setSpacing(3)
        self.flavour = label(role="flavour")
        self.verified_chip = label(name="verifiedChip")
        lay.addWidget(row(self.pill, self.progress_label, self.dots, self.flavour, "stretch", self.verified_chip,
                          spacing=12))

        self.repo_label = label(role="repo")
        self.title_label = label(role="title", wrap=True)
        self.meta_label = label(role="meta", rich=True, wrap=True)
        pr = QWidget(); pl = QVBoxLayout(pr); pl.setContentsMargins(0, 0, 0, 0); pl.setSpacing(4)
        pl.addWidget(self.repo_label); pl.addWidget(self.title_label); pl.addWidget(self.meta_label)
        lay.addWidget(pr)

        # intent first: verified headline, then the solution path, AI verdict alongside
        self.intent_panel, ig = frame("intentPanel", QGridLayout, (16, 14, 16, 14), 16)
        left = QWidget(); ll = QVBoxLayout(left); ll.setContentsMargins(0, 0, 0, 0); ll.setSpacing(12)
        self.intent_source = label(name="sourceNote", tone="sprout")
        self.intent_text = label(name="intentHeadline", wrap=True)
        head = QWidget(); hl = QVBoxLayout(head); hl.setContentsMargins(0, 0, 0, 0); hl.setSpacing(4)
        self.intent_pr = label(role="meta", wrap=True)
        hl.addWidget(row(label("INTENT", role="label"), self.intent_source, "stretch")); hl.addWidget(self.intent_text)
        hl.addWidget(self.intent_pr)
        ll.addWidget(head)
        self.path_source = label(name="sourceNote")
        self.path_box = QWidget(); self.path_lay = QHBoxLayout(self.path_box)
        self.path_lay.setContentsMargins(0, 0, 0, 0); self.path_lay.setSpacing(6)
        how = QWidget(); hw = QVBoxLayout(how); hw.setContentsMargins(0, 0, 0, 0); hw.setSpacing(6)
        hw.addWidget(row(label("HOW · SOLUTION PATH", role="label"), self.path_source, "stretch"))
        hw.addWidget(self.path_box)
        ll.addWidget(how)
        ig.addWidget(left, 0, 0); ig.setColumnStretch(0, 1)
        self.ai_panel, al = frame("aiPanel", spacing=6)
        self.ai_panel.setFixedWidth(400)
        self.ai_tag = label("UNVERIFIED", name="unverifiedTag")
        al.addWidget(row(label("AI PRE-REVIEW", role="label"), self.ai_tag, "stretch"))
        self.verdict_chip = label(name="verdictChip")
        self.verdict_chip.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.verdict_reason = label(role="meta", wrap=True)
        self.verdict_row = row(self.verdict_chip, self.verdict_reason)
        self.verdict_row.layout().setAlignment(self.verdict_chip, Qt.AlignTop)
        al.addWidget(self.verdict_row)
        self.ai_summary = label(role="meta", wrap=True)
        al.addWidget(self.ai_summary); al.addStretch(1)
        ig.addWidget(self.ai_panel, 0, 1, Qt.AlignTop)
        lay.addWidget(self.intent_panel)


        files_panel, fl = frame("filesPanel", margins=(0, 6, 0, 6), spacing=0)
        files_panel.setFixedWidth(300)
        fl.addWidget(row(label("FILES · BY RISK", role="label"), "stretch", label("j/k", role="caption"),
                         margins=(12, 2, 12, 6)))
        self.files = QListWidget(); self.files.setObjectName("fileList")
        self.files.setFocusPolicy(Qt.NoFocus)
        self.files.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.files.currentRowChanged.connect(self._clicked_row)
        fl.addWidget(self.files)
        self.left_col = files_panel

        diff_panel, dl = frame("diffPanel", margins=(0, 0, 0, 0), spacing=0)
        dh, dhl = frame("diffHeader", QHBoxLayout, (12, 7, 12, 7), 10)
        self.diff_path = label(role="mono", bold=True)
        self.diff_info = label(role="caption")
        self.hunk_state = label(role="meta")
        dhl.addWidget(self.diff_path); dhl.addWidget(self.diff_info); dhl.addStretch(1); dhl.addWidget(self.hunk_state)
        dl.addWidget(dh)
        self.body = DiffView()
        self.body.verticalScrollBar().valueChanged.connect(lambda _: self._mark_on_screen())
        dl.addWidget(self.body, 1)

        review = QWidget(); rl = QHBoxLayout(review); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(12)
        rl.addWidget(self.left_col); rl.addWidget(diff_panel, 1)
        lay.addWidget(review, 1)

        self.coverage_panel, cvl = frame("coveragePanel", QHBoxLayout, (12, 8, 12, 8), 16)
        self.gate_label = label(name="gateLabel")
        self.files_bar, self.hunks_bar = QProgressBar(), QProgressBar()
        self.files_bar.setObjectName("filesBar"); self.hunks_bar.setObjectName("hunksBar")
        for b in (self.files_bar, self.hunks_bar):
            b.setTextVisible(False)
        self.files_count, self.hunks_count = label(role="mono"), label(role="mono")
        self.hunks_group = row(label("flagged hunks", role="meta"), self.hunks_bar, self.hunks_count)
        self.gate_hint = label(role="meta")
        cvl.addWidget(self.gate_label)
        cvl.addWidget(row(label("files seen", role="meta"), self.files_bar, self.files_count))
        cvl.addWidget(self.hunks_group); cvl.addStretch(1); cvl.addWidget(self.gate_hint)
        lay.addWidget(self.coverage_panel)

        self.keys, main_keys = {}, []
        for key, name in (("←", "close"), ("→", "approve"), ("↑↓", "scroll"), ("f", "whole diff"), ("r", "deep review")):
            cap, lab = label(key, name="keycap"), label(name, name="keyLabel")
            cap.setAlignment(Qt.AlignCenter); cap.setFixedSize(28, 24)
            self.keys[name] = (cap, lab)
            main_keys.append(row(cap, lab, spacing=7))
        divider = QFrame(); divider.setObjectName("vDivider"); divider.setFixedHeight(20)
        self.secondary_keys = label(name="secondaryKeys", rich=True, wrap=True)
        self.secondary_keys.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        lay.addWidget(row(*main_keys, divider, self.secondary_keys, spacing=18))
        return screen

    def _centered_column(self):
        screen = QWidget(); outer = QVBoxLayout(screen); outer.setContentsMargins(20, 16, 20, 16)
        col = QWidget(); col.setFixedWidth(680)
        cl = QVBoxLayout(col); cl.setContentsMargins(0, 0, 0, 0); cl.setSpacing(24)
        mid = QHBoxLayout(); mid.addStretch(1); mid.addWidget(col); mid.addStretch(1)
        outer.addStretch(1); outer.addLayout(mid); outer.addStretch(1)
        return screen, cl

    def _heading(self, cl):
        kicker, display = label(role="label"), label(role="display", wrap=True)
        box = QWidget(); bl = QVBoxLayout(box); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(6)
        bl.addWidget(kicker); bl.addWidget(display)
        cl.addWidget(box)
        return kicker, display

    def _build_start(self):
        screen, cl = self._centered_column()
        self.start_kicker, self.start_display = self._heading(cl)
        tiles = QWidget(); tl = QGridLayout(tiles); tl.setContentsMargins(0, 0, 0, 0); tl.setSpacing(12)
        self.start_tiles = {}
        for i, kind in enumerate(Q.ORDER):
            tile = QFrame(); tile.setObjectName("encounterTile"); tile.setProperty("tone", kind)
            t = QVBoxLayout(tile); t.setContentsMargins(12, 12, 12, 12); t.setSpacing(4)
            stat, name = label("0", role="stat", tone=kind), label(role="body", bold=True)
            t.addWidget(stat); t.addWidget(name); t.addWidget(label(TILE_TEXT[kind], role="meta", wrap=True))
            t.addStretch(1)
            tile.setMinimumHeight(124)
            tl.addWidget(tile, 0, i); tl.setColumnStretch(i, 1)
            self.start_tiles[kind] = (stat, name)
        cl.addWidget(tiles)
        rewards, rl = frame("rewardsPanel", margins=(16, 14, 16, 14), spacing=8)
        rl.addWidget(label("Rewards are for care, not speed.", role="body", bold=True))
        grid = QGridLayout(); grid.setHorizontalSpacing(24); grid.setVerticalSpacing(6)
        for i, text in enumerate(["🔍 catching what the AI missed", "📜 writing lore",
                                  "⚖️ overruling, with a reason", "👻 laying ghosts to rest"]):
            grid.addWidget(label(text, role="meta"), i // 2, i % 2)
        rl.addLayout(grid)
        cl.addWidget(rewards)
        cl.addWidget(row(label("any key", name="keycap"), label("to begin", role="meta"), "stretch", spacing=10))
        return screen

    def _build_complete(self):
        screen, cl = self._centered_column()
        self.complete_kicker, self.complete_display = self._heading(cl)
        self.care_box = QWidget(); cb = QVBoxLayout(self.care_box); cb.setContentsMargins(0, 0, 0, 0); cb.setSpacing(10)
        cb.addWidget(label("WHAT YOU TOOK CARE OF", role="label"))
        grid = QGridLayout(); grid.setSpacing(12)
        self.care_stats = {}
        for i, (key, text) in enumerate(CARE):
            tile, t = frame("careTile", margins=(12, 12, 12, 12), spacing=4)
            tile.setMinimumHeight(92)
            stat = label("0", role="stat")
            t.addWidget(stat); t.addWidget(label(text, role="meta", wrap=True))
            grid.addWidget(tile, 0, i); grid.setColumnStretch(i, 1)
            self.care_stats[key] = stat
        cb.addLayout(grid)
        cl.addWidget(self.care_box)
        self.tally = label(role="body", tone="muted", wrap=True)
        cl.addWidget(self.tally)
        div = QFrame(); div.setObjectName("hDivider")
        cl.addWidget(div)
        cl.addWidget(label("New encounters will appear here when the collector finds them.", role="meta"))
        return screen

    def _build_status(self):
        strip, sl = frame("statusStrip", QHBoxLayout, (20, 0, 20, 0), 16)
        strip.setFixedHeight(30)
        self.footer = label(name="statusText")
        self.chronicle = label(name="chronicle")
        self.gate_health = label(name="gateHealth")
        sl.addWidget(self.footer, 1); sl.addWidget(self.chronicle)
        sl.addWidget(label("|", name="statusSep")); sl.addWidget(self.gate_health)
        return strip

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
        self.session_start, self.noted, self.rendered_key = self.clock(), set(), None  # first card starts fresh
        self.render()

    def _start_screen(self):
        s = Q.quest_summary(self.deck)
        plural = lambda n, w: f"{n} {w}{'' if n == 1 else 's'}"  # noqa: E731
        day = time.strftime("%A %-d %b", time.localtime(self.clock())).upper()
        self.start_kicker.setText(f"🗺 A NEW QUEST · {day}")
        self.start_display.setText(f"{plural(s['total'], 'encounter')} across {plural(s['repos'], 'repo')}")
        for kind, (stat, name) in self.start_tiles.items():
            n, e = s["counts"][kind], Q.ENCOUNTERS[kind]
            stat.setText(str(n)); name.setText(f"{e['emoji']} {e['name'].lower()}{'' if n == 1 else 's'}")
        self.screens.setCurrentWidget(self.start_screen)
        self._review_off()
        self._footer()

    def _complete_screen(self, celebrate):
        self.screens.setCurrentWidget(self.complete_screen)
        self._review_off()
        self.care_box.setVisible(celebrate); self.tally.setVisible(celebrate)
        if not celebrate:
            self.complete_kicker.setText("📭 NOTHING TO REVIEW")
            self.complete_display.setText("Inbox zero.")
            return self._footer()
        r = Q.recap(self.store.rows(), since_ts=self.session_start)
        self.complete_kicker.setText("🏆 QUEST COMPLETE")
        self.complete_display.setText(self.celebration)
        for key, stat in self.care_stats.items():
            stat.setText(str(r[key]))
        self.tally.setText(f"{r['approved']} approved · {r['closed']} closed — counted, never scored.")
        self._footer()

    def _encounter_header(self, c):
        kind = Q.encounter(c)
        e = Q.ENCOUNTERS[kind]
        total = max(self.quest_total, 1)
        n = min(self.done_count + 1, total)
        self.pill.setText(f"{e['emoji']} {e['name']}"); set_props(self.pill, tone=kind)
        self.progress_label.setText(f"Encounter {n} of {total}")
        clear(self.dots_lay)
        for i in range(min(total, 30)):
            d = QLabel(); d.setObjectName("dot")
            d.setProperty("state", "done" if i < n - 1 else "current" if i == n - 1 else "upcoming")
            d.setProperty("tone", kind)
            self.dots_lay.addWidget(d)
        key = D.card_key(c)
        if key not in self.flavours:
            self.flavours[key] = Q.flavour(c)
        self.flavour.setText(self.flavours[key])

    # --- rendering ---
    def render(self):
        """Draw the current card. The 30 s reload re-renders the same card: its dwell clock, flags and
        diff scroll position carry over, so only a new card starts fresh."""
        c = self.current()
        same = c is not None and D.card_key(c) == self.rendered_key
        scroll = self.body.verticalScrollBar().value()
        if not same:
            self.shown_at, self.detail_seen, self.one_off = self.clock(), False, False
            self.rendered_key = D.card_key(c) if c is not None else None
        if c is not None and not self.quest_started:
            return self._start_screen()
        if c is None:
            if self.quest_started or self.quest_complete:
                if self.quest_started:
                    self.celebration = Q.celebration()
                self.quest_started, self.quest_complete = False, True
                return self._complete_screen(celebrate=True)
            return self._complete_screen(celebrate=False)
        self.screens.setCurrentWidget(self.encounter_screen)
        self._encounter_header(c)
        self._pr_block(c)
        self._intent(c)
        self._review_on(c)
        self._render_body()
        if same:
            self.body.verticalScrollBar().setValue(scroll)
        self._footer()

    def _pr_block(self, c):
        self.repo_label.setText(f"{c['repo']} #{c['number']}")
        self.verified_chip.setText(f"verified ✓ {c['head_sha'][:7]}")
        self.verified_chip.setVisible(bool(c.get("_verified")))
        self.title_label.setText(c["title"])
        ds, ci, sig = c["diffstat"], c["ci"], c["signals"]
        sep = " " + span("·", S.C["border_dashed"]) + " "
        if ci["state"] == "failure":
            failing = esc(" (" + ", ".join(ci["failing"]) + ")") if ci["failing"] else ""
            ci_txt = span(f"<b>CI ✕ failing{failing}</b>", S.TONES["dragon"][0])
        elif ci["state"] == "success":
            ci_txt = span("CI ✓", S.TONES["sprout"][0])
        else:
            ci_txt = f"CI {esc(ci['state'])}"
        who = (f"by {esc(c['author'])} " + f'<span style="color:{S.TONES["traveler"][0]};'
               f'background:{S.TONES["traveler"][1]};">&nbsp;external&nbsp;</span>'
               if c["author_class"] == "external" else f"by {esc(c['author'])} ({esc(c['author_class'])})")
        parts = [who, f"started {esc(c['first_commit_at'][:10])}",
                 f"idle {esc(sig['days_since_update'])}d", ci_txt,
                 span(f"+{int(ds['additions'])}", S.C["add_fg"], mono=True) + " "
                 + span(f"−{int(ds['deletions'])}", S.C["del_fg"], mono=True) + f" · {int(ds['files'])} file{'' if ds['files'] == 1 else 's'}"]
        if c["mergeable"] != "clean":
            parts.append(span(esc(c["mergeable"]), S.TONES["dragon"][0]))
        if sig["overlapping_open"]:
            parts.append("overlaps #" + ", #".join(esc(n) for n in sig["overlapping_open"]))
        n = SU.warning_total(c)  # the list itself lives behind w: the review screen is for the change
        if n:
            parts.append(span(f"⚠ {n} warning{'' if n == 1 else 's'} · w", S.TONES["dragon"][0]))
        self.meta_label.setText(sep.join(parts))

    def _intent(self, c):
        headline, source, tone, pr_line = SU.intent(c)
        self.intent_text.setText(headline)
        self.intent_source.setText(source); set_props(self.intent_source, tone=tone)
        self.intent_pr.setText(pr_line); self.intent_pr.setVisible(bool(pr_line))
        self.path_source.setText(SU.path_source_note(c))
        clear(self.path_lay)
        for i, st in enumerate(SU.solution_path(c, c.get("_commits", []))):
            if i:
                arrow = label("→", name="stepArrow"); self.path_lay.addWidget(arrow)
            step, sl = frame("pathStep", margins=(10, 7, 10, 7), spacing=3)
            step.setProperty("source", st["source"])
            num, text = label(str(i + 1), name="stepNum"), label(st["text"], name="stepText", wrap=True)
            num.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
            top = row(num, text, spacing=6); top.layout().setStretch(1, 1)
            top.layout().setAlignment(num, Qt.AlignTop)
            src = label(st["src"], name="stepSrc"); src.setProperty("source", st["source"])
            sl.addWidget(top); sl.addWidget(src); sl.addStretch(1)
            self.path_lay.addWidget(step, 1)
        v, ctx = c["verdict"], c["context"]
        has_ai = any((ctx.get(k) or "").strip() for k in ("purpose", "solution")) or c.get("deep_review")
        set_props(self.ai_panel, state="present" if has_ai else "none")
        self.ai_tag.setText("UNVERIFIED" if has_ai else "NONE"); set_props(self.ai_tag, state="present" if has_ai else "none")
        self.verdict_row.setVisible(bool(has_ai))
        if not has_ai:
            self.ai_summary.setText(f"{v['reason']}. The path above uses verified files and CI only.\n"
                                    "r requests a deep review.")
            return
        self.verdict_chip.setText(v["recommendation"].upper())
        set_props(self.verdict_chip, verdict="close" if v["recommendation"] == "close" else "approve")
        self.verdict_reason.setText(f"{v['confidence']} confidence — {SU.short(v['reason'], 240)}"
                                    + (" · STALE" if v["stale"] else "")
                                    + (f" · superseded by {', '.join(v['superseded_by'])}"
                                       if v["superseded_by"] and "superseded" not in v["reason"] else ""))
        # the intent panel already carries the briefing; repeat the purpose only for cards without one
        lines = [f"Purpose: {SU.short(ctx['purpose'], 240)}"] if ctx["purpose"] and not ctx.get("headline") else []
        if ctx.get("unsure"):
            lines.append(SU.short(ctx["unsure"], 240))
        if ctx["notes"]:
            lines.append(SU.short(ctx["notes"], 240))
        if c.get("deep_review"):
            lines.append("Deep review: " + c["deep_review"]["summary"])
            lines += [f"  • {f}" for f in c["deep_review"]["findings"]]
        self.ai_summary.setText("\n".join(lines))

    def show_all_warnings(self, c):
        dlg = QDialog(self); dlg.setWindowTitle("All warnings"); dlg.resize(760, 480)
        lay = QVBoxLayout(dlg)
        lst = QListWidget(); lst.setWordWrap(True)
        for r in SU.warning_rows(c):
            lst.addItem(f"{r['count']} {r['text']}")
        lay.addWidget(lst)
        dlg.exec()

    # --- review view (verified cards only; others keep the hotspot pane) ---
    def _review_off(self):
        self.review = None
        self.left_col.hide(); self.coverage_panel.hide()

    def _review_on(self, c):
        if not c.get("_view"):
            return self._review_off()
        key = D.card_key(c)
        if key not in self.reviews:
            self.reviews[key] = ReviewState(c, self.clock)
        self.review = self.reviews[key]
        self.left_col.show(); self.coverage_panel.show()
        self._fill_files()
        rows = self.review.rows()
        if rows and "path" in rows[self.review.row] and self.review.cov.current != rows[self.review.row]["path"]:
            self.review.cov.showing(rows[self.review.row]["path"])

    def _file_row_widget(self, f, seen):
        items = [label(f["path"], role="mono"), "stretch"]
        if f["ai_severity"] and not f["rule"]:
            items.append(label(RV.SEV_NAME[f["ai_severity"]], name="sevChip"))
        items.append(label(f"+{f['additions']}", role="mono", tone="sprout"))
        if f["deletions"]:
            items.append(label(f"−{f['deletions']}", role="mono", tone="dragon"))
        tick = label("✓" if seen else "", role="mono", tone="sprout"); tick.setFixedWidth(12)
        items.append(tick)
        return row(*items, spacing=6, margins=(0, 0, 12, 0))  # the item's own padding gives the left inset

    def _fill_files(self):
        r = self.review
        self.files.blockSignals(True)
        self.files.clear()
        self.list_rows, last = [], None
        heads = {"rule": ("🚩 rule-flagged", S.TONES["dragon"][0]), "ai": ("⚠ AI-flagged", S.TONES["ai"][0]),
                 "other": ("other", S.C["text3"]), "low": ("low-signal", S.C["text3"])}
        for i, f in enumerate(r.rows()):
            if "group" in f or f["low_signal"]:
                cat = "low"
            else:
                cat = "rule" if f["rule"] else "ai" if f["ai_severity"] else "other"
            if cat != last and "group" not in f:
                text, colour = heads[cat]
                h = QListWidgetItem(text); h.setFlags(Qt.NoItemFlags); h.setForeground(QColor(colour))
                h.setFont(S.ui_font(11))
                h.setSizeHint(QSize(0, 22)); h.setData(ROW_TEXT, text)
                self.files.addItem(h); self.list_rows.append(None)
            last = cat
            item = QListWidgetItem(); item.setSizeHint(QSize(0, 26))
            if "group" in f:
                desc = f"▸ {f['group']} low-signal file{'s' if f['group'] != 1 else ''} (vendored/generated)"
                widget = row(label(desc, role="caption"), "stretch", margins=(0, 0, 12, 0))
            else:
                seen = f["path"] in r.cov.seen
                tag = "🚩" if f["rule"] else (f"⚠ {RV.SEV_NAME[f['ai_severity']]}" if f["ai_severity"] else "·")
                desc = f"{tag} {f['path']}  +{f['additions']} -{f['deletions']}{'  ✓' if seen else ''}"
                widget = self._file_row_widget(f, seen)
            item.setData(ROW_TEXT, desc)
            self.files.addItem(item); self.files.setItemWidget(item, widget)
            self.list_rows.append(i)
        rows = r.rows()
        r.row = max(0, min(r.row, len(rows) - 1))
        if rows:
            self.files.setCurrentRow(self.list_rows.index(r.row))
        self.files.blockSignals(False)

    def file_rows(self):
        """Plain descriptions of the file list, top to bottom (headers included)."""
        return [self.files.item(i).data(ROW_TEXT) for i in range(self.files.count())]

    def _clicked_row(self, list_row):
        if self.review is not None and 0 <= list_row < len(self.list_rows) and self.list_rows[list_row] is not None:
            self._select_row(self.list_rows[list_row])

    def _select_row(self, idx, hunk=None):
        r = self.review
        rows = r.rows()
        if not rows:
            return
        idx = max(0, min(idx, len(rows) - 1))
        if "group" in rows[idx]:
            r.low_open = True
            rows = r.rows()
        r.row = idx
        self.detail_open = False
        r.cov.showing(rows[idx]["path"])
        self._fill_files()
        self._render_body(hunk=hunk)
        self._footer()

    def _spot_lines(self):
        """Flagged-hunk index -> line in the diff pane, for the file on screen. A file-level flag sits
        on the first hunk; a file with no hunk lines (binary view) carries its flags at the top."""
        r = self.review
        rows = r.rows() if r is not None and not self.detail_open else []
        if not rows or "path" not in rows[r.row]:
            return {}
        out = {}
        for i, h in r.file_spots(rows[r.row]["path"]):
            head = h.get("hunk") or "@@"
            out[i] = next((n for n, (k, t) in enumerate(self.body.lines) if k == "hunk" and t.startswith(head)), 0)
        return out

    def _on_screen(self):
        first = self.body.firstVisibleBlock().blockNumber()
        last = self.body.cursorForPosition(QPoint(0, self.body.viewport().height() - 1)).blockNumber()
        return {i for i, n in self._spot_lines().items() if first <= n <= last}

    def _mark_on_screen(self):
        """A flagged hunk whose header has been on screen counts as visited."""
        new = self._on_screen() - self.review.cov.visited if self.review is not None else set()
        if new:
            self.review.cov.visited |= new
            self._coverage()

    def _next_flag(self):
        """Jump to the next flagged hunk that is not on screen, unvisited ones first, so every press moves."""
        r = self.review
        if not r.spots:
            return self._footer("no flagged hunks on this card")
        here = self._on_screen()
        order = [(r.flag_i + k) % len(r.spots) for k in range(1, len(r.spots) + 1)]
        away = [i for i in order if i not in here]
        if not away:
            return self._footer("every flagged hunk on this card is on screen")
        r.flag_i = next((i for i in away if i not in r.cov.visited), away[0])
        h = r.spots[r.flag_i]
        r.cov.visit_hunk(r.flag_i)
        if any(f["low_signal"] and f["path"] == h["file"] for f in r.ordered):
            r.low_open = True
        idx = next(i for i, f in enumerate(r.rows()) if f.get("path") == h["file"])
        self._select_row(idx, hunk=h.get("hunk") or "@@")

    def _gate_locked(self, c):
        r = self.review
        return r is not None and c["can_merge"] and D.needs_confirm(c, "approve") and not r.cov.gate_ok()

    def _coverage(self):
        c, r = self.current(), self.review
        if r is None or c is None:
            return
        cov = r.cov
        cov.tick()
        self.files_bar.setMaximum(max(len(cov.paths), 1)); self.files_bar.setValue(len(cov.seen))
        self.files_count.setText(f"{len(cov.seen)}/{len(cov.paths)}")
        nflag = len(cov.flagged)
        self.hunks_group.setVisible(nflag > 0)
        self.hunks_bar.setMaximum(max(nflag, 1)); self.hunks_bar.setValue(len(cov.visited))
        self.hunks_count.setText(f"{len(cov.visited)}/{nflag}")
        set_props(self.hunks_bar, complete="true" if len(cov.visited) == nflag else "false")
        locked, dragon = self._gate_locked(c), D.needs_confirm(c, "approve")
        self.gate_label.setText("🔒 Approve locked" if locked else "✓ Approve unlocked" if dragon else "✓ Ready")
        set_props(self.gate_label, locked="true" if locked else "false")
        left = nflag - len(cov.visited)
        self.gate_hint.setText(f"{left} more flagged hunk{'s' if left != 1 else ''} to unlock → · n jumps there"
                               if locked else "")
        rows = r.rows()
        if r.row < len(rows) and "path" in rows[r.row] and not self.detail_open:
            spots = r.file_spots(rows[r.row]["path"])
            seen = sum(i in cov.visited for i, _ in spots)
            self.hunk_state.setText(" · ".join(f"hunk {n} {'✓' if i in cov.visited else 'unvisited'}"
                                               for n, (i, _) in enumerate(spots, 1)) if len(spots) <= 4
                                    else f"{seen}/{len(spots)} flags seen" + (" ✓" if seen == len(spots) else ""))
        self._keys()

    def _keys(self):
        c = self.current()
        locked = c is not None and self._gate_locked(c)
        cap, lab = self.keys["approve"]
        lab.setText("approve 🔒" if locked else "approve")
        for w in (cap, lab):
            set_props(w, disabled="true" if locked else "false")
        hints = ([("j/k", "file"), ("n", "next flag")] if self.review is not None else []) + [
            ("w", "all warnings"), ("t", "note"), ("m", "AI missed"), ("x", "one-off" + (" ✓" if self.one_off else "")),
            ("s", "skip"), ("u", "undo"), ("o", "browser")]
        self.secondary_keys.setText("&nbsp;&nbsp; ".join(
            f'<b style="color:{S.C["text2"]}">{k}</b>&nbsp;{t.replace(" ", "&nbsp;")}' for k, t in hints))

    def _render_body(self, hunk=None):
        c = self.current()
        if self.detail_open:
            lines = RV.render_diff(c["detail"]["diff"], c["hotspots"])
            if c["detail"]["truncated"]:
                lines.append(("meta", "[diff truncated]"))
            for m in c["detail"]["comments"]:
                lines.append(("file", f"💬 comment by {m['author']}"))
                lines += [("meta", l) for l in m["body"].splitlines()]
            self.diff_path.setText("whole diff"); self.diff_info.setText("f or Esc to return"); self.hunk_state.setText("")
            return self.body.show_lines(lines or [("meta", "(empty diff)")])
        if self.review is None and not c["hotspots"]:  # nothing flagged: the whole diff is the review
            lines = RV.render_diff(c["detail"]["diff"], [])
            self.diff_path.setText("diff"); self.diff_info.setText("no hotspots flagged"); self.hunk_state.setText("")
            empty = ("GitHub would not send a diff this large. o opens the PR in the browser."
                     if c["detail"]["truncated"] else "This card carries no diff.")
            return self.body.show_lines(lines or [("meta", empty)])
        if self.review is None:
            lines = []
            for h in c["hotspots"]:
                lines.append(("file", h["file"]))
                if h.get("hunk"):
                    lines.append(("hunk", h["hunk"]))
                lines.append(RV.callout(h))
                for l in h.get("lines", "").splitlines():
                    lines.append(("add" if l.startswith("+") else "del" if l.startswith("-") else "ctx", f"     {l}"))
            self.diff_path.setText("hotspots"); self.diff_info.setText("f for the full diff"); self.hunk_state.setText("")
            return self.body.show_lines(lines or [("meta", "No hotspots. f for the full diff.")])
        r = self.review
        rows = r.rows()
        if not rows:
            self.diff_path.setText(""); self.diff_info.setText("")
            return self.body.show_lines([("meta", "No changed files in the verified diff.")])
        f = rows[r.row]
        nflag = len(r.file_spots(f["path"]))
        self.diff_path.setText(f["path"])
        self.diff_info.setText(STATUS_WORD.get(f["status"][:1], "changed")
                               + (f" · {nflag} flagged hunk{'s' if nflag != 1 else ''}" if nflag else ""))
        lines = r.file_lines(f)
        focus = None
        if hunk:
            focus = next((i for i, (k, t) in enumerate(lines) if k == "hunk" and t.startswith(hunk)), None)
        self.body.show_lines(lines, focus=focus, path=f["path"])
        self._mark_on_screen()
        self._coverage()

    def _footer(self, msg=""):
        s = D.stats(self.store.metrics(days=7))
        ch = Q.chronicle(self.store.rows(), now=self.clock())
        self.chronicle.setText(f"This week 🔍 {ch['finds']} finds · 📜 {ch['lore']} lore")
        self.gate_health.setText(f"gate: {s['n']} decisions · median approve {s['median_approve_s']:.0f}s · "
                                 f"close {s['close_rate']:.0%} · detail {s['detail_rate']:.0%}")
        if msg:
            kind = "toast" if msg in Q.TOASTS.values() else "warn" if msg.startswith(("🐉", "executor", "AI did")) else "info"
        else:
            kind = "info"
            if self.pending:
                msg = f"sending soon: {self.pending[1]} {self.pending[0]['repo']}#{self.pending[0]['number']} · u to undo"
            elif self.one_off:
                msg = "one-off: this decision won't teach the learning loop"
            elif self.screens.currentWidget() is self.encounter_screen:
                msg = f"{len(self.deck)} left in the deck"
            else:
                msg = f"{len({c['repo'] for c in self.deck})} repos waiting" if self.deck else ""
        self.footer.setText(msg)
        set_props(self.footer, kind=kind)
        if self.screens.currentWidget() is self.encounter_screen:
            self._keys()

    # --- actions ---
    def keyPressEvent(self, ev):
        c, k = self.current(), ev.key()
        if c is not None and not self.quest_started:
            return self.begin_quest()
        if k == Qt.Key_U:
            return self.undo()
        if c is None:
            return
        if k in (Qt.Key_Up, Qt.Key_Down):
            sb = self.body.verticalScrollBar()
            return sb.setValue(sb.value() + (3 if k == Qt.Key_Down else -3) * sb.singleStep())
        if k == Qt.Key_F:
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
        if k == Qt.Key_W and SU.warning_total(c):
            return self.show_all_warnings(c)
        if k in (Qt.Key_J, Qt.Key_K) and self.review is not None:
            return self._select_row(self.review.row + (1 if k == Qt.Key_J else -1))
        if k == Qt.Key_N and self.review is not None:
            return self._next_flag()
        if k == Qt.Key_T:
            return self.note(c, "note")
        if k == Qt.Key_M:
            return self.note(c, "missed")
        if k == Qt.Key_S:
            self.store.record(c, "skip", one_off=self.one_off)
            return self._advance(skip=True)
        if k == Qt.Key_R:
            self.store.request_deep_review(c)
            self.store.record(c, "deep-review", one_off=self.one_off)
            return self._advance(skip=True)
        if k in (Qt.Key_Left, Qt.Key_Right):
            return self.decide(c, "close" if k == Qt.Key_Left else "approve")

    def decide(self, c, action):
        if not c["can_merge"]:
            self.store.request_open(c["url"])
            return self._advance(skip=True)
        # ← and → are the reviewer's call: one press, whatever the AI said. Only a dragon's approve
        # waits, and only until its flagged hunks have been looked at (no second press after that).
        dwell = self.clock() - self.shown_at
        if action == "approve" and D.needs_confirm(c, action):
            r = self.review
            if r is None and dwell < DWELL_S:
                return self._footer("🐉 Hold your ground: hunks must be on screen 2 s first")
            if r is not None and not r.cov.gate_ok():
                left = r.cov.remaining()
                files = ", ".join(sorted({p.rsplit("/", 1)[-1] for p, _ in left}))
                return self._footer(f"🐉 Hold your ground — {len(left)} flagged hunk{'s' if len(left) != 1 else ''} "
                                    f"unvisited in {files}. n jumps there.")
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
    def ask_text(self, title, prompt):
        text, ok = QInputDialog.getMultiLineText(self, title, prompt)
        return text.strip() if ok and text.strip() else None

    def ask_item(self, title, prompt, items):
        item, ok = QInputDialog.getItem(self, title, prompt, items, 0, False)
        return items.index(item) if ok else None

    def note(self, c, kind):
        hotspot = None
        if kind == "note" and c["hotspots"] and not self.detail_open:
            items = ["whole PR"] + [f"{i}: {h['file']}  {h['hunk'][:80]}" for i, h in enumerate(c["hotspots"], 1)]
            pick = self.ask_item("Note on…", "Attach the note to:", items)
            if pick is None:
                return
            hotspot = c["hotspots"][pick - 1] if pick else None
        prompt = ("What did the AI miss? (file/line, what is wrong, why it matters)" if kind == "missed"
                  else "Your note: what is right or wrong here, and why")
        text = self.ask_text("Feedback", prompt)
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
    app.setFont(S.ui_font())
    app.setWindowIcon(S.app_icon())
    app.setDesktopFileName("pr-swipe")
    w = Window(Store(cfg), ExecutorClient(cfg.socket),
               load_cards=lambda: EV.verified_cards(cfg, C.load_cards(cfg.inbox)))
    w.show()
    app.aboutToQuit.connect(w.flush_pending)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
