import pytest

from pr_swipe import loops as L
from tests.fakes import FakeIssues

REPO = "me/.claude"


def make(now=None):
    gh, t = FakeIssues(), {"now": 1000.0}
    return gh, L.Loops(gh, REPO, clock=now or (lambda: t["now"])), t


def labels(gh, n):
    return {l["name"] for l in gh.issues[(REPO, n)]["labels"]}


def test_add_files_a_labelled_issue_in_the_configured_repo_only():
    gh, lp, _ = make()
    loop, created = lp.add("Create the App key", body="needs the org owner", kind="followup", source="o/r",
                           due="2026-10-10")
    assert created and loop.number == 1 and {r for r, _ in gh.issues} == {REPO}
    assert labels(gh, 1) == {"loop", "kind:followup", "owner:human", "src:o/r"}
    assert (loop.kind, loop.owner, loop.source, loop.due, loop.body) == (
        "followup", "human", "o/r", "2026-10-10", "needs the org owner")
    assert L.KEY_RE.search(gh.issues[(REPO, 1)]["body"])


def test_add_returns_the_existing_loop_for_a_known_key_even_when_closed():
    gh, lp, _ = make()
    first, _ = lp.add("Rotate the key", source="o/r")
    lp.close(first.number)
    again, created = lp.add("rotate  the KEY", source="o/r")
    assert not created and again.number == first.number and len(gh.issues) == 1


def test_writes_to_an_issue_without_the_loop_label_are_refused():
    gh, lp, _ = make()
    other = gh.create_issue(REPO, "a config bug", "", ["bug"])
    for write in (lambda: lp.close(other["number"]), lambda: lp.snooze(other["number"], "2026-10-09"),
                  lambda: lp.set_owner(other["number"], "agent"), lambda: lp.note(other["number"], "hi")):
        with pytest.raises(L.LoopError):
            write()
    assert gh.issues[(REPO, other["number"])]["state"] == "open" and gh.comments == []


def test_size_caps_and_validation():
    _, lp, _ = make()
    for bad in (dict(title="x" * 121), dict(title="ok", body="x" * 4001), dict(title="ok", kind="x"),
                dict(title="ok", owner="boss"), dict(title="ok", due="soon"), dict(title=" "),
                dict(title="ok", source="not a repo")):
        with pytest.raises(L.LoopError):
            lp.add(**bad)


def test_rate_limit_refuses_the_31st_write_in_a_minute():
    _, lp, t = make()
    for i in range(30):
        lp.add(f"loop {i}")
    with pytest.raises(L.LoopError):
        lp.add("one too many")
    t["now"] += 61
    lp.add("after the window")


def test_snooze_sets_label_and_until_line_and_clearing_removes_them():
    gh, lp, _ = make()
    n = lp.add("Check the deploy", due="2026-10-05")[0].number
    lp.snooze(n, "2026-10-09")
    loop = L.from_issue(gh.issues[(REPO, n)])
    assert "snoozed" in labels(gh, n) and loop.until == "2026-10-09" and loop.due == "2026-10-05"
    lp.snooze(n, "")
    loop = L.from_issue(gh.issues[(REPO, n)])
    assert "snoozed" not in labels(gh, n) and loop.until == "" and loop.key


def test_close_done_vs_dropped_and_owner_handoff():
    gh, lp, _ = make()
    a, b = lp.add("a")[0].number, lp.add("b")[0].number
    lp.close(a); lp.close(b, done=False)
    assert gh.issues[(REPO, a)]["state_reason"] == "completed"
    assert gh.issues[(REPO, b)]["state_reason"] == "not_planned"
    c = lp.add("c")[0].number
    lp.set_owner(c, "agent")
    assert "owner:agent" in labels(gh, c) and "owner:human" not in labels(gh, c)


def test_list_returns_open_loops_only_by_default():
    gh, lp, _ = make()
    lp.add("open one"); lp.close(lp.add("done one")[0].number)
    gh.create_issue(REPO, "not a loop", "", ["bug"])
    assert [x.title for x in lp.list()] == ["open one"]
    assert {x.title for x in lp.list("all")} == {"open one", "done one"}


def test_a_repeat_add_right_after_creation_is_deduped_despite_a_lagging_list():
    gh, lp, _ = make()
    gh.list_issues = lambda repo, label, state="all": []    # GitHub had not indexed the new issue yet
    first, _ = lp.add("Rotate the key", key="o/r#1:rotate")
    again, created = lp.add("Rotate the key", key="o/r#1:rotate")
    assert not created and again.number == first.number and len(gh.issues) == 1
