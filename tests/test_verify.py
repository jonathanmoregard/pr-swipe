import json
from pr_swipe import verify as V
from tests.fakes import FakeGitHub
from tests.gitrepo import make_origin, git

R = "jonathanmoregard/x"


class Tokens:
    def __init__(self, installed=True): self._i = installed
    def installed(self, repo): return self._i
    def token(self, repo=None): return "t"


def setup(tmp_path, installed=True, card_head=None, api_head=None):
    (tmp_path / "o").mkdir()
    o = make_origin(tmp_path / "o")
    gh = FakeGitHub()
    gh.add_pr(R, o["number"], api_head or o["head"], base_sha=o["base"], title="Real title", body="real body")
    inbox = tmp_path / "inbox"; inbox.mkdir()
    (inbox / "c.json").write_text(json.dumps({"repo": R, "number": o["number"], "head_sha": card_head or o["head"]}))
    v = V.Verifier(gh, Tokens(installed), git_root=tmp_path / "git", verified_dir=tmp_path / "verified",
                   inbox=inbox, fetch_url=lambda repo: f"file://{o['origin']}", clock=lambda: 50.0)
    return o, gh, v


def test_verifies_from_the_origin_and_writes_a_record(tmp_path):
    o, gh, v = setup(tmp_path)
    v.step()
    rec = V.load_record(tmp_path / "verified", R, o["number"])
    assert rec["head_sha"] == o["head"] and rec["merge_base"] == o["base"]
    assert rec["title"] == "Real title" and rec["body"] == "real body" and rec["ci"]["state"] == "success"
    assert V.is_verified(tmp_path / "verified", R, o["number"], o["head"])
    m = V.mirror_path(tmp_path / "git", R)
    assert git(m, "config", "transfer.fsckObjects") == "true"
    assert git(m, "rev-parse", f"refs/prs/{o['number']}") == o["head"]


def test_api_head_not_matching_the_fetched_ref_writes_no_record(tmp_path):
    o, gh, v = setup(tmp_path, api_head="c" * 40)   # GitHub says one head, the ref holds another
    v.step()
    assert V.load_record(tmp_path / "verified", R, o["number"]) is None


def test_a_forged_card_head_never_becomes_verified(tmp_path):
    o, gh, v = setup(tmp_path, card_head="d" * 40)
    v.step()
    assert not V.is_verified(tmp_path / "verified", R, o["number"], "d" * 40)
    assert V.is_verified(tmp_path / "verified", R, o["number"], o["head"])   # truth comes from GitHub


def test_uninstalled_and_closed_prs_are_skipped(tmp_path):
    o, gh, v = setup(tmp_path, installed=False)
    v.step()
    assert V.load_record(tmp_path / "verified", R, o["number"]) is None
    v.tokens = Tokens(True)
    gh.prs[(R, o["number"])]["state"] = "closed"
    v.step()
    assert V.load_record(tmp_path / "verified", R, o["number"]) is None


def test_junk_in_the_inbox_is_ignored(tmp_path):
    o, gh, v = setup(tmp_path)
    (v.inbox / "bad.json").write_text("{not json")
    (v.inbox / "evil.json").write_text(json.dumps({"repo": "../../etc", "number": 1}))
    v.step()
    assert V.is_verified(tmp_path / "verified", R, o["number"], o["head"])
