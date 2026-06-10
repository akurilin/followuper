#!/usr/bin/env python3
"""
followuper — export 1-on-1 conversations from iMessage + WhatsApp as Markdown.

Reads your local macOS iMessage (chat.db) and WhatsApp Desktop (ChatStorage.sqlite)
databases STRICTLY READ-ONLY, finds the individual (non-group) conversations that had
any activity in the last N months, merges the two platforms per person, and prints a
single Markdown document to stdout (one section per person, every message dated).
Reactions (iMessage tapbacks, WhatsApp emoji reactions) are attached to the message
they target, so an acknowledged question reads as acknowledged.

No third-party dependencies — only the Python standard library (sqlite3).

Usage:
    python3 followuper.py --months 3 > conversations.md
    python3 followuper.py --months 6 > conversations.md   # wider window
    python3 followuper.py --months 3 --source imessage    # restrict to one platform
    python3 followuper.py --months 3 --last 25            # show 25 messages/person
    python3 followuper.py --months 3 --last 0             # all messages in window
    python3 followuper.py --months 3 --inactive-days 14   # only threads quiet 2+ weeks
    python3 followuper.py --months 3 --inactive-days 0    # include fresh threads too
    python3 followuper.py --months 3 --ignore-file mine.json   # custom ignore list
    python3 followuper.py --months 3 --max-chars 300     # cap each message's length
    python3 followuper.py --months 3 --full              # verbose Markdown instead
    python3 followuper.py --ignore                       # add someone to the ignore list
    python3 followuper.py --ignore "Diana"               # ...pre-filling the name search

Output defaults to a token-lean format (one line per message) meant for feeding to an
AI. Pass --full for the verbose, human-readable Markdown instead.

Ignore list: people you've decided not to follow up with can be listed in a JSON file
(default: ignore.json next to this script). Each entry is a name mapped to the date you
snoozed them. They stay hidden until they send a message dated AFTER that date, at which
point they reappear automatically, flagged as "resurfaced". See README for the format.

Safety: both databases are opened with SQLite URI `mode=ro`. The script issues only
SELECT statements and never writes to, copies over, or alters the original files.
"""

import argparse
import glob
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta

# Apple Core Data epoch offset: seconds between 1970-01-01 and 2001-01-01.
MAC_EPOCH = 978307200

# The database owner (messages sent by this identity are labelled "Me").
ME_LABEL = "Me"


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def log(message):
    """Print a progress line to stderr.

    Progress goes to stderr, never stdout: stdout is the data stream (it may be
    redirected to a file or piped into another tool, e.g. `claude -p`), and these
    lines are just for a human watching the run. They carry only counts, never
    message content.
    """
    print(message, file=sys.stderr, flush=True)


def months_ago_epoch(months):
    """Return the unix timestamp `months` calendar months before now (local)."""
    now = datetime.now()
    month_index = (now.year * 12 + (now.month - 1)) - months
    year, month = divmod(month_index, 12)
    month += 1
    # Clamp the day so e.g. 'months ago' from the 31st never overflows a short month.
    day = min(now.day, 28)
    cutoff = now.replace(year=year, month=month, day=day)
    return int(cutoff.timestamp()), cutoff


def normalize_phone(number):
    """Reduce any phone string to its last 10 digits for cross-source matching."""
    digits = re.sub(r"\D", "", number or "")
    return digits[-10:] if len(digits) >= 10 else digits


def id_token(value):
    """Canonical, comparable form of an identifier (phone / email / WhatsApp JID).

    Phones reduce to their last 10 digits, so the same person matches whether they
    appear as +1..., a WhatsApp phone JID, or a contacts-formatted number. Emails
    lowercase. Opaque WhatsApp @lid identities compare as-is. This mirrors how the
    script already merges people across the two platforms.
    """
    v = (value or "").strip().lower()
    if v.endswith("@s.whatsapp.net"):
        v = v.split("@")[0]            # keep the phone, fall through to digit handling
    elif v.endswith("@lid"):
        return v                       # linked-identity: opaque but stable
    elif "@" in v:
        return v                       # email
    digits = re.sub(r"\D", "", v)
    return digits[-10:] if len(digits) >= 10 else v


def looks_like_name(value):
    """True if the string looks like a human name rather than a raw phone/JID."""
    if not value:
        return False
    return bool(re.search(r"[A-Za-z]", value)) and "@" not in value


def load_ignore_list(path):
    """Load the ignore file into a list of entries.

    The file is a JSON object keyed by a stable identifier (phone / email / WhatsApp
    JID) — the reliable way to pin a person. Each value is either the snooze date
    string, or an object {"since": "YYYY-MM-DD", "note": "label"} where note is just a
    human label. A name can also be used as the key as a convenience fallback.

    Missing file is fine (nobody ignored). The date means "hide unless newer message".
    """
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    entries = []
    for key, value in raw.items():
        since = value["since"] if isinstance(value, dict) else value
        entries.append({
            "token": id_token(key),     # for matching against identifiers
            "name": key.strip().lower(),  # for the name fallback
            "since": datetime.strptime(since, "%Y-%m-%d").date(),
        })
    return entries


def ignore_since_date(person, entries):
    """If this person is on the ignore list, return their snooze date, else None.

    Matches primarily on identifier token (phone/email/JID), which is stable. Falls
    back to the display name (exact, or first word(s) — so "Sam" matches
    "Sam 🌸") for entries written as a name rather than an ID.
    """
    tokens = {id_token(i) for i in person.identifiers}
    name = person.display_name.lower()
    for entry in entries:
        if (entry["token"] in tokens
                or name == entry["name"]
                or name.startswith(entry["name"] + " ")):
            return entry["since"]
    return None


def clean_text(text):
    """Trim Apple typedstream trailing garbage (null bytes / replacement chars)."""
    if not text:
        return text
    for marker in ("\x00", "�"):
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx]
    return text.strip()


def shorten_urls(text):
    """Replace full URLs with a [domain] marker — the tracking path is dead weight."""
    def repl(match):
        domain = re.sub(r"^https?://(www\.)?", "", match.group(0)).split("/")[0]
        return f"[{domain}]"
    return re.sub(r"https?://\S+", repl, text)


def squeeze_body(text, max_chars):
    """Collapse a message to a single compact line, shortening URLs and trimming length."""
    text = " ".join(shorten_urls(text).split())
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


# --------------------------------------------------------------------------- #
# iMessage typedstream text extraction
# --------------------------------------------------------------------------- #
def extract_text_from_typedstream(blob):
    """Pull the message body out of message.attributedBody (Apple typedstream)."""
    if not blob:
        return None
    try:
        data = bytes(blob)
        idx = data.find(b"\x84\x01+")
        if idx == -1:
            return None
        pos = idx + 3
        if pos >= len(data):
            return None
        length = data[pos]
        pos += 1
        if length == 0x81:
            if pos + 1 >= len(data):
                return None
            length = (data[pos] << 8) | data[pos + 1]
            pos += 2
        elif length == 0x82:
            if pos + 2 >= len(data):
                return None
            length = (data[pos] << 16) | (data[pos + 1] << 8) | data[pos + 2]
            pos += 3
        if pos + length > len(data):
            length = len(data) - pos
        return data[pos:pos + length].decode("utf-8", errors="replace").strip()
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Person aggregation
# --------------------------------------------------------------------------- #
class Person:
    """Merged conversation for one individual across both platforms."""

    def __init__(self, key):
        self.key = key
        self.name_candidates = []   # list of (name, source) in discovery order
        self.identifiers = set()    # raw phone/email/jid strings seen
        self.sources = set()        # {"iMessage", "WhatsApp"}
        self.messages = []          # list of dicts: ts, source, is_from_me, text
        self.resurfaced = False     # was ignored, but came back with a newer message

    def add_name(self, name, source):
        if name and name not in [n for n, _ in self.name_candidates]:
            self.name_candidates.append((name, source))

    def absorb(self, other):
        """Fold another Person — the same human on a different number/address — in."""
        self.identifiers |= other.identifiers
        self.sources |= other.sources
        self.messages.extend(other.messages)
        for name, source in other.name_candidates:
            self.add_name(name, source)

    @property
    def display_name(self):
        # Prefer a real (alphabetic) name; otherwise fall back to first identifier.
        for name, _ in self.name_candidates:
            if looks_like_name(name):
                return name
        if self.name_candidates:
            return self.name_candidates[0][0]
        return next(iter(self.identifiers), "Unknown")

    @property
    def last_ts(self):
        return max((m["ts"] for m in self.messages), default=0)


# --------------------------------------------------------------------------- #
# Contacts (name resolution + cross-number identity)
# --------------------------------------------------------------------------- #
def load_contacts():
    """Read the macOS AddressBook (best-effort, read-only).

    Returns three maps:
      phone_to_name   — normalized phone (last 10 digits) -> display name
      email_to_name   — lowercase email                   -> display name
      token_to_record — id_token(phone/email)             -> Contacts record id

    The record id is the crux of cross-number merging: every phone number and email on
    one contact card shares the same ZABCDRECORD.Z_PK, so two numbers for the same
    person map to the same record and can be folded into one conversation. All maps are
    empty if the AddressBook can't be found or read.
    """
    phone_to_name, email_to_name, token_to_record = {}, {}, {}
    sources = glob.glob(os.path.expanduser(
        "~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb"
    ))
    if not sources:
        return phone_to_name, email_to_name, token_to_record
    try:
        conn = sqlite3.connect(f"file:{sources[0]}?mode=ro", uri=True)
        # ZOWNER points at the owning ZABCDRECORD row — the per-person identity.
        for row in conn.execute("""
            SELECT p.ZFULLNUMBER, p.ZOWNER, r.ZFIRSTNAME, r.ZLASTNAME, r.ZNICKNAME, r.ZORGANIZATION
            FROM ZABCDPHONENUMBER p
            JOIN ZABCDRECORD r ON p.ZOWNER = r.Z_PK
        """):
            name = (f"{row[2] or ''} {row[3] or ''}".strip() or row[4] or row[5] or None)
            key = normalize_phone(row[0])
            if key:
                token_to_record.setdefault(key, row[1])
                if name:
                    phone_to_name.setdefault(key, name)
        for row in conn.execute("""
            SELECT e.ZADDRESS, e.ZOWNER, r.ZFIRSTNAME, r.ZLASTNAME, r.ZNICKNAME, r.ZORGANIZATION
            FROM ZABCDEMAILADDRESS e
            JOIN ZABCDRECORD r ON e.ZOWNER = r.Z_PK
        """):
            name = (f"{row[2] or ''} {row[3] or ''}".strip() or row[4] or row[5] or None)
            if row[0]:
                token_to_record.setdefault(row[0].lower(), row[1])
                if name:
                    email_to_name.setdefault(row[0].lower(), name)
        conn.close()
    except sqlite3.Error as exc:
        print(f"# Note: could not read Contacts DB: {exc}", file=sys.stderr)
    return phone_to_name, email_to_name, token_to_record


def load_contact_cards():
    """Read Contacts grouped by person, for the `--ignore` picker (read-only).

    Returns a list of {"name", "identifiers"} — one entry per address-book card that
    has a name and at least one phone or email. `identifiers` holds *every* phone and
    email on the card, so ignoring the person covers all their numbers in one shot
    (the whole reason a name-based picker is worth having). Empty if Contacts can't be
    read. Sorted by name for a stable picker order.
    """
    sources = glob.glob(os.path.expanduser(
        "~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb"
    ))
    if not sources:
        return []

    def card_name(first, last, nick, org):
        return (f"{first or ''} {last or ''}".strip() or nick or org or "")

    def clean_identifier(value):
        # Phones keep only digits and a leading +; emails lowercase. The ignore list
        # matches on the last 10 digits regardless, so this is just for a tidy file.
        v = (value or "").strip()
        return v.lower() if "@" in v else re.sub(r"[^\d+]", "", v)

    cards = {}  # record Z_PK -> {"name", "identifiers": set}
    try:
        conn = sqlite3.connect(f"file:{sources[0]}?mode=ro", uri=True)
        for table, column in (("ZABCDPHONENUMBER", "ZFULLNUMBER"),
                              ("ZABCDEMAILADDRESS", "ZADDRESS")):
            for row in conn.execute(f"""
                SELECT r.Z_PK, r.ZFIRSTNAME, r.ZLASTNAME, r.ZNICKNAME, r.ZORGANIZATION,
                       t.{column}
                FROM {table} t JOIN ZABCDRECORD r ON t.ZOWNER = r.Z_PK
            """):
                ident = clean_identifier(row[5])
                if not ident:
                    continue
                card = cards.setdefault(row[0],
                                        {"name": card_name(*row[1:5]), "identifiers": set()})
                card["identifiers"].add(ident)
        conn.close()
    except sqlite3.Error as exc:
        print(f"# Note: could not read Contacts DB: {exc}", file=sys.stderr)
        return []

    result = [{"name": c["name"], "identifiers": sorted(c["identifiers"])}
              for c in cards.values() if c["name"] and c["identifiers"]]
    result.sort(key=lambda c: c["name"].lower())
    return result


def add_to_ignore(ignore_path, initial_query=""):
    """Interactive picker: search Contacts by name and snooze the person you pick.

    Writes every identifier on the chosen card into `ignore_path` with today's date,
    so any number that person texts from is covered. Loops so you can add several in
    one sitting; a blank search quits. Run straight from the terminal (it reads stdin).
    """
    cards = load_contact_cards()
    if not cards:
        log("No Contacts found to search — add identifiers to the ignore file by hand.")
        return

    data = {}
    if os.path.exists(ignore_path):
        with open(ignore_path, encoding="utf-8") as fh:
            text = fh.read().strip()
        data = json.loads(text) if text else {}

    today = datetime.now().strftime("%Y-%m-%d")
    query = initial_query
    while True:
        if not query:
            query = input("search contacts (blank to quit): ").strip()
        if not query:
            break
        matches = [c for c in cards if query.lower() in c["name"].lower()]
        query = ""  # consumed; prompt fresh next round
        if not matches:
            print("  no matches")
            continue
        for i, c in enumerate(matches, 1):
            print(f"  {i}. {c['name']:<24} {', '.join(c['identifiers'])}")
        sel = input("ignore which # (blank to search again)? ").strip()
        if not sel:
            continue
        try:
            chosen = matches[int(sel) - 1]
        except (ValueError, IndexError):
            print("  invalid selection")
            continue
        for ident in chosen["identifiers"]:
            data[ident] = {"since": today, "note": chosen["name"]}
        with open(ignore_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print(f"✓ ignored {chosen['name']} ({', '.join(chosen['identifiers'])}) "
              f"— added to {os.path.basename(ignore_path)}")


def merge_by_contact(persons, token_to_record):
    """Collapse Person fragments that belong to one Contacts card into one conversation.

    Someone with two phone numbers (or a phone and an email) on a single card is one
    human; keyed by raw identifier alone they'd show up as separate sections. Anyone not
    in Contacts — or whose card can't be matched — is left exactly as-is, keyed by their
    own identifier. Discovery order is preserved so later sorting is unaffected.
    """
    merged = {}
    order = []
    for person in persons:
        record = None
        for ident in person.identifiers:
            record = token_to_record.get(id_token(ident))
            if record is not None:
                break
        key = ("contact", record) if record is not None else person.key
        if key in merged:
            merged[key].absorb(person)
        else:
            merged[key] = person
            order.append(key)
    return [merged[k] for k in order]


# --------------------------------------------------------------------------- #
# iMessage collection
# --------------------------------------------------------------------------- #
# Standard tapback kinds, keyed by associated_message_type % 1000 (2000s add a
# tapback, 3000s remove one). Kind 6 is a custom emoji tapback, whose emoji lives
# in the associated_message_emoji column instead.
TAPBACK_EMOJI = {0: "❤️", 1: "👍", 2: "👎", 3: "😂", 4: "‼️", 5: "❓"}


def collect_imessage(people, cutoff_epoch, phone_to_name, email_to_name):
    chat_db = os.path.expanduser("~/Library/Messages/chat.db")
    if not os.path.exists(chat_db):
        print(f"# Note: iMessage database not found at {chat_db}", file=sys.stderr)
        return

    conn = sqlite3.connect(f"file:{chat_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    def resolve(identifier):
        if not identifier:
            return identifier
        if "@" in identifier:
            return email_to_name.get(identifier.lower(), identifier)
        return phone_to_name.get(normalize_phone(identifier), identifier)

    # Individual chats only: exactly one handle, not an iMessage group ('chat...'),
    # and not an automated sender. We drop SMS short codes (<=6 all-digit identifiers
    # like 39781 or 346637) and toll-free numbers — these are businesses/automations
    # (T-Mobile, Resy, Partiful, ...), never people to follow up with.
    individual_chats = {}  # chat ROWID -> chat_identifier
    for row in conn.execute("""
        SELECT c.ROWID AS rowid, c.chat_identifier AS ident
        FROM chat c
        WHERE c.chat_identifier NOT LIKE 'chat%'
          AND NOT (length(c.chat_identifier) <= 6
                   AND c.chat_identifier NOT GLOB '*[^0-9]*')
          AND c.chat_identifier NOT LIKE '+1800%'
          AND c.chat_identifier NOT LIKE '+1833%'
          AND c.chat_identifier NOT LIKE '+1844%'
          AND c.chat_identifier NOT LIKE '+1855%'
          AND c.chat_identifier NOT LIKE '+1866%'
          AND c.chat_identifier NOT LIKE '+1877%'
          AND c.chat_identifier NOT LIKE '+1888%'
          AND (SELECT COUNT(DISTINCT handle_id)
               FROM chat_handle_join chj
               WHERE chj.chat_id = c.ROWID) = 1
    """):
        if row["ident"]:
            individual_chats[row["rowid"]] = row["ident"]

    if not individual_chats:
        conn.close()
        return

    placeholders = ",".join("?" for _ in individual_chats)
    query = f"""
        SELECT cmj.chat_id AS chat_id,
               m.guid AS guid,
               m.date AS date,
               m.is_from_me AS is_from_me,
               m.text AS text,
               m.attributedBody AS attributedBody,
               m.cache_has_attachments AS has_attach
        FROM chat_message_join cmj
        JOIN message m ON cmj.message_id = m.ROWID
        WHERE cmj.chat_id IN ({placeholders})
          AND m.associated_message_type = 0
          AND (m.date / 1000000000 + {MAC_EPOCH}) >= CAST(? AS INTEGER)
        ORDER BY m.date
    """
    params = list(individual_chats.keys()) + [cutoff_epoch]

    by_guid = {}  # message guid -> message dict, for attaching tapbacks below
    for row in conn.execute(query, params):
        identifier = individual_chats[row["chat_id"]]
        ts = row["date"] / 1_000_000_000 + MAC_EPOCH

        text = clean_text(row["text"]) or extract_text_from_typedstream(row["attributedBody"])
        text = clean_text(text)
        if not text:
            text = "[attachment]" if row["has_attach"] else None
        if not text:
            continue

        if "@" in identifier:
            key = ("email", identifier.lower())
        else:
            key = ("phone", normalize_phone(identifier))

        person = people.setdefault(key, Person(key))
        person.identifiers.add(identifier)
        person.sources.add("iMessage")
        person.add_name(resolve(identifier), "iMessage")
        message = {
            "ts": ts,
            "source": "iMessage",
            "is_from_me": bool(row["is_from_me"]),
            "text": text,
        }
        person.messages.append(message)
        by_guid[row["guid"]] = message

    # Second pass: tapbacks. They are stored as separate message rows pointing at
    # their target via associated_message_guid ("p:0/GUID" or "bp:GUID"). A tapback
    # always postdates its target, so the same date cutoff cannot miss one whose
    # target is in the window. Processed in date order so a removal (3000s) or a
    # changed tapback lands after the add it supersedes.
    reaction_query = f"""
        SELECT m.associated_message_guid AS target,
               m.associated_message_type AS rtype,
               m.associated_message_emoji AS emoji,
               m.is_from_me AS is_from_me
        FROM chat_message_join cmj
        JOIN message m ON cmj.message_id = m.ROWID
        WHERE cmj.chat_id IN ({placeholders})
          AND m.associated_message_type BETWEEN 2000 AND 3999
          AND (m.date / 1000000000 + {MAC_EPOCH}) >= CAST(? AS INTEGER)
        ORDER BY m.date
    """
    for row in conn.execute(reaction_query, params):
        guid = (row["target"] or "").split("/")[-1].split(":")[-1]
        message = by_guid.get(guid)
        if message is None:
            continue
        emoji = TAPBACK_EMOJI.get(row["rtype"] % 1000) or clean_text(row["emoji"])
        if not emoji:
            continue
        from_me = bool(row["is_from_me"])
        reactions = message.setdefault("reactions", [])
        # One tapback per sender: an add replaces that sender's previous one.
        reactions[:] = [r for r in reactions if r["from_me"] != from_me]
        if row["rtype"] < 3000:
            reactions.append({"from_me": from_me, "emoji": emoji})

    conn.close()


# --------------------------------------------------------------------------- #
# WhatsApp collection
# --------------------------------------------------------------------------- #
WA_MEDIA_LABELS = {1: "[image]", 2: "[video]", 3: "[audio]", 10: "[sticker]", 14: "[deleted message]"}


def pb_fields(data):
    """Iterate (field_number, wire_type, value) over one protobuf message.

    Minimal decoder for WhatsApp's ZRECEIPTINFO blob: varints come back as ints,
    length-delimited fields as raw bytes, fixed32/64 skipped. Malformed input
    raises (IndexError/ValueError) — callers treat that as "no reactions here".
    """
    pos = 0
    while pos < len(data):
        key = 0
        shift = 0
        while True:
            byte = data[pos]
            pos += 1
            key |= (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                break
        field, wire = key >> 3, key & 7
        if wire == 0:
            value = 0
            shift = 0
            while True:
                byte = data[pos]
                pos += 1
                value |= (byte & 0x7F) << shift
                shift += 7
                if not byte & 0x80:
                    break
        elif wire == 2:
            length = 0
            shift = 0
            while True:
                byte = data[pos]
                pos += 1
                length |= (byte & 0x7F) << shift
                shift += 7
                if not byte & 0x80:
                    break
            value = data[pos:pos + length]
            pos += length
        elif wire == 1:
            value = data[pos:pos + 8]
            pos += 8
        elif wire == 5:
            value = data[pos:pos + 4]
            pos += 4
        else:
            raise ValueError(f"unsupported wire type {wire}")
        yield field, wire, value


def wa_reactions(blob):
    """Pull reactions out of a ZWAMESSAGEINFO.ZRECEIPTINFO blob.

    The blob is a protobuf where repeated field 7 carries one reaction per
    reactor, as a nested message (field 1) of {2: sender JID, 3: emoji,
    4: timestamp ms}. The sender JID is ABSENT when the reaction is our own.
    A changed reaction appears as a newer entry for the same sender and a removed
    one as an empty emoji, so keep each sender's latest entry and drop empties.

    Returns a list of (from_me, emoji).
    """
    latest = {}  # sender jid bytes (None = me) -> (ts, emoji)
    try:
        for field, wire, value in pb_fields(bytes(blob)):
            if field != 7 or wire != 2:
                continue
            for f1, w1, entry in pb_fields(value):
                if f1 != 1 or w1 != 2:
                    continue
                sender, emoji, ts = None, None, 0
                for f2, w2, v2 in pb_fields(entry):
                    if f2 == 2 and w2 == 2:
                        sender = bytes(v2)
                    elif f2 == 3 and w2 == 2:
                        emoji = v2.decode("utf-8", errors="replace")
                    elif f2 == 4 and w2 == 0:
                        ts = v2
                if emoji is not None and ts >= latest.get(sender, (-1, ""))[0]:
                    latest[sender] = (ts, emoji)
    except (IndexError, ValueError):
        return []
    return [(sender is None, emoji) for sender, (_, emoji) in latest.items() if emoji]


def collect_whatsapp(people, cutoff_epoch):
    db_path = os.path.expanduser(
        "~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite"
    )
    if not os.path.exists(db_path):
        print(f"# Note: WhatsApp database not found at {db_path}", file=sys.stderr)
        return

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    # Fallback display names from self-set push names.
    push_names = {}
    try:
        for row in conn.execute("""
            SELECT ZJID, ZPUSHNAME FROM ZWAPROFILEPUSHNAME
            WHERE ZPUSHNAME IS NOT NULL AND ZPUSHNAME != ''
        """):
            push_names[row["ZJID"]] = row["ZPUSHNAME"]
    except sqlite3.Error:
        pass

    query = f"""
        SELECT m.Z_PK AS pk,
               s.ZCONTACTJID AS jid,
               s.ZPARTNERNAME AS partner,
               s.ZCONTACTIDENTIFIER AS alt_id,
               m.ZMESSAGEDATE AS date,
               m.ZISFROMME AS is_from_me,
               m.ZTEXT AS text,
               m.ZMESSAGETYPE AS mtype
        FROM ZWAMESSAGE m
        JOIN ZWACHATSESSION s ON m.ZCHATSESSION = s.Z_PK
        WHERE s.ZGROUPINFO IS NULL
          AND s.ZSESSIONTYPE = 0
          AND s.ZCONTACTJID NOT LIKE '%@status'
          AND m.ZMESSAGETYPE != 6
          AND (m.ZMESSAGEDATE + {MAC_EPOCH}) >= CAST(? AS INTEGER)
        ORDER BY m.ZMESSAGEDATE
    """

    by_pk = {}  # ZWAMESSAGE.Z_PK -> message dict, for attaching reactions below
    for row in conn.execute(query, [cutoff_epoch]):
        jid = row["jid"] or ""
        mtype = row["mtype"]

        if mtype in (0, 7):
            text = clean_text(row["text"])
        else:
            text = WA_MEDIA_LABELS.get(mtype)
        if not text:
            continue

        ts = row["date"] + MAC_EPOCH

        # Build a cross-platform merge key (phone last-10 when available).
        phone10 = ""
        if jid.endswith("@s.whatsapp.net"):
            phone10 = normalize_phone(jid.split("@")[0])
        elif jid.endswith("@lid") and row["alt_id"] and "@s.whatsapp.net" in row["alt_id"]:
            phone10 = normalize_phone(row["alt_id"].split("@")[0])

        if phone10 and len(phone10) == 10:
            key = ("phone", phone10)
        else:
            key = ("wa", jid)

        person = people.setdefault(key, Person(key))
        person.identifiers.add(jid)
        person.sources.add("WhatsApp")
        person.add_name(row["partner"] or push_names.get(jid) or jid, "WhatsApp")
        message = {
            "ts": ts,
            "source": "WhatsApp",
            "is_from_me": bool(row["is_from_me"]),
            "text": text,
        }
        person.messages.append(message)
        by_pk[row["pk"]] = message

    # Second pass: reactions. They are not message rows — each message's
    # ZWAMESSAGEINFO.ZRECEIPTINFO protobuf carries the current reactions on it.
    # In a 1-on-1 chat the reactor is either us (no sender JID in the blob) or
    # the contact, so no JID matching is needed.
    info_query = f"""
        SELECT i.ZMESSAGE AS pk, i.ZRECEIPTINFO AS blob
        FROM ZWAMESSAGEINFO i
        JOIN ZWAMESSAGE m ON i.ZMESSAGE = m.Z_PK
        JOIN ZWACHATSESSION s ON m.ZCHATSESSION = s.Z_PK
        WHERE s.ZGROUPINFO IS NULL
          AND s.ZSESSIONTYPE = 0
          AND s.ZCONTACTJID NOT LIKE '%@status'
          AND i.ZRECEIPTINFO IS NOT NULL
          AND (m.ZMESSAGEDATE + {MAC_EPOCH}) >= CAST(? AS INTEGER)
    """
    for row in conn.execute(info_query, [cutoff_epoch]):
        message = by_pk.get(row["pk"])
        if message is None:
            continue
        for from_me, emoji in wa_reactions(row["blob"]):
            message.setdefault("reactions", []).append(
                {"from_me": from_me, "emoji": emoji})

    conn.close()


# --------------------------------------------------------------------------- #
# Markdown rendering
# --------------------------------------------------------------------------- #
def render_person(person, last):
    # Keep only the most recent `last` messages (last == 0 means all of them).
    messages = sorted(person.messages, key=lambda m: m["ts"])
    if last:
        messages = messages[-last:]

    lines = []
    name = person.display_name
    lines.append(f"# {name}")
    lines.append("")
    if person.resurfaced:
        lines.append("- **⚡ Resurfaced:** on your ignore list, but messaged you since.")
    lines.append(f"- **Sources:** {', '.join(sorted(person.sources))}")
    lines.append(f"- **Identifiers:** {', '.join(sorted(person.identifiers))}")
    lines.append(f"- **Messages shown:** {len(messages)} of {len(person.messages)} in window")
    last_seen = datetime.fromtimestamp(person.last_ts).strftime("%Y-%m-%d %H:%M")
    lines.append(f"- **Last message:** {last_seen}")
    lines.append("")
    lines.append("## Conversation")
    lines.append("")

    for msg in messages:
        when = datetime.fromtimestamp(msg["ts"]).strftime("%Y-%m-%d %H:%M")
        sender = ME_LABEL if msg["is_from_me"] else name
        lines.append(f"**{when} — {sender}** ({msg['source']})")
        # Preserve multi-line message bodies under the header line.
        for textline in msg["text"].splitlines() or [""]:
            lines.append(textline)
        if msg.get("reactions"):
            lines.append("*Reactions: " + ", ".join(
                f"{ME_LABEL if r['from_me'] else name} {r['emoji']}"
                for r in msg["reactions"]) + "*")
        lines.append("")

    return "\n".join(lines)


def render_person_compact(person, last, max_chars):
    """Token-lean rendering: one line per message, no repeated name/date/markdown.

    Direction is a single arrow (-> you sent, <- they sent), the date is printed once
    per day, and only the time prefixes each line. For people on both platforms an
    'i'/'w' tag marks each message's source; otherwise the source lives in the header.
    """
    messages = sorted(person.messages, key=lambda m: m["ts"])
    if last:
        messages = messages[-last:]

    multi_source = len(person.sources) > 1
    last_msg = messages[-1]
    who_last = "you" if last_msg["is_from_me"] else "them"
    last_when = datetime.fromtimestamp(person.last_ts).strftime("%m-%d %H:%M")

    lines = [f"# {person.display_name}"]
    if person.resurfaced:
        lines.append("resurfaced: was ignored, messaged you since")
    lines.append(f"{'+'.join(sorted(person.sources))} · "
                 f"{', '.join(sorted(person.identifiers))} · "
                 f"{len(person.messages)} msgs · last: {who_last} {last_when}")

    current_day = None
    for msg in messages:
        dt = datetime.fromtimestamp(msg["ts"])
        day = dt.strftime("%m-%d")
        if day != current_day:
            lines.append(day)
            current_day = day
        arrow = "->" if msg["is_from_me"] else "<-"
        tag = ("i " if msg["source"] == "iMessage" else "w ") if multi_source else ""
        reactions = "".join(f" [{'you' if r['from_me'] else 'them'}: {r['emoji']}]"
                            for r in msg.get("reactions", []))
        lines.append(f"{dt:%H:%M} {arrow} {tag}{squeeze_body(msg['text'], max_chars)}"
                     f"{reactions}")

    return "\n".join(lines)


# A person's merge key is a (kind, value) tuple, e.g. ("phone", "2065551234")
# or ("email", "a@b.com"). Same key on both platforms => one merged person.


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(
        description="Export individual iMessage + WhatsApp conversations as Markdown."
    )
    parser.add_argument("--months", type=int, default=3,
                        help="How many months back to include (default: 3).")
    parser.add_argument("--source", choices=["both", "imessage", "whatsapp"],
                        default="both", help="Which platform(s) to read (default: both).")
    parser.add_argument("--min-messages", type=int, default=1,
                        help="Skip people with fewer than this many messages in the window.")
    parser.add_argument("--inactive-days", type=int, default=7,
                        help="Only include conversations with no activity in the last N "
                             "days; anything more recent is still 'fresh' and skipped "
                             "(default: 7). Use 0 to include even fresh conversations.")
    parser.add_argument("--last", type=int, default=10,
                        help="Show only the most recent N messages per person "
                             "(default: 10; use 0 for all messages in the window).")
    default_ignore = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "ignore.json")
    parser.add_argument("--ignore-file", default=default_ignore,
                        help="JSON file of names to skip, mapped to the date you "
                             "snoozed them (default: ignore.json next to this script). "
                             "A snoozed person reappears once they send a newer message.")
    parser.add_argument("--ignore", nargs="?", const="", default=None,
                        metavar="NAME",
                        help="Add someone to the ignore file by name instead of "
                             "exporting: searches Contacts, lists matches (with all "
                             "their numbers), and snoozes the one you pick. Pass a name "
                             "to pre-fill the search, or bare --ignore to be prompted.")
    parser.add_argument("--full", action="store_true",
                        help="Verbose, human-readable Markdown instead of the default "
                             "token-lean compact format.")
    parser.add_argument("--max-chars", type=int, default=0,
                        help="In compact mode, truncate each message to this many "
                             "characters (default: 0 = no truncation).")
    args = parser.parse_args()

    # --ignore is a maintenance mode, not an export: add a person and stop.
    if args.ignore is not None:
        add_to_ignore(args.ignore_file, args.ignore)
        return

    if args.months <= 0:
        parser.error("--months must be a positive integer")
    if args.last < 0:
        parser.error("--last must be a non-negative integer (0 means unlimited)")
    if args.max_chars < 0:
        parser.error("--max-chars must be a non-negative integer")
    if args.inactive_days < 0:
        parser.error("--inactive-days must be a non-negative integer (0 means unlimited)")

    cutoff_epoch, cutoff_dt = months_ago_epoch(args.months)

    log("Reading Contacts…")
    phone_to_name, email_to_name, token_to_record = load_contacts()
    log(f"  {len(phone_to_name)} phone + {len(email_to_name)} email name(s) from Contacts")

    def counts(people):
        return (sum(len(p.messages) for p in people.values()),
                sum(len(m.get("reactions", ())) for p in people.values()
                    for m in p.messages))

    people = {}
    if args.source in ("both", "imessage"):
        log("Reading iMessage…")
        collect_imessage(people, cutoff_epoch, phone_to_name, email_to_name)
        msgs, reactions = counts(people)
        log(f"  {msgs} message(s), {reactions} reaction(s) so far")
    if args.source in ("both", "whatsapp"):
        before_msgs, before_reactions = counts(people)
        log("Reading WhatsApp…")
        collect_whatsapp(people, cutoff_epoch)
        msgs, reactions = counts(people)
        log(f"  {msgs - before_msgs} WhatsApp message(s), "
            f"{reactions - before_reactions} reaction(s)")

    log("Merging contacts and applying filters…")

    # Fold together the numbers/addresses that belong to one Contacts card, so a person
    # you reach on two phones (or phone + WhatsApp) is a single conversation. Done before
    # the min-messages and inactivity filters so those judge the merged thread, not a
    # fragment of it.
    persons = merge_by_contact(list(people.values()), token_to_record)

    # Drop empty / below-threshold people, order by most recent activity.
    persons = [p for p in persons if len(p.messages) >= args.min_messages]
    persons.sort(key=lambda p: p.last_ts, reverse=True)

    # Hide conversations that are still fresh: if the last message landed within the
    # inactivity window, the thread is warm and needs no follow-up yet. We only surface
    # people who have gone quiet for at least `--inactive-days`.
    if args.inactive_days:
        fresh_cutoff = (datetime.now() - timedelta(days=args.inactive_days)).timestamp()
        persons = [p for p in persons if p.last_ts < fresh_cutoff]

    # Apply the ignore list. A snoozed person is hidden unless their most recent
    # message is dated after the snooze date — in which case they reappear, flagged.
    ignore = load_ignore_list(args.ignore_file)
    kept = []
    for person in persons:
        since = ignore_since_date(person, ignore)
        if since is None:
            kept.append(person)
        elif datetime.fromtimestamp(person.last_ts).date() > since:
            person.resurfaced = True
            kept.append(person)
        # else: still snoozed — drop silently.
    persons = kept

    log(f"Done: {len(persons)} conversation(s) to review.")

    quiet_note = (f" quiet for {args.inactive_days}+ day(s)"
                  if args.inactive_days else "")
    window_note = (f"Window: last {args.months} month(s) "
                   f"(since {cutoff_dt.strftime('%Y-%m-%d %H:%M')}). "
                   f"{len(persons)} individual conversation(s){quiet_note}.")

    out = sys.stdout
    if args.full:
        out.write(f"<!-- followuper export · {window_note} -->\n\n")
        for i, person in enumerate(persons):
            if i:
                out.write("\n---\n\n")
            out.write(render_person(person, args.last))
            out.write("\n")
    else:
        out.write(f"# followuper export · {window_note} · "
                  f"-> you sent, <- they sent · "
                  f"[you: x]/[them: x] = reaction to that message\n\n")
        for person in persons:
            out.write(render_person_compact(person, args.last, args.max_chars))
            out.write("\n\n")


if __name__ == "__main__":
    main()
