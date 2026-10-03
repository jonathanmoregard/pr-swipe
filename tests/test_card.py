import json, os, stat
import pytest
from pr_swipe import card as C
from tests.cards import make_card

def test_valid_card_roundtrips(tmp_path):
    c = make_card()
    p = C.write_card(tmp_path, c)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o640
    assert C.load_cards(tmp_path) == [c]

def test_invalid_cards_are_skipped_not_fatal(tmp_path):
    C.write_card(tmp_path, make_card())
    (tmp_path / "junk.json").write_text("{not json")
    (tmp_path / "bad.json").write_text(json.dumps(make_card(repo="../etc")))
    assert len(C.load_cards(tmp_path)) == 1

@pytest.mark.parametrize("bad", [
    {"repo": "o/r/../x"}, {"head_sha": "zz"}, {"number": 0},
    {"verdict": {"recommendation": "merge-now", "stale": False, "superseded_by": [],
                 "confidence": "high", "reason": ""}},
    {"extra_field": 1},
])
def test_schema_rejects_bad_fields(bad):
    with pytest.raises(C.CardError):
        C.validate(make_card(**bad))

def test_digest_is_stable_and_content_sensitive():
    a, b = make_card(), make_card()
    assert C.digest(a) == C.digest(b)
    assert C.digest(a) != C.digest(make_card(title="other"))

def test_filename_is_safe_and_unique_per_head():
    n1 = C.filename(make_card(sha="a" * 40))
    n2 = C.filename(make_card(sha="c" * 40))
    assert n1 != n2 and "/" not in n1 and n1.endswith(".json")
