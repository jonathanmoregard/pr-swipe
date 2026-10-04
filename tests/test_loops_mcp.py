import json
import anyio
from mcp.shared.memory import create_connected_server_and_client_session

from pr_swipe.loops import Loops
from pr_swipe.loops_mcp import make_server
from tests.fakes import FakeIssues

REPO = "me/.claude"


def session_run(gh, fn):
    async def go():
        async with create_connected_server_and_client_session(make_server(Loops(gh, REPO))._mcp_server) as s:
            return await fn(s)
    return anyio.run(go)


async def call(s, name, **args):
    r = await s.call_tool(name, args)
    if r.structuredContent is not None:
        return r.structuredContent.get("result", r.structuredContent)
    assert not r.isError, r.content
    return json.loads(r.content[0].text)


def test_exactly_four_tools_and_a_full_loop_lifecycle():
    gh = FakeIssues()

    async def flow(s):
        names = sorted(t.name for t in (await s.list_tools()).tools)
        added = await call(s, "add_loop", title="Create the App key", kind="followup", source="o/r", key="o/r#1:app")
        again = await call(s, "add_loop", title="Create the App key", kind="followup", source="o/r", key="o/r#1:app")
        n = added["loop"]["number"]
        snoozed = await call(s, "update_loop", number=n, snooze_until="2026-10-09", note="waiting on org owner")
        listed = await call(s, "list_loops")
        closed = await call(s, "close_loop", number=n, done=False, note="not needed after all")
        return names, added, again, snoozed, listed, closed

    names, added, again, snoozed, listed, closed = session_run(gh, flow)
    assert names == ["add_loop", "close_loop", "list_loops", "update_loop"]
    assert added["ok"] and added["created"] and not again["created"]
    assert snoozed["loop"]["until"] == "2026-10-09"
    assert [x["title"] for x in listed["loops"]] == ["Create the App key"]
    assert closed["loop"]["reason"] == "not_planned" and len(gh.comments) == 2


def test_gate_refusals_come_back_as_errors_not_exceptions():
    gh = FakeIssues()
    gh.create_issue(REPO, "config bug", "", ["bug"])

    async def flow(s):
        return (await call(s, "close_loop", number=1),
                await call(s, "add_loop", title="x" * 200),
                await call(s, "update_loop", number=99, owner="agent"))

    not_loop, too_long, missing = session_run(gh, flow)
    assert not_loop == {"ok": False, "error": "#1 is not a loop"}
    assert not too_long["ok"] and "title" in too_long["error"]
    assert missing == {"ok": False, "error": "GitHub 404"}
    assert gh.issues[(REPO, 1)]["state"] == "open"
