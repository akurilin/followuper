---
description: Run followuper.py and report which conversations need a follow-up
---

Run the export script and tell me which conversations need a follow-up from me.

## Step 1 — run the script

```bash
python3 followuper.py --months 2 --inactive-days 0
```

Defaults: a 2-month window and `--inactive-days 0` (include fresh threads too — do
*not* hide recently-active conversations). If I gave arguments, treat them as overrides:
`$ARGUMENTS` (e.g. a number = months, or any explicit `--flag`).

Read the full output. It may be large and get saved to a file — read all of it before
analyzing; don't answer from a truncated preview.

## Step 2 — decide what needs a follow-up

A conversation needs a follow-up when the ball is in **my** court:

- **They sent the last message** and it warrants a reply (a question, a plan, something
  left open). A reaction/sticker/emoji-only last message usually does not.
- **I made a commitment** I haven't delivered (an intro, materials, "I'll send X").
- **An open loop went quiet** — a plan never finalized, or a "let's reconnect when I'm
  back / soon" that's now overdue.

Do **not** flag:

- Threads where **I** sent the last message and they owe the reply.
- Cleanly closed conversations (goodbyes, graceful dating closures, "thanks!" enders).
- Businesses, automations, spam, OTP/marketing texts, delivery/appointment bots,
  sticker-only or reaction-only exchanges.
- Anyone on the ignore list (the script already drops them).

Watch for **the same person split across numbers** — the script merges by Contacts card,
but if two sections are clearly one human, treat them as one and judge the most recent
thread.

## Step 3 — output

Give a **prioritized** list, grouped:

- 🔴 **Time-sensitive** — concrete plans with a near deadline (today/this week).
- 🟠 **Open commitments** — things I promised and owe.
- 🟡 **Stale / overdue loops** — "reconnect soon" that never happened.
- ⚪ **Optional** — casual chats that fizzled; keep-warm only.

For each: the person's name, a one-line reason, and the key quote or fact that triggered
it. End with a brief note on what was excluded (businesses, ball-in-their-court, closed
threads) so I can sanity-check. Offer to draft replies, starting with the most urgent.
