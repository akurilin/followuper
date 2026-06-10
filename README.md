# followuper

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/akurilin/followuper)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3](https://img.shields.io/badge/Python-3-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![macOS](https://img.shields.io/badge/macOS-000000?logo=apple&logoColor=white)](https://www.apple.com/macos/)
[![dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](followuper.py)

`followuper` is a local-first CLI that exports your one-on-one **iMessage** and
**WhatsApp** conversations into a single merged, per-person report, so you (or an AI
reading the export) can spot who's waiting on a reply from you. Everything runs on your
Mac against the local message databases — **no cloud APIs, no accounts, no
dependencies**, just one Python file and the standard library.

The problem it solves: personal threads quietly fall through the cracks. A friend asks
a question, you mean to reply later, and three weeks pass. Your conversations are split
across two apps with no combined view, and neither app will tell you "the ball is in
your court here." followuper reads both databases read-only, merges the platforms per
person, and prints a dated timeline whose header line carries the highest-signal facts:
who sent the **last** message and when. Pipe it into Claude and you get back a
prioritized list of who you owe a reply.

## Highlights

- **One merged timeline per person.** iMessage and WhatsApp threads are folded together
  by phone number (last 10 digits) and by Contacts card, so someone you talk to on both
  platforms — or on two different numbers — shows up as a single section, not two.
- **Token-lean output built for AI review.** The default compact format prints names
  and dates once, uses `->` / `<-` arrows for direction, and collapses URLs to a
  `[domain]` marker. On a typical two-month export it roughly halves the token count
  versus the verbose `--full` Markdown.
- **Reactions are captured.** iMessage tapbacks and WhatsApp emoji reactions are
  appended to the message they target (`[them: ❤️]`), so a question that was
  acknowledged with a thumbs-up doesn't read as ignored.
- **One-command AI review.** `./followups.sh` pipes the export straight into Claude
  Code's headless mode (`claude -p`) and returns a prioritized follow-up list — and the
  private message content never touches disk along the way.
- **Snooze list with auto-resurfacing.** Ignore people by stable identifier (phone,
  email, WhatsApp JID) with a `since` date. The moment they send something newer than
  that date, they reappear in the report, flagged as resurfaced — no manual pruning.
- **Name-based ignore picker.** `--ignore "Diana"` searches your macOS Contacts and
  snoozes every identifier on the card you pick, so one pick covers a person no matter
  which of their numbers they text from.
- **Noise filtered out.** Group chats, SMS short codes, toll-free numbers, status
  updates, and system messages are excluded — only actual humans surface.
- **Strictly read-only.** Both databases are opened with SQLite `mode=ro` and the
  script only ever runs `SELECT` queries. Your message history is never modified.

## How it works

`followuper.py` reads three local databases, all read-only:

| Source   | File                                                                            | Used for                      |
| -------- | ------------------------------------------------------------------------------- | ----------------------------- |
| iMessage | `~/Library/Messages/chat.db`                                                    | Messages, tapbacks            |
| WhatsApp | `~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite` | Messages, reactions           |
| Contacts | `~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb`    | Names, cross-platform merging |

It finds the individual (non-group) conversations with any activity in the last *N*
months, merges the two platforms per person, and prints a dated summary to stdout.
What gets included:

- **Fresh conversations are skipped.** A thread whose most recent message landed within
  the last `--inactive-days` days (default 7) is still warm and needs no follow-up yet.
  Pass `--inactive-days 0` to include everything regardless of recency.
- **Individuals only.** Group chats, WhatsApp status updates, and system messages are
  excluded.
- **Automated senders are excluded.** SMS short codes (≤6-digit numeric identifiers)
  and toll-free numbers (1-800/833/844/855/866/877/888) are businesses and bots, never
  people to follow up with. A stray automation from a normal-looking number can be
  added to the ignore list.
- **People are merged across platforms and numbers.** Phone numbers match on their last
  10 digits, and every number/address on one Contacts card shares a single merged
  section — a person you text on two numbers is one thread, not two.
- **Names** come from your Contacts and from WhatsApp's stored contact names; unknown
  numbers are shown raw.

## Requirements

- **macOS**, with the Messages app and/or WhatsApp Desktop set up on this machine.
- **Python 3** — uses only the standard library, so there's nothing to install.
- **Full Disk Access** for your terminal (System Settings → Privacy & Security) — macOS
  requires it to read `~/Library/Messages/`.
- **[Claude Code](https://claude.com/claude-code)** — only if you want the
  `./followups.sh` AI review; the export itself doesn't need it.

## Getting started

```bash
git clone https://github.com/akurilin/followuper.git
cd followuper

# Last 3 months, compact output to stdout (redirect to save it):
python3 followuper.py --months 3 > conversations.md

# Or go straight to the AI review — export + prioritized follow-up list:
./followups.sh
```

## Usage

```bash
# Verbose, human-readable Markdown instead of the compact format:
python3 followuper.py --months 3 --full

# Only one platform:
python3 followuper.py --months 3 --source imessage
python3 followuper.py --months 3 --source whatsapp

# Skip thin conversations (fewer than 5 messages in the window):
python3 followuper.py --months 3 --min-messages 5

# Only surface threads that have gone quiet (default: 7+ days of no activity):
python3 followuper.py --months 3 --inactive-days 14
python3 followuper.py --months 3 --inactive-days 0   # include fresh threads too

# Show every message in the window instead of just the last 10:
python3 followuper.py --months 3 --last 0

# Cap each message's length (handy for trimming long pasted text):
python3 followuper.py --months 3 --max-chars 300

# Use a custom ignore list:
python3 followuper.py --months 3 --ignore-file mine.json
```

### Options

| Option | Default | Meaning |
|--------|---------|---------|
| `--months N` | `3` | How many months back to include. |
| `--source` | `both` | `both`, `imessage`, or `whatsapp`. |
| `--min-messages N` | `1` | Drop people with fewer than N messages in the window. |
| `--inactive-days N` | `7` | Only include threads with no activity in the last N days; fresher ones are skipped. `0` includes everything. |
| `--last N` | `10` | Show only the most recent N messages per person; `0` for all. |
| `--ignore-file PATH` | `ignore.json` | People to skip (see below). Defaults to `ignore.json` next to the script. |
| `--ignore [NAME]` | — | Maintenance mode: search Contacts by name and add the person you pick to the ignore file (instead of exporting). See below. |
| `--full` | off | Verbose Markdown instead of the default compact format. |
| `--max-chars N` | `0` | Truncate each message to N chars; `0` = no limit. |

## Reviewing follow-ups with Claude

`followups.sh` runs the export and pipes it into Claude Code's headless mode
(`claude -p`) to get back a prioritized list of who needs a reply from you — no
interactive session required:

```bash
./followups.sh                 # defaults: 2-month window, include fresh threads
./followups.sh --months 3      # any flags are forwarded to followuper.py
```

The criteria for what counts as a follow-up and the output format live inline in the
script. The export (full of private message content) is piped straight into `claude -p`
over stdin, so it never touches disk beyond the model's own context.

While it runs, `followuper.py` logs its export progress to **stderr** (reading Contacts,
iMessage, WhatsApp, with message counts), so the export phase isn't opaque. Progress
goes to stderr and the report to stdout, so `./followups.sh > out.md` still captures a
clean report while you watch progress in the terminal.

## Output format (default: compact)

The default format is built to minimize tokens when feeding the export to a model —
e.g. "which threads did I drop?" — by stripping the repetition that otherwise
dominates the size:

- the contact's name and the date are printed **once**, not on every message line;
- direction is a single arrow (`->` you sent, `<-` they sent);
- the date appears once per day, with just `HH:MM` per message;
- URLs collapse to a `[domain]` marker;
- reactions (iMessage tapbacks, WhatsApp emoji reactions) are appended to the
  message they target as `[you: 👍]` / `[them: 👍]`, so a question that was
  acknowledged with a reaction doesn't read as ignored;
- `--max-chars` optionally caps long pasted messages.

```
# Jordan Lee
iMessage · +15550000001 · 29 msgs · last: them 06-06 14:23
05-18
16:39 <- Good luck tomorrow!
16:51 -> Thank you, I pushed it back a week to prep more. [them: ❤️]
05-23
16:46 <- Totally understand. Rooting for you.
```

The header line carries the highest-signal facts for follow-up: who sent the **last**
message and when. The first line of the document states the arrow convention so the
model doesn't have to guess.

### Full format (`--full`)

A more readable per-person section: resolved name, platforms, identifiers, a message
count, and the last-message time, followed by the conversation. Messages from both
platforms are interleaved in chronological order and tagged with their source.
Messages you sent are labelled `Me`; reactions appear on their own line under the
message they target (e.g. `Reactions: Me 👍`).

```markdown
# Jordan Lee

- **Sources:** iMessage, WhatsApp
- **Identifiers:** +15550000001, jordan@example.com
- **Messages shown:** 10 of 42 in window
- **Last message:** 2026-06-01 18:30

## Conversation

**2026-05-10 09:15 — Me** (iMessage)
are we still on for friday?

**2026-05-10 09:18 — Jordan Lee** (WhatsApp)
yes! see you then
```

## Ignoring people you don't need to follow up with

List them in an ignore file (default `ignore.json` next to the script). Each entry is
keyed by a **stable identifier** — a phone number, an email, or a WhatsApp JID —
mapped to the date you snoozed them. The value can be a bare date, or an object with a
`note` for readability. See `ignore.example.json` for the shape:

```json
{
  "+15550000001": { "since": "2026-06-06", "note": "label" },
  "+15550000002": "2026-06-06",
  "someone@example.com": { "since": "2026-06-06", "note": "old coworker" }
}
```

Why identifiers instead of names: names are ambiguous (two different people can share a
name, display names can contain emoji, etc.). Phone numbers are matched on their last
10 digits, so the same person is pinned whether they show up via iMessage, a WhatsApp
phone JID, or a contacts-formatted number. A name can still be used as a key as a
fallback, but an ID is reliable. The matching identifiers for anyone are printed in
their section, so you can copy one straight from there.

### Adding someone by name

You rarely know a number off the top of your head, and a contact may have several. So
instead of editing the JSON by hand, run the picker:

```bash
python3 followuper.py --ignore            # prompts for a name to search
python3 followuper.py --ignore "Diana"    # pre-fills the search with "Diana"
```

It searches your macOS Contacts by name, lists the matches with **all** the numbers and
emails on each card, and snoozes the one you pick — writing every identifier on that
card to the ignore file with today's date. One pick covers a person no matter which of
their numbers they text from. It loops so you can add several in a sitting; a blank
search quits.

**Auto-resurfacing:** a snoozed person stays hidden only while their most recent
message is on or before the `since` date. The moment they send something newer, they
reappear in the report, flagged as resurfaced. Nothing is rewritten — the date itself
is the rule — so you never have to manually prune the list when a dropped connection
comes back to life. To drop someone permanently, just delete their line.

> Your real `ignore.json` holds personal contacts, so it is **git-ignored**. Only the
> fictitious `ignore.example.json` is tracked.

## Privacy & safety

- **Nothing leaves your machine** unless you choose to pipe the export somewhere. The
  script itself makes no network calls.
- **The databases are never modified.** Both are opened strictly read-only (SQLite
  `mode=ro`), and the script only ever runs `SELECT` queries — it never writes to,
  copies over, or alters the original files.
- **The AI review keeps message content off disk.** `followups.sh` streams the export
  into `claude -p` over stdin, so private conversations are never persisted to an
  intermediate file.

## Known limitations

- **WhatsApp Desktop is not a full archive.** It only contains messages that synced
  while the desktop app was linked to your phone, so its history may be shallower
  than iMessage's.
- **Email-based iMessage contacts merge with WhatsApp only via Contacts.** Merging
  across platforms relies on a shared phone number or a shared contact card. Someone you
  reach by Apple ID email who is *not* in your Contacts (so the email and their WhatsApp
  phone can't be tied to one card) will appear as a separate section.
- Reading `~/Library/Messages/` may require granting your terminal **Full Disk
  Access** in System Settings → Privacy & Security.

## License

[MIT](LICENSE)
