"""Follow-ups: the steps a PR's briefing says the owner must still do by hand become issues in one
private repo once that PR merges. Runs as the user (same token as the collector) and writes only to
that repo; the review screen never shows them.

Each pass snapshots the manual steps of every card in the inbox, because the collector deletes a card
as soon as its PR closes. A watched PR whose card is gone is looked up on GitHub: merged files its
steps, closed unmerged forgets them, still open keeps waiting. Every issue carries a key derived
from repo, PR and step text, so a lost state file or a re-run never files a step twice.
"""
import argparse
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

from . import card as C
from .config import load
from .github import GitHub, GhCliToken, GitHubError

log = logging.getLogger("pr-swipe.followups")
LABEL = "followup"
TITLE_MAX = 120
KEY_RE = re.compile(r"<!-- fu-key: ([0-9a-f]{16}) -->")


def step_key(repo, number, text):
    norm = re.sub(r"\s+", " ", text).strip().lower()
    return hashlib.sha256(f"{repo}#{number}:{norm}".encode()).hexdigest()[:16]


def _title(text):
    t = re.sub(r"\s+", " ", text).strip()
    return t if len(t) <= TITLE_MAX else t[:TITLE_MAX - 1].rstrip() + "…"


def _load(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {"watch": {}}


def _save(path, st):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    C.write_json_atomic(path.parent, path.name, st, mode=0o600)


def run_once(gh, inbox, state_path, target):
    st = _load(state_path)
    watch = st.setdefault("watch", {})
    newest = {}
    for c in C.load_cards(inbox):
        pr = f"{c['repo']}#{c['number']}"
        if pr not in newest or c["review_meta"]["reviewed_at"] >= newest[pr]["review_meta"]["reviewed_at"]:
            newest[pr] = c
    for pr, c in newest.items():
        steps = [s for s in (c.get("context", {}).get("manual") or []) if s.strip()]
        if steps:
            watch[pr] = {"repo": c["repo"], "number": c["number"], "steps": steps}
        else:
            watch.pop(pr, None)
    filed = None
    for pr, w in list(watch.items()):
        if pr in newest:
            continue  # card still in the inbox: the PR is open
        try:
            info = gh.pr(w["repo"], w["number"])
        except GitHubError as e:
            log.warning("follow-ups %s: %s; retrying next pass", pr, e)
            continue
        if info["state"] == "open":
            continue
        if info.get("merged"):
            if filed is None:
                filed = {m for i in gh.list_issues(target, LABEL) for m in KEY_RE.findall(i.get("body") or "")}
            for text in w["steps"]:
                key = step_key(w["repo"], w["number"], text)
                if key in filed:
                    continue
                body = (f"From {pr} ({info['html_url']}), merged {(info.get('merged_at') or '')[:10]}: "
                        f"{info.get('title', '')}\n\n{text}\n\n<!-- fu-key: {key} -->")
                gh.create_issue(target, _title(text), body, [LABEL, w["repo"]])
                filed.add(key)
                log.info("filed follow-up for %s: %s", pr, _title(text))
        del watch[pr]
        _save(state_path, st)
    _save(state_path, st)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pr-swipe-followups")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=600)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    cfg = load()
    target = os.environ.get("PR_SWIPE_FOLLOWUPS_REPO", f"{cfg.user}/followups")
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    state_path = state_home / "pr-swipe" / "followups.json"
    gh = GitHub(GhCliToken())
    while True:
        try:
            run_once(gh, cfg.inbox, state_path, target)
        except Exception:
            log.exception("follow-up pass failed; retrying next interval")
        if a.once:
            return
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
