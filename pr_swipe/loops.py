"""Loops: open balls (follow-ups, blocked-on-human items, decisions, chores) as GitHub issues labelled
`loop` in one private repo. Every rule lives here, so the MCP server, the loops service and anything
else that writes loops share the same gates:

- the repo is fixed when `Loops` is built; no method takes one;
- every write first checks the issue carries the `loop` label, so nothing else in the repo is touched;
- titles, bodies and notes are size-capped; kind, owner, dates and source are validated;
- writes are rate-limited per process;
- every loop carries a key; adding a known key returns the existing loop, open or closed.

The issue body holds the free text plus machine lines (`due:`, `until:`, the key marker); labels hold
kind, owner, source and snooze.
"""
import dataclasses
import hashlib
import re
import time

LABEL = "loop"
KINDS = ("followup", "blocked", "decision", "chore")
OWNERS = ("human", "agent")
TITLE_MAX, BODY_MAX, NOTE_MAX, WRITES_PER_MIN = 120, 4000, 2000, 30
KEY_RE = re.compile(r"<!-- loop-key: ([0-9a-f]{16}) -->")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_MACHINE = re.compile(r"^(?:due:\d{4}-\d{2}-\d{2}|until:\d{4}-\d{2}-\d{2}|<!-- loop-key: [0-9a-f]{16} -->)\s*$", re.M)


class LoopError(ValueError):
    pass


def loop_key(*parts) -> str:
    norm = "\x1f".join(re.sub(r"\s+", " ", str(p)).strip().lower() for p in parts)
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


@dataclasses.dataclass(frozen=True)
class Loop:
    number: int
    title: str
    kind: str
    owner: str
    source: str
    state: str
    reason: str
    due: str
    until: str
    key: str
    url: str
    created_at: str
    body: str

    def as_dict(self):
        return dataclasses.asdict(self)


def _names(issue):
    return [l["name"] if isinstance(l, dict) else l for l in issue.get("labels", [])]


def _line(body, name):
    m = re.search(rf"^{name}:(\d{{4}}-\d{{2}}-\d{{2}})\s*$", body or "", re.M)
    return m.group(1) if m else ""


def from_issue(i) -> Loop:
    names, body = _names(i), i.get("body") or ""
    pick = lambda prefix, default="": next((n[len(prefix):] for n in names if n.startswith(prefix)), default)  # noqa: E731
    key = KEY_RE.search(body)
    return Loop(number=i["number"], title=i["title"], kind=pick("kind:", "chore"), owner=pick("owner:", "human"),
                source=pick("src:"), state=i.get("state", "open"), reason=i.get("state_reason") or "",
                due=_line(body, "due"), until=_line(body, "until"), key=key.group(1) if key else "",
                url=i.get("html_url", ""), created_at=i.get("created_at", ""),
                body=_MACHINE.sub("", body).strip())


def _body(text, due, until, key):
    tail = [f"due:{due}"] * bool(due) + [f"until:{until}"] * bool(until) + [f"<!-- loop-key: {key} -->"]
    return ((text.strip() + "\n\n") if text.strip() else "") + "\n".join(tail)


def _date(value, what):
    if value and not DATE_RE.fullmatch(value):
        raise LoopError(f"{what} must be YYYY-MM-DD, got {value!r}")
    return value or ""


class Loops:
    def __init__(self, gh, repo, clock=time.time):
        if not REPO_RE.fullmatch(repo):
            raise LoopError(f"bad loops repo {repo!r}")
        self.gh, self.repo, self.clock, self._writes = gh, repo, clock, []
        self._created = {}  # key -> Loop: GitHub's issue list lags a fresh issue by seconds

    # --- gates ---
    def _tick(self):
        now = self.clock()
        self._writes = [t for t in self._writes if now - t < 60]
        if len(self._writes) >= WRITES_PER_MIN:
            raise LoopError(f"rate limit: at most {WRITES_PER_MIN} loop writes per minute")
        self._writes.append(now)

    def _issue(self, n):
        i = self.gh.get_issue(self.repo, int(n))
        if LABEL not in _names(i) or "pull_request" in i:
            raise LoopError(f"#{n} is not a loop")
        return i

    # --- reads ---
    def list(self, state="open"):
        return [from_issue(i) for i in self.gh.list_issues(self.repo, LABEL, state) if "pull_request" not in i]

    # --- writes ---
    def add(self, title, body="", kind="chore", owner="human", source="", due="", key=""):
        title = re.sub(r"\s+", " ", title or "").strip()
        if not title or len(title) > TITLE_MAX:
            raise LoopError(f"title must be 1-{TITLE_MAX} characters")
        if len(body or "") > BODY_MAX:
            raise LoopError(f"body over {BODY_MAX} characters")
        if kind not in KINDS or owner not in OWNERS:
            raise LoopError(f"kind must be one of {KINDS}, owner one of {OWNERS}")
        if source and not REPO_RE.fullmatch(source):
            raise LoopError("source must be owner/repo")
        if key and not re.fullmatch(r"[0-9a-f]{16}", key):
            key = loop_key(key)
        key = key or loop_key(source, title)
        due = _date(due, "due")
        if key in self._created:
            return self._created[key], False
        for existing in self.list("all"):
            if existing.key == key:
                return existing, False
        self._tick()
        labels = [LABEL, f"kind:{kind}", f"owner:{owner}"] + [f"src:{source}"] * bool(source)
        loop = from_issue(self.gh.create_issue(self.repo, title, _body(body or "", due, "", key), labels))
        self._created[key] = loop
        return loop, True

    def _edit(self, n, labels=None, **body_fields):
        i = self._issue(n)
        self._tick()
        cur = from_issue(i)
        fields = {}
        if labels is not None:
            fields["labels"] = labels
        if body_fields:
            vals = {"due": cur.due, "until": cur.until, **body_fields}
            fields["body"] = _body(cur.body, vals["due"], vals["until"], cur.key or loop_key(cur.source, cur.title))
        return from_issue(self.gh.edit_issue(self.repo, int(n), **fields))

    def snooze(self, n, until):
        until = _date(until, "until")
        names = [x for x in _names(self._issue(n)) if x != "snoozed"] + ["snoozed"] * bool(until)
        return self._edit(n, labels=names, until=until)

    def set_due(self, n, due):
        return self._edit(n, due=_date(due, "due"))

    def set_owner(self, n, owner):
        if owner not in OWNERS:
            raise LoopError(f"owner must be one of {OWNERS}")
        names = [x for x in _names(self._issue(n)) if not x.startswith("owner:")] + [f"owner:{owner}"]
        return self._edit(n, labels=names)

    def note(self, n, text):
        if not text.strip() or len(text) > NOTE_MAX:
            raise LoopError(f"note must be 1-{NOTE_MAX} characters")
        self._issue(n)
        self._tick()
        self.gh.comment(self.repo, int(n), text)

    def close(self, n, done=True):
        self._issue(n)
        self._tick()
        return from_issue(self.gh.edit_issue(self.repo, int(n), state="closed",
                                             state_reason="completed" if done else "not_planned"))
