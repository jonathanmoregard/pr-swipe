# pr-swipe quest mode — design

Date: 2026-10-03. Directive: "make the swipe experience fun, quest like".

## Constraint that shapes everything

The failure mode of human review gates is habituation: approvals rise and scrutiny falls as
the reviewer gets used to the gate (see the core spec §2 and the compounding-review research).
Gamification that rewards throughput would accelerate exactly that. So:

- **No reward is ever tied to approve count, close count, or speed.**
- **No streaks.** A daily streak pulls the user into a rushed pass just to keep it alive.
- Rewards attach only to acts of care: finding what the AI missed, writing notes, overruling
  the AI with a reason, clearing out stale work.
- Card data stays untrusted plain text. Flavour text comes only from a fixed in-code pool.

## Encounters

Every card is one encounter, classified from existing card fields (first match wins):

| Encounter | Rule | Flavour |
|---|---|---|
| 🧭 traveler | `can_merge` is false (external repo; opens the browser) | "a traveler from another land" |
| 👻 ghost | AI recommends close, or verdict stale, or superseded | "a ghost of work past"; closing shows "laid to rest" |
| 🐉 dragon | `deck.needs_confirm(card, "approve")` (CI red, hidden content, returned, rule hotspot) | "a dragon guards this one"; the existing 2 s dwell becomes "hold your ground" |
| 🌱 sprout | everything else | "a young sprout" |

## Screens

1. **Quest start** (shown when the deck is first loaded non-empty, and after a quest completes and
   new cards arrive): "Today's quest: N encounters across R repos · counts per encounter type".
   Any key begins.
2. **Encounter card:** existing card view plus a coloured header strip (emoji, encounter name,
   one flavour line) and a progress line "encounter i/N".
3. **Toasts** (footer, transient) for acts of care: 🔍 *Sharp eye* on `m`, 📜 *Lore* on `n`,
   ⚖️ *Overrule* on a swipe against the AI with a note recorded on that card this session,
   👻 *Laid to rest* on closing a ghost.
4. **Quest complete** at inbox zero: one celebration line from a rotating pool (plant-based,
   outdoorsy, fika flavour) plus the session recap: sharp-eye finds, lore, overrules, ghosts laid
   to rest, PRs sent to the merge train. Plain approvals are shown as a count, never scored.
5. **Weekly chronicle** in the footer replaces the 7-day gate line's prominence: finds and lore
   over 7 days, next to the existing gate stats (which stay: they are the habituation monitor).

## Architecture

- `pr_swipe/gui/quest.py` — pure, no Qt:
  - `encounter(card) -> str` (traveler | ghost | dragon | sprout)
  - `quest_summary(cards) -> dict` (total, repos, per-encounter counts)
  - `recap(rows, since_ts) -> dict` from `feedback.jsonl` rows: finds (`missed`), lore (`note`),
    overrules (decision with `override` and a note on the same key since `since_ts`),
    ghosts laid to rest (close of a card whose AI recommendation was close or stale/superseded),
    approvals sent. Undone/refused decisions excluded (same rule as `Store.metrics`).
  - `chronicle(rows, now, days=7) -> dict` (finds, lore).
  - `ENCOUNTERS` table (emoji, name, colour, flavour lines) and `CELEBRATIONS` pool.
- `Store.rows(since_ts=None)` returns raw feedback rows (with ghost info: rows already carry
  `ai.recommendation`, `ai.stale`, `ai.superseded`).
- `app.py`: header strip label, progress text, start/complete screens as states of the existing
  window (no new windows), toast via the footer message.

Nothing new is persisted; everything derives from cards plus `feedback.jsonl`.

## Testing

- quest.py unit tests: classification precedence; recap counts finds/lore/overrules/ghosts and
  ignores plain approvals for scoring; undone decisions excluded; chronicle window.
- app tests: start screen gates the first key; header shows the encounter; sharp-eye toast;
  quest-complete recap at inbox zero.
- Offscreen screenshots of start, dragon card and complete screens, checked by eye.
