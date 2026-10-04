"""Apply the merge-gate ruleset to the default branch of every repo the user owns.

Only the merge-gate GitHub App may update, delete or force-push a default branch; everyone else
(the user's agents included) must go through a PR that pr-swipe merges. Run by the human with a
short-lived admin token typed at a hidden prompt: agents hold no credential that can change rulesets.
Dry run unless --apply.
"""
import argparse
import getpass
import os
import sys
import urllib.request

from .github import API, GitHubError, _check_repo, http

NAME = "pr-swipe merge gate"
COMPARED = ("name", "target", "enforcement", "conditions", "rules", "bypass_actors")


def gate_ruleset(app_id) -> dict:
    return {
        "name": NAME, "target": "branch", "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}, {"type": "update"}],
        "bypass_actors": [{"actor_id": int(app_id), "actor_type": "Integration", "bypass_mode": "always"}],
    }


def _norm(rs) -> dict:
    out = {k: rs.get(k) for k in COMPARED}
    out["rules"] = sorted(({"type": r["type"], **({"parameters": r["parameters"]} if r.get("parameters") else {})}
                           for r in out["rules"] or []), key=lambda r: r["type"])
    out["bypass_actors"] = sorted(out["bypass_actors"] or [], key=lambda a: (a["actor_type"], a["actor_id"]))
    return out


def owned_repos(auth, opener, api=API) -> list:
    repos, page = [], 1
    while True:
        _, batch = http(opener, "GET", f"{api}/user/repos?affiliation=owner&per_page=100&page={page}", auth)
        if not batch:
            return repos
        repos += [r for r in batch if not r["archived"]]
        page += 1


def plan(token, app_id, opener=urllib.request.urlopen, api=API) -> list:
    auth, want = f"Bearer {token}", _norm(gate_ruleset(app_id))
    out = []
    for r in owned_repos(auth, opener, api):
        name, branch = r["full_name"], r["default_branch"]
        _check_repo(name)
        _, existing = http(opener, "GET", f"{api}/repos/{name}/rulesets?includes_parents=false", auth)
        match = next((e for e in existing if e["name"] == NAME), None)
        if match is None:
            action, rid = "create", None
        else:
            _, full = http(opener, "GET", f"{api}/repos/{name}/rulesets/{match['id']}", auth)
            action, rid = ("noop" if _norm(full) == want else "update"), match["id"]
        try:
            http(opener, "GET", f"{api}/repos/{name}/branches/{branch}/protection", auth)
            legacy = True
        except GitHubError as e:
            if e.status not in (403, 404):
                raise
            legacy = False
        out.append({"repo": name, "branch": branch, "action": action, "id": rid, "legacy_protection": legacy})
    return out


def apply(token, app_id, steps, opener=urllib.request.urlopen, api=API) -> list:
    auth, body, results = f"Bearer {token}", gate_ruleset(app_id), []
    for s in steps:
        if s["action"] == "noop":
            continue
        _check_repo(s["repo"])
        method, url = (("POST", f"{api}/repos/{s['repo']}/rulesets") if s["action"] == "create"
                       else ("PUT", f"{api}/repos/{s['repo']}/rulesets/{int(s['id'])}"))
        try:
            http(opener, method, url, auth, body=body)
            results.append({"repo": s["repo"], "action": s["action"], "ok": True})
        except GitHubError as e:
            results.append({"repo": s["repo"], "action": s["action"], "ok": False, "error": str(e)})
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--app-id", type=int, default=int(os.environ.get("PR_SWIPE_APP_ID", "0")) or None,
                    required="PR_SWIPE_APP_ID" not in os.environ)
    ap.add_argument("--apply", action="store_true", help="make the changes (default: dry run)")
    a = ap.parse_args(argv)
    token = getpass.getpass("Short-lived admin token (input hidden): ").strip()
    steps = plan(token, a.app_id)
    for s in steps:
        legacy = "  [legacy branch protection present: remove it by hand after verifying]" if s["legacy_protection"] else ""
        print(f"{s['action']:6} {s['repo']} ({s['branch']}){legacy}")
    if not a.apply:
        print("dry run: re-run with --apply to make these changes")
        return 0
    failed = [r for r in apply(token, a.app_id, steps) if not r["ok"]]
    for r in failed:
        print(f"FAILED {r['action']} {r['repo']}: {r['error']}", file=sys.stderr)
    print(f"done: {sum(s['action'] != 'noop' for s in steps) - len(failed)} changed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
