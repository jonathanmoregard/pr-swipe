"""Fixed-shape decisions from the GUI. Nothing here is free text."""
import re

FIELDS = {"action", "repo", "number", "head_sha", "card_sha256", "ts"}
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class DecisionError(ValueError):
    pass


def validate(d) -> dict:
    if not isinstance(d, dict) or set(d) != FIELDS:
        raise DecisionError("decision must have exactly the fields " + ", ".join(sorted(FIELDS)))
    if d["action"] not in ("close", "approve"):
        raise DecisionError("bad action")
    if not isinstance(d["repo"], str) or not REPO.fullmatch(d["repo"]) or ".." in d["repo"]:
        raise DecisionError("bad repo")
    if type(d["number"]) is not int or d["number"] < 1:
        raise DecisionError("bad number")
    if not isinstance(d["head_sha"], str) or not SHA.fullmatch(d["head_sha"]):
        raise DecisionError("bad head_sha")
    if not isinstance(d["card_sha256"], str) or not HEX64.fullmatch(d["card_sha256"]):
        raise DecisionError("bad card_sha256")
    if type(d["ts"]) not in (int, float):
        raise DecisionError("bad ts")
    return d
