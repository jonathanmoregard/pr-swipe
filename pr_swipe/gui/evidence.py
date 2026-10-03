"""Turn untrusted inbox cards into display cards built from verified, SHA-addressed evidence.

A card is shown only if the verifier (prswipe, GitHub App token) recorded the same head SHA.
Title, body, comments, CI, diff, rule hotspots and hidden-content flags come from that record and
the verified mirror. From the card, only the AI layer survives, and only raise-only: an AI hotspot
is kept if its file is in the verified diff, and it always shows the verified hunk text, never code
the card claims. External repos (no App, cannot be merged) pass through marked unverified.
"""
from .. import analysis
from ..gitview import GitView
from ..verify import load_record, mirror_path, _repo_ok

DIFF_MAX = 300_000
_cache = {}  # (repo, number, head) -> (diff, commits)


def _ai_hotspots(card, files):
    by_path = {f.path: f for f in files}
    out = []
    for h in card.get("hotspots", []):
        f = by_path.get(h.get("file")) if h.get("source") == "ai" else None
        if f is None or not f.hunks:
            continue
        hunk = next((x for x in f.hunks if x.header == h.get("hunk")), f.hunks[0])
        out.append({"source": "ai", "file": f.path, "hunk": hunk.header,
                    "lines": "\n".join(hunk.lines[:analysis.MAX_HOTSPOT_LINES]),
                    "why": h.get("why", ""), "severity": h.get("severity", "low")})
    return out


def display_card(card, rec, view):
    key = (rec["repo"], rec["number"], rec["head_sha"])
    if key not in _cache:
        diff = view.diff(rec["merge_base"], rec["head_sha"])
        _cache[key] = (diff, view.commits(rec["merge_base"], rec["head_sha"]))
    diff, commits = _cache[key]
    files = analysis.parse_diff(diff)
    hidden = (analysis.hidden_content("title", rec["title"]) + analysis.hidden_content("body", rec["body"])
              + [h for cm in rec.get("comments", []) for h in analysis.hidden_content(f"comment:{cm['author']}", cm["body"])]
              + analysis.hidden_in_diff(files))
    out = dict(card)
    out.update(
        title=rec["title"], ci=rec["ci"], base_sha=rec["merge_base"],
        diffstat=analysis.diffstat(files), hidden_content=hidden,
        hotspots=analysis.rule_hotspots(files) + _ai_hotspots(card, files),
        detail={"diff": diff[:DIFF_MAX], "truncated": len(diff) > DIFF_MAX, "comments": rec.get("comments", [])},
        _verified=True, _commits=commits, _view=view, _record=rec,
    )
    return out


def verified_cards(cfg, cards) -> list:
    out = []
    for c in cards:
        if not c.get("can_merge", False):
            out.append({**c, "_unverified": True})
            continue
        if not _repo_ok(c.get("repo")):
            continue
        rec = load_record(cfg.verified, c["repo"], c["number"])
        if not rec or rec.get("head_sha") != c.get("head_sha"):
            continue
        out.append(display_card(c, rec, GitView(mirror_path(cfg.git, c["repo"]))))
    return out
