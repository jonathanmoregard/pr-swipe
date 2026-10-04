from tests.fakes import FakeGitHub

def test_fake_merge_enforces_sha_pin():
    gh = FakeGitHub()
    gh.add_pr("o/r", 1, "a" * 40)
    assert gh.merge("o/r", 1, "d" * 40, "squash")[0] == 409
    assert gh.merge("o/r", 1, "a" * 40, "squash")[0] == 200
    assert gh.pr("o/r", 1)["state"] == "closed"


def test_fake_issues_filter_by_label_and_state():
    from tests.fakes import FakeIssues
    gh = FakeIssues()
    a = gh.create_issue("me/x", "a", "", ["loop"]); gh.create_issue("me/x", "b", "", ["other"])
    gh.edit_issue("me/x", a["number"], state="closed", state_reason="completed")
    assert [i["title"] for i in gh.list_issues("me/x", "loop")] == ["a"]
    assert gh.list_issues("me/x", "loop", "open") == []
