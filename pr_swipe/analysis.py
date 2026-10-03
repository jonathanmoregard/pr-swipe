"""Deterministic PR analysis. Pure functions; nothing here talks to the network or a model.

Rule hotspots are computed here so the AI reviewer cannot suppress them.
"""
import hashlib
import re
from dataclasses import dataclass, field

MAX_HOTSPOT_LINES = 40
MAX_HOTSPOT_LINE = 400  # one minified line must not blow the card's 20000-char hotspot cap


@dataclass
class Hunk:
    header: str
    lines: list = field(default_factory=list)


@dataclass
class FileDiff:
    path: str
    hunks: list = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    binary: bool = False


_DIFF_GIT = re.compile(r"^diff --git a/(.+?) b/(.+)$")


def parse_diff(text: str) -> list:
    files, cur, hunk = [], None, None
    for line in text.splitlines():
        m = _DIFF_GIT.match(line)
        if m:
            cur, hunk = FileDiff(path=m.group(2)), None
            files.append(cur)
            continue
        if cur is None:
            continue
        if line.startswith("Binary files ") or line == "GIT binary patch":
            cur.binary = True
            continue
        if line.startswith("@@"):
            hunk = Hunk(header=line)
            cur.hunks.append(hunk)
            continue
        if hunk is None:  # index / --- / +++ / mode lines before the first hunk
            continue
        hunk.lines.append(line)
        if line.startswith("+"):
            cur.additions += 1
        elif line.startswith("-"):
            cur.deletions += 1
    return files


PATH_RULES = [
    ("ci-workflow", re.compile(r"(^|/)\.github/workflows/")),
    ("dependency-manifest", re.compile(
        r"(^|/)(package(-lock)?\.json|pnpm-lock\.yaml|yarn\.lock|Cargo\.(toml|lock)|pyproject\.toml"
        r"|poetry\.lock|uv\.lock|requirements[^/]*\.txt|go\.(mod|sum)|Gemfile(\.lock)?)$")),
    ("nix-flake", re.compile(r"(^|/)flake\.(nix|lock)$")),
    ("secrets", re.compile(r"(^|/)secrets/|\.age$")),
    ("auth-crypto", re.compile(r"(auth|crypt|token|secret|passw|credential|permission)", re.I)),
    ("container-iac", re.compile(r"(^|/)(Dockerfile[^/]*|docker-compose[^/]*\.ya?ml|[^/]+\.tf)$")),
    ("agent-permissions", re.compile(r"(^|/)(settings(\.local)?\.json$|hooks/|\.claude/|CLAUDE\.md$|AGENTS\.md$)")),
    ("install-hook", re.compile(r"(^|/)(install\.sh|postinstall[^/]*|setup\.py|\.husky/)")),
]

LINE_RULES = [
    ("install-hook", re.compile(r'"(pre|post)?install"\s*:')),
    ("secret-pattern", re.compile(
        r"(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-ant-[A-Za-z0-9-]{20,}"
        r"|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")),
]

LARGE_DELETION = 50


def hotspot_lines(hunk) -> str:
    return "\n".join(l if len(l) <= MAX_HOTSPOT_LINE else l[:MAX_HOTSPOT_LINE] + " …[line truncated]"
                     for l in hunk.lines[:MAX_HOTSPOT_LINES])


def _spot(rule, f, hunk):
    return {
        "source": "rule", "rule": rule, "file": f.path,
        "hunk": hunk.header if hunk else "",
        "lines": hotspot_lines(hunk) if hunk else "",
    }


def rule_hotspots(files) -> list:
    out, seen = [], set()

    def add(rule, f, hunk):
        key = (rule, f.path, hunk.header if hunk else "")
        if key not in seen:
            seen.add(key)
            out.append(_spot(rule, f, hunk))

    for f in files:
        first = f.hunks[0] if f.hunks else None
        for rule, rx in PATH_RULES:
            if rx.search(f.path):
                add(rule, f, first)
        for h in f.hunks:
            added = [l[1:] for l in h.lines if l.startswith("+")]
            for rule, rx in LINE_RULES:
                if any(rx.search(l) for l in added):
                    add(rule, f, h)
        if f.binary:
            add("binary", f, None)
        if f.deletions > LARGE_DELETION:
            add("large-deletion", f, first)
    return out


_HTML_COMMENT = re.compile(r"<!--(.*?)-->", re.S)
_ZW = re.compile("[\u200b\u200c\u200d\u200e\u200f\u2060\ufeff]")
_BIDI = re.compile("[\u202a-\u202e\u2066-\u2069]")


def _reveal(text, rx):
    m = rx.search(text)
    start = max(0, m.start() - 60)
    snippet = text[start:m.end() + 60]
    return rx.sub(lambda c: f"<U+{ord(c.group()):04X}>", snippet)


def hidden_content(where, text, kinds=("html-comment", "zero-width", "bidi")) -> list:
    if not text:
        return []
    out = []
    if "html-comment" in kinds:
        for m in _HTML_COMMENT.finditer(text):
            out.append({"where": where, "kind": "html-comment", "text": m.group(1).strip()[:500]})
    if "zero-width" in kinds and _ZW.search(text):
        out.append({"where": where, "kind": "zero-width", "text": _reveal(text, _ZW)})
    if "bidi" in kinds and _BIDI.search(text):
        out.append({"where": where, "kind": "bidi", "text": _reveal(text, _BIDI)})
    return out


def hidden_in_diff(files) -> list:
    """Invisible characters in added lines. HTML comments in code are normal, so not flagged."""
    out = []
    for f in files:
        added = "\n".join(l[1:] for h in f.hunks for l in h.lines if l.startswith("+"))
        out += hidden_content(f"diff:{f.path}", added, kinds=("zero-width", "bidi"))
    return out


def diff_fingerprint(text: str) -> str:
    """Hash of the PR's own changes: file paths plus added/removed lines.

    Context lines and hunk offsets are excluded, so merging the base branch into the PR
    without conflicts leaves the fingerprint unchanged.
    """
    h = hashlib.sha256()
    for f in parse_diff(text):
        h.update(f"F {f.path} {f.binary}\n".encode())
        for hunk in f.hunks:
            for line in hunk.lines:
                if line[:1] in ("+", "-"):
                    h.update(line.encode() + b"\n")
    return h.hexdigest()


def diffstat(files) -> dict:
    return {
        "files": len(files),
        "additions": sum(f.additions for f in files),
        "deletions": sum(f.deletions for f in files),
    }
