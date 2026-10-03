"""Deck ordering and confirmation rules. Pure; no Qt."""
import statistics

SEV = {"high": 3, "medium": 2, "low": 1}


def pr_key(c):
    return f"{c['repo']}#{c['number']}"


def card_key(c):
    return f"{pr_key(c)}@{c['head_sha']}"


def risk(c) -> int:
    rule = sum(3 for h in c["hotspots"] if h["source"] == "rule")
    ai = sum(SEV.get(h.get("severity"), 0) for h in c["hotspots"] if h["source"] == "ai")
    return rule + ai + 5 * len(c["hidden_content"])


def build_deck(cards, in_train, decided, returns) -> list:
    latest = {}
    for c in cards:
        k = pr_key(c)
        if k not in latest or c["review_meta"]["reviewed_at"] > latest[k]["review_meta"]["reviewed_at"]:
            latest[k] = c
    last_return = {}
    for r in returns:
        k = f"{r['repo']}#{r['number']}"
        if k not in last_return or r["ts"] > last_return[k]["ts"]:
            last_return[k] = r
    deck = []
    for k, c in latest.items():
        if k in in_train:
            continue
        ret = last_return.get(k)
        decided_at = decided.get(card_key(c))
        if decided_at is not None and not (ret and ret["ts"] > decided_at):
            continue
        c = dict(c)
        if ret and (decided_at is None or ret["ts"] > decided_at):
            c["_returned"] = ret["reason"]
        deck.append(c)

    def order(c):
        if c["verdict"]["recommendation"] == "close":
            bucket = 0
        elif c["ci"]["state"] == "failure" or c.get("_returned"):
            bucket = 1
        else:
            bucket = 2
        return (bucket, -risk(c), c["created_at"])

    return sorted(deck, key=order)


def needs_confirm(c, action) -> bool:
    if action == "close":
        return c["verdict"]["recommendation"] != "close"
    return (c["ci"]["state"] == "failure" or bool(c["hidden_content"]) or bool(c.get("_returned"))
            or any(h["source"] == "rule" for h in c["hotspots"]))


def stats(metrics) -> dict:
    n = len(metrics)
    if not n:
        return {"n": 0, "median_approve_s": 0.0, "close_rate": 0.0, "detail_rate": 0.0}
    approves = [m["dwell"] for m in metrics if m["action"] == "approve"]
    return {
        "n": n,
        "median_approve_s": float(statistics.median(approves)) if approves else 0.0,
        "close_rate": sum(m["action"] == "close" for m in metrics) / n,
        "detail_rate": sum(bool(m["detail"]) for m in metrics) / n,
    }
