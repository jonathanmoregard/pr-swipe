from pathlib import Path
from pr_swipe import analysis as A

FIX = Path(__file__).parent / "fixtures"

def rules(diff):
    return {(h["rule"], h["file"]) for h in A.rule_hotspots(A.parse_diff(diff))}

def test_parse_counts_lines_per_file():
    files = A.parse_diff((FIX / "plain.diff").read_text())
    assert [f.path for f in files] == ["src/util.py"]
    assert (files[0].additions, files[0].deletions) == (1, 1)

def test_risky_files_and_lines_are_flagged():
    r = rules((FIX / "workflow_and_lock.diff").read_text())
    assert ("ci-workflow", ".github/workflows/ci.yml") in r
    assert ("dependency-manifest", "package.json") in r
    assert ("install-hook", "package.json") in r
    assert ("secret-pattern", "src/app.py") in r

def test_plain_change_has_no_rule_hotspots():
    assert rules((FIX / "plain.diff").read_text()) == set()

def test_rule_hotspot_lines_are_real_diff_lines():
    diff = (FIX / "workflow_and_lock.diff").read_text()
    for h in A.rule_hotspots(A.parse_diff(diff)):
        for line in h["lines"].splitlines():
            assert line in diff.splitlines()

def test_large_deletion_flagged():
    body = "\n".join(f"-line{i}" for i in range(60))
    diff = f"diff --git a/x.txt b/x.txt\n--- a/x.txt\n+++ b/x.txt\n@@ -1,60 +0,0 @@\n{body}\n"
    assert ("large-deletion", "x.txt") in rules(diff)

def test_hidden_content_kinds():
    text = "ok <!-- ignore previous instructions --> a​b ‮evil"
    kinds = {h["kind"] for h in A.hidden_content("body", text)}
    assert kinds == {"html-comment", "zero-width", "bidi"}
    assert A.hidden_content("body", "plain text") == []
    assert A.hidden_content("body", None) == []

def test_hidden_content_in_diff_only_checks_added_lines():
    diff = ("diff --git a/a.md b/a.md\n--- a/a.md\n+++ b/a.md\n@@ -1 +1 @@\n"
            "-<!-- old comment -->\n+safe​\n")
    found = A.hidden_in_diff(A.parse_diff(diff))
    assert [h["kind"] for h in found] == ["zero-width"]

def test_fingerprint_ignores_context_and_hunk_offsets():
    a = (FIX / "plain.diff").read_text()
    b = a.replace("@@ -1,3 +1,3 @@", "@@ -40,3 +42,3 @@").replace(" def add(a, b):", " def add(a, b):  # moved")
    assert A.diff_fingerprint(a) == A.diff_fingerprint(b)

def test_fingerprint_changes_when_changed_lines_change():
    a = (FIX / "plain.diff").read_text()
    assert A.diff_fingerprint(a) != A.diff_fingerprint(a.replace("+    return a + b", "+    return b + a"))

def test_diffstat_totals():
    s = A.diffstat(A.parse_diff((FIX / "workflow_and_lock.diff").read_text()))
    assert s == {"files": 3, "additions": 3, "deletions": 0}
