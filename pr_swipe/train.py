"""Per-repo merge train. Ordinary software, no model: update branch, wait for CI, check the PR's
own diff is unchanged since approval, merge with the head SHA pinned."""
import json
import time
from pathlib import Path

from . import analysis
from .card import write_json_atomic
from .github import GitHubError

CI_TIMEOUT = 3600
CI_GRACE = 120        # a fresh head with no CI reported yet is not "no CI"
UPDATE_TIMEOUT = 600
MAX_ATTEMPTS = 6


class Train:
    def __init__(self, gh, audit, state_path, returns_dir, clock=time.time):
        self.gh, self.audit, self.clock = gh, audit, clock
        self.state_path, self.returns_dir = Path(state_path), Path(returns_dir)
        self.queues = json.loads(self.state_path.read_text())["queues"] if self.state_path.exists() else {}
        self._methods = {}

    # --- persistence / views ---
    def _save(self):
        write_json_atomic(self.state_path.parent, self.state_path.name, {"queues": self.queues}, mode=0o600)

    def queued(self) -> set:
        return {f"{it['repo']}#{it['number']}" for q in self.queues.values() for it in q}

    def _method(self, repo):
        if repo not in self._methods:
            r = self.gh.repo(repo)
            self._methods[repo] = ("squash" if r.get("allow_squash_merge", True)
                                   else "rebase" if r.get("allow_rebase_merge") else "merge")
        return self._methods[repo]

    def _pop(self, it):
        self.queues[it["repo"]] = [x for x in self.queues[it["repo"]] if x["number"] != it["number"]]
        if not self.queues[it["repo"]]:
            del self.queues[it["repo"]]
        self._save()

    def _return(self, it, reason, head):
        note = {"repo": it["repo"], "number": it["number"], "head_sha": head, "reason": reason, "ts": self.clock()}
        name = f"{it['repo'].replace('/', '__')}__{it['number']}__{int(self.clock() * 1000)}.json"
        write_json_atomic(self.returns_dir, name, note, mode=0o640)
        self.audit.append("returned", repo=it["repo"], number=it["number"], head_sha=head, reason=reason)
        if any(x["number"] == it["number"] for x in self.queues.get(it["repo"], [])):
            self._pop(it)

    # --- API ---
    def enqueue(self, d):
        repo, n = d["repo"], d["number"]
        pr = self.gh.pr(repo, n)
        if pr["state"] != "open" or pr["head"]["sha"] != d["head_sha"]:
            self._return({"repo": repo, "number": n}, "head moved before approval was queued", pr["head"]["sha"])
            return False
        if f"{repo}#{n}" in self.queued():
            return True
        it = {"repo": repo, "number": n, "approved_sha": d["head_sha"], "current_sha": d["head_sha"],
              "fingerprint": analysis.diff_fingerprint(self.gh.pr_diff(repo, n)),
              "state": "queued", "since": self.clock(), "attempts": 0}
        self.queues.setdefault(repo, []).append(it)
        self._save()
        self.audit.append("enqueued", repo=repo, number=n, head_sha=d["head_sha"], card_sha256=d["card_sha256"])
        return True

    def step(self):
        for repo in list(self.queues):
            if self.queues.get(repo):
                it = self.queues[repo][0]
                try:
                    self._advance(it)
                except GitHubError as e:
                    it["attempts"] += 1
                    self.audit.append("error", repo=it["repo"], number=it["number"], status=e.status)
                    if it["attempts"] >= MAX_ATTEMPTS:
                        self._return(it, f"GitHub error {e.status}", it["current_sha"])
                    else:
                        self._save()

    def _advance(self, it):
        repo, n = it["repo"], it["number"]
        pr = self.gh.pr(repo, n)
        if pr["state"] != "open":
            self.audit.append("gone", repo=repo, number=n, merged=bool(pr.get("merged")))
            self._pop(it)
            return
        head = pr["head"]["sha"]
        if head != it["current_sha"]:
            if analysis.diff_fingerprint(self.gh.pr_diff(repo, n)) != it["fingerprint"]:
                self._return(it, "changed after approval", head)
                return
            it.update(current_sha=head, state="queued", since=self.clock())
        ms = pr.get("mergeable_state", "unknown")
        if ms == "dirty":
            self._return(it, "merge conflict with base", head)
            return
        if it["state"] == "updating":
            if self.clock() - it["since"] > UPDATE_TIMEOUT:
                it.update(state="queued", since=self.clock())
            self._save()
            return
        if it["state"] == "queued":
            if ms == "behind":
                self.gh.update_branch(repo, n, head)
                it.update(state="updating", since=self.clock())
                self.audit.append("update-branch", repo=repo, number=n, head_sha=head)
                self._save()
                return
            it.update(state="waiting_ci", since=self.clock())
        ci = self.gh.ci_state(repo, head)
        waited = self.clock() - it["since"]
        if ci["state"] == "pending" or (ci["state"] == "none" and waited < CI_GRACE):
            if waited > CI_TIMEOUT:
                self._return(it, "CI timed out", head)
            else:
                self._save()
            return
        if ci["state"] == "unknown":
            self._return(it, "can't read CI checks (token lacks Checks: read)", head)
            return
        if ci["state"] == "failure":
            self._return(it, "CI failed: " + ", ".join(ci["failing"]), head)
            return
        status, body = self.gh.merge(repo, n, head, self._method(repo))
        self.audit.append("merge", repo=repo, number=n, head_sha=head, status=status)
        if status == 200:
            self._pop(it)
        elif status in (405, 409):
            it["attempts"] += 1
            if it["attempts"] >= MAX_ATTEMPTS:
                self._return(it, f"merge refused ({status}) {MAX_ATTEMPTS} times", head)
            else:
                it.update(state="queued", since=self.clock())
                self._save()
