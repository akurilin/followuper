---
description: Run followuper.py and report which conversations need a follow-up
---

Run the export script and tell me which conversations need a follow-up from me.

## Step 1 — run the script

**Always redirect the export to a file in `/tmp`. Never let it reach the conversation.**
Run *exactly* this — nothing else:

```bash
python3 followuper.py --months 2 --inactive-days 0 > /tmp/followuper_export.md
```

Then open `/tmp/followuper_export.md` with the Read tool and analyze from there. Do
**not** run the script bare, and do **not** pipe it through `head`/`tail`/`rg`/`cat` or
otherwise route its output back through a tool result — that puts personal message content
into Claude's context.

Why this matters: the export is full of private message content. Any of it that flows
through a Bash result (stdout, a pipe, a preview) gets persisted by the harness under the
project's `tool-results/` folder, where it lingers for weeks. The redirect keeps the Bash
result empty (nothing to persist), and the `/tmp` file self-deletes via macOS's 3-day temp
cleanup. Re-runs overwrite the same path.

Defaults: a 2-month window and `--inactive-days 0` (include fresh threads too — do
*not* hide recently-active conversations). If I gave arguments, treat them as overrides:
`$ARGUMENTS` (e.g. a number = months, or any explicit `--flag`).

Read the **whole** file before analyzing — it may be large; never answer from a truncated
preview.

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
