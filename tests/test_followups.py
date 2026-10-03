from pr_swipe import card as C, followups as F
from tests.cards import make_card


class Hub:
    """GitHub surface the follow-up filer uses: PR state reads and issues in one repo."""
    def __init__(self):
        self.prs, self.issues = {}, []

    def pr(self, repo, n):
        return self.prs[(repo, n)]

    def list_issues(self, repo, label):
        return [i for i in self.issues if label in i["labels"]]

    def create_issue(self, repo, title, body, labels):
        self.issues.append({"repo": repo, "title": title, "body": body, "labels": labels,
                            "number": len(self.issues) + 1})


def state(pr_state="open", merged=False):
    return {"state": pr_state, "merged": merged, "merged_at": "2026-10-03T20:00:00Z" if merged else None,
            "html_url": "https://github.com/o/r/pull/1", "title": "Wrap gdocs MCP"}


def card(manual):
    c = make_card()
    c["context"]["manual"] = manual
    return c


def run(tmp_path, hub, cards):
    inbox = tmp_path / "inbox"; inbox.mkdir(exist_ok=True)
    for p in inbox.glob("*.json"):
        p.unlink()
    for c in cards:
        C.write_card(inbox, c)
    F.run_once(hub, inbox, tmp_path / "followups.json", "me/followups")


def test_steps_are_filed_once_the_pr_merges_even_after_its_card_is_gone(tmp_path):
    hub = Hub(); hub.prs[("o/r", 1)] = state()
    run(tmp_path, hub, [card(["Create the OAuth client secret"])])
    assert hub.issues == []                                    # still open: nothing filed yet
    hub.prs[("o/r", 1)] = state("closed", merged=True)
    run(tmp_path, hub, [])                                     # collector pruned the card on close
    [i] = hub.issues
    assert i["repo"] == "me/followups" and i["title"] == "Create the OAuth client secret"
    assert "o/r#1" in i["body"] and "https://github.com/o/r/pull/1" in i["body"]
    assert set(i["labels"]) == {"followup", "o/r"}


def test_a_step_is_never_filed_twice(tmp_path):
    hub = Hub(); hub.prs[("o/r", 1)] = state("closed", merged=True)
    run(tmp_path, hub, [card(["Rotate the key"])]); run(tmp_path, hub, [])
    (tmp_path / "followups.json").unlink()                     # lost state: the issue marker still dedups
    run(tmp_path, hub, [card(["Rotate  the key"])]); run(tmp_path, hub, [])
    assert len(hub.issues) == 1


def test_closed_without_merge_files_nothing_and_forgets_the_pr(tmp_path):
    hub = Hub(); hub.prs[("o/r", 1)] = state()
    run(tmp_path, hub, [card(["Rotate the key"])])
    hub.prs[("o/r", 1)] = state("closed")
    run(tmp_path, hub, [])
    hub.prs[("o/r", 1)] = state("closed", merged=True)        # even if GitHub later disagreed
    run(tmp_path, hub, [])
    assert hub.issues == []


def test_steps_follow_the_newest_card_for_a_pr(tmp_path):
    hub = Hub(); hub.prs[("o/r", 1)] = state()
    run(tmp_path, hub, [card(["Old step"])])
    run(tmp_path, hub, [card(["New step"])])
    hub.prs[("o/r", 1)] = state("closed", merged=True)
    run(tmp_path, hub, [])
    assert [i["title"] for i in hub.issues] == ["New step"]
