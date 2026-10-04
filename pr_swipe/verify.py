"""Verifier: turns untrusted inbox cards into SHA-addressed evidence fetched from GitHub.

Runs in the executor process as prswipe with the merge-gate App token. From a card it reads only
`repo` and `number` (validated); everything else comes from the GitHub API and from git objects
fetched into a prswipe-owned bare mirror with transfer.fsckObjects (index-pack re-hashes every
object). The GUI shows a card only if its head SHA matches the record written here.
"""
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

from .card import write_json_atomic
from .github import REPO_RE, GitHubError, git_auth_header
from .gitview import GitView

log = logging.getLogger("pr-swipe.verify")
SHA = re.compile(r"^[0-9a-f]{40}$")
REF = re.compile(r"^(?!.*\.\.)(?!/)(?!.*//)[A-Za-z0-9._/-]{1,200}(?<!/)(?<!\.lock)$")
FETCH_TIMEOUT = 300


def _repo_ok(repo):
    return isinstance(repo, str) and bool(REPO_RE.fullmatch(repo)) and ".." not in repo


def mirror_path(git_root, repo) -> Path:
    return Path(git_root) / (repo.replace("/", "__") + ".git")


def _record_path(verified_dir, repo, n) -> Path:
    return Path(verified_dir) / f"{repo.replace('/', '__')}__{int(n)}.json"


def load_record(verified_dir, repo, n):
    p = _record_path(verified_dir, repo, n)
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def is_verified(verified_dir, repo, n, head_sha) -> bool:
    rec = load_record(verified_dir, repo, n) if _repo_ok(repo) else None
    return bool(rec) and rec.get("head_sha") == head_sha


class Verifier:
    def __init__(self, gh, tokens, git_root, verified_dir, inbox,
                 fetch_url=lambda repo: f"https://github.com/{repo}.git", clock=time.time):
        self.gh, self.tokens, self.clock = gh, tokens, clock
        self.git_root, self.verified_dir, self.inbox = Path(git_root), Path(verified_dir), Path(inbox)
        self.fetch_url = fetch_url

    def targets(self):
        seen = set()
        for p in sorted(self.inbox.glob("*.json")):
            try:
                c = json.loads(p.read_text())
                repo, n = c["repo"], c["number"]
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if _repo_ok(repo) and isinstance(n, int) and n > 0 and (repo, n) not in seen:
                seen.add((repo, n))
                yield repo, n

    def step(self):
        for repo, n in self.targets():
            try:
                self._verify(repo, n)
            except (GitHubError, subprocess.SubprocessError, OSError, ValueError) as e:
                log.warning("verify %s#%s: %s", repo, n, e)

    def _verify(self, repo, n):
        if not self.tokens.installed(repo):
            return
        pr = self.gh.pr(repo, n)
        if pr.get("state") != "open":
            return
        head, base_ref = pr["head"]["sha"], pr["base"]["ref"]
        if not SHA.fullmatch(head) or not REF.fullmatch(base_ref):
            raise ValueError(f"unexpected head/base from API: {head!r} {base_ref!r}")
        rec = load_record(self.verified_dir, repo, n)
        if rec and rec.get("head_sha") == head:
            rec.update(ci=self.gh.ci_state(repo, head), title=pr.get("title", ""), body=pr.get("body") or "",
                       refreshed_at=self.clock())
            self._write(repo, n, rec)
            return
        mirror = self._fetch(repo, n, base_ref)
        fetched = self._git(mirror, "rev-parse", f"refs/prs/{n}").strip()
        if fetched != head:
            log.warning("verify %s#%s: ref holds %s, API says %s; not recording", repo, n, fetched, head)
            return
        base_tip = self._git(mirror, "rev-parse", f"refs/base/{base_ref}").strip()
        comments = [{"author": (c.get("user") or {}).get("login", "?"), "body": c.get("body") or ""}
                    for c in self.gh.issue_comments(repo, n)]
        self._write(repo, n, {
            "repo": repo, "number": n, "head_sha": head, "base_ref": base_ref, "base_sha": base_tip,
            "merge_base": GitView(mirror).merge_base(base_tip, head),
            "title": pr.get("title", ""), "body": pr.get("body") or "", "comments": comments,
            "ci": self.gh.ci_state(repo, head), "verified_at": self.clock(),
        })

    def _write(self, repo, n, rec):
        self.verified_dir.mkdir(parents=True, exist_ok=True)
        p = _record_path(self.verified_dir, repo, n)
        write_json_atomic(p.parent, p.name, rec, mode=0o640)

    def _git(self, mirror, *args, env=None):
        base_env = {"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_TERMINAL_PROMPT": "0", "HOME": "/nonexistent"}
        return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                               f"--git-dir={mirror}", *args], env={**base_env, **(env or {})},
                              capture_output=True, check=True, text=True, timeout=FETCH_TIMEOUT).stdout

    def _fetch(self, repo, n, base_ref) -> Path:
        mirror = mirror_path(self.git_root, repo)
        if not mirror.exists():
            self.git_root.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "-q", "--bare", str(mirror)], check=True, capture_output=True,
                           env={"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_GLOBAL": "/dev/null",
                                "GIT_CONFIG_SYSTEM": "/dev/null", "HOME": "/nonexistent"})
            for k, v in (("transfer.fsckObjects", "true"), ("fetch.fsckObjects", "true"),
                         ("core.hooksPath", "/dev/null"), ("gc.auto", "0")):
                self._git(mirror, "config", k, v)
        url = self.fetch_url(repo)
        env = {}
        if url.startswith("https://"):  # token via env-provided config, never argv
            env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                   "GIT_CONFIG_VALUE_0": git_auth_header(self.tokens.token(repo))}
        self._git(mirror, "fetch", "--no-tags", "--quiet", url,
                  f"+refs/pull/{int(n)}/head:refs/prs/{int(n)}",
                  f"+refs/heads/{base_ref}:refs/base/{base_ref}", env=env)
        return mirror
