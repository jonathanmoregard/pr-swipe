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


def annotate(diff_text, hotspots) -> str:
    """Insert callout lines above each flagged hunk header of one file's diff."""
    by_hunk = {}
    for h in hotspots:
        by_hunk.setdefault(h.get("hunk", ""), []).append(h)
    out, first_hunk_done = [], False
    for line in diff_text.splitlines():
        if line.startswith("@@"):
            notes = by_hunk.get(line, [])
            if not first_hunk_done:
                notes = notes + [h for h in by_hunk.get("", [])]  # file-level flags go on the first hunk
                first_hunk_done = True
            for h in sorted(notes, key=lambda h: h["source"] != "rule"):
                if h["source"] == "rule":
                    out.append(f"▶ 🚩 rule: {h['rule']}")
                else:
                    out.append(f"▶ ⚠ AI {h.get('severity', 'low')}: {h.get('why', '')}")
        out.append(line)
    return "\n".join(out)
