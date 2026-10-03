"""What a card is for and how it gets there, in words a human reads first. Pure; no Qt.

The intent headline is always verified git text (the PR body's first prose sentence, else the title),
never AI prose. The solution path mixes sources but tags every step with where it came from; AI steps
are marked unverified. Without an AI review the path is built only from verified file roles, counts
and CI. Warnings are grouped so dozens of identical hits read as one line, while the risky kinds
(invisible characters, executor hand-backs) keep a line of their own.
"""
import re

from .. import analysis
from .review import low_signal

HEADLINE_MAX = 160
MAX_STEPS = 5

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_TAG = re.compile(r"<[^>]+>")
_MARK = re.compile(r"\*\*|__|`|~~|(?<!\w)[*_](?=\w)|(?<=\w)[*_](?!\w)")  # emphasis, not snake_case
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_HTML_TAG = re.compile(r"</?(a|abbr|b|blockquote|br|code|details|div|em|h[1-6]|hr|i|img|li|ol|p|pre|span|strong|"
                       r"sub|summary|sup|table|tbody|td|th|thead|tr|ul)\b[^>]*>", re.I)
_PARTIAL_TAG = re.compile(r"<[^>]*$")
STEP_MAX = 140
_SKIP = re.compile(r"^\s*(\||#|>|```|---|===|<details|<summary|- \[[ x]\])")


def clean_body(body):
    """Body text for display: HTML comments and known HTML tags removed (their text kept), invisible
    characters revealed. Unknown angle-bracket text such as `<dependency name>` stays."""
    text = _HTML_TAG.sub("", _COMMENT.sub("", body or ""))
    for rx in (analysis._ZW, analysis._BIDI):
        text = rx.sub(lambda m: f"⟨U+{ord(m.group()):04X}⟩", text)
    return text


def desc_preview(body, lines=4):
    """Collapsed description: the first paragraph, at most `lines` lines, stopping before a table or
    code fence so the preview never ends mid-row."""
    out = []
    for line in clean_body(body).lstrip("\n").splitlines():
        if not line.strip() or line.lstrip().startswith(("|", "```", "~~~")) or len(out) == lines:
            break
        out.append(line)
    text = "\n".join(out).strip()
    return text or "_(starts with a table or code block · d expands)_"


def short(text, limit=STEP_MAX):
    """First sentence, whitespace collapsed, capped."""
    text = re.sub(r"\s+", " ", text or "").strip()
    first = _SENTENCE.split(text, maxsplit=1)[0]
    return first if len(first) <= limit else first[:limit - 1].rstrip() + "…"


_CC_PREFIX = re.compile(r"^[a-z]+(\([^)]*\))?!?:\s*")
_PAREN = re.compile(r"\s*\([^()]*\)")
STEP_SHORT = 72


def step_text(text):
    """A path step in a glance: no conventional-commit prefix, no parentheticals, one short sentence."""
    t = _PAREN.sub("", _CC_PREFIX.sub("", re.sub(r"\s+", " ", text or "").strip()))
    t = short(t, STEP_SHORT)
    return t[:1].upper() + t[1:]


def html_comment_count(body):
    return len(_COMMENT.findall(body or ""))


def intent_headline(body, title):
    """(headline, from_body). First prose sentence of the PR body, markdown removed, else the title."""
    for line in clean_body(body).splitlines():
        if not line.strip() or _SKIP.match(line):
            continue
        text = _MARK.sub("", _TAG.sub("", _LINK.sub(r"\1", line))).strip(" -*+\t")
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 8:
            continue
        first = _SENTENCE.split(text, maxsplit=1)[0].rstrip(":").strip()
        if len(first) > HEADLINE_MAX:
            first = first[:HEADLINE_MAX - 1].rstrip() + "…"
        return first, True
    return title, False


# --- solution path -------------------------------------------------------------------------------------------
LOCKFILES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "Cargo.lock", "flake.lock", "poetry.lock",
             "uv.lock", "go.sum", "Gemfile.lock", "composer.lock", "Pipfile.lock", "bun.lockb"}
MANIFESTS = {"package.json", "pyproject.toml", "Cargo.toml", "go.mod", "Gemfile", "composer.json", "Pipfile",
             "setup.py", "setup.cfg", "flake.nix"}
ROLE_ORDER = ["manifest", "source", "generated", "tests", "docs", "workflow"]
ROLE_TAG = {"manifest": "files · manifest", "source": "files · source", "generated": "files · lockfile · generated",
            "tests": "files · tests", "docs": "files · docs", "workflow": "files · workflow"}
_TEST = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]+$|_test\.\w+$|\.(spec|test)\.\w+$")
_NPM_DEP = re.compile(r'^([+-])\s*"([^"]+)"\s*:\s*"[\^~>=<]*v?(\d+)[^"]*"')


def file_role(path):
    name = path.rsplit("/", 1)[-1]
    if name in LOCKFILES or low_signal(path):
        return "generated"
    if path.startswith(".github/workflows/"):
        return "workflow"
    if name in MANIFESTS or re.match(r"requirements.*\.txt$", name):
        return "manifest"
    if _TEST.search(path):
        return "tests"
    if name.endswith((".md", ".rst", ".txt")) or path.startswith("docs/"):
        return "docs"
    return "source"


def npm_bumps(fd):
    """(changed, major) version ranges in a package.json diff, from verified +/- lines only."""
    old, new = {}, {}
    for h in fd.hunks:
        for line in h.lines:
            m = _NPM_DEP.match(line)
            if m:
                (old if m.group(1) == "-" else new)[m.group(2)] = int(m.group(3))
    changed = [k for k in new if k in old]
    return len(changed), sum(1 for k in changed if new[k] != old[k])


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def _role_step(role, fds):
    adds, dels = sum(f.additions for f in fds), sum(f.deletions for f in fds)
    size = f"(+{adds} −{dels})"
    one = fds[0].path if len(fds) == 1 else None
    if role == "manifest" and one and one.endswith("package.json"):
        changed, major = npm_bumps(fds[0])
        if changed:
            return f"{one}: {_plural(changed, 'version range')} bumped" + (f", {major} major" if major else "")
    if role == "generated":
        return f"{one} regenerated {size}" if one else f"{len(fds)} generated files regenerated {size}"
    noun = {"manifest": "manifest file", "source": "source file", "tests": "test file", "docs": "doc file",
            "workflow": "workflow file"}[role]
    return f"{one} changed {size}" if one else f"{_plural(len(fds), noun)} changed {size}"


def solution_path(card, commits=()):
    """[{text, src, source}] with source in plain|ai|risk. 2–5 steps, never empty prose."""
    fds = analysis.parse_diff(card.get("detail", {}).get("diff", ""))
    rule = {}
    for h in card.get("hotspots", []):
        if h.get("source") == "rule":
            rule.setdefault(h["file"], h)
    ai_solution = (card.get("context", {}).get("solution") or "").strip()
    steps = []
    if ai_solution:
        for cm in [c for c in commits if not c["subject"].startswith("Merge ")][:2]:
            steps.append({"text": step_text(cm["subject"]), "src": f"commit {cm['sha'][:7]}", "source": "plain"})
        steps.append({"text": step_text(ai_solution), "src": "AI · unverified", "source": "ai"})
    else:
        by_role = {}
        for f in fds:
            by_role.setdefault(file_role(f.path), []).append(f)
        for role in ROLE_ORDER:
            if role in by_role:
                flagged = [f for f in by_role[role] if f.path in rule]
                steps.append({"text": _role_step(role, by_role[role]), "source": "risk" if flagged else "plain",
                              "src": ROLE_TAG[role] + (" · 🚩 rule" if flagged else "")})
    for path, h in rule.items():
        if ai_solution and len(steps) < MAX_STEPS - 1:
            steps.append({"text": f"Touches {h.get('rule', 'a flagged path')}: {path.rsplit('/', 1)[-1]}",
                          "src": "files · 🚩 rule", "source": "risk"})
    steps = steps[:MAX_STEPS - 1]
    ci = card.get("ci", {})
    if ci.get("state") == "failure":
        failing = ", ".join(ci.get("failing") or [])
        steps.append({"text": short(f"CI fails: {failing}" if failing else "CI fails", STEP_SHORT),
                      "src": "CI · verified", "source": "risk"})
    elif ci.get("state") == "pending":
        steps.append({"text": "CI still running", "src": "CI · verified", "source": "plain"})
    return steps[:MAX_STEPS]


def path_source_note(card):
    if (card.get("context", {}).get("solution") or "").strip():
        return "from commits, files, CI and AI"
    return "from commits, file roles and CI only — no AI context"


# --- warnings --------------------------------------------------------------------------------------------------
KIND_NAME = {"html-comment": "HTML comment", "zero-width": "zero-width character", "bidi": "bidi control"}


def _where(w):
    return "diff" if w.startswith("diff:") else "comment" if w.startswith("comment:") else w


def warning_rows(card):
    """[{count, text, where, risky}] risky first. Identical non-risky hits collapse to one row."""
    risky, grouped = [], {}
    if card.get("_unverified"):
        risky.append({"count": "🧭", "text": "unverified: external repo, shown as the collector saw it; any swipe "
                      "opens the browser", "where": "", "risky": True})
    if card.get("_returned"):
        risky.append({"count": "↩", "text": f"returned by executor: {card['_returned']}", "where": "", "risky": True})
    for h in card.get("hidden_content", []):
        if h["kind"] in ("zero-width", "bidi"):
            sample = re.sub(r"\s+", " ", _PARTIAL_TAG.sub("", _TAG.sub("", h["text"]))).strip()[:160]  # raw excerpt: w shows all
            risky.append({"count": "1×", "text": f"{KIND_NAME[h['kind']]} in {h['where']}: {sample}",
                          "where": h["where"], "risky": True})
        else:
            key = (h["kind"], _where(h["where"]))
            g = grouped.setdefault(key, {"count": 0, "where": h["where"]})
            g["count"] += 1
    rows = risky + [{"count": f"{g['count']}×", "text": f"{KIND_NAME.get(k, k)} in {w} · not rendered",
                     "where": g["where"], "risky": False} for (k, w), g in grouped.items()]
    return rows


def warning_total(card):
    return len(card.get("hidden_content", [])) + bool(card.get("_unverified")) + bool(card.get("_returned"))


_MERGED_LEAD = re.compile(r"^(?:with this (?:merged|in place)|once (?:this )?(?:is )?merged?s?|after (?:this )?merg(?:es|ing)),\s*", re.I)


def value_first(purpose):
    """Drop a "Once merged," lead-in: every PR's value is conditional on merging, so it says nothing."""
    text = _MERGED_LEAD.sub("", purpose.strip())
    return text[:1].upper() + text[1:]


def intent(card):
    """(headline, source, tone, pr_line). The value, if the AI stated one, else the PR's own words.
    pr_line is the verified sentence shown under an AI headline so the evidence stays in view."""
    rec = card.get("_record")
    said, from_body = intent_headline(rec.get("body") if rec else None, card["title"])
    verified = ("first sentence of PR body" if from_body else "PR title") + (" · verified ✓" if rec else " · not verified")
    purpose = (card.get("context", {}).get("purpose") or "").strip()
    if purpose:
        return short(value_first(purpose), HEADLINE_MAX), "what it enables · AI · unverified", "ai", f"PR says: {said}"
    return said, verified, "sprout" if rec else "muted", ""
