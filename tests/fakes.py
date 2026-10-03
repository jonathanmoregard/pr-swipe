"""In-memory GitHub with the same method surface as pr_swipe.github.GitHub."""
from pr_swipe.github import GitHubError

PLAIN = (
    "diff --git a/src/util.py b/src/util.py\n--- a/src/util.py\n+++ b/src/util.py\n"
    "@@ -1,3 +1,3 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n"
)


class FakeGitHub:
    def __init__(self):
        self.prs = {}          # (repo, n) -> dict
        self.diffs = {}        # (repo, n) -> str
        self.ci = {}           # (repo, sha) -> dict
        self.calls = []
        self.merge_status = {}  # (repo, n) -> forced status
        self.on_update = None   # callable(repo, n) -> new head sha
        self.repos = {}

    def add_pr(self, repo, n, sha, author="jonathanmoregard", diff=PLAIN, mergeable_state="clean",
               title="Fix", body="", base_sha="b" * 40):
        self.prs[(repo, n)] = {
            "number": n, "state": "open", "title": title, "body": body,
            "html_url": f"https://github.com/{repo}/pull/{n}",
            "user": {"login": author},
            "head": {"sha": sha, "ref": f"feat-{n}"},
            "base": {"ref": "main", "sha": base_sha},
            "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-02T00:00:00Z",
            "mergeable_state": mergeable_state, "merged": False,
        }
        self.diffs[(repo, n)] = diff
        self.ci.setdefault((repo, sha), {"state": "success", "failing": []})

    def search_open_prs(self, user):
        return [{"repo": r, "number": n} for (r, n), p in self.prs.items() if p["state"] == "open"]

    def pr(self, repo, n):
        if (repo, n) not in self.prs:
            raise GitHubError(404, "nf")
        return dict(self.prs[(repo, n)])

    def pr_diff(self, repo, n):
        d = self.diffs[(repo, n)]
        if isinstance(d, Exception):
            raise d
        return d

    def pr_commits(self, repo, n):
        return [{"commit": {"message": "commit msg", "author": {"date": "2026-09-30T00:00:00Z"}}}]

    def issue_comments(self, repo, n):
        return []

    def list_prs(self, repo, state):
        return [dict(p) for (r, _), p in self.prs.items() if r == repo and (state == "all" or p["state"] == state)]

    def repo(self, repo):
        return self.repos.get(repo, {"allow_squash_merge": True, "allow_rebase_merge": True})

    def ci_state(self, repo, sha):
        return self.ci.get((repo, sha), {"state": "none", "failing": []})

    def update_branch(self, repo, n, expected_head):
        self.calls.append(("update_branch", repo, n, expected_head))
        p = self.prs[(repo, n)]
        if p["head"]["sha"] != expected_head:
            raise GitHubError(422, "expected head mismatch")
        if self.on_update:
            new = self.on_update(repo, n)
            p["head"]["sha"] = new
            p["mergeable_state"] = "clean"
        return 202, {}

    def merge(self, repo, n, sha, method):
        self.calls.append(("merge", repo, n, sha, method))
        forced = self.merge_status.get((repo, n))
        if forced:
            return forced, {}
        p = self.prs[(repo, n)]
        if p["head"]["sha"] != sha:
            return 409, {"message": "Head branch was modified"}
        p["state"], p["merged"] = "closed", True
        return 200, {"sha": "c" * 40, "merged": True}

    def close(self, repo, n):
        self.calls.append(("close", repo, n))
        self.prs[(repo, n)]["state"] = "closed"
        return 200, {}

    def comment(self, repo, n, body):
        self.calls.append(("comment", repo, n, body))
        return 201, {}
