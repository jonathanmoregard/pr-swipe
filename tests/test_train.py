import json
from pr_swipe.train import Train
from pr_swipe.audit import AuditLog
from tests.fakes import FakeGitHub, PLAIN

R = "jonathanmoregard/x"


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


def make(tmp_path, gh):
    clock = Clock()
    (tmp_path / "returns").mkdir()
    t = Train(gh, AuditLog(tmp_path / "audit.jsonl", clock), tmp_path / "train.json",
              tmp_path / "returns", clock=clock)
    return t, clock


def decision(n, sha):
    return {"action": "approve", "repo": R, "number": n, "head_sha": sha, "card_sha256": "f" * 64, "ts": 1.0}


def returns(tmp_path):
    return [json.loads(p.read_text()) for p in (tmp_path / "returns").glob("*.json")]


def run(t, clock, steps=10):
    for _ in range(steps):
        t.step()
        clock.t += 100


def test_clean_pr_merges_with_pinned_sha(tmp_path):
    gh = FakeGitHub(); gh.add_pr(R, 1, "a" * 40)
    t, clock = make(tmp_path, gh)
    t.enqueue(decision(1, "a" * 40))
    run(t, clock)
    assert ("merge", R, 1, "a" * 40, "squash") in gh.calls
    assert t.queued() == set()


def test_behind_pr_is_updated_then_merged_when_diff_unchanged(tmp_path):
    gh = FakeGitHub(); gh.add_pr(R, 1, "a" * 40, mergeable_state="behind")
    gh.on_update = lambda repo, n: "e" * 40
    gh.ci[(R, "e" * 40)] = {"state": "success", "failing": []}
    t, clock = make(tmp_path, gh)
    t.enqueue(decision(1, "a" * 40))
    run(t, clock)
    assert ("merge", R, 1, "e" * 40, "squash") in gh.calls
    assert returns(tmp_path) == []


def test_rebase_that_changes_the_diff_goes_back_to_the_human(tmp_path):
    gh = FakeGitHub(); gh.add_pr(R, 1, "a" * 40, mergeable_state="behind")
    def update(repo, n):
        gh.diffs[(repo, n)] = PLAIN.replace("a + b", "a + b + 1")
        return "e" * 40
    gh.on_update = update
    t, clock = make(tmp_path, gh)
    t.enqueue(decision(1, "a" * 40))
    run(t, clock)
    assert not any(c[0] == "merge" for c in gh.calls)
    assert returns(tmp_path)[0]["reason"] == "changed after approval"


def test_failing_ci_returns_card_with_job_names(tmp_path):
    gh = FakeGitHub(); gh.add_pr(R, 1, "a" * 40)
    gh.ci[(R, "a" * 40)] = {"state": "failure", "failing": ["vm-base"]}
    t, clock = make(tmp_path, gh)
    t.enqueue(decision(1, "a" * 40))
    run(t, clock)
    assert "vm-base" in returns(tmp_path)[0]["reason"]
    assert not any(c[0] == "merge" for c in gh.calls)


def test_no_ci_yet_waits_for_grace_period_before_merging(tmp_path):
    gh = FakeGitHub(); gh.add_pr(R, 1, "a" * 40)
    gh.ci[(R, "a" * 40)] = {"state": "none", "failing": []}
    t, clock = make(tmp_path, gh)
    t.enqueue(decision(1, "a" * 40))
    t.step()
    assert not any(c[0] == "merge" for c in gh.calls)
    clock.t += 200
    t.step()
    assert any(c[0] == "merge" for c in gh.calls)


def test_head_moved_before_enqueue_is_rejected(tmp_path):
    gh = FakeGitHub(); gh.add_pr(R, 1, "b" * 40)
    t, _ = make(tmp_path, gh)
    t.enqueue(decision(1, "a" * 40))
    assert t.queued() == set() and returns(tmp_path)[0]["reason"].startswith("head moved")


def test_prs_in_one_repo_merge_in_order_and_state_survives_restart(tmp_path):
    gh = FakeGitHub(); gh.add_pr(R, 1, "a" * 40); gh.add_pr(R, 2, "c" * 40)
    t, clock = make(tmp_path, gh)
    t.enqueue(decision(1, "a" * 40)); t.enqueue(decision(2, "c" * 40))
    t2 = Train(gh, AuditLog(tmp_path / "audit.jsonl", clock), tmp_path / "train.json",
               tmp_path / "returns", clock=clock)
    assert t2.queued() == {f"{R}#1", f"{R}#2"}
    run(t2, clock)
    merges = [c[2] for c in gh.calls if c[0] == "merge"]
    assert merges == [1, 2]


def test_unreadable_ci_returns_instead_of_merging(tmp_path):
    gh = FakeGitHub(); gh.add_pr(R, 1, "a" * 40)
    gh.ci[(R, "a" * 40)] = {"state": "unknown", "failing": []}
    t, clock = make(tmp_path, gh)
    t.enqueue(decision(1, "a" * 40))
    clock.t += 200
    t.step()
    assert not any(c[0] == "merge" for c in gh.calls)
    assert "can't read CI" in returns(tmp_path)[0]["reason"]
