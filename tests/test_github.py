import io, json, time
import urllib.error
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from pr_swipe import github as G


class FakeResp(io.BytesIO):
    def __init__(self, status, body):
        super().__init__(body if isinstance(body, bytes) else json.dumps(body).encode())
        self.status = status
    def __enter__(self): return self
    def __exit__(self, *a): pass


class Opener:
    def __init__(self, routes):
        self.routes, self.calls = routes, []
    def __call__(self, req, timeout=None):
        self.calls.append(req)
        status, body = self.routes[(req.get_method(), req.full_url)]
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "x", {}, io.BytesIO(json.dumps(body).encode()))
        return FakeResp(status, body)


API = "https://api.github.com"


def test_repo_names_are_validated_before_any_request():
    gh = G.GitHub(G.StaticToken("t"), opener=Opener({}))
    with pytest.raises(ValueError):
        gh.pr("o/r/../../x", 1)


def test_merge_passes_sha_and_returns_conflict_status():
    url = f"{API}/repos/o/r/pulls/5/merge"
    op = Opener({("PUT", url): (409, {"message": "Head branch was modified"})})
    gh = G.GitHub(G.StaticToken("t"), opener=op)
    status, _ = gh.merge("o/r", 5, "a" * 40, "squash")
    assert status == 409
    assert json.loads(op.calls[0].data) == {"sha": "a" * 40, "merge_method": "squash"}
    assert op.calls[0].get_header("Authorization") == "Bearer t"


def test_errors_raise_with_status():
    url = f"{API}/repos/o/r/pulls/5"
    gh = G.GitHub(G.StaticToken("t"), opener=Opener({("GET", url): (404, {"message": "nf"})}))
    with pytest.raises(G.GitHubError) as e:
        gh.pr("o/r", 5)
    assert e.value.status == 404


@pytest.mark.parametrize("runs,statuses,expected", [
    ([], [], "none"),
    ([{"name": "a", "status": "completed", "conclusion": "success"}], [], "success"),
    ([{"name": "a", "status": "in_progress", "conclusion": None}], [], "pending"),
    ([{"name": "a", "status": "completed", "conclusion": "failure"}],
     [{"context": "b", "state": "pending"}], "failure"),
    ([], [{"context": "b", "state": "error"}], "failure"),
    ([{"name": "a", "status": "completed", "conclusion": "skipped"}], [], "success"),
])
def test_ci_summary(runs, statuses, expected):
    assert G.summarize_ci(runs, statuses)["state"] == expected


def test_app_token_is_minted_per_installation_and_cached():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    exp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
    op = Opener({
        ("GET", f"{API}/repos/o/r/installation"): (200, {"id": 42}),
        ("POST", f"{API}/app/installations/42/access_tokens"): (201, {"token": "ghs_x", "expires_at": exp}),
    })
    tokens = G.AppTokens("123", pem, opener=op)
    assert tokens.token("o/r") == "ghs_x" and tokens.token("o/r") == "ghs_x"
    assert len(op.calls) == 2
    claims = jwt.decode(op.calls[0].get_header("Authorization").split()[1],
                        key.public_key(), algorithms=["RS256"])
    assert claims["iss"] == "123"


def test_app_reports_uninstalled_repo():
    op = Opener({("GET", f"{API}/repos/x/y/installation"): (404, {"message": "nf"})})
    assert G.AppTokens("1", b"unused", opener=op, signer=lambda: "jwt").installed("x/y") is False
