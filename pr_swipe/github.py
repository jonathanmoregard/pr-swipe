"""Minimal GitHub REST client. Repo names are validated before they reach a URL."""
import base64
import calendar
import json
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

import jwt

API = "https://api.github.com"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
JSON = "application/vnd.github+json"
DIFF = "application/vnd.github.diff"
FAILED = {"failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"}


def git_auth_header(token):
    """http.extraheader value for git over HTTPS. GitHub's git endpoint rejects `bearer`; it takes
    basic auth as user x-access-token, for App installation tokens and user tokens alike."""
    return "AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()


class GitHubError(Exception):
    def __init__(self, status, body):
        super().__init__(f"GitHub HTTP {status}: {str(body)[:300]}")
        self.status, self.body = status, body


def _check_repo(repo):
    if not REPO_RE.fullmatch(repo or "") or ".." in repo:
        raise ValueError(f"bad repo name: {repo!r}")


def _check_sha(sha):
    if not SHA_RE.fullmatch(sha or ""):
        raise ValueError(f"bad sha: {sha!r}")


def http(opener, method, url, auth, body=None, accept=JSON, ok=(200, 201, 202, 204)):
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": auth, "Accept": accept, "User-Agent": "pr-swipe",
                 "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
    try:
        with opener(req, timeout=30) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    text = raw.decode("utf-8", "replace")
    data = json.loads(text) if text and accept == JSON else text
    if status not in ok:
        raise GitHubError(status, data)
    return status, data


class StaticToken:
    def __init__(self, token):
        self._t = token

    def token(self, repo=None):
        return self._t


class GhCliToken:
    """The user's own `gh` token (collector side, read-only once phase 2 lands)."""

    def __init__(self):
        self._t = None

    def token(self, repo=None):
        if self._t is None:
            self._t = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True,
                                     check=True).stdout.strip()
        return self._t


class AppTokens:
    """Installation tokens for the merge-gate GitHub App, minted per installation, cached."""

    def __init__(self, app_id, key_pem, opener=urllib.request.urlopen, api=API,
                 clock=time.time, signer=None):
        self.app_id, self.key, self.opener, self.api, self.clock = str(app_id), key_pem, opener, api, clock
        self._sign = signer or self._jwt
        self._inst, self._tok = {}, {}

    def _jwt(self):
        now = int(self.clock())
        return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": self.app_id}, self.key, algorithm="RS256")

    def installation_id(self, repo):
        _check_repo(repo)
        if repo not in self._inst:
            try:
                _, d = http(self.opener, "GET", f"{self.api}/repos/{repo}/installation",
                            f"Bearer {self._sign()}")
                self._inst[repo] = d["id"]
            except GitHubError as e:
                if e.status != 404:
                    raise
                self._inst[repo] = None
        return self._inst[repo]

    def installed(self, repo) -> bool:
        return self.installation_id(repo) is not None

    def token(self, repo=None):
        inst = self.installation_id(repo)
        if inst is None:
            raise GitHubError(404, f"merge-gate App not installed on {repo}")
        tok, exp = self._tok.get(inst, (None, 0))
        if tok is None or exp - self.clock() < 300:
            _, d = http(self.opener, "POST", f"{self.api}/app/installations/{inst}/access_tokens",
                        f"Bearer {self._sign()}")
            tok = d["token"]
            exp = calendar.timegm(time.strptime(d["expires_at"], "%Y-%m-%dT%H:%M:%SZ"))
            self._tok[inst] = (tok, exp)
        return tok


def summarize_ci(runs, statuses) -> dict:
    failing, pending, seen = [], False, False
    for r in runs:
        seen = True
        if r["status"] != "completed":
            pending = True
        elif r.get("conclusion") in FAILED:
            failing.append(r["name"])
    for s in statuses:
        seen = True
        if s["state"] == "pending":
            pending = True
        elif s["state"] in ("failure", "error"):
            failing.append(s["context"])
    if failing:
        return {"state": "failure", "failing": sorted(set(failing))}
    if pending:
        return {"state": "pending", "failing": []}
    return {"state": "success" if seen else "none", "failing": []}


class GitHub:
    def __init__(self, tokens, opener=urllib.request.urlopen, api=API):
        self.tokens, self.opener, self.api = tokens, opener, api

    def _req(self, method, path, repo=None, body=None, accept=JSON, ok=(200, 201, 202, 204)):
        if repo is not None:
            _check_repo(repo)
        return http(self.opener, method, self.api + path, f"Bearer {self.tokens.token(repo)}",
                    body=body, accept=accept, ok=ok)

    def _pages(self, path, repo=None, key=None, limit=10):
        out = []
        sep = "&" if "?" in path else "?"
        for page in range(1, limit + 1):
            _, d = self._req("GET", f"{path}{sep}per_page=100&page={page}", repo=repo)
            items = d[key] if key else d
            out += items
            if len(items) < 100:
                break
        return out

    # --- reads ---
    def search_open_prs(self, user) -> list:
        found = {}
        for q in (f"is:pr is:open user:{user}", f"is:pr is:open author:{user}"):
            path = "/search/issues?q=" + urllib.parse.quote(q)
            for it in self._pages(path, key="items"):
                repo = it["repository_url"].split("/repos/", 1)[1]
                found[(repo, it["number"])] = {"repo": repo, "number": it["number"]}
        return list(found.values())

    def pr(self, repo, n):
        return self._req("GET", f"/repos/{repo}/pulls/{int(n)}", repo=repo)[1]

    def pr_diff(self, repo, n) -> str:
        return self._req("GET", f"/repos/{repo}/pulls/{int(n)}", repo=repo, accept=DIFF)[1]

    def pr_commits(self, repo, n):
        return self._pages(f"/repos/{repo}/pulls/{int(n)}/commits", repo=repo, limit=3)

    def issue_comments(self, repo, n):
        return self._pages(f"/repos/{repo}/issues/{int(n)}/comments", repo=repo, limit=3)

    def list_prs(self, repo, state):
        return self._pages(f"/repos/{repo}/pulls?state={state}&sort=updated&direction=desc",
                           repo=repo, limit=1)

    def repo(self, repo):
        return self._req("GET", f"/repos/{repo}", repo=repo)[1]

    def ci_state(self, repo, sha) -> dict:
        _check_sha(sha)
        runs = []
        try:
            runs = self._req("GET", f"/repos/{repo}/commits/{sha}/check-runs?per_page=100", repo=repo)[1]["check_runs"]
        except GitHubError as e:
            if e.status not in (403, 404):
                raise
        statuses = self._req("GET", f"/repos/{repo}/commits/{sha}/status", repo=repo)[1].get("statuses", [])
        return summarize_ci(runs, statuses)

    # --- writes (executor only; its token is the only one that can do these) ---
    def update_branch(self, repo, n, expected_head):
        _check_sha(expected_head)
        return self._req("PUT", f"/repos/{repo}/pulls/{int(n)}/update-branch", repo=repo,
                         body={"expected_head_sha": expected_head})

    def merge(self, repo, n, sha, method):
        _check_sha(sha)
        if method not in ("merge", "squash", "rebase"):
            raise ValueError(method)
        return self._req("PUT", f"/repos/{repo}/pulls/{int(n)}/merge", repo=repo,
                         body={"sha": sha, "merge_method": method}, ok=(200, 405, 409))

    def close(self, repo, n):
        return self._req("PATCH", f"/repos/{repo}/pulls/{int(n)}", repo=repo, body={"state": "closed"})

    def comment(self, repo, n, body):
        return self._req("POST", f"/repos/{repo}/issues/{int(n)}/comments", repo=repo, body={"body": body})
