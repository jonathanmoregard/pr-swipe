from pr_swipe.gui import review as RV


def f(path, add=1, dele=0):
    return {"path": path, "additions": add, "deletions": dele, "binary": False, "status": "M"}


def rule(path):
    return {"source": "rule", "rule": "ci-workflow", "file": path, "hunk": "@@ -1 +1 @@", "lines": "+x"}


def ai(path, sev="high", hunk="@@ -1 +1 @@"):
    return {"source": "ai", "file": path, "hunk": hunk, "lines": "+x", "why": "because", "severity": sev}


def test_low_signal_is_deterministic_and_conservative():
    assert RV.low_signal("vendor/lib/x.go") and RV.low_signal("web/app.min.js") and RV.low_signal("api_pb2.py")
    assert RV.low_signal("tests/__snapshots__/a.snap")
    assert not RV.low_signal("package-lock.json")      # supply chain: never low-signal
    assert not RV.low_signal("src/app.py")


def test_order_tiers_rule_then_ai_by_severity_then_churn_then_low_signal():
    files = [f("src/small.py", 1), f("vendor/x.go", 500), f("src/big.py", 90), f("src/ai_low.py"),
             f("src/ai_high.py"), f(".github/workflows/ci.yml")]
    spots = [rule(".github/workflows/ci.yml"), ai("src/ai_low.py", "low"), ai("src/ai_high.py", "high")]
    order = [x["path"] for x in RV.order_files(files, spots)]
    assert order == [".github/workflows/ci.yml", "src/ai_high.py", "src/ai_low.py", "src/big.py",
                     "src/small.py", "vendor/x.go"]


def test_a_rule_flag_beats_low_signal_and_ai_cannot_demote():
    files = [f("vendor/evil.go"), f("src/a.py", 50)]
    order = RV.order_files(files, [rule("vendor/evil.go"), ai("vendor/evil.go", "low")])
    assert order[0]["path"] == "vendor/evil.go" and order[0]["tier"] == 0 and not order[0]["low_signal"]


def test_coverage_gate_needs_every_flagged_hunk():
    spots = [rule("a.py"), ai("b.py"), ai("b.py", hunk="@@ -9 +9 @@")]
    cov = RV.Coverage(["a.py", "b.py", "c.py"], spots)
    assert not cov.gate_ok() and cov.summary() == "seen 0/3 files · flagged hunks 0/3"
    cov.visit_hunk(0)
    cov.see_file("b.py")             # seeing a file counts its flagged hunks as visited
    assert cov.gate_ok() and cov.summary() == "seen 1/3 files · flagged hunks 3/3"
    assert cov.remaining() == []


def test_annotate_puts_callouts_above_flagged_hunks():
    diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
    out = RV.annotate(diff, [rule("a.py"), ai("a.py")])
    lines = out.splitlines()
    i = lines.index("@@ -1 +1 @@")
    assert lines[i - 2].startswith("▶ 🚩 rule: ci-workflow") and lines[i - 1].startswith("▶ ⚠ AI high: because")
