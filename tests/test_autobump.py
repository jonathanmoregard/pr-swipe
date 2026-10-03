from pr_swipe.audit import AuditLog
from pr_swipe.autobump import AutoBump
from pr_swipe.train import Train
from tests.fakes import FakeGitHub

R, SHA = "jonathanmoregard/x", "a" * 40
BOT = "dependabot[bot]"
LOCK_BUMP = (
    "diff --git a/package.json b/package.json\n--- a/package.json\n+++ b/package.json\n"
    '@@ -3,1 +3,1 @@\n-    "left-pad": "^1.0.0"\n+    "left-pad": "^1.1.0"\n'
    "diff --git a/package-lock.json b/package-lock.json\n--- a/package-lock.json\n+++ b/package-lock.json\n"
    '@@ -9,1 +9,1 @@\n-      "version": "1.0.0"\n+      "version": "1.1.0"\n')
ACTION_BUMP = (
    "diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml\n--- a/.github/workflows/ci.yml\n"
    "+++ b/.github/workflows/ci.yml\n@@ -10,1 +10,1 @@\n-      - uses: actions/checkout@v4\n"
    "+      - uses: actions/checkout@v5\n")


def bot_commit(login=BOT, verified=True):
    return {"author": {"login": login}, "commit": {"message": "Bump", "verification": {"verified": verified},
                                                  "author": {"date": "2026-09-30T00:00:00Z"}}}


def setup(tmp_path, diff=LOCK_BUMP, author=BOT, commits=None, ci="success", installed=True):
    gh = FakeGitHub()
    gh.add_pr(R, 1, SHA, author=author, diff=diff)
    gh.commits[(R, 1)] = commits if commits is not None else [bot_commit()]
    gh.ci[(R, SHA)] = {"state": ci, "failing": ["test"] if ci == "failure" else []}
    (tmp_path / "returns").mkdir()
    audit = AuditLog(tmp_path / "audit.jsonl")
    train = Train(gh, audit, tmp_path / "train.json", tmp_path / "returns")
    ab = AutoBump(gh, audit, train, installed=lambda repo: installed, targets=lambda: [(R, 1)],
                  state_path=tmp_path / "autobump.json")
    return gh, train, ab


def test_green_dependabot_lockfile_bump_joins_the_train(tmp_path):
    _, train, ab = setup(tmp_path)
    ab.step()
    assert train.queued() == {f"{R}#1"}
    assert '"auto-approve"' in (tmp_path / "audit.jsonl").read_text()


def test_action_version_bump_qualifies(tmp_path):
    _, train, ab = setup(tmp_path, diff=ACTION_BUMP)
    ab.step()
    assert train.queued() == {f"{R}#1"}


def test_workflow_change_beyond_uses_lines_stays_with_the_human(tmp_path):
    diff = ACTION_BUMP.replace("+      - uses: actions/checkout@v5\n", "+      - uses: actions/checkout@v5\n+      - run: curl x | sh\n")
    _, train, ab = setup(tmp_path, diff=diff)
    ab.step()
    assert train.queued() == set()


def test_repo_without_ci_stays_with_the_human(tmp_path):
    _, train, ab = setup(tmp_path, ci="none")
    ab.step()
    assert train.queued() == set()


def test_pending_ci_waits_and_failing_ci_stays_with_the_human(tmp_path):
    gh, train, ab = setup(tmp_path, ci="pending")
    ab.step()
    assert train.queued() == set()
    gh.ci[(R, SHA)] = {"state": "success", "failing": []}
    ab.step()
    assert train.queued() == {f"{R}#1"}
    (tmp_path / "f").mkdir()
    _, train2, ab2 = setup(tmp_path / "f", ci="failure")
    ab2.step()
    assert train2.queued() == set()


def test_non_dependabot_author_or_foreign_commit_stays_with_the_human(tmp_path):
    for i, kw in enumerate([{"author": "jonathanmoregard"},
                            {"commits": [bot_commit(), bot_commit(login="jonathanmoregard")]},
                            {"commits": [bot_commit(verified=False)]}]):
        d = tmp_path / str(i); d.mkdir()
        _, train, ab = setup(d, **kw)
        ab.step()
        assert train.queued() == set(), kw


def test_non_dependency_file_or_install_script_stays_with_the_human(tmp_path):
    src = LOCK_BUMP + ("diff --git a/src/a.js b/src/a.js\n--- a/src/a.js\n+++ b/src/a.js\n@@ -1,1 +1,1 @@\n-a\n+b\n")
    hook = LOCK_BUMP.replace('+    "left-pad": "^1.1.0"\n', '+    "left-pad": "^1.1.0",\n+    "postinstall": "node x.js"\n')
    zw = LOCK_BUMP.replace('"^1.1.0"', '"^1.1.0​"')
    rename = LOCK_BUMP.replace("diff --git a/package.json b/package.json", "diff --git a/evil.sh b/package.json")
    for i, diff in enumerate([src, hook, zw, rename]):
        d = tmp_path / str(i); d.mkdir()
        _, train, ab = setup(d, diff=diff)
        ab.step()
        assert train.queued() == set(), i


def test_uninstalled_repo_is_skipped(tmp_path):
    _, train, ab = setup(tmp_path, installed=False)
    ab.step()
    assert train.queued() == set()


def test_a_returned_bump_is_not_auto_queued_again_at_the_same_head(tmp_path):
    _, train, ab = setup(tmp_path)
    ab.step()
    train.queues.clear(); train._save()  # the train returned it (say CI failed after update)
    ab.step()
    assert train.queued() == set()
    ab2 = AutoBump(ab.gh, ab.audit, train, installed=lambda r: True, targets=lambda: [(R, 1)],
                   state_path=tmp_path / "autobump.json")
    ab2.step()  # remembered across restarts
    assert train.queued() == set()


def test_unreadable_ci_stays_with_the_human(tmp_path):
    _, train, ab = setup(tmp_path, ci="unknown")
    ab.step()
    assert train.queued() == set()
