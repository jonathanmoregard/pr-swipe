from pr_swipe.gui import deck as D
from tests.cards import make_card


def v(rec):
    return {"recommendation": rec, "stale": False, "superseded_by": [], "confidence": "high", "reason": ""}


def test_order_close_suggestions_then_failing_ci_then_risk():
    calm = make_card(number=1)
    risky = make_card(number=2, hotspots=[{"source": "rule", "rule": "ci-workflow", "file": "f", "hunk": "", "lines": ""}])
    red = make_card(number=3, ci={"state": "failure", "failing": ["t"]})
    dup = make_card(number=4, verdict=v("close"))
    deck = D.build_deck([calm, risky, red, dup], in_train=set(), decided={}, returns=[])
    assert [c["number"] for c in deck] == [4, 3, 2, 1]


def test_decided_and_in_train_cards_are_hidden_unless_returned():
    a, b, c = make_card(number=1), make_card(number=2), make_card(number=3)
    returns = [{"repo": "o/r", "number": 3, "head_sha": "a" * 40, "reason": "CI failed: t", "ts": 50.0}]
    deck = D.build_deck([a, b, c], in_train={"o/r#2"},
                        decided={"o/r#1@" + "a" * 40: 10.0, "o/r#3@" + "a" * 40: 10.0}, returns=returns)
    assert [x["number"] for x in deck] == [3]
    assert deck[0]["_returned"] == "CI failed: t"


def test_only_latest_head_per_pr_is_shown():
    old = make_card(number=1, sha="a" * 40, review_meta={"model": "m", "reviewed_at": "2026-10-01T00:00:00Z", "prose_seen": False})
    new = make_card(number=1, sha="c" * 40, review_meta={"model": "m", "reviewed_at": "2026-10-02T00:00:00Z", "prose_seen": False})
    assert [c["head_sha"] for c in D.build_deck([old, new], set(), {}, [])] == ["c" * 40]


def test_confirmation_rules():
    calm = make_card()
    assert not D.needs_confirm(calm, "approve")
    assert D.needs_confirm(make_card(ci={"state": "failure", "failing": ["t"]}), "approve")
    assert D.needs_confirm(make_card(hidden_content=[{"where": "body", "kind": "bidi", "text": "x"}]), "approve")
    assert D.needs_confirm(calm, "close")                 # AI did not suggest closing
    assert not D.needs_confirm(make_card(verdict=v("close")), "close")


def test_stats():
    m = [{"action": "approve", "dwell": 4.0, "detail": False, "ts": 0},
         {"action": "approve", "dwell": 10.0, "detail": True, "ts": 0},
         {"action": "close", "dwell": 2.0, "detail": False, "ts": 0}]
    s = D.stats(m)
    assert s == {"n": 3, "median_approve_s": 7.0, "close_rate": 1 / 3, "detail_rate": 1 / 3}
