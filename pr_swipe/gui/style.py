"""Look and feel: palette, fonts and the Qt stylesheet (from the Claude Design handoff, 2026-10-03).

Dynamic properties drive variants: role (text style), tone (dragon/ai/sprout/ghost/traveler/muted),
state, locked, kind, disabled. After changing one at runtime, call `repolish(widget)`.
"""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ICON = Path(__file__).with_name("icon.svg")

UI_FAMILIES = ["IBM Plex Sans", "Segoe UI", "Noto Sans", "Cantarell", "DejaVu Sans"]
MONO_FAMILIES = ["IBM Plex Mono", "JetBrains Mono", "DejaVu Sans Mono", "Consolas", "Liberation Mono"]

C = {
    "bg": "#F5F4F0", "chrome": "#EDEBE6", "surface": "#FFFFFF", "surface_ai": "#FCFBF8",
    "selection": "#EEEBE3", "track": "#ECEAE3", "border": "#E3E0D8", "border_strong": "#D2CEC4",
    "border_dashed": "#C9C5BB", "text": "#1E1D1A", "text2": "#5B5850", "text3": "#8A867C", "text4": "#B8B4AA",
    "add_fg": "#1E6B37", "add_bg": "#EEF6EE", "del_fg": "#A33A2E", "del_bg": "#FBEEEC",
}
TONES = {  # fg, tint, border
    "dragon": ("#A93A2F", "#FAECE9", "#EDC9C3"),
    "ai": ("#8A5A00", "#FCF3DF", "#E2C98A"),
    "sprout": ("#2D7046", "#E8F3EB", "#C3DECB"),
    "ghost": ("#5F57A0", "#EFEDF7", "#D5D1EA"),
    "traveler": ("#2B6690", "#E8F0F6", "#C6D8E6"),
}
ENCOUNTER_TONE = {"dragon": "dragon", "sprout": "sprout", "ghost": "ghost", "traveler": "traveler"}


def _first(families):
    have = set(QFontDatabase.families())
    return next((f for f in families if f in have), families[-1])


def mono_family():
    return _first(MONO_FAMILIES)


def mono_font(px=12):
    f = QFont(); f.setFamilies(MONO_FAMILIES); f.setPixelSize(px); f.setStyleHint(QFont.Monospace)
    return f


def ui_font(px=13):
    f = QFont(); f.setFamilies(UI_FAMILIES); f.setPixelSize(px)
    return f


def repolish(w):
    w.style().unpolish(w); w.style().polish(w)


def color(name):
    return QColor(C[name])


def stylesheet():
    mono = mono_family()
    tone_rules = "\n".join(
        f'QLabel[tone="{t}"] {{ color:{fg}; }}\n'
        f'QLabel#encounterPill[tone="{t}"] {{ color:{fg}; background:{tint}; border:1px solid {bd}; }}\n'
        f'QLabel#dot[state="current"][tone="{t}"] {{ background:{fg}; }}\n'
        f'QFrame#encounterTile[tone="{t}"] {{ background:{tint}; border:1px solid {bd}; }}'
        for t, (fg, tint, bd) in TONES.items())
    return f"""
QWidget {{ background:#F5F4F0; color:#1E1D1A; font-size:13px; }}

QFrame#intentPanel, QFrame#filesPanel, QFrame#commitsPanel, QFrame#diffPanel, QFrame#coveragePanel,
QFrame#descPanel, QFrame#rewardsPanel, QFrame#careTile {{ background:#FFFFFF; border:1px solid #E3E0D8; border-radius:6px; }}
QFrame#aiPanel {{ background:#FCFBF8; border:1px dashed #C9C5BB; border-radius:6px; }}
QFrame#warningsStrip {{ background:#FDF6F4; border:1px solid #EDC9C3; border-radius:6px; }}
QFrame#diffHeader {{ background:#FFFFFF; border:none; border-bottom:1px solid #E3E0D8;
  border-top-left-radius:6px; border-top-right-radius:6px; }}
QFrame#statusStrip {{ background:#EDEBE6; border:none; border-top:1px solid #E3E0D8; }}
QFrame#vDivider {{ background:#E3E0D8; max-width:1px; min-width:1px; }}
QFrame#hDivider {{ background:#E3E0D8; max-height:1px; min-height:1px; }}
QFrame#intentPanel QWidget, QFrame#filesPanel QWidget, QFrame#commitsPanel QWidget, QFrame#diffPanel QWidget,
QFrame#coveragePanel QWidget, QFrame#rewardsPanel QWidget, QFrame#careTile QWidget, QFrame#aiPanel QWidget,
QFrame#warningsStrip QWidget, QFrame#statusStrip QWidget, QFrame#encounterTile QWidget,
QFrame#descPanel QWidget {{ background:transparent; }}
QFrame#diffPanel QPlainTextEdit, QFrame#filesPanel QListWidget, QFrame#descPanel QTextBrowser {{ background:#FFFFFF; }}
QFrame#intentPanel QFrame#aiPanel {{ background:#FCFBF8; border:1px dashed #C9C5BB; border-radius:6px; }}
QFrame#intentPanel QFrame#aiPanel[state="none"] {{ background:#F5F4F0; border:1px solid #E3E0D8; }}
QFrame#intentPanel QFrame#pathStep {{ background:#F5F4F0; border:none; border-radius:4px; }}
QFrame#intentPanel QFrame#pathStep[source="ai"] {{ background:#FCFBF8; border:1px dashed #C9C5BB; }}
QFrame#intentPanel QFrame#pathStep[source="risk"] {{ background:#FAECE9; }}
QFrame#pathStep QWidget {{ background:transparent; }}

QLabel[role="display"] {{ font-size:26px; font-weight:600; }}
QLabel[role="title"] {{ font-size:20px; font-weight:600; }}
QLabel[role="stat"] {{ font-size:24px; font-weight:600; }}
QLabel[role="body"] {{ font-size:13px; }}
QLabel[role="flavour"] {{ font-size:13px; font-style:italic; color:#5B5850; }}
QLabel[role="meta"] {{ font-size:12px; color:#5B5850; }}
QLabel[role="label"] {{ font-size:11px; font-weight:600; color:#8A867C; }}
QLabel[role="caption"] {{ font-size:11px; color:#8A867C; }}
QLabel[role="mono"] {{ font-family:"{mono}"; font-size:12px; }}
QLabel[role="repo"] {{ font-family:"{mono}"; font-size:12px; color:#8A867C; }}
QLabel[tone="muted"] {{ color:#8A867C; }}
QLabel[weight="bold"] {{ font-weight:600; }}
{tone_rules}

QLabel#encounterPill {{ font-size:12px; font-weight:600; padding:3px 9px; border-radius:4px; }}
QLabel#verifiedChip {{ font-family:"{mono}"; font-size:11px; color:#2D7046; background:#E8F3EB;
  border-radius:4px; padding:2px 7px; }}
QLabel#unverifiedTag {{ font-size:10px; font-weight:600; color:#8A5A00; border:1px solid #E2C98A;
  border-radius:3px; padding:0 5px; }}
QLabel#verdictChip {{ font-family:"{mono}"; font-size:11px; font-weight:600; border-radius:3px; padding:1px 6px; }}
QLabel#verdictChip[verdict="approve"] {{ color:#2D7046; border:1px solid #9AC2A6; }}
QLabel#verdictChip[verdict="close"] {{ color:#A93A2F; border:1px solid #EDC9C3; }}
QFrame#filesPanel QLabel#sevChip {{ font-family:"{mono}"; font-size:10px; color:#8A5A00; background:#FCF3DF; border-radius:3px;
  padding:0 4px; }}

QLabel#intentHeadline {{ font-size:17px; font-weight:500; color:#1E1D1A; }}
QLabel#sourceNote {{ font-size:11px; color:#8A867C; }}
QLabel#sourceNote[tone="sprout"] {{ color:#2D7046; }}
QLabel#sourceNote[tone="ai"] {{ color:#8A5A00; }}
QLabel#stepNum {{ font-family:"{mono}"; font-size:13px; color:#8A867C; }}
QLabel#stepText {{ font-size:13px; color:#1E1D1A; }}
QLabel#stepSrc {{ font-size:11px; color:#8A867C; }}
QLabel#stepSrc[source="ai"] {{ color:#8A5A00; }}
QLabel#stepSrc[source="risk"] {{ color:#A93A2F; }}
QLabel#stepArrow {{ font-size:13px; color:#B8B4AA; }}
QLabel#manualSteps {{ color:#8A5A00; background:#FBF3E2; border:1px solid #EBD9B0; border-radius:6px; padding:6px 10px; font-weight:600; }}
QLabel#unverifiedTag[state="none"] {{ color:#8A867C; border:1px solid #D2CEC4; }}
QLabel#warnCount {{ font-family:"{mono}"; font-size:11px; color:#8A867C; }}
QLabel#warnCount[risky="true"] {{ color:#A93A2F; }}
QLabel#warnText {{ font-size:12px; }}
QLabel#warnText[risky="true"] {{ color:#1E1D1A; }}
QLabel#warnText[risky="false"] {{ color:#5B5850; }}
QLabel#warnWhere {{ font-size:11px; color:#8A867C; }}
QTextBrowser#descView {{ border:none; font-size:13px; color:#1E1D1A; selection-background-color:#E8F0F6; }}
QLabel#descFooter {{ font-size:12px; color:#8A867C; border-top:1px solid #ECEAE3; padding-top:6px; }}
QLabel#commitSha {{ font-family:"{mono}"; font-size:12px; color:#8A867C; }}
QLabel#commitSubj {{ font-size:12px; color:#1E1D1A; }}

QLabel#dot {{ min-width:6px; max-width:6px; min-height:6px; max-height:6px; border-radius:1px; background:#D2CEC4; }}
QLabel#dot[state="done"] {{ background:#8A867C; }}

QListWidget#fileList {{ border:none; outline:0; font-family:"{mono}"; font-size:12px; }}
QListWidget#fileList::item {{ border:none; color:#1E1D1A; padding-left:12px; }}
QListWidget#fileList::item:selected {{ background:#EEEBE3; color:#1E1D1A; }}

QPlainTextEdit#diffView {{ border:none; padding:6px 0; font-family:"{mono}"; font-size:12px; color:#5B5850;
  selection-background-color:#E8F0F6; selection-color:#1E1D1A; }}
QScrollBar:vertical {{ background:transparent; width:8px; margin:2px; }}
QScrollBar::handle:vertical {{ background:#D2CEC4; border-radius:3px; min-height:24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height:0; width:0; }}
QScrollBar:horizontal {{ background:transparent; height:8px; margin:2px; }}
QScrollBar::handle:horizontal {{ background:#D2CEC4; border-radius:3px; min-width:24px; }}

QFrame#coveragePanel QProgressBar#filesBar, QFrame#coveragePanel QProgressBar#hunksBar {{ min-width:110px; max-width:110px; min-height:6px; max-height:6px;
  border:none; border-radius:3px; background:#ECEAE3; }}
QProgressBar#filesBar::chunk {{ background:#8A867C; border-radius:3px; }}
QProgressBar#hunksBar::chunk {{ background:#A93A2F; border-radius:3px; }}
QProgressBar#hunksBar[complete="true"]::chunk {{ background:#2D7046; }}
QLabel#gateLabel[locked="true"] {{ color:#A93A2F; font-weight:600; font-size:12px; }}
QLabel#gateLabel[locked="false"] {{ color:#2D7046; font-weight:600; font-size:12px; }}

QLabel#keycap {{ font-family:"{mono}"; font-size:12px; color:#1E1D1A; background:#FFFFFF;
  border:1px solid #D2CEC4; border-bottom:2px solid #D2CEC4; border-radius:4px; min-width:24px; min-height:20px;
  padding:0 4px; }}
QLabel#keycap[disabled="true"] {{ color:#8A867C; background:#F5F4F0; border:1px dashed #D2CEC4; }}
QLabel#keyLabel {{ font-size:13px; color:#1E1D1A; }}
QLabel#keyLabel[disabled="true"] {{ color:#8A867C; }}
QLabel#secondaryKeys {{ font-size:11px; color:#8A867C; }}

QLabel#statusText {{ font-size:12px; color:#1E1D1A; }}
QLabel#statusText[kind="toast"] {{ color:#2D7046; }}
QLabel#statusText[kind="warn"] {{ color:#A93A2F; }}
QLabel#chronicle, QLabel#gateHealth, QLabel#statusSep {{ font-size:11px; color:#8A867C; }}

QFrame#encounterTile {{ border-radius:6px; }}
"""


def app_icon():
    """The jewel, rasterised here so it does not depend on Qt's SVG icon-engine plugin being on the path."""
    r, icon = QSvgRenderer(str(ICON)), QIcon()
    for px in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(px, px); pm.fill(Qt.transparent)
        p = QPainter(pm); r.render(p); p.end()
        icon.addPixmap(pm)
    return icon
