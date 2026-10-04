"""Auto-approve Dependabot version bumps in repos with CI. Runs in the executor (prswipe uid).

The owner's standing decision (2026-10-03): Dependabot bumps in repos with CI need no swipe.
Every input comes from GitHub through the App token, never from a card the user's uid wrote, and
each condition is one an agent pushing as the user cannot fake:
- the PR author is dependabot[bot] and every commit on it is authored by dependabot[bot] with a
  GitHub-verified signature, so no one else's commit rides along on the bump branch;
- the diff only touches dependency manifests and lockfiles, or workflow `uses:` lines (action
  bumps); no install-script line, no invisible characters, no renames;
- CI reported at least one check on that head and all of it passed ("repo with CI").
A bump that qualifies joins the ordinary merge train, which updates the branch, waits for CI again
and merges with the head pinned. A head is tried once: if the train returns it, it goes to the deck.
"""
import contextlib
import json
import re
from pathlib import Path

from . import analysis
from .card import write_json_atomic

BOT = "dependabot[bot]"
_DIFF_GIT = re.compile(r"^diff --git a/(.+?) b/(.+)$")
_WORKFLOW = re.compile(r"(^|/)\.github/workflows/[^/]+\.ya?ml$")
_USES = re.compile(r"^\s*(-\s+)?uses:\s*\S+(\s+#.*)?$")
DEP_FILE = re.compile(
    r"(^|/)(package(-lock)?\.json|npm-shrinkwrap\.json|pnpm-lock\.yaml|yarn\.lock|Cargo\.(toml|lock)"
    r"|pyproject\.toml|poetry\.lock|uv\.lock|Pipfile(\.lock)?|requirements[^/]*\.txt|go\.(mod|sum)"
    r"|Gemfile(\.lock)?|flake\.lock|elm\.json)$")


def diff_is_bump_only(diff) -> str:
    """'' when the diff is only dependency-version churn, else why not."""
    for line in diff.splitlines():
        m = _DIFF_GIT.match(line)
        if m and m.group(1) != m.group(2):
            return f"rename {m.group(1)} -> {m.group(2)}"
    files = analysis.parse_diff(diff)
    if not files:
        return "empty diff"
    for f in files:
        if f.binary:
            return f"binary file {f.path}"
        changed = [l[1:] for h in f.hunks for l in h.lines if l[:1] in "+-"]
        if _WORKFLOW.search(f.path):
            if not all(_USES.match(l) for l in changed if l.strip()):
                return f"{f.path} changes more than `uses:` lines"
        elif not DEP_FILE.search(f.path):
            return f"{f.path} is not a dependency file"
        for rule, rx in analysis.LINE_RULES:
            if any(rx.search(l) for h in f.hunks for l in h.lines if l.startswith("+")):
                return f"{rule} line in {f.path}"
    if analysis.hidden_in_diff(files):
        return "invisible characters in the diff"
    return ""


class AutoBump:
    def __init__(self, gh, audit, train, installed, targets, state_path, lock=None):
        """`targets()` yields (repo, number) to consider; `installed(repo)` says the App covers it.
        `lock` guards the train: the checks run without it, only the enqueue takes it."""
        self.gh, self.audit, self.train, self.installed, self.targets = gh, audit, train, installed, targets
        self.lock = lock or contextlib.nullcontext()
        self.state_path = Path(state_path)
        self.tried = set(json.loads(self.state_path.read_text())["tried"]) if self.state_path.exists() else set()

    def step(self):
        for repo, n in self.targets():
            if f"{repo}#{n}" in self.train.queued() or not self.installed(repo):
                continue
            pr = self.gh.pr(repo, n)
            head = pr["head"]["sha"]
            key = f"{repo}#{n}@{head}"
            if pr.get("state") != "open" or (pr.get("user") or {}).get("login") != BOT or key in self.tried:
                continue
            why = self._why_not(repo, n, head)
            if why == "wait":
                continue
            self.tried.add(key)
            write_json_atomic(self.state_path.parent, self.state_path.name, {"tried": sorted(self.tried)}, mode=0o600)
            if why:
                self.audit.append("auto-skip", repo=repo, number=n, head_sha=head, reason=why)
                continue
            with self.lock:
                self.audit.append("auto-approve", repo=repo, number=n, head_sha=head)
                self.train.enqueue({"repo": repo, "number": n, "head_sha": head, "card_sha256": "auto-bump"})

    def _why_not(self, repo, n, head):
        for c in self.gh.pr_commits(repo, n):
            if (c.get("author") or {}).get("login") != BOT:
                return "a commit not authored by dependabot"
            if not ((c.get("commit") or {}).get("verification") or {}).get("verified"):
                return "an unsigned commit"
        why = diff_is_bump_only(self.gh.pr_diff(repo, n))
        if why:
            return why
        ci = self.gh.ci_state(repo, head)["state"]
        if ci == "pending":
            return "wait"
        if ci != "success":
            return {"none": "no CI on this repo", "unknown": "can't read CI checks"}.get(ci, "CI failed")
        return ""
