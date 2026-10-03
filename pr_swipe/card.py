"""Card = everything the GUI shows for one PR head. Untrusted data to the GUI."""
import hashlib
import json
import os
import tempfile
from pathlib import Path

import jsonschema

REPO = r"^(?!.*\.\.)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"  # no "..": same rule as github._check_repo
SHA = r"^[0-9a-f]{40}$"
_S = {"type": "string"}
_SL = lambda n: {"type": "string", "maxLength": n}  # noqa: E731

HOTSPOT = {
    "type": "object", "additionalProperties": False,
    "required": ["source", "file", "hunk", "lines"],
    "properties": {
        "source": {"enum": ["rule", "ai"]}, "rule": _S, "file": _SL(500), "hunk": _SL(500),
        "lines": _SL(20000), "why": _SL(600), "severity": {"enum": ["high", "medium", "low"]},
    },
}

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["schema", "repo", "number", "url", "head_sha", "base_ref", "base_sha", "author",
                 "author_class", "title", "created_at", "first_commit_at", "updated_at", "can_merge",
                 "ci", "mergeable", "diffstat", "hotspots", "hidden_content", "context", "verdict",
                 "signals", "detail", "review_meta"],
    "properties": {
        "schema": {"const": 1},
        "repo": {"type": "string", "pattern": REPO},
        "number": {"type": "integer", "minimum": 1},
        "url": {"type": "string", "pattern": r"^https://github\.com/"},
        "head_sha": {"type": "string", "pattern": SHA},
        "base_ref": _SL(255), "base_sha": {"type": "string", "pattern": SHA},
        "author": _SL(100), "author_class": {"enum": ["self", "agent", "external"]},
        "title": _SL(1000), "created_at": _S, "first_commit_at": _S, "updated_at": _S,
        "can_merge": {"type": "boolean"},
        "ci": {"type": "object", "additionalProperties": False, "required": ["state", "failing"],
               "properties": {"state": {"enum": ["success", "failure", "pending", "none"]},
                              "failing": {"type": "array", "items": _SL(300)}}},
        "mergeable": {"enum": ["clean", "behind", "dirty", "unknown"]},
        "diffstat": {"type": "object", "additionalProperties": False,
                     "required": ["files", "additions", "deletions"],
                     "properties": {k: {"type": "integer", "minimum": 0}
                                    for k in ("files", "additions", "deletions")}},
        "hotspots": {"type": "array", "items": HOTSPOT},
        "hidden_content": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["where", "kind", "text"],
            "properties": {"where": _SL(600), "kind": {"enum": ["html-comment", "zero-width", "bidi"]},
                           "text": _SL(2000)}}},
        "context": {"type": "object", "additionalProperties": False,
                    "required": ["purpose", "solution", "notes"],
                    "properties": {"purpose": _SL(1500), "solution": _SL(1500), "notes": _SL(1500)}},
        "verdict": {"type": "object", "additionalProperties": False,
                    "required": ["recommendation", "stale", "superseded_by", "confidence", "reason"],
                    "properties": {
                        "recommendation": {"enum": ["approve", "close", "look"]},
                        "stale": {"type": "boolean"},
                        "superseded_by": {"type": "array", "items": {"type": "string",
                                          "pattern": r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[0-9]+$"}},
                        "confidence": {"enum": ["high", "medium", "low"]},
                        "reason": _SL(1500)}},
        "signals": {"type": "object", "additionalProperties": False,
                    "required": ["days_since_update", "overlapping_open"],
                    "properties": {"days_since_update": {"type": "integer", "minimum": 0},
                                   "overlapping_open": {"type": "array", "items": {"type": "integer"}}}},
        "detail": {"type": "object", "additionalProperties": False,
                   "required": ["diff", "truncated", "comments"],
                   "properties": {"diff": _SL(300000), "truncated": {"type": "boolean"},
                                  "comments": {"type": "array", "items": {
                                      "type": "object", "additionalProperties": False,
                                      "required": ["author", "body"],
                                      "properties": {"author": _SL(100), "body": _SL(20000)}}}}},
        "review_meta": {"type": "object", "additionalProperties": False,
                        "required": ["model", "reviewed_at", "prose_seen"],
                        "properties": {"model": _SL(100), "reviewed_at": _S,
                                       "prose_seen": {"type": "boolean"}}},
        "deep_review": {"type": "object", "additionalProperties": False,
                        "required": ["summary", "findings"],
                        "properties": {"summary": _SL(4000),
                                       "findings": {"type": "array", "maxItems": 10, "items": _SL(1000)}}},
    },
}


class CardError(ValueError):
    pass


def validate(card: dict) -> dict:
    try:
        jsonschema.validate(card, SCHEMA)
    except jsonschema.ValidationError as e:
        raise CardError(e.message[:300]) from None
    return card


def key(card) -> str:
    return f"{card['repo']}#{card['number']}@{card['head_sha']}"


def pr_key(card) -> str:
    return f"{card['repo']}#{card['number']}"


def filename(card) -> str:
    owner, name = card["repo"].split("/")
    return f"{owner}__{name}__{card['number']}__{card['head_sha'][:12]}.json"


def digest(card) -> str:
    return hashlib.sha256(json.dumps(card, sort_keys=True).encode()).hexdigest()


def write_json_atomic(directory: Path, name: str, obj, mode=0o640) -> Path:
    directory = Path(directory)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f)
        os.chmod(tmp, mode)
        dest = directory / name
        os.replace(tmp, dest)
        return dest
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_card(directory, card) -> Path:
    validate(card)
    return write_json_atomic(directory, filename(card), card)


def load_cards(directory) -> list:
    out = []
    for p in sorted(Path(directory).glob("*.json")):
        try:
            out.append(validate(json.loads(p.read_text())))
        except (OSError, ValueError):
            continue  # unreadable or invalid card: never shown
    return out
