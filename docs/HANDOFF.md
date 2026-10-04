# pr-swipe handoff — state as of 2026-10-04

Pick-up point for the next session. Delete this file once its items are done.

## Branches and PRs

| Repo | Branch | PR | State |
|---|---|---|---|
| jonathanmoregard/pr-swipe | `feat/core` (= `feat/design-refresh`, b335c02 + this doc) | https://github.com/jonathanmoregard/pr-swipe/pull/1 | open; body not yet updated for the loops work |
| jonathanmoregard/nixos-config | `feat/pr-swipe` (575cbd2) | https://github.com/jonathanmoregard/nixos-config/pull/303 | open; pushed 2026-10-04, CI not checked since; merge = deploy |
| jonathanmoregard/.claude | `feat/loops-mcp` (worktree `~/worktrees/claude-loops-mcp`) | none yet | WIP: registers `pr-swipe-loops` in `mcp-servers.json`, allows `mcp__pr-swipe-loops__*` |

All pr-swipe work stays on PR #1, the nixos side on #303 (directive 2026-10-04: one PR).

## What landed (pr-swipe, 190 tests pass)

- GUI: ↑/↓ scroll the diff, `f` whole diff, `r` deep review; ←/→ decide on one press (a dragon's
  approve stays locked until its flagged hunks were seen); the 30 s reload keeps scroll + dwell;
  description, commits, "you still need to" and the warnings strip are off the card (warnings are one
  `⚠ N warnings · w` token); Bash colouring inside Nix `''…''` script strings.
- Hidden characters in pr-swipe's own source are `\u` escapes; a test fails on any literal one.
- **Loops** (spec `docs/superpowers/specs/2026-10-04-loops-design.md`, plan
  `docs/superpowers/plans/2026-10-04-loops.md`): open balls as `loop` issues in the private
  `jonathanmoregard/.claude` repo.
  - `pr_swipe/loops.py`: all gates (fixed repo, `loop` label, size caps, rate limit, dedup key incl.
    GitHub's lagging issue list).
  - `pr-swipe-loops-mcp`: stdio MCP server, tools `add_loop / list_loops / update_loop / close_loop`.
  - `pr-swipe-loops` user service: files merged PRs' manual steps (and a ```loops block in a PR
    body), applies GUI requests from `outbox/loops/`, writes `inbox/loops/open.json`.
  - GUI Loops deck: `L` toggles; → done, ← drop, s/S snooze day/week, r hand to agent, o open.
  - Real e2e done against `.claude` issues #135–#138 (all closed; test loops).

## Next, in order

1. Check #303 CI (`~/.claude/scripts/wait-for pr 303 --repo jonathanmoregard/nixos-config`), ask for the
   merge click, then smoke after deploy: `systemctl --user status pr-swipe-loops`,
   `ls -l /var/lib/pr-swipe/inbox/loops/`, open the GUI and press `L`.
2. `.claude` `feat/loops-mcp`: the agent-e2e harness passes no MCP servers (`--strict-mcp-config`).
   Planned: if `setup.sh` writes `$RUN/mcp.json`, pass it with `--mcp-config`; scenario
   `loops-not-dropped` with a stub loops server on the in-memory fake (no real GitHub), prompt in the
   user's voice ("make sure X doesn't get dropped"), grade on the stub's log; arms origin/master vs
   branch. Then the PR (needs #303 deployed for the binary path
   `/run/current-system/sw/bin/pr-swipe-loops-mcp`).
3. Update PR #1 body (loops, GUI changes, 190 tests).
4. Convention switch (needs Jonathan's OK): agents log blocked-on-human items via `add_loop` instead
   of `pending_for_human.md`; one-time sweep of the existing files into loops.
5. Later (spec "out of scope"): TickTick mirror for due/blocked loops, weekday digest, cloud-agent
   access.

## Open questions for Jonathan

- Keep the dragon approve lock (flagged hunks must be seen) now that ←/→ are one press?
- Hidden-character CI check across the other repos: wanted, given pr-swipe flags them per card?
- Dependabot cards: show "not auto-approved: <reason>"?
- Merge-gate App permissions (Checks: read, Commit statuses: read) still unconfirmed.
