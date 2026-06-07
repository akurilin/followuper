#!/usr/bin/env bash
#
# followups.sh — export recent conversations and have Claude flag which ones
# need a follow-up from me. Run it straight from the terminal:
#
#   ./followups.sh                 # defaults: 2-month window, include fresh threads
#   ./followups.sh --months 3      # override the window
#
# Any flags are forwarded to followuper.py. Because flags are applied in order
# and argparse keeps the last value, your overrides win over the defaults below.
#
# Privacy note: the export (full of private message content) is piped straight
# into `claude -p` over stdin. It never lands in a Bash tool result, so nothing
# gets persisted to a tool-results/ folder — no /tmp redirect needed.
#
# Progress: followuper.py logs its export progress to stderr (reading Contacts,
# iMessage, WhatsApp, message counts) so the export phase isn't opaque. Claude's
# own analysis still runs quietly until it prints the final report.

set -euo pipefail

cd "$(dirname "$0")"

read -r -d '' PROMPT <<'EOF' || true
Below (on stdin) is an export of my recent conversations. Tell me which ones
need a follow-up from me.

## Decide what needs a follow-up

A conversation needs a follow-up when the ball is in **my** court:

- **They sent the last message** and it warrants a reply (a question, a plan,
  something left open). A reaction/sticker/emoji-only last message usually does not.
- **I made a commitment** I haven't delivered (an intro, materials, "I'll send X").
- **An open loop went quiet** — a plan never finalized, or a "let's reconnect when
  I'm back / soon" that's now overdue.

Do **not** flag:

- Threads where **I** sent the last message and they owe the reply.
- Cleanly closed conversations (goodbyes, graceful dating closures, "thanks!" enders).
- Businesses, automations, spam, OTP/marketing texts, delivery/appointment bots,
  sticker-only or reaction-only exchanges.
- Anyone on the ignore list (the script already drops them).

Watch for **the same person split across numbers** — the script merges by Contacts
card, but if two sections are clearly one human, treat them as one and judge the most
recent thread.

## Output

Give a **prioritized** list, grouped:

- 🔴 **Time-sensitive** — concrete plans with a near deadline (today/this week).
- 🟠 **Open commitments** — things I promised and owe.
- 🟡 **Stale / overdue loops** — "reconnect soon" that never happened.
- ⚪ **Optional** — casual chats that fizzled; keep-warm only.

For each: the person's name, a one-line reason, and the key quote or fact that
triggered it. End with a brief note on what was excluded (businesses,
ball-in-their-court, closed threads) so I can sanity-check. Offer to draft replies,
starting with the most urgent.
EOF

python3 followuper.py --months 2 --inactive-days 0 "$@" | claude -p "$PROMPT"
