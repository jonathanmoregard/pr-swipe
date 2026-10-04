import json
from pr_swipe import rulesets as RS
from tests.test_github import Opener

API = "https://api.github.com"
APP = 5173130


def repos_route(*repos):
    return {("GET", f"{API}/user/repos?affiliation=owner&per_page=100&page=1"): (200, list(repos)),
            ("GET", f"{API}/user/repos?affiliation=owner&per_page=100&page=2"): (200, [])}


def repo(name, archived=False, branch="main"):
    return {"full_name": f"me/{name}", "archived": archived, "default_branch": branch}


def test_gate_ruleset_blocks_everyone_but_the_app():
    g = RS.gate_ruleset(APP)
    assert g["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]
    assert {r["type"] for r in g["rules"]} == {"deletion", "non_fast_forward", "update"}
    assert g["bypass_actors"] == [{"actor_id": APP, "actor_type": "Integration", "bypass_mode": "always"}]


def test_plan_creates_missing_updates_drifted_skips_archived_and_flags_legacy_protection():
    routes = repos_route(repo("a"), repo("b"), repo("old", archived=True))
    routes.update({
        ("GET", f"{API}/repos/me/a/rulesets?includes_parents=false"): (200, []),
        ("GET", f"{API}/repos/me/a/branches/main/protection"): (200, {"enforce_admins": {"enabled": False}}),
        ("GET", f"{API}/repos/me/b/rulesets?includes_parents=false"): (200, [{"id": 7, "name": RS.NAME}]),
        ("GET", f"{API}/repos/me/b/rulesets/7"): (200, {**RS.gate_ruleset(APP), "rules": [{"type": "deletion"}]}),
        ("GET", f"{API}/repos/me/b/branches/main/protection"): (404, {"message": "Branch not protected"}),
    })
    plan = RS.plan("t", APP, opener=Opener(routes))
    assert [(p["repo"], p["action"]) for p in plan] == [("me/a", "create"), ("me/b", "update")]
    assert plan[0]["legacy_protection"] is True and plan[1]["legacy_protection"] is False


def test_plan_is_noop_when_ruleset_matches():
    routes = repos_route(repo("a"))
    routes.update({
        ("GET", f"{API}/repos/me/a/rulesets?includes_parents=false"): (200, [{"id": 3, "name": RS.NAME}]),
        ("GET", f"{API}/repos/me/a/rulesets/3"): (200, {**RS.gate_ruleset(APP), "id": 3, "source": "me/a"}),
        ("GET", f"{API}/repos/me/a/branches/main/protection"): (404, {}),
    })
    assert RS.plan("t", APP, opener=Opener(routes))[0]["action"] == "noop"


def test_apply_sends_create_and_update_and_reports_errors_per_repo():
    routes = {
        ("POST", f"{API}/repos/me/a/rulesets"): (201, {"id": 1}),
        ("PUT", f"{API}/repos/me/b/rulesets/7"): (200, {"id": 7}),
        ("POST", f"{API}/repos/me/c/rulesets"): (403, {"message": "Upgrade to GitHub Pro"}),
    }
    op = Opener(routes)
    plan = [{"repo": "me/a", "action": "create", "id": None}, {"repo": "me/b", "action": "update", "id": 7},
            {"repo": "me/c", "action": "create", "id": None}, {"repo": "me/d", "action": "noop", "id": 2}]
    results = RS.apply("t", APP, plan, opener=op)
    assert [r["ok"] for r in results] == [True, True, False]
    assert "Upgrade" in results[2]["error"]
    assert json.loads(op.calls[0].data)["name"] == RS.NAME
