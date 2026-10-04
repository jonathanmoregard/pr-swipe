"""Collector: runs as the user with a read-only token. Writes cards; never writes to GitHub."""
import argparse
import datetime as dt
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from . import analysis, card as C, reviewer
from .config import load
from .github import GitHub, GhCliToken, GitHubError, git_auth_header

log = logging.getLogger("pr-swipe.collector")
MAX_DETAIL_DIFF = 300000
STALE_DAYS = 14
MERGEABLE = {"clean": "clean", "unstable": "clean", "has_hooks": "clean", "blocked": "clean",
             "behind": "behind", "dirty": "dirty"}


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def days_since(iso):
    t = dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    return max(0, (dt.datetime.now(dt.timezone.utc) - t).days)


def classify(login, cfg):
    if login == cfg.user:
        return "self"
    if login in cfg.agent_logins:
        return "agent"
    return "external"


class Reviewer:
    """Adapter so tests can swap the claude-backed reviewer."""

    def review_diff(self, diff, files, rule_spots, model):
        return reviewer.review_diff(diff, files, rule_spots, model)

    def review_context(self, pr, commits, open_titles, merged_titles, model, files=(), diff=""):
        return reviewer.review_context(pr, commits, open_titles, merged_titles, model, files=files, diff=diff)


BRIEFING = ("purpose", "solution", "notes", "headline", "why", "before", "after", "how", "manual",
            "internal_only", "unsure")
TOO_LARGE = "diff too large for GitHub's API (over 20000 lines or 300 files): review it in the browser"


def fetch_diff(gh, repo, n):
    """The PR diff, or None when GitHub refuses to render one that large (HTTP 406 too_large)."""
    try:
        return gh.pr_diff(repo, n)
    except GitHubError as e:
        if e.status == 406:
            return None
        raise


def build_card(gh, rv, cfg, repo, n, open_prs_files):
    pr = gh.pr(repo, n)
    diff = fetch_diff(gh, repo, n)
    too_large, diff = diff is None, diff or ""
    files = analysis.parse_diff(diff)
    rule_spots = analysis.rule_hotspots(files)
    comments = gh.issue_comments(repo, n)
    commits = gh.pr_commits(repo, n)
    hidden = (analysis.hidden_content("title", pr["title"]) + analysis.hidden_content("body", pr.get("body"))
              + [h for cm in comments for h in analysis.hidden_content(f"comment:{cm['user']['login']}", cm.get("body"))]
              + analysis.hidden_in_diff(files))
    author = pr["user"]["login"]
    klass = classify(author, cfg)
    my_paths = {f.path for f in files}
    overlapping = sorted(m for m, paths in open_prs_files.get(repo, {}).items() if m != n and paths & my_paths)
    since = days_since(pr["updated_at"])
    stale_signal = since > STALE_DAYS or pr.get("mergeable_state") == "dirty"

    ai_spots = []
    context = {"purpose": "", "solution": "", "notes": ""}
    verdict = {"recommendation": "look", "stale": stale_signal, "superseded_by": [],
               "confidence": "low", "reason": TOO_LARGE if too_large else "no AI review: external author"}
    if klass != "external" and not too_large:
        try:
            d = rv.review_diff(diff, files, rule_spots, cfg.model)
            ai_spots = reviewer.resolve_hotspots(d["hotspots"], files)
            others = [f"{repo}#{p['number']} {p['title']}" for p in gh.list_prs(repo, "open") if p["number"] != n]
            merged = [f"{repo}#{p['number']} {p['title']}" for p in gh.list_prs(repo, "closed")
                      if p.get("merged_at")][:20]
            ctx = rv.review_context(pr, [c["commit"]["message"] for c in commits], others, merged, cfg.model,
                                    files=files, diff=diff)
            context = {k: ctx[k] for k in BRIEFING if k in ctx}
            verdict = {k: ctx[k] for k in ("recommendation", "superseded_by", "confidence", "reason")}
            verdict["stale"] = ctx["stale"] or stale_signal
            if d["risk_note"]:
                context["notes"] = (context["notes"] + "\nRisk: " + d["risk_note"]).strip()[:1500]
        except reviewer.ReviewError as e:
            verdict["reason"] = f"AI review failed: {e}"[:1500]

    card = {
        "schema": 1, "repo": repo, "number": n, "url": pr["html_url"],
        "head_sha": pr["head"]["sha"], "base_ref": pr["base"]["ref"], "base_sha": pr["base"]["sha"],
        "author": author, "author_class": klass, "title": pr["title"][:1000],
        "created_at": pr["created_at"],
        "first_commit_at": commits[0]["commit"]["author"]["date"] if commits else pr["created_at"],
        "updated_at": pr["updated_at"],
        "can_merge": repo.split("/")[0] == cfg.user,
        "ci": gh.ci_state(repo, pr["head"]["sha"]),
        "mergeable": MERGEABLE.get(pr.get("mergeable_state"), "unknown"),
        "diffstat": ({"files": pr.get("changed_files", 0), "additions": pr.get("additions", 0),
                      "deletions": pr.get("deletions", 0)} if too_large else analysis.diffstat(files)),
        "hotspots": rule_spots + ai_spots,
        "hidden_content": [dict(h, text=h["text"][:2000], where=h["where"][:600]) for h in hidden],
        "context": context, "verdict": verdict,
        "signals": {"days_since_update": since, "overlapping_open": overlapping},
        "detail": {"diff": diff[:MAX_DETAIL_DIFF], "truncated": too_large or len(diff) > MAX_DETAIL_DIFF,
                   "comments": [{"author": cm["user"]["login"][:100], "body": (cm.get("body") or "")[:20000]}
                                for cm in comments]},
        "review_meta": {"model": cfg.model, "reviewed_at": now_iso(), "prose_seen": False},
    }
    return C.validate(card)


def collect_once(gh, rv, cfg):
    open_prs = gh.search_open_prs(cfg.user)
    existing = {p.name for p in cfg.inbox.glob("*.json")}
    live = set()
    files_by_repo = {}
    heads = {}
    for item in open_prs:
        repo, n = item["repo"], item["number"]
        try:
            pr = gh.pr(repo, n)
            heads[(repo, n)] = pr
            files_by_repo.setdefault(repo, {})[n] = {f.path for f in analysis.parse_diff(fetch_diff(gh, repo, n) or "")}
        except GitHubError as e:
            log.warning("skip %s#%s: %s", repo, n, e)
    for (repo, n), pr in heads.items():
        name = C.filename({"repo": repo, "number": n, "head_sha": pr["head"]["sha"]})
        live.add(name)
        if name in existing:
            continue
        try:
            C.write_card(cfg.inbox, build_card(gh, rv, cfg, repo, n, files_by_repo))
        except (GitHubError, C.CardError) as e:
            log.warning("card %s#%s failed: %s", repo, n, e)
    for name in existing - live:
        (cfg.inbox / name).unlink(missing_ok=True)


URL_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[0-9]+$")


def run_deep_review(gh_token, cfg, req):
    """Clone the PR head read-only into a temp dir and run the tool-enabled reviewer on it."""
    for c in C.load_cards(cfg.inbox):
        if c["repo"] == req.get("repo") and c["number"] == req.get("number") and c["head_sha"] == req.get("head_sha"):
            break
    else:
        return
    env = dict(os.environ, GIT_CONFIG_COUNT="2",
               GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
               GIT_CONFIG_VALUE_0=git_auth_header(gh_token),
               GIT_CONFIG_KEY_1="core.hooksPath", GIT_CONFIG_VALUE_1="/dev/null",
               GIT_TERMINAL_PROMPT="0")
    with tempfile.TemporaryDirectory() as work:
        run = lambda *a: subprocess.run(["git", *a], cwd=work, env=env, check=True, capture_output=True)  # noqa: E731
        run("init", "-q")
        run("fetch", "-q", "--depth=50", f"https://github.com/{c['repo']}.git", f"pull/{c['number']}/head")
        run("checkout", "-q", "--detach", c["head_sha"])
        result = reviewer.deep_review(work, c, cfg.deep_model)
    C.write_card(cfg.inbox, dict(c, deep_review=result))


def process_outbox(cfg, gh, reviewer, opener=None, deep=None):
    opener = opener or (lambda url: subprocess.Popen(["xdg-open", url]))
    deep = deep or (lambda req: run_deep_review(GhCliToken().token(), cfg, req))
    for p in sorted(cfg.outbox.glob("*.json")):
        try:
            req = json.loads(p.read_text())
            if req.get("kind") == "open" and URL_RE.fullmatch(req.get("url", "")):
                opener(req["url"])
            elif req.get("kind") == "deep-review":
                deep(req)
        except Exception as e:  # one bad request must not stop the loop
            log.warning("outbox %s: %s", p.name, e)
        finally:
            p.unlink(missing_ok=True)


def tick(last, now, interval, gh, rv, cfg):
    """One loop pass. A failed pass (network blip, GitHub 5xx) is logged and retried next interval;
    the outbox is still served, so the deck's requests do not stall behind a broken collection."""
    if now - last >= interval:
        try:
            collect_once(gh, rv, cfg)
        except Exception:
            log.exception("collection pass failed; retrying next interval")
        last = now
    try:
        process_outbox(cfg, gh, rv)
    except Exception:
        log.exception("outbox pass failed")
    return last


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pr-swipe-collector")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=600)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    cfg = load()
    gh, rv = GitHub(GhCliToken()), Reviewer()
    if a.once:
        collect_once(gh, rv, cfg)
        process_outbox(cfg, gh, rv)
        return
    last = 0.0
    while True:
        last = tick(last, time.time(), a.interval, gh, rv, cfg)
        time.sleep(5)


if __name__ == "__main__":
    main()
