"""Syntax colouring for rendered diff lines. Pure apart from Pygments; no Qt.

Each side of a hunk run is lexed as one text (old side: context + deletions, new side: context +
additions), so multi-line strings and comments colour correctly, and the spans are mapped back onto
the display lines. Unknown file types get no colouring, never a guess.
"""
from pygments.lexers import get_lexer_for_filename
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


def _lex(lexer, rows):
    """rows: [(line_index, code)] -> {line_index: [(col, length, category)]} in code columns (str indices)."""
    text = "\n".join(code for _, code in rows)
    if len(text) > MAX_SIDE:
        return {}
    out, r, col = {}, 0, 0
    for tok, val in lexer.get_tokens(text):
        cat = _category(tok)
        parts = val.split("\n")
        for k, part in enumerate(parts):
            if k:
                r, col = r + 1, 0
            if part and cat and r < len(rows):
                out.setdefault(rows[r][0], []).append((col, len(part), cat))
            col += len(part)
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
