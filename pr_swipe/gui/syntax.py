"""Syntax colouring for rendered diff lines. Pure apart from Pygments; no Qt.

Each side of a hunk run is lexed as one text (old side: context + deletions, new side: context +
additions), so multi-line strings and comments colour correctly, and the spans are mapped back onto
the display lines. Unknown file types get no colouring, never a guess.

Nix indented strings (`'' ... ''`) whose attribute or builder names a shell script (`script`, `text`,
`ExecStart`, `buildPhase`, `writeShellScript`, ...) are coloured as Bash, with `${...}` interpolations
keeping their Nix colours. A hunk that starts inside such a string has no opener to go on and stays Nix.
"""
import re

from pygments.lexers import BashLexer, NixLexer, get_lexer_for_filename
from pygments.token import Comment, Keyword, Literal, Name, Number, String
from pygments.util import ClassNotFound

CODE_AT = 6  # render_diff lines are f"{n:>4} {sign}{code}"
CATEGORIES = [  # most specific first
    (Comment, "comment"), (String, "string"), (Number, "number"), (Keyword, "keyword"),
    (Name.Builtin, "builtin"), (Name.Function, "function"), (Name.Class, "function"),
    (Name.Tag, "attr"), (Name.Attribute, "attr"), (Name.Property, "attr"), (Name.Variable, "attr"),
    (Literal, "number"),
]
MAX_SIDE = 400_000
SHELL_HINT = re.compile(
    r"\b(script|text|reload|pre(Start|Stop)|post(Start|Stop)|Exec\w+|\w*Phase|\w*Hook|shellInit"
    r"|\w*[iI]nitExtra|\w*ShellInit|initContent|profileExtra|envExtra|extraCommands|extraInit"
    r"|(pre|post)(Install|Fixup|Build|Patch|Unpack|Configure)|activationScripts\S*"
    r"|writeShellScript\w*|writeShellApplication|write(Bash|Dash)\w*|writeScript\w*|runCommand\w*)\b")


def _category(tok):
    for parent, cat in CATEGORIES:
        if tok in parent:
            return cat
    return None


def lexer_for(path):
    if not path:
        return None
    try:
        return get_lexer_for_filename(path, stripnl=False, stripall=False, ensurenl=False)
    except ClassNotFound:
        return None


def _shell_bodies(toks, text):
    """[(start, end)] of Nix indented-string bodies that hold shell code, from NixLexer tokens."""
    out, open_at = [], None
    for pos, tok, val in toks:
        if tok in String.Multiline and val == "''":
            if open_at is None:
                before = re.split(r"[;{}]", text[max(0, pos - 200):pos])[-1]
                open_at = pos + 2 if SHELL_HINT.search(before) else -1
            else:
                if open_at >= 0:
                    out.append((open_at, pos))
                open_at = None
    return out


def _char_categories(lexer, text):
    """Category per character of text (None where uncoloured)."""
    cats = [None] * len(text)
    toks = list(lexer.get_tokens_unprocessed(text))
    for pos, tok, val in toks:
        cats[pos:pos + len(val)] = [_category(tok)] * len(val)
    if isinstance(lexer, NixLexer):
        for b0, b1 in _shell_bodies(toks, text):
            keep = [False] * (b1 - b0)  # Nix ${...} interpolations keep their Nix colours
            depth = 0
            for pos, tok, val in toks:
                if b0 <= pos < b1:
                    if tok in String.Interpol:
                        depth += 1 if val.endswith("{") else -1 if val == "}" else 0
                        keep[pos - b0:pos - b0 + len(val)] = [True] * len(val)
                    elif depth > 0:
                        keep[pos - b0:pos - b0 + len(val)] = [True] * len(val)
            for pos, tok, val in BashLexer().get_tokens_unprocessed(text[b0:b1]):
                for k in range(pos, min(pos + len(val), b1 - b0)):
                    if not keep[k]:
                        cats[b0 + k] = _category(tok)
    return cats


def _lex(lexer, rows):
    """rows: [(line_index, code)] -> {line_index: [(col, length, category)]} in code columns (str indices)."""
    text = "\n".join(code for _, code in rows)
    if len(text) > MAX_SIDE:
        return {}
    out, start = {}, 0
    cats = _char_categories(lexer, text)
    for r, (i, code) in enumerate(rows):
        col = 0
        while col < len(code):
            cat, run = cats[start + col], col
            while col < len(code) and cats[start + col] == cat:
                col += 1
            if cat:
                out.setdefault(i, []).append((run, col - run, cat))
        start += len(code) + 1
    return out


def spans(lines, path=None):
    """lines: [(kind, text)] from render_diff. Returns {line_index: [(col, length, category)]}, where col
    counts str characters from the start of the display text. `file` lines switch the current path."""
    out, cur, old_rows, new_rows = {}, path, [], []

    def flush():
        lx = lexer_for(cur)
        if lx is not None:
            for side in (old_rows, new_rows):
                for i, sp in _lex(lx, side).items():
                    out.setdefault(i, []).extend((c + CODE_AT, n, cat) for c, n, cat in sp)
        old_rows.clear(); new_rows.clear()

    for i, (kind, text) in enumerate(lines):
        if kind == "file":
            flush(); cur = text
        elif kind in ("add", "ctx", "del"):
            code = text[CODE_AT:]
            if kind == "del":
                old_rows.append((i, code))
            elif kind == "add":
                new_rows.append((i, code))
            else:  # context belongs to both sides; it is coloured from the new side only
                old_rows.append((-1, code)); new_rows.append((i, code))
    flush()
    out.pop(-1, None)
    return out
