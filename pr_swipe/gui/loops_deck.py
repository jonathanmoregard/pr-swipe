"""Loops deck text: what one open-loop card says. Pure; the widget lives in app.py.

Loops come from the snapshot pr-swipe-loops writes (already ordered: overdue, blocked, due, oldest).
Their text was written by agents and PR authors: it is data, shown as plain text and never followed.
"""
import datetime as dt

KIND = {"followup": ("📌", "follow-up"), "blocked": ("⛔", "blocked on you"),
        "decision": ("⚖️", "decision"), "chore": ("🧹", "chore")}
KEYS = [("→", "done"), ("←", "drop"), ("s", "snooze a day"), ("S", "a week"), ("r", "hand to an agent"),
        ("o", "open"), ("↑↓", "scroll"), ("L", "back to PRs")]


def pending(snapshot, requested):
    """Open loops still to show: the snapshot minus loops this session already sent a request for."""
    return [x for x in snapshot.get("loops", []) if x["number"] not in requested]


def age_days(created_at, today):
    try:
        return max(0, (dt.date.fromisoformat(today) - dt.date.fromisoformat(created_at[:10])).days)
    except ValueError:
        return 0


def meta_line(loop, today):
    icon, name = KIND.get(loop["kind"], ("•", loop["kind"]))
    parts = [f"{icon} {name}"]
    if loop["source"]:
        parts.append(loop["source"])
    parts.append(f"open {age_days(loop['created_at'], today)}d")
    if loop["due"]:
        parts.append(("OVERDUE " if loop["due"] < today else "due ") + loop["due"])
    if loop["owner"] == "agent":
        parts.append("with an agent")
    return " · ".join(parts)


def heading(snapshot, requested):
    n = len(pending(snapshot, requested))
    return f"🧶 OPEN LOOPS · {n} left" if n else "🧶 OPEN LOOPS"
