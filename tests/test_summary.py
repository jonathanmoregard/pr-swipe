from pr_swipe.gui import summary as SU
from tests.cards import make_card

DEPENDABOT_BODY = """Bumps the npm_and_yarn group with 8 updates in the / directory:

| Package | From | To |
| --- | --- | --- |
| [vite](https://github.com/vitejs/vite) | `5.4.2` | `6.0.3` |

<!-- Dependabot commands -->
<!-- more -->
"""

PKG_DIFF = """diff --git a/package.json b/package.json
--- a/package.json
+++ b/package.json
@@ -18,4 +18,4 @@
-    "vite": "^5.4.2",
-    "esbuild": "^0.21.5",
+    "vite": "^6.0.3",
+    "esbuild": "^0.25.0",
diff --git a/pnpm-lock.yaml b/pnpm-lock.yaml
--- a/pnpm-lock.yaml
+++ b/pnpm-lock.yaml
@@ -1,2 +1,2 @@
-a
+b
"""


def test_headline_is_first_prose_sentence_not_markdown_table():
    head, from_body = SU.intent_headline(DEPENDABOT_BODY, "chore(deps): bump")
    assert from_body and head == "Bumps the npm_and_yarn group with 8 updates in the / directory"
    assert "|" not in head


def test_headline_strips_links_and_caps_length_and_falls_back_to_title():
    head, _ = SU.intent_headline("See [the docs](http://x) for **why**. Second.", "t")
    assert head == "See the docs for why."
    assert len(SU.intent_headline("word " * 100, "t")[0]) <= SU.HEADLINE_MAX
    assert SU.intent_headline("<!-- only a comment -->\n| a |", "the title") == ("the title", False)


def test_clean_body_hides_comments_and_reveals_invisible_characters():
    out = SU.clean_body("a <!-- hidden --> @​bob")
    assert "hidden" not in out and "​" not in out and "U+200B" in out
    assert SU.html_comment_count(DEPENDABOT_BODY) == 2


def test_path_without_ai_uses_file_roles_verified_counts_and_ci():
    c = make_card(detail={"diff": PKG_DIFF, "truncated": False, "comments": []},
                  ci={"state": "failure", "failing": ["test"]}, hotspots=[])
    c["context"] = {"purpose": "", "solution": "", "notes": ""}
    steps = SU.solution_path(c)
    assert [s["text"] for s in steps] == ["package.json: 2 version ranges bumped, 1 major",
                                          "pnpm-lock.yaml regenerated (+1 −1)", "CI fails: test"]
    assert all(s["source"] != "ai" for s in steps)
    assert "no AI" in SU.path_source_note(c)


def test_path_with_ai_marks_the_ai_step_unverified_and_stays_capped():
    c = make_card(detail={"diff": PKG_DIFF, "truncated": False, "comments": []}, ci={"state": "success", "failing": []},
                  hotspots=[{"source": "rule", "file": f"f{i}", "hunk": "", "why": "w", "rule": "r"} for i in range(9)])
    c["context"] = {"purpose": "p", "solution": "retry with backoff", "notes": ""}
    commits = [{"sha": "a" * 40, "subject": "add helper"}, {"sha": "b" * 40, "subject": "Merge main"}]
    steps = SU.solution_path(c, commits)
    assert steps[0] == {"text": "Add helper", "src": "commit aaaaaaa", "source": "plain"}
    assert {"text": "Retry with backoff", "src": "AI · unverified", "source": "ai"} in steps
    assert len(steps) <= SU.MAX_STEPS


def test_identical_warnings_collapse_and_risky_ones_stay_separate_and_first():
    hidden = [{"where": "body", "kind": "html-comment", "text": "x"}] * 26 + [
        {"where": "body", "kind": "zero-width", "text": "@⟨U+200B⟩l-Michalek"}]
    rows = SU.warning_rows(make_card(hidden_content=hidden))
    assert len(rows) == 2
    assert rows[0]["risky"] and "zero-width" in rows[0]["text"]
    assert rows[1] == {"count": "26×", "text": "HTML comment in body · not rendered", "where": "body", "risky": False}
    assert SU.warning_total(make_card(hidden_content=hidden)) == 27


def test_steps_and_body_stay_readable():
    long = "Adds the module. " + "It does more. " * 40
    assert SU.short(long) == "Adds the module."
    assert len(SU.short("y" * 500)) <= SU.STEP_MAX
    out = SU.clean_body('<details><summary>Notes</summary><a href="u">x</a></details> `ignore <dependency name>`')
    assert out == "Notesx `ignore <dependency name>`"


def test_step_text_is_a_glance_and_intent_prefers_the_stated_value():
    assert SU.step_text("fix(mcp): let a caller override USER_GOOGLE_EMAIL (for gdocs-review)") == \
        "Let a caller override USER_GOOGLE_EMAIL"
    assert len(SU.step_text("feat: " + "word " * 40)) <= SU.STEP_SHORT
    c = make_card(); c["context"]["purpose"] = "Make the Forms tools usable. Because reasons."
    c["_record"] = {"body": "The server was spawned from a config. More."}
    head, src, tone, pr = SU.intent(c)
    assert head == "Make the Forms tools usable." and "unverified" in src and tone == "ai"
    assert pr == "PR says: The server was spawned from a config."
    c["context"]["purpose"] = ""
    assert SU.intent(c) == ("The server was spawned from a config.", "first sentence of PR body · verified ✓", "sprout", "")


def test_description_preview_stops_before_a_table_or_fence():
    body = "Bumps foo.\n\nWhy now.\n| a | b |\n|---|---|\n| 1 | 2 |\nmore"
    assert SU.desc_preview(body) == "Bumps foo."
    assert SU.desc_preview("Why now.\n| a | b |") == "Why now."
    assert SU.desc_preview("```\ncode\n```") == "_(starts with a table or code block · d expands)_"
    assert SU.desc_preview("1\n2\n3\n4\n5\n6") == "1\n2\n3\n4"


def test_value_first_drops_merge_lead_in():
    assert SU.value_first("Once this merges, you can swipe PRs.") == "You can swipe PRs."
    assert SU.value_first("With this merged, commands can't touch the cache.") == "Commands can't touch the cache."
    assert SU.value_first("Once merged, x") == "X"
    assert SU.value_first("Lets agents run designs.") == "Lets agents run designs."
