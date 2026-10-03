def make_card(repo="o/r", number=1, sha="a" * 40, **over):
    card = {
        "schema": 1, "repo": repo, "number": number,
        "url": f"https://github.com/{repo}/pull/{number}",
        "head_sha": sha, "base_ref": "main", "base_sha": "b" * 40,
        "author": "jonathanmoregard", "author_class": "self",
        "title": "Fix thing", "created_at": "2026-10-01T00:00:00Z",
        "first_commit_at": "2026-09-30T00:00:00Z", "updated_at": "2026-10-02T00:00:00Z",
        "can_merge": True,
        "ci": {"state": "success", "failing": []},
        "mergeable": "clean",
        "diffstat": {"files": 1, "additions": 1, "deletions": 1},
        "hotspots": [],
        "hidden_content": [],
        "context": {"purpose": "p", "solution": "s", "notes": ""},
        "verdict": {"recommendation": "approve", "stale": False, "superseded_by": [],
                    "confidence": "high", "reason": "fine"},
        "signals": {"days_since_update": 1, "overlapping_open": []},
        "detail": {"diff": "", "truncated": False, "comments": []},
        "review_meta": {"model": "m", "reviewed_at": "2026-10-03T00:00:00Z", "prose_seen": False},
    }
    card.update(over)
    return card
