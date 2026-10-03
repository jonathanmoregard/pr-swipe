"""Read-only views over a verified bare mirror. Runs as prswipe; no network.

Every revision must be a full SHA and every path is passed after `--`, so nothing from a card or a
PR can become a git option. Diffs use --no-ext-diff --no-textconv: a PR's .gitattributes cannot
invoke a diff driver, and the mirror's own hooks and fsmonitor are disabled.
"""
import os
import re
import subprocess
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")
SHOW_MAX = 512 * 1024
SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "diff.external=",
        "-c", "core.pager=cat", "-c", "color.ui=false"]


def _sha(s):
    if not SHA.fullmatch(s or ""):
        raise ValueError(f"not a full sha: {s!r}")
    return s


def _path(p):
    if not p or p.startswith("-") or "\0" in p:
        raise ValueError(f"bad path: {p!r}")
    return p


class GitView:
    def __init__(self, git_dir):
        self.git_dir = Path(git_dir)
        self.env = {"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_TERMINAL_PROMPT": "0", "HOME": "/nonexistent"}

    def _git(self, *args, binary=False):
        r = subprocess.run(["git", "--no-pager", *SAFE, f"--git-dir={self.git_dir}", *args],
                           env=self.env, capture_output=True, check=True, timeout=60)
        return r.stdout if binary else r.stdout.decode("utf-8", "replace")

    def merge_base(self, a, b) -> str:
        return self._git("merge-base", _sha(a), _sha(b)).strip()

    def commits(self, base, head) -> list:
        out = self._git("log", "--no-decorate", "-z", "--format=%H%x1f%an%x1f%aI%x1f%s",
                        f"{_sha(base)}..{_sha(head)}", "--")
        rows = [r.split("\x1f") for r in out.split("\0") if r.strip()]
        return [{"sha": r[0].strip(), "author": r[1], "date": r[2], "subject": r[3]} for r in rows]

    def files(self, base, head) -> list:
        rng = (_sha(base), _sha(head))
        status = self._git("diff", "--no-ext-diff", "--no-textconv", "--name-status", "-z", "--no-renames", *rng, "--")
        parts = [p for p in status.split("\0") if p]
        st = {parts[i + 1]: parts[i] for i in range(0, len(parts) - 1, 2)}
        num = self._git("diff", "--no-ext-diff", "--no-textconv", "--numstat", "-z", "--no-renames", *rng, "--")
        out = []
        for rec in [r for r in num.split("\0") if r]:
            add, dele, path = rec.split("\t", 2)
            binary = add == "-"
            out.append({"path": path, "status": st.get(path, "M"), "binary": binary,
                        "additions": 0 if binary else int(add), "deletions": 0 if binary else int(dele)})
        return out

    def diff(self, base, head) -> str:
        return self._git("diff", "--no-ext-diff", "--no-textconv", "--no-renames", _sha(base), _sha(head), "--")

    def file_diff(self, base, head, path) -> str:
        return self._git("diff", "--no-ext-diff", "--no-textconv", "--no-renames", _sha(base), _sha(head),
                         "--", _path(path))

    def blob(self, rev, path, limit=SHOW_MAX):
        """Raw bytes of path at rev (capped), or None when the file does not exist there."""
        try:
            return self._git("cat-file", "blob", f"{_sha(rev)}:{_path(path)}", binary=True)[:limit]
        except subprocess.CalledProcessError:
            return None

    def show(self, head, path) -> str:
        data = self._git("cat-file", "blob", f"{_sha(head)}:{_path(path)}", binary=True)
        text = data[:SHOW_MAX].decode("utf-8", "replace")
        return text + ("\n[truncated]" if len(data) > SHOW_MAX else "")
