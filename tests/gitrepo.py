"""Real git repos for tests: an origin with a base branch and a PR ref."""
import os
import subprocess
from pathlib import Path

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


def git(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True, capture_output=True,
                          text=True).stdout.strip()


def make_origin(tmp: Path, number=7, files=None):
    """Work repo with main + a PR branch; a bare origin exposing refs/pull/N/head. Returns dict."""
    work, origin = tmp / "work", tmp / "origin.git"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    (work / "README.md").write_text("hello\n")
    git(work, "add", "-A"); git(work, "commit", "-qm", "base")
    base = git(work, "rev-parse", "HEAD")
    git(work, "switch", "-qc", "pr")
    for path, text in (files or {"app.py": "print('hi')\n"}).items():
        p = work / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(work, "add", "-A"); git(work, "commit", "-qm", "feat: change")
    head = git(work, "rev-parse", "HEAD")
    git(tmp, "init", "-q", "--bare", str(origin))
    git(work, "push", "-q", str(origin), "main", f"pr:refs/pull/{number}/head")
    return {"work": work, "origin": origin, "base": base, "head": head, "number": number}
