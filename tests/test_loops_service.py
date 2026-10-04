import json
from types import SimpleNamespace

from pr_swipe import card as C, loops_service as S
from pr_swipe.loops import Loops
from tests.cards import make_card
from tests.fakes import FakeIssues

REPO = "me/.claude"
TODAY = "2026-10-04"


class Hub(FakeIssues):
    def __init__(self):
        super().__init__()
        self.prs = {}

    def pr(self, repo, n):
        return self.prs[(repo, n)]


def pr_state(state="open", merged=False, body=""):
    return {"state": state, "merged": merged, "merged_at": "2026-10-03T20:00:00Z" if merged else None,
            "html_url": "https://github.com/o/r/pull/1", "title": "Wrap gdocs MCP", "body": body}


def card(manual):
    c = make_card()
    c["context"]["manual"] = manual
    return c


def setup(tmp_path):
    cfg = SimpleNamespace(inbox=tmp_path / "inbox", outbox=tmp_path / "outbox")
    cfg.inbox.mkdir(); cfg.outbox.mkdir()
    return cfg


def run(tmp_path, cfg, hub, cards, today=TODAY):
    for p in cfg.inbox.glob("*.json"):
        p.unlink()
    for c in cards:
        C.write_card(cfg.inbox, c)
    S.run_once(hub, cfg, Loops(hub, REPO), tmp_path / "loops-state.json", today)


def titles(hub):
    return [i["title"] for i in hub.issues.values()]


def snapshot(cfg):
    return json.loads((cfg.inbox / "loops" / "open.json").read_text())["loops"]


def test_steps_are_filed_once_the_pr_merges_even_after_its_card_is_gone(tmp_path):
    cfg, hub = setup(tmp_path), Hub()
    hub.prs[("o/r", 1)] = pr_state()
    run(tmp_path, cfg, hub, [card(["Create the OAuth client secret"])])
    assert titles(hub) == []                                   # still open: nothing filed yet
    hub.prs[("o/r", 1)] = pr_state("closed", merged=True)
    run(tmp_path, cfg, hub, [])                                # collector pruned the card on close
    [i] = hub.issues.values()
    assert i["title"] == "Create the OAuth client secret" and "o/r#1" in i["body"]
    assert {l["name"] for l in i["labels"]} == {"loop", "kind:followup", "owner:human", "src:o/r"}


def test_a_step_is_never_filed_twice_even_with_lost_state(tmp_path):
    cfg, hub = setup(tmp_path), Hub()
    hub.prs[("o/r", 1)] = pr_state("closed", merged=True)
    run(tmp_path, cfg, hub, [card(["Rotate the key"])]); run(tmp_path, cfg, hub, [])
    (tmp_path / "loops-state.json").unlink()
    run(tmp_path, cfg, hub, [card(["Rotate  the key"])]); run(tmp_path, cfg, hub, [])
    assert titles(hub) == ["Rotate the key"]


def test_closed_without_merge_files_nothing(tmp_path):
    cfg, hub = setup(tmp_path), Hub()
    hub.prs[("o/r", 1)] = pr_state()
    run(tmp_path, cfg, hub, [card(["Rotate the key"])])
    hub.prs[("o/r", 1)] = pr_state("closed")
    run(tmp_path, cfg, hub, [])
    hub.prs[("o/r", 1)] = pr_state("closed", merged=True)
    run(tmp_path, cfg, hub, [])
    assert titles(hub) == []


def test_a_loops_block_in_the_pr_body_is_filed_too(tmp_path):
    cfg, hub = setup(tmp_path), Hub()
    hub.prs[("o/r", 1)] = pr_state()
    run(tmp_path, cfg, hub, [card([])])
    hub.prs[("o/r", 1)] = pr_state("closed", merged=True,
                                   body="Adds X.\n\n```loops\n- Run the e2e test on a throwaway repo\n- \n```\n")
    run(tmp_path, cfg, hub, [])
    assert titles(hub) == ["Run the e2e test on a throwaway repo"]


def test_snapshot_orders_overdue_blocked_due_then_oldest_and_hides_snoozed(tmp_path):
    cfg, hub = setup(tmp_path), Hub()
    lp = Loops(hub, REPO)
    lp.add("plain old"); lp.add("due later", due="2026-10-20"); lp.add("blocked", kind="blocked")
    lp.add("overdue", due="2026-10-01")
    lp.snooze(lp.add("snoozed")[0].number, "2026-10-08")
    run(tmp_path, cfg, hub, [])
    assert [x["title"] for x in snapshot(cfg)] == ["overdue", "blocked", "due later", "plain old"]
    run(tmp_path, cfg, hub, [], today="2026-10-08")             # the snooze ran out
    assert "snoozed" in [x["title"] for x in snapshot(cfg)]


def test_gui_requests_are_applied_and_non_loops_refused(tmp_path):
    cfg, hub = setup(tmp_path), Hub()
    lp = Loops(hub, REPO)
    a, b, c, d = (lp.add(t)[0].number for t in "abcd")
    bug = hub.create_issue(REPO, "config bug", "", ["bug"])["number"]
    (cfg.outbox / "loops").mkdir()
    for i, (action, n) in enumerate([("close", a), ("drop", b), ("snooze", c), ("agent", d), ("close", bug),
                                     ("explode", a)]):
        (cfg.outbox / "loops" / f"{i}.json").write_text(json.dumps({"action": action, "number": n}))
    run(tmp_path, cfg, hub, [])
    get = lambda n: hub.issues[(REPO, n)]  # noqa: E731
    assert get(a)["state_reason"] == "completed" and get(b)["state_reason"] == "not_planned"
    assert "until:2026-10-05" in get(c)["body"]
    assert "owner:agent" in {l["name"] for l in get(d)["labels"]}
    assert get(bug)["state"] == "open"
    assert list((cfg.outbox / "loops").glob("*.json")) == []
