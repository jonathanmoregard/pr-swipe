# pr-swipe verified evidence — design

Date: 2026-10-03. Closes review finding F2 (nixos-config#303): the inbox is writable by the
user's uid, where agents run, so an agent could write a card for a real PR head showing a
fabricated diff and verdict; the human approves fabricated evidence and the executor merges the
real commit.

## Principle

Everything the human decides on that *can* be verified is taken from GitHub by the trusted uid
(`prswipe`) and addressed by commit SHA. The card written by the collector shrinks to an
**untrusted AI layer** whose influence is bounded: it can raise attention, never lower it.

## Pieces

### Verifier (in the executor process, prswipe, App token)

Every executor tick (30 s), for each card in the inbox (untrusted; only `repo` and `number` are
read, both regex-validated) whose repo has the merge-gate App installed:

1. `GET pulls/N` → real `head.sha`, `base.ref`, `base.sha`, title, body, state. Skip closed PRs.
2. If no verified record exists for that head, `git fetch` into the bare mirror
   `/var/lib/pr-swipe/git/<owner>__<name>.git`:
   `+refs/pull/N/head:refs/prs/N` and `+refs/heads/<base_ref>:refs/base/<base_ref>`,
   authenticated with an installation token via `GIT_CONFIG_*` env (never argv), hooks disabled,
   `transfer.fsckObjects=true` (index-pack re-hashes every object).
3. Check `refs/prs/N` resolves to the API's `head.sha`; compute `merge_base(base tip, head)`.
4. Fetch CI state for the head and issue comments.
5. Write `state/verified/<owner>__<name>__<N>.json` (prswipe-only):
   `{repo, number, head_sha, base_ref, base_sha, merge_base, title, body, comments, ci, verified_at}`.

A record is refreshed when the API head changes, and CI is refreshed every tick while the card
is in the deck.

External repos (App not installed) cannot be verified. They are travelers already (no merge
possible), are shown with an "unverified" banner, and swipes open the browser.

### Git view (GUI, prswipe, no network)

Read-only helpers over the mirror, git run as a subprocess with
`--no-pager -c core.hooksPath=/dev/null -c core.fsmonitor=false`, diffs with
`--no-ext-diff --no-textconv` (a PR's `.gitattributes` cannot invoke diff drivers):

- `commits(base, head)` → sha, author, date, subject.
- `files(base, head)` → path, status, additions, deletions, binary flag (`--numstat -z`, `--name-status -z`).
- `file_diff(base, head, path)` → unified diff for one path.
- `show(head, path)` → file content at head (size-capped).

The diff the human sees is `merge_base...head`, the same diff GitHub shows for the PR.

### Deterministic analysis on verified data

Rule hotspots, hidden content (body, comments, diff) and diffstat are recomputed in the GUI from
the verified record and the git view. The card's copies of these fields are ignored.

### Card trust rules (GUI)

- A card is shown only if a verified record exists **and** `card.head_sha == record.head_sha`.
  Otherwise it waits as "verifying…" (moved to the back) or, if the heads differ, is dropped as stale.
- Title, body and CI on screen come from the record. AI summary/verdict come from the card and
  are labelled "AI (unverified)".
- **AI salience is raise-only.** AI annotations can mark a file/hunk as important (ordering,
  highlight, reason). They cannot hide, collapse or demote anything below its default, and
  never override a rule flag. An annotation pointing at a path/line not in the verified diff is
  discarded.
- The decision sent to the executor carries `head_sha` (as today). The executor re-checks the
  head against GitHub before acting (as today) and also refuses if no verified record exists for
  that head.

## Not covered (stated in the UI)

- AI verdict text can still be forged by an agent (the reviewer runs as the user). Mitigation:
  label + raise-only salience. Full closure needs the reviewer under its own uid with its own
  credential (option B, not chosen).
- PR prose is authentic (from GitHub) but attacker-authored; it stays plain text.

## Testing

- verify.py against a real local git "remote" (file:// URL injected for the fetch target) with a
  fake API: records written only when the fetched ref matches the API head; forged head in card →
  no record; closed PR skipped; fsck enabled.
- gitview.py on a real temp repo: commits/files/diff/show; a `.gitattributes` diff driver in the
  PR does not execute.
- Deck: card without record → not shown; head mismatch → dropped; AI annotation outside the diff →
  discarded; AI cannot demote a rule-flagged file.
- Executor: refuses a decision without a verified record for that head.
