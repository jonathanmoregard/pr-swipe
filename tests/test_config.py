from pathlib import Path
from pr_swipe.config import load

def test_defaults_point_at_var_lib():
    c = load({})
    assert c.user == "jonathanmoregard"
    assert c.inbox == Path("/var/lib/pr-swipe/inbox")
    assert c.socket == Path("/run/pr-swipe/executor.sock")
    assert c.agent_logins == frozenset()

def test_env_overrides():
    c = load({"PR_SWIPE_ROOT": "/tmp/x", "PR_SWIPE_AGENT_LOGINS": "a[bot], b[bot]",
              "PR_SWIPE_SOCKET": "/tmp/s.sock", "PR_SWIPE_MODEL": "m1"})
    assert c.inbox == Path("/tmp/x/inbox") and c.outbox == Path("/tmp/x/outbox")
    assert c.returns == Path("/tmp/x/returns") and c.state == Path("/tmp/x/state")
    assert c.agent_logins == frozenset({"a[bot]", "b[bot]"})
    assert c.socket == Path("/tmp/s.sock") and c.model == "m1"
