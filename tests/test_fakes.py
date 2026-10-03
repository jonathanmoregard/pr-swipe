from tests.fakes import FakeGitHub

def test_fake_merge_enforces_sha_pin():
    gh = FakeGitHub()
    gh.add_pr("o/r", 1, "a" * 40)
    assert gh.merge("o/r", 1, "d" * 40, "squash")[0] == 409
    assert gh.merge("o/r", 1, "a" * 40, "squash")[0] == 200
    assert gh.pr("o/r", 1)["state"] == "closed"
