# pr-swipe — design

Date: 2026-10-03
Status: approved direction (brainstorming session "swipe gui prs"); physical-key stage deferred.

## 1. Problem

~65 open PRs across ~13 repos, most written by coding agents. Review today is mostly
clicking: updating branches, nudging agents about red CI, telling them to merge. The human
merge click has become security theater (fast approvals without reading), yet agents are
not actually prevented from merging: the `gh` token is a classic `gho_` OAuth token with
`repo` scope (includes repo admin), the only guard is a string-deny on `gh pr merge`, and
`gh api .../merge`, `gh pr close`, pushes to main and ruleset edits are all open.
Over 30 days of transcripts agents ran `gh pr merge` 11 times (denied) and `gh pr close`
80 times (allowed).

Goal: **human direction-level oversight with less friction**. An AI pre-review puts the
context and the riskiest hunks on the first screen; the human swipes; a non-LLM executor
does the chores (update branch, wait for CI, merge in order). Agents can never merge or
close on their own — enforced by GitHub, not by prompts.

## 2. Research basis (summarised; full reports in research-agent reports dir)

- Practitioners: AI review as *attention allocator*, human keeps the merge (Willison,
  Anthropic internal, batch-triage OSS maintainers). Full auto-merge only on a
  classifier-selected low-risk subset with strong CI (Intercom, ~19%).
- Fatigue is the failure mode: approval rate on agent PRs rose 30.1%→36.8% while inline
  comments fell 22% (Habituation at the Gate, 2026). Design for fewer, information-dense
  decisions; instrument the gate.
- No swipe-style PR triage tool exists. Closest prior art: workdash (cached
  change/intent/risky-parts briefing), CodeRabbit Triage (close candidates), ghn
  (action buckets, batch undo).
- GitHub facts: merging needs `Contents: write` (same as push), so no token can
  push-but-not-merge; the merge gate must be a ruleset whose bypass list excludes agents.
  Classic `repo` scope can edit/delete rulesets. Closing a PR needs `Pull requests: write`,
  the same permission needed to open one. Merge API takes `sha` and returns 409 if head
  moved. Private-repo rulesets work on this account (klaffat has an active ruleset).
- X11 has no inter-client isolation; any same-session client can inject keystrokes. A
  FIDO2 touch is the only gesture host software can't forge. Deferred (§10).
- Prompt injection lives in PR prose/comments; mitigations: read-only reviewer,
  diff reviewed without PR prose, deterministic hotspots the model cannot suppress,
  hidden-content banner, model output never reaches an argument position.

## 3. Scope

In scope: PRs that are open and either (a) in a repo owned by `jonathanmoregard`, or
(b) authored by `jonathanmoregard` elsewhere. The merge-gate App cannot be installed on repos
the user doesn't own, so for (b) the executor has no write path: ← and → both open the PR in
the browser (via outbox) for the user to act there.

AI review runs only on PRs authored by the user or the user's agents (same GitHub
identity, or the agent-push App once §7 lands). Third-party PRs show deterministic
analysis only, labelled "no AI review: external author".

Auto-merge without a human swipe applies to one class only (owner decision, 2026-10-03): Dependabot
version bumps in repos with CI. The executor checks it from GitHub data alone: every commit by
dependabot[bot] and GitHub-signed, the diff only dependency files or workflow `uses:` lines, CI with at
least one check all green; then the ordinary merge train runs. See `pr_swipe/autobump.py`.

Out of scope: any other auto-merge without a human swipe; GitHub web UI as merge path (break-glass
only, by temporarily editing a ruleset); Wayland migration; running agents as separate
Unix users.

## 4. Architecture

```
            jonathan uid                         │  prswipe uid (system)            agent-push uid (system, §7)
                                                 │
 collector (user timer) ──read-only token──▶ GitHub API
   │ deterministic analysis                      │
   │ claude -p reviewer (no tools)               │
   ▼                                             │
 /var/lib/pr-swipe/inbox/*.json  ───────────────▶│ GUI (Qt, on :0) ──unix sock 0600──▶ executor
                                                 │    keys ←→↑↓                         │ merge-gate App key
 /var/lib/pr-swipe/outbox/*.json ◀───────────────│                                      │ (LoadCredential)
   (deep-review / open-in-browser requests)      │                                      ▼
                                                 │                               merge train ──▶ GitHub API
 agents: git/gh wrappers ──unix sock──▶ push broker (§7) ──agent-push App──▶ GitHub API
```

Trust boundaries:
- Everything under jonathan's uid (agents, collector, reviewer) is untrusted with respect
  to merging. It can read GitHub but cannot write merges, closes or rulesets.
- `prswipe` uid holds the only credential able to merge or close. Its socket is mode 0600
  owned by `prswipe`; nothing under jonathan's uid can talk to the executor.
- Inbox cards are untrusted data to the GUI: schema-validated, rendered as plain text
  (no HTML/markdown/image rendering).

### 4.1 Units

| Unit | Runs as | Purpose | Depends on |
|---|---|---|---|
| `collector` | jonathan, systemd user timer (10 min) + on-demand | enumerate PRs, fetch diff/checks/metadata, run analysis + reviewer, write cards | read-only GitHub token, `claude` CLI |
| `analysis` (library) | in collector | deterministic hotspots, hidden content, diffstat, staleness signals | none (pure functions) |
| `reviewer` (library) | in collector | builds prompts, calls `claude -p`, validates JSON | `claude` CLI |
| `gui` | prswipe, launched via `pr-swipe` command | swipe deck, detail views, sends decisions to executor | inbox (read), outbox (write), executor socket |
| `executor` | prswipe, system service | applies decisions: close, merge train, audit log | merge-gate App key |
| `broker` (§7) | agent-push, system service | narrow push/PR-create API for agents | agent-push App key |
| NixOS module | nixos-config | users, dirs, services, polkit rule for launching GUI, secrets | agenix |

Language: Python 3 (matches research-agent/aggregator), PySide6 for the GUI, stdlib
`urllib` + `cryptography`/`PyJWT` for GitHub App auth. Minimal dependencies in the
executor (T12 in the threat model).

## 5. Data

### 5.1 Card (inbox JSON, one file per `repo#number@head_sha`)

```
{
  "schema": 1,
  "repo": "jonathanmoregard/nixos-config", "number": 287, "url": "...",
  "head_sha": "<40 hex>", "base_ref": "main", "base_sha": "<40 hex>",
  "author": "jonathanmoregard", "author_class": "self" | "agent" | "external",
  "title": "...", "created_at": "...", "first_commit_at": "...", "updated_at": "...",
  "can_merge": true,              // repo owned by user
  "ci": {"state": "success|failure|pending|none", "failing": ["job", ...]},
  "mergeable": "clean|behind|dirty|unknown",
  "diffstat": {"files": 4, "additions": 120, "deletions": 30},
  "hotspots": [ {"source": "rule", "rule": "ci-workflow", "file": ".github/workflows/x.yml",
                 "hunk": "@@ ...", "lines": "..."} ,
                {"source": "ai", "file": "...", "hunk": "...", "lines": "...",
                 "why": "...", "severity": "high|medium|low"} ],
  "hidden_content": [ {"where": "body", "kind": "html-comment|zero-width|bidi", "text": "..."} ],
  "context": {"purpose": "...", "solution": "...", "notes": "..."},   // from PR prose; may be steered
  "verdict": {"recommendation": "approve|close|look", "stale": false,
              "superseded_by": [ "repo#n" ], "confidence": "high|medium|low",
              "reason": "..."},
  "review_meta": {"model": "...", "reviewed_at": "...", "prose_seen": false}
}
```

Rules: `hotspots` with `source: rule` always render, before AI hotspots, and cannot be
removed by the reviewer. `context` is labelled in the UI as "from PR text".

### 5.2 Decision (GUI → executor, line-delimited JSON over the unix socket)

`{"action": "close"|"approve", "repo", "number", "head_sha", "card_sha256", "ts"}`

Fixed shape. The executor rejects unknown fields and never interpolates free text into
API paths or commands. Decisions for repos outside the App installation are rejected.

### 5.3 Audit log

Executor-owned `/var/lib/pr-swipe/audit.jsonl`, append-only, each record carries the
SHA-256 of the previous record. Records every decision and every GitHub write attempt
(including 409s and failures), plus the hash of the card the human saw.

## 6. Behaviour

### 6.1 Collector + reviewer

1. Enumerate: `search/issues?q=is:pr is:open user:jonathanmoregard` plus
   `author:jonathanmoregard`. Skip PRs whose `head_sha` already has a card.
2. Deterministic analysis (`analysis`):
   - Rule hotspots: `.github/workflows/**`, lockfiles and dependency manifests,
     install/postinstall hooks, `flake.nix`/`flake.lock`, `*.nix` under `secrets/`,
     auth/crypto paths, Dockerfiles/IaC, permission configs (`settings.json`,
     `permissions`, `hooks/`), deletions over 50 lines, binaries, secret-pattern matches.
   - Hidden content in title/body/comments/diff: HTML comments, zero-width chars, bidi
     overrides.
   - Staleness signals: days since last commit, `behind_by`, conflicts, files overlapping
     PRs merged since branch point, open PRs touching the same files.
3. Reviewer (`claude -p`, all tools disabled, JSON output validated against schema):
   - **Diff pass**: sees diff + file list + rule hotspots, **not** the PR title/body/comments.
     Returns AI hotspots (≤5, ranked) and a risk note.
   - **Context pass**: sees title, body, commit messages, linked overlapping/merged PR
     titles. Returns purpose/solution/notes and the stale/superseded verdict.
   - External-author PRs: skip both passes.
4. Write the card atomically to the inbox (`tmp` + rename).
5. Consume outbox requests: `deep-review` → `claude -p` with read-only tools on a
   throwaway worktree, result appended to the card as `deep_review`; `open` → `xdg-open url`.

### 6.2 GUI

- Deck order: AI-suggested-close first (one swipe each, quick to clear), then failing CI,
  then by risk (rule hotspots + AI severity), then age.
- Card first screen: repo, number, title, author class, age (first commit → now), CI,
  mergeable state, diffstat; purpose and solution (labelled "from PR text"); verdict with
  reason; hidden-content banner (red) if any; hotspot hunks with syntax highlighting.
- Keys:
  - **←** close. If the AI says "keep", ask for a second press to confirm.
  - **→** approve (enters merge train). PRs with failing CI or rule hotspots need a
    second press after the hunks have been on screen ≥2 s. External-repo PRs: ← and →
    open the PR in the browser instead (no executor write path).
  - **↑** deep AI review (request goes to outbox; card moves to the back and returns
    when the review is in).
  - **↓** detail view: full diff, all comments, checks. `o` opens in browser
    (via outbox). Esc returns.
  - **u** undo last decision if the executor has not yet acted on it.
  - **s** skip (to back of deck).
- Instrumentation: per decision, time on card, keys pressed, whether detail view was opened.
  Weekly summary in the GUI footer: median time per approve, reject rate, detail-view rate.

### 6.3 Executor

- **Close**: re-GET the PR; abort if `head_sha` or state changed; PATCH `state=closed`;
  comment "Closed via pr-swipe (human decision)".
- **Merge train** (per repo, sequential; repos in parallel):
  1. Take the oldest approved PR in that repo.
  2. If behind base: `PUT update-branch` with `expected_head_sha` = approved head.
  3. Wait for CI on the new head (poll, 30 s, timeout 60 min).
  4. **Approval carry-over check**: compute the PR's own change before and after the
     update (`git diff merge-base...head`, normalised to a patch-id via the compare API).
     Equal → approval carries over. Different (conflict resolution changed code) → send
     the card back to the deck flagged "changed by rebase".
  5. CI green → `PUT merge` with `sha` = current head, `merge_method` = repo default
     (squash if allowed). 409 → back to deck, "head moved".
  6. CI red → back to deck flagged "CI failed after update" with failing job names.
     (An optional later hook could ping the authoring agent; out of scope now.)
- Credentials: merge-gate App private key via agenix → `LoadCredential`; installation
  tokens minted per action (1 h expiry). Never in env or argv.
- Hardening: `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp`, `NoNewPrivileges`,
  `CapabilityBoundingSet=`, `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`,
  `SystemCallFilter=@system-service`.

### 6.4 Launch

`pr-swipe` (on jonathan's PATH) asks systemd to start `pr-swipe-gui.service`
(`User=prswipe`, `DISPLAY=:0`, X cookie handed over via a prswipe-readable copy made by
the launcher). A polkit rule lets jonathan start/stop only that unit. An agent starting it
only opens a window for the human; it gains no decision power.

## 7. Push broker (phase 2)

Agents' `gh` token becomes **read-only** (Contents/PRs/Issues/Actions/Statuses read,
Administration read). Writes go through `pr-broker` (`agent-push` uid, unix socket,
group `claude-agents` + jonathan, peer-cred logged):

- `push(repo_path, branch, force_with_lease?)`: refuses the default branch and any
  ruleset-protected ref; force only `--force-with-lease` on non-default branches.
- `pr_create(repo, head, base, title, body_file)`, `pr_edit(...)`, `pr_comment(...)`.
- No merge, close, delete-branch, ruleset or settings operation exists.

`git` and `gh` wrappers on the agents' PATH route `git push`, `gh pr create|edit|comment`
to the broker and pass everything else through to the read-only binaries. PRs created by
the broker are authored by the `agent-push` App, which also lets the collector tell
agent PRs from the user's own (`author_class`).

## 8. GitHub configuration (phase 0)

1. **Agent token**: fine-grained PAT, all owned repos: Contents RW, Pull requests RW,
   Issues RW, Actions R, Commit statuses R, Workflows RW, **Administration R**,
   Metadata R; Webhooks/Secrets none. Install with `gh auth login --with-token`; revoke
   the old `gho_` grant. Probe: `gh api -X POST repos/O/R/rulesets` → 403.
   (Phase 2 drops the RW entries.)
2. **merge-gate App** (owned by user, private): Contents RW, Pull requests RW,
   Checks R, Metadata R. Installed on all owned repos.
3. **Ruleset `pr-swipe gate`** on each owned repo's default branch: Restrict updates,
   Restrict deletions, Block force pushes, Require status checks (where CI exists).
   Bypass list: **merge-gate App only** (not repository admin). Applied by a script
   (`scripts/apply-rulesets.py`), idempotent. This also fixes nixos-config's current admin
   bypass of required checks (`enforce_admins: false`).
4. **Verification (blocking)**: on a throwaway repo, with the agent PAT:
   `PUT .../pulls/N/merge` → must fail; `git push origin main` → must fail;
   `gh pr close` → succeeds in phase 0 (closed by phase 2). With the App: merge succeeds.
   If "Restrict updates" does **not** block API merges, fall back to
   "Require a pull request" + 1 required approval + bypass = merge-gate App.
5. Break-glass: the user edits the ruleset in the web UI; this needs a GitHub web login,
   which agents don't have.

## 9. Threat model (residuals after phases 0–2)

| Threat | Status |
|---|---|
| Agent merges via any API path | blocked by ruleset (server-side) |
| Agent edits/disables rulesets | blocked (Administration read-only) |
| Agent closes PRs | phase 0: possible; phase 2: blocked (read-only token) |
| Agent reads merge key | blocked by uid separation + LoadCredential; `jonathan` is removed from `docker` (silent root, unused in 30 days) |
| Agent talks to executor | blocked (socket 0600 prswipe) |
| Agent injects keystrokes into GUI (X11) | **open** until §10; requires deliberate adversarial action |
| Agent draws an overlay that misrepresents a card | **open** until §10 / Wayland |
| Injected PR text steers the reviewer | mitigated: diff pass never sees prose; rule hotspots unsuppressible; hidden-content banner; verdict advisory |
| Head moves between review and merge | `sha`-pinned merge, carry-over check, 409 handling |
| Approval fatigue | instrumentation, risk ordering, second press on risky cards |

## 10. Deferred: physical-key gesture

After phases 0–2: discuss FIDO2 (`ssh-keygen -Y sign`, `ed25519-sk`, verify-required)
with one touch per batch, verified by the executor before the train runs. Closes the
X11 injection residual. Decision explicitly postponed by the user.

## 11. Phases

0. GitHub config + verification (§8). User-performed: PAT and App creation in the browser.
1. pr-swipe: analysis, collector, reviewer, GUI, executor, merge train, NixOS module.
2. Push broker + read-only agent token.
3. Physical-key discussion.

## 12. Testing

- `analysis`: unit tests on fixture diffs (workflow edit, zero-width in body, lockfile
  bump, bidi override) — invariants, not mirrors of the implementation.
- `reviewer`: schema validation tests with recorded `claude` outputs incl. malformed and
  injected ones (verdict text containing instructions must render as text only).
- `executor`: tests against a fake GitHub (HTTP stub) for close race, 409, update-branch
  + carry-over equal/different, CI red, audit hash chain.
- GUI: pytest-qt for key handling and second-press gating.
- End-to-end (empirical, required): on a throwaway GitHub repo, two PRs, approve both;
  the train updates the second after the first merges, waits for CI, merges; an agent-PAT
  merge attempt fails.
- NixOS: VM test lane asserting the executor socket is not connectable by `jonathan`
  and the credential is unreadable by `jonathan`.

## Addendum 2026-10-03: feedback capture (learning loop deferred)

The GUI logs typed feedback to `state/feedback.jsonl` (mode 0600, local only, never committed or uploaded):
decisions (approve/close with dwell, detail-seen, AI verdict, `override` = swipe contradicts AI approve/close),
undo (`reason` human|refused), skip, deep-review, `note` (key `n`, optionally pinned to a hotspot) and
`missed` (key `m`, an issue the AI did not flag). Key `x` marks the card one-off ("don't learn from this").
No learning consumes this yet. Design review scheduled 2026-10-17, informed by
`research-agent/reports/d6441685112c4dd6ab6654e15e7b888d.md`: memory writes only from human actions,
human-approved rules, approve/close never trains the finder, replay set + cold slice before any learning.
