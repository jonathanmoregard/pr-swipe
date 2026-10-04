"""Quest framing for the deck. Pure; no Qt.

Rewards attach only to acts of care (finds, lore, overrules with a reason, clearing stale work),
never to approve count or speed: rewarding throughput would train the habituation the gate exists
to resist. Flavour text comes only from the fixed pools below, never from card data.
"""
import random

from . import deck as D

ENCOUNTERS = {
    "traveler": {"emoji": "🧭", "name": "Traveler", "colour": "#0969da",
                 "lines": ["A traveler from another land. Their road, their gate: any swipe opens it in the browser.",
                           "Someone else's repo. Wave hello, then visit them in the browser."]},
    "ghost": {"emoji": "👻", "name": "Ghost", "colour": "#8250df",
              "lines": ["A ghost of work past. The AI thinks it can be laid to rest.",
                        "Stale or superseded. Does it still haunt anything you need?"]},
    "dragon": {"emoji": "🐉", "name": "Dragon", "colour": "#cf222e",
               "lines": ["A dragon guards this one. Read the hunks, hold your ground, then decide.",
                         "Risky change ahead. Look it in the eye before you let it pass."]},
    "sprout": {"emoji": "🌱", "name": "Sprout", "colour": "#1a7f37",
               "lines": ["A young sprout. Small and green, but still worth a real look.",
                         "A calm one. Calm is not the same as correct: give it a glance."]},
}
ORDER = ("traveler", "ghost", "dragon", "sprout")

CELEBRATIONS = [
    "Quest complete! The deck is clear. Time for fika ☕",
    "Inbox zero. Go stretch your legs in the forest 🌲",
    "All encounters met. The garden is weeded 🌻",
    "Deck cleared. A good moment for a walk and some berries 🫐",
    "Done! The repos are tidy and the kettle is calling 🍵",
]

TOASTS = {
    "finds": "🔍 Sharp eye! You caught something the AI missed.",
    "lore": "📜 Lore recorded. Future reviews will be wiser for it.",
    "overrules": "⚖️ Overruled, with reasons. That is what the human is for.",
    "laid_to_rest": "👻 Laid to rest.",
}

_SCORE = ("finds", "lore", "overrules", "laid_to_rest")


def score_keys():
    return _SCORE


def encounter(card) -> str:
    v = card["verdict"]
    if not card["can_merge"]:
        return "traveler"
    if v["recommendation"] == "close" or v["stale"] or v["superseded_by"]:
        return "ghost"
    if D.needs_confirm(card, "approve"):
        return "dragon"
    return "sprout"


def flavour(card, rng=random) -> str:
    return rng.choice(ENCOUNTERS[encounter(card)]["lines"])


def quest_summary(cards) -> dict:
    counts = {k: 0 for k in ORDER}
    for c in cards:
        counts[encounter(c)] += 1
    return {"total": len(cards), "repos": len({c["repo"] for c in cards}), "counts": counts}


def _is_ghost_row(r) -> bool:
    ai = r.get("ai", {})
    return ai.get("recommendation") == "close" or ai.get("stale", False) or ai.get("superseded", False)


def recap(rows, since_ts) -> dict:
    rows = [r for r in rows if r["ts"] >= since_ts]
    kept, latest = [], {}
    for r in rows:  # same cancellation rule as Store.metrics: an undo drops that key's latest decision
        if r["action"] == "undo":
            if r["key"] in latest:
                kept[latest.pop(r["key"])] = None
        elif r["action"] in ("approve", "close"):
            latest[r["key"]] = len(kept)
            kept.append(r)
    decisions = [r for r in kept if r is not None]
    noted = {r["key"] for r in rows if r["action"] == "note"}
    return {
        "finds": sum(r["action"] == "missed" for r in rows),
        "lore": sum(r["action"] == "note" for r in rows),
        "overrules": sum(r.get("override", False) and r["key"] in noted for r in decisions),
        "laid_to_rest": sum(r["action"] == "close" and _is_ghost_row(r) for r in decisions),
        "approved": sum(r["action"] == "approve" for r in decisions),
        "closed": sum(r["action"] == "close" for r in decisions),
    }


def chronicle(rows, now, days=7) -> dict:
    recent = [r for r in rows if r["ts"] >= now - days * 86400]
    return {"finds": sum(r["action"] == "missed" for r in recent),
            "lore": sum(r["action"] == "note" for r in recent)}


def celebration(rng=random) -> str:
    return rng.choice(CELEBRATIONS)
