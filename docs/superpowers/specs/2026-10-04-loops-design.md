# Loops: one place for every open ball

Status: design, 2026-10-04. Supersedes the "followups repo" idea of 453767b.

## Why

Agents leave work for the owner in many places: a PR briefing's "you still need to" steps,
`pending_for_human.md` files in each repo's notes dir, chat replies that end in a question. Nothing
collects them, so balls drop. pr-swipe already sees every PR the owner and his agents open; it grows
into the place that makes sure nothing is dropped. The review screen stays for reviewing code: open
loops get their own deck.

## Decisions

- **Store:** GitHub issues in `jonathanmoregard/.claude` (private, issues on, no workflows, so no
  Actions minutes), label `loop`. One issue per loop. Reachable by local agents, cloud agents, the
  GUI and the owner's phone; a later PR closes one with `Closes jonathanmoregard/.claude#N`.
  Configurable as `PR_SWIPE_LOOPS_REPO`.
- **Agents write without approval prompts** through one narrow tool, never raw `gh`: the
  `pr-swipe-loops` MCP server (stdio). The gates live in the server's code, not in permission prompts:
  - the target repo is fixed by configuration, never a tool argument;
  - tools are `add`, `list`, `update` (status, due, note) and `close`; no delete, no edit of other
    issues (every write checks the issue carries the `loop` label);
  - title ≤ 120 chars, body ≤ 4000, ≤ 30 writes per minute per process;
  - every add carries a dedup key; an existing open or closed loop with that key is returned instead
    of a new one;
  - loop text is data: the GUI renders it as plain text, and the server never follows links in it.
  The tool is allow-listed for Claude Code and Codex once, in the `.claude` config repo.
- **Cloud agents** (claude.ai/code, Codex cloud) write the same way when the MCP server is available to
  them; otherwise they end their PR body with a fenced `loops` block, and the local filer picks it up
  on merge. No token for them in this phase.

## Data model (issue = loop)

| field | where |
|---|---|
| title | issue title (one line, imperative: "Create the merge-gate App key") |
| kind | label `kind:followup` / `kind:blocked` / `kind:decision` / `kind:chore` |
| owner | label `owner:human` (default) or `owner:agent` |
| source | label `src:<owner>/<repo>`; body links the PR or session |
| status | open / closed (done or dropped: close reason `completed` / `not_planned`); snooze = label `snoozed` + `until:YYYY-MM-DD` in body |
| due | `due:YYYY-MM-DD` line in body (optional) |
| key | `<!-- loop-key: <16 hex> -->` in body |

## Sources (who files loops)

1. **Merged PRs** (`pr-swipe-followups`, now `pr-swipe-loops-filer`): the briefing's manual steps,
   snapshotted from inbox cards while the PR is open, filed when GitHub says merged; closed unmerged
   files nothing. Key = repo, PR, normalised text. Also reads a fenced `loops` block from the PR body.
2. **Agents directly** via the MCP `add` tool (blocked-on-human, decisions, chores).
3. **Notes files**: `~/.local/state/claude-tasks/*/pending_for_human.md` entries are swept once and then
   the convention changes to the MCP tool (separate change in `.claude`).

## GUI: the Loops deck

A second deck next to the PR deck, same quest framing, same keys where they mean the same thing:
→ done, ← drop (close as not planned), `s` snooze 1 day (`S` 1 week), `r` hand to an agent (label
`owner:agent`), `o` open the issue/source in the browser, ↑/↓ scroll. Each card shows title, kind,
source link, age, due. Order: overdue, blocked, due soon, oldest. The deck reads the issue list once
per reload; writes go through the same library as the MCP server.

## Units

- `pr_swipe/loops.py` (pure-ish): `Loop` parse/format to and from issues, `key()`, `add/list/update/close`
  over a GitHub client, the gates above. Shared by MCP server, filer and GUI.
- `pr_swipe/loops_mcp.py`: stdio MCP server exposing the four tools, repo from env.
- `pr_swipe/followups.py` → filer on top of `loops.py`.
- `pr_swipe/gui/loops_deck.py`: the deck.
- nixos-config #303: user service for the filer; MCP server on PATH.
- `.claude` (separate PR, required by its config rules): register + allow-list the MCP server.

## Testing

- `loops.py`: gates (wrong repo impossible, non-loop issue refused, size caps, dedup across lost
  state, rate limit) against an in-memory GitHub.
- MCP server: tool calls end to end over stdio against the fake.
- Filer: the four follow-up tests already written, re-pointed.
- GUI: deck keys close/snooze/hand-off on a fake store.
- Real: one loop filed, listed, closed in `jonathanmoregard/.claude` from the MCP server, then from the
  GUI; agent-config e2e for the allow-list PR.

## Out of scope (this phase)

TickTick mirror, daily digest, auto-closing checks, cloud-agent tokens.
