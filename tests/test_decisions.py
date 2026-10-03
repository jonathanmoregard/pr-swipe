import pytest
from pr_swipe.decisions import validate, DecisionError

GOOD = {"action": "approve", "repo": "o/r", "number": 3, "head_sha": "a" * 40,
        "card_sha256": "f" * 64, "ts": 1.0}

def test_good_decision_passes():
    assert validate(dict(GOOD)) == GOOD

@pytest.mark.parametrize("bad", [
    {"action": "merge"}, {"repo": "o/r; rm"}, {"number": True}, {"number": -1},
    {"head_sha": "A" * 40}, {"card_sha256": "x"}, {"extra": 1},
])
def test_anything_off_shape_is_rejected(bad):
    with pytest.raises(DecisionError):
        validate({**GOOD, **bad})

def test_missing_field_rejected():
    d = dict(GOOD); del d["head_sha"]
    with pytest.raises(DecisionError):
        validate(d)
