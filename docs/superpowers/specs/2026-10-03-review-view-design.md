# pr-swipe review view — design

Date: 2026-10-03. Request: "nice UI where relevant things to review are made more salient by the AI
reviewer, a bit more advanced/selective than standard git UIs; git is the backend."

## Evidence this rests on

- Human review gates fail by habituation: approvals rise, comments fall (Habituation at the Gate,
  in `research-agent/reports/e82eb681…`). Salience must make the right things easy to see without
  making the rest easy to skip.
- Labelling code as AI-generated shifts gaze but not rigor; reviewers do better assessing against the
  stated intent (Khojah et al., ASE '26, same report). So intent is shown first.
- Ranking what the reviewer sees is where measured wins are (Meta Next Reviewable Diff, report
  `d6441685…`); precision beats recall for AI findings (Uber uReview, Cursor, same report).
- From general knowledge, not verified this run: reviewers comment more on files shown first
  (file-position effects in code review), and highlighting invites automation bias toward
  un-highlighted code. The design counters both with ordering by risk and a coverage meter.
- A dedicated SOTA survey on AI-guided review UIs (report 9e0b7c6a…) was rejected by the research
  scanner and is not used.

## Trust rules (from the verified-evidence design)

All code, file lists and commits come from the verified mirror. AI annotations are raise-only:
they can move a file up and attach a "why" to a hunk; they cannot hide, collapse or demote. The
only collapsing is deterministic (low-signal files), and a rule flag always overrides it.

## Layout (replaces the hotspot pane)

```
[encounter strip: 🐉 Dragon · encounter 3/9 — flavour]
[title (verified) · meta · verified ✓ <sha>]
[banners: hidden content / returned / unverified]
Intent (PR body, verified): first lines …        AI (unverified): APPROVE (high) — reason
┌ files (ordered) ────────────┬ diff of selected file ───────────────────────────────┐
│ 🚩 .github/workflows/ci.yml │ ▶ 🚩 rule: ci-workflow                               │
│ ⚠ high  src/train.py   ✓   │ ▶ ⚠ AI high: retries merge without re-checking head  │
│ ·       src/util.py         │ @@ -10,3 +10,4 @@                                    │
│ ▸ 2 low-signal (lockfiles)  │ + ...                                                │
├ commits ────────────────────┤                                                      │
│ a1b2c3d feat: retry on 409  │                                                      │
└─────────────────────────────┴──────────────────────────────────────────────────────┘
seen: 2/4 files · flagged hunks 1/3     j/k file  n next flag  ← close → approve …
```

## Behaviour

- **File order:** tier 0 rule-flagged → tier 1 AI-flagged (by severity) → tier 2 the rest (by churn)
  → tier 3 low-signal (lockfiles, vendored, generated, snapshots), shown as one collapsed row that
  expands on select. A rule-flagged file is never low-signal.
- **Diff pane:** the selected file's verified diff, with callout lines above flagged hunks
  (🚩 rule in red, ⚠ AI in amber with severity and why). Binary files show a placeholder.
- **Coverage:** a file counts as seen once it has been selected for ≥ 1.5 s; a flagged hunk once
  `n` has landed on it (or its file was seen). Footer shows `seen x/y files · flagged hunks a/b`.
- **Approve gate:** on cards that need confirmation (dragons), approve requires every flagged hunk
  to have been visited, instead of the 2 s dwell. The footer says which remain. Close is unchanged.
- **Keys:** `j`/`k` next/previous file, `n` next flagged hunk (across files), `↓` toggles the whole
  diff (all files, in display order). Swipes and `s o u n m x` are unchanged. The `n` key moves
  from "note" to "next flag"; note becomes `t` (tell).

## Units

- `gui/review.py` (pure): `low_signal(path)`, `order_files(files, hotspots)`, `Coverage` (seen files,
  visited hunks, `gate_ok()`, `summary()`), `annotate(file_diff_text, hotspots_for_file)` → text with
  callout lines.
- `gui/app.py`: file list (QListWidget, no focus), commits label, diff pane, coverage footer, keys.

## Testing

- review.py: ordering tiers; a rule flag beats low-signal; AI cannot push a file below its tier;
  coverage counting; the gate needs every flagged hunk.
- app: `j` changes the selected file and the diff; approve on a dragon is refused until flagged hunks
  are visited, then accepted; low-signal row present.
- Offscreen screenshots of a dragon card in the review view.
