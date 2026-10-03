"""Review view logic: file ordering, low-signal grouping, coverage. Pure; no Qt.

Salience rules: rule flags (deterministic, from the verified diff) rank first and can never be
collapsed; AI annotations can only raise a file into the AI tier, never lower it; the only
collapsing is the deterministic low-signal group.
"""
import re
import time

SEV = {"high": 3, "medium": 2, "low": 1}
SEV_NAME = {v: k for k, v in SEV.items()}
LOW_SIGNAL = re.compile(
    r"(^|/)(vendor|third_party|node_modules|dist)/"
    r"|\.min\.(js|css)$|\.map$|_pb2(_grpc)?\.py$|\.pb\.go$|(^|/)__snapshots__/|\.snap$")
SEEN_AFTER_S = 1.5


def low_signal(path) -> bool:
    return bool(LOW_SIGNAL.search(path))


def order_files(files, hotspots) -> list:
    rule_files = {h["file"] for h in hotspots if h["source"] == "rule"}
    ai_sev = {}
    for h in hotspots:
        if h["source"] == "ai":
            ai_sev[h["file"]] = max(ai_sev.get(h["file"], 0), SEV.get(h.get("severity"), 1))
    out = []
    for f in files:
        p = f["path"]
        if p in rule_files:
            tier = 0
        elif p in ai_sev:
            tier = 1
        elif low_signal(p):
            tier = 3
        else:
            tier = 2
        out.append({**f, "tier": tier, "low_signal": tier == 3, "rule": p in rule_files,
                    "ai_severity": ai_sev.get(p, 0)})
    return sorted(out, key=lambda x: (x["tier"], -x["ai_severity"], -(x["additions"] + x["deletions"]), x["path"]))


class Coverage:
    """What the human has actually looked at on this card."""

    def __init__(self, paths, hotspots, clock=time.monotonic):
        self.paths, self.clock = list(paths), clock
        self.flagged = [(h["file"], h.get("hunk", "")) for h in hotspots if h["file"] in set(self.paths)]
        self.seen, self.visited = set(), set()
        self.current, self._since = None, None

    def see_file(self, path):
        self.seen.add(path)
        self.visited |= {i for i, (p, _) in enumerate(self.flagged) if p == path}

    def visit_hunk(self, i):
        self.visited.add(i)

    def showing(self, path):
        """Call when the selected file changes; a file shown for SEEN_AFTER_S counts as seen."""
        self.tick()
        self.current, self._since = path, self.clock()

    def tick(self):
        if self.current is not None and self.clock() - self._since >= SEEN_AFTER_S:
            self.see_file(self.current)

    def gate_ok(self) -> bool:
        self.tick()
        return len(self.visited) == len(self.flagged)

    def remaining(self) -> list:
        return [self.flagged[i] for i in range(len(self.flagged)) if i not in self.visited]

    def summary(self) -> str:
        self.tick()
        return f"seen {len(self.seen)}/{len(self.paths)} files · flagged hunks {len(self.visited)}/{len(self.flagged)}"


_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_FILE = re.compile(r"^diff --git a/.* b/(.*)$")
_SKIP = ("index ", "--- ", "+++ ", "similarity ", "rename ", "old mode", "new mode", "new file mode",
         "deleted file mode")


def callout(h) -> tuple:
    if h["source"] == "rule":
        return "rule", f"🚩 rule: {h['rule']}"
    return "ai", f"⚠ AI {h.get('severity', 'low')} · unverified — {h.get('why', '')}"


def render_diff(diff_text, hotspots, path=None) -> list:
    """Diff → [(kind, text)] for display: line numbers, and callouts under each flagged hunk header.

    kind is one of file, meta, hunk, rule, ai, add, del, ctx. File-level flags (no hunk) go under
    the file's first hunk. `path` names the file when the text has no `diff --git` header.
    """
    out, cur, first, old, new = [], path, True, 0, 0
    for line in diff_text.splitlines():
        m = _FILE.match(line)
        if m:
            if path is None:  # whole-diff mode: a header row per file
                cur, first = m.group(1), True
                out.append(("file", cur))
            continue
        if line.startswith(_SKIP):
            continue
        h = _HUNK.match(line)
        if h:
            old, new = int(h.group(1)), int(h.group(2))
            out.append(("hunk", line))
            mine = [x for x in hotspots if x["file"] == cur and (x.get("hunk") == line or (first and not x.get("hunk")))]
            out += [callout(x) for x in sorted(mine, key=lambda x: x["source"] != "rule")]
            first = False
        elif line.startswith("+"):
            out.append(("add", f"{new:>4} {line}")); new += 1
        elif line.startswith("-"):
            out.append(("del", f"{old:>4} {line}")); old += 1
        elif line.startswith(" "):
            out.append(("ctx", f"{new:>4} {line}")); old += 1; new += 1
        else:
            out.append(("meta", line))
    return out


AGE_MAGIC = b"age-encryption.org/v1\n"


def age_recipients(data):
    """Recipient stanzas from an age header: [(type, id)]. X25519 stanzas carry only an ephemeral share,
    so their id is None; ssh stanzas carry a tag of the recipient's key."""
    if not data or not data.startswith(AGE_MAGIC):
        return None
    out = []
    for line in data[len(AGE_MAGIC):].split(b"\n"):
        if line.startswith(b"---"):
            break
        if line.startswith(b"-> "):
            parts = line[3:].decode("ascii", "replace").split()
            kind = parts[0] if parts else "?"
            if kind.endswith("-grease"):  # random padding stanzas, not recipients
                continue
            out.append((kind, parts[1] if kind.startswith("ssh-") and len(parts) > 1 else None))
    return out


def _describe_recipients(rs):
    kinds = {}
    for k, _ in rs:
        kinds[k] = kinds.get(k, 0) + 1
    return f"{len(rs)} recipient{'s' if len(rs) != 1 else ''} (" + ", ".join(f"{n}× {k}" for k, n in kinds.items()) + ")"


def binary_lines(path, old, new, hotspots):
    """[(kind, text)] for a binary file: flags first, then what can be checked without decoding it."""
    out = [callout(h) for h in sorted((h for h in hotspots if h["file"] == path), key=lambda h: h["source"] != "rule")]
    size = lambda b: "absent" if b is None else f"{len(b)} bytes"  # noqa: E731
    out.append(("meta", f"binary file · before: {size(old)} · after: {size(new)}"))
    ra, rb = age_recipients(old), age_recipients(new)
    if rb is not None or ra is not None:
        out.append(("hunk", "age-encrypted secret: contents cannot be shown; check who can decrypt it"))
        if ra is not None:
            out.append(("del", f"     - before  {_describe_recipients(ra)}"))
        if rb is not None:
            out.append(("add", f"     + after   {_describe_recipients(rb)}"))
        tags_a = {i for _, i in ra or [] if i}
        tags_b = {i for _, i in rb or [] if i}
        out += [("add", f"     + ssh recipient {t}") for t in sorted(tags_b - tags_a)]
        out += [("del", f"     - ssh recipient {t}") for t in sorted(tags_a - tags_b)]
        if ra is not None and rb is not None and len(ra) == len(rb) and tags_a == tags_b:
            out.append(("ctx", "       same recipients; the ciphertext changed (re-encrypted or new value)"))
        return out
    if new:
        out.append(("ctx", "       first bytes: " + new[:24].hex(" ")))
    return out
