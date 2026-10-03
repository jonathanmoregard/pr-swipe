from pr_swipe.gui import quest as Q
from tests.cards import make_card


def hot_rule():
    return {"source": "rule", "rule": "ci-workflow", "file": ".github/workflows/x.yml", "hunk": "@@", "lines": "+x"}


def test_encounter_precedence():
    assert Q.encounter(make_card(can_merge=False, ci={"state": "failure", "failing": ["t"]})) == "traveler"
    ghost = make_card(ci={"state": "failure", "failing": ["t"]})
    ghost["verdict"]["superseded_by"] = ["o/r#9"]
    assert Q.encounter(ghost) == "ghost"
    stale = make_card()
    stale["verdict"]["stale"] = True
    assert Q.encounter(stale) == "ghost"
    assert Q.encounter(make_card(hotspots=[hot_rule()])) == "dragon"
    assert Q.encounter(make_card()) == "sprout"


def test_quest_summary_counts_encounters_and_repos():
    cards = [make_card(), make_card(number=2, hotspots=[hot_rule()]), make_card(repo="o/s", number=3)]
    s = Q.quest_summary(cards)
    assert s == {"total": 3, "repos": 2, "counts": {"traveler": 0, "ghost": 0, "dragon": 1, "sprout": 2}}


def row(action, key="o/r#1@a", ts=10.0, rec="approve", override=False, stale=False, superseded=False, **kw):
    return {"ts": ts, "key": key, "action": action, "override": override,
            "ai": {"recommendation": rec, "confidence": "high", "stale": stale, "superseded": superseded}, **kw}


def test_recap_rewards_care_not_throughput():
    rows = [
        row("approve", key="k1"), row("approve", key="k2"), row("approve", key="k3"),
        row("missed", key="k4", note="race"),
        row("note", key="k5", note="why"),
        row("approve", key="k5", rec="close", override=True),      # overrule with a note → counts
        row("close", key="k6", rec="approve", override=True),      # overrule without a note → no
        row("close", key="k7", rec="close"),                       # ghost laid to rest
        row("close", key="k8", rec="approve", stale=True),         # stale ghost laid to rest
        row("approve", key="k9"), row("undo", key="k9"),           # undone: gone
        row("missed", key="k0", ts=1.0, note="old"),               # before the session
    ]
    r = Q.recap(rows, since_ts=5.0)
    assert r == {"finds": 1, "lore": 1, "overrules": 1, "laid_to_rest": 2, "approved": 4, "closed": 3}
    assert Q.score_keys() == ("finds", "lore", "overrules", "laid_to_rest")


def test_chronicle_window():
    rows = [row("missed", ts=100.0), row("note", ts=100.0), row("missed", ts=100.0 - 8 * 86400)]
    assert Q.chronicle(rows, now=100.0) == {"finds": 1, "lore": 1}
