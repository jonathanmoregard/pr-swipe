"""pr-swipe-loops: the user-side service that keeps loops flowing. Runs as the user (the collector's
token) and touches GitHub only through pr_swipe.loops, so only `loop` issues in the loops repo.

Each pass:
1. Merged PRs → follow-up loops. The briefing's manual steps are snapshotted from inbox cards while a
   PR is open (the collector deletes a card once its PR closes). A watched PR whose card is gone is
   looked up: merged files its steps plus any ```loops block in the PR body; closed unmerged files
   nothing; open keeps waiting. Keys are repo#PR:step, so nothing is filed twice.
2. GUI requests in outbox/loops/ (close, drop, snooze, snooze-week, agent) are applied and deleted.
   The GUI has no token; this is its only way to change a loop.
3. A snapshot of open loops goes to inbox/loops/open.json for the GUI's Loops deck: overdue first,
   then blocked, then by due date, then oldest; loops snoozed past today are left out.
"""
import argparse
import datetime as dt
import json
import logging
import os
import re
import time
from pathlib import Path

from . import card as C
from .config import load
from .github import GitHub, GhCliToken, GitHubError
from .loops import LoopError, Loops
from .loops_mcp import DEFAULT_REPO

log = logging.getLogger("pr-swipe.loops")
BLOCK_RE = re.compile(r"^```loops[ \t]*\n(.*?)^```", re.M | re.S)


def _load(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {"watch": {}}


def _save(path, st):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    C.write_json_atomic(path.parent, path.name, st, mode=0o600)


def body_steps(body):
    out = []
    for block in BLOCK_RE.findall(body or ""):
        out += [s for s in (re.sub(r"^\s*(?:[-*]|\d+\.)\s*", "", line).strip() for line in block.splitlines()) if s]
    return out


def _file_merged(gh, loops, st, live):
    watch = st.setdefault("watch", {})
    for pr, w in list(watch.items()):
        if pr in live:
            continue  # card still in the inbox: the PR is open
        try:
            info = gh.pr(w["repo"], w["number"])
        except GitHubError as e:
            log.warning("loops %s: %s; retrying next pass", pr, e)
            continue
        if info["state"] == "open":
            continue
        if info.get("merged"):
            note = f"From {pr} ({info['html_url']}), merged {(info.get('merged_at') or '')[:10]}: {info.get('title', '')}"
            for text in w["steps"] + body_steps(info.get("body")):
                try:
                    loop, created = loops.add(text, body=note, kind="followup", source=w["repo"], key=f"{pr}:{text}")
                except LoopError as e:
                    log.warning("loops %s: step refused: %s", pr, e)
                    continue
                if created:
                    log.info("filed loop #%s for %s", loop.number, pr)
        del watch[pr]


def _watch(cards, st):
    newest = {}
    for c in cards:
        pr = f"{c['repo']}#{c['number']}"
        if pr not in newest or c["review_meta"]["reviewed_at"] >= newest[pr]["review_meta"]["reviewed_at"]:
            newest[pr] = c
    watch = st.setdefault("watch", {})
    for pr, c in newest.items():
        # every open PR is watched: a ```loops block in its body counts even without briefed steps
        watch[pr] = {"repo": c["repo"], "number": c["number"],
                     "steps": [s for s in (c.get("context", {}).get("manual") or []) if s.strip()]}
    return set(newest)


def _plus(today, days):
    return (dt.date.fromisoformat(today) + dt.timedelta(days=days)).isoformat()


def _requests(loops, outbox, today):
    actions = {"close": lambda n: loops.close(n), "drop": lambda n: loops.close(n, done=False),
               "snooze": lambda n: loops.snooze(n, _plus(today, 1)),
               "snooze-week": lambda n: loops.snooze(n, _plus(today, 7)),
               "agent": lambda n: loops.set_owner(n, "agent")}
    for p in sorted(Path(outbox).glob("*.json")):
        try:
            req = json.loads(p.read_text())
            act = actions.get(req.get("action"))
            if act is None or not isinstance(req.get("number"), int):
                log.warning("loops request %s: unknown action or number", p.name)
            else:
                act(req["number"])
        except (LoopError, GitHubError, ValueError, OSError) as e:
            log.warning("loops request %s: %s", p.name, e)
        finally:
            p.unlink(missing_ok=True)


def _snapshot(loops, inbox, today):
    def rank(x):
        overdue = bool(x.due) and x.due < today
        return (not overdue, x.kind != "blocked", x.due or "9999", x.created_at, x.number)
    shown = sorted((x for x in loops.list("open") if not x.until or x.until <= today), key=rank)
    out = Path(inbox) / "loops"
    out.mkdir(parents=True, exist_ok=True)
    C.write_json_atomic(out, "open.json", {"today": today, "repo": loops.repo,
                                           "loops": [x.as_dict() for x in shown]}, mode=0o640)


def run_once(gh, cfg, loops, state_path, today):
    st = _load(state_path)
    live = _watch(C.load_cards(cfg.inbox), st)
    _file_merged(gh, loops, st, live)
    _save(state_path, st)
    _requests(loops, Path(cfg.outbox) / "loops", today)
    _snapshot(loops, cfg.inbox, today)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pr-swipe-loops")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=300)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    cfg = load()
    gh = GitHub(GhCliToken())
    loops = Loops(gh, os.environ.get("PR_SWIPE_LOOPS_REPO", DEFAULT_REPO))
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    state_path = state_home / "pr-swipe" / "loops.json"
    while True:
        try:
            run_once(gh, cfg, loops, state_path, dt.date.today().isoformat())
        except Exception:
            log.exception("loops pass failed; retrying next interval")
        if a.once:
            return
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
