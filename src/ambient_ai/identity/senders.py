"""Sender identity. The phone number is the primary key for everything downstream.

Records live in a local SQLite file (settings.db_path()). Tokens and numbers are
never logged; callers emit telemetry with redact(phone) only.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime

from pydantic import BaseModel

from ambient_ai.settings import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS senders (
    phone TEXT PRIMARY KEY,
    verified_at TEXT,
    github_token TEXT,
    created_at TEXT NOT NULL,
    telegram_user_id TEXT,
    github_login TEXT,
    token_added_at TEXT
)
"""

ADDED_COLUMNS = (
    "ALTER TABLE senders ADD COLUMN telegram_user_id TEXT",
    "ALTER TABLE senders ADD COLUMN github_login TEXT",
    "ALTER TABLE senders ADD COLUMN token_added_at TEXT",
)
TELEGRAM_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS senders_telegram_user_id ON senders (telegram_user_id)"
)
"""The live store predates every column after `created_at`, so each is added on open and a
duplicate is tolerated. Uniqueness is an index and not a column constraint because SQLite
refuses `ADD COLUMN ... UNIQUE` outright: writing it inline would work on a fresh file and
fail on the one the demo is running against. Columns are only ever added; provisional `tg:`
records are merged onto a number by adopt_telegram_contact when a sender shares a contact.
`github_login` and `token_added_at` exist so `/tools` can say whose token is connected and
when, without ever reading the token itself back to anyone."""

COLUMNS = (
    "phone, verified_at, github_token, created_at, telegram_user_id, github_login, token_added_at"
)

PROVISIONAL_PREFIX = "tg:"
"""A sender key with no number behind it yet. The number is the key on every transport
(ADR-003); Telegram only gives the webhook a user id, so a sender starts provisional and
stops being provisional the moment they share their contact."""


def provisional_key(telegram_user_id: int | str) -> str:
    return f"{PROVISIONAL_PREFIX}{telegram_user_id}"


def is_provisional(sender: str) -> bool:
    return sender.startswith(PROVISIONAL_PREFIX)


def e164(raw: str) -> str:
    """Telegram sends `15551234567` or `+15551234567`; the key is always the second form."""
    return "+" + "".join(ch for ch in raw if ch.isdigit())


class SenderProfile(BaseModel):
    phone: str
    verified_at: datetime | None = None
    github_token: str | None = None
    created_at: datetime | None = None
    telegram_user_id: str | None = None
    github_login: str | None = None
    token_added_at: datetime | None = None

    @property
    def verified(self) -> bool:
        return self.verified_at is not None


@contextmanager
def _connect():
    conn = sqlite3.connect(db_path())
    try:
        conn.execute(SCHEMA)
        for statement in ADDED_COLUMNS:
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc):
                    raise
        conn.execute(TELEGRAM_INDEX)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _row_to_profile(row: tuple) -> SenderProfile:
    phone, verified_at, github_token, created_at, telegram_user_id, login, token_added_at = row
    return SenderProfile(
        phone=phone,
        verified_at=datetime.fromisoformat(verified_at) if verified_at else None,
        github_token=github_token,
        created_at=datetime.fromisoformat(created_at),
        telegram_user_id=telegram_user_id,
        github_login=login,
        token_added_at=datetime.fromisoformat(token_added_at) if token_added_at else None,
    )


def lookup_sender(phone: str) -> SenderProfile | None:
    """Return the profile for a known phone number, or None for a stranger."""
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {COLUMNS} FROM senders WHERE phone = ?", (phone,)
        ).fetchone()
    return _row_to_profile(row) if row else None


def lookup_by_telegram_id(telegram_user_id: int | str) -> SenderProfile | None:
    """Return the phone-keyed profile a Telegram user id maps to, or None when unmapped."""
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {COLUMNS} FROM senders WHERE telegram_user_id = ?",
            (str(telegram_user_id),),
        ).fetchone()
    return _row_to_profile(row) if row else None


def resolve_telegram_sender(telegram_user_id: int) -> str:
    """The sender key for a Telegram user: their number once shared, a provisional key until."""
    profile = lookup_by_telegram_id(telegram_user_id)
    return profile.phone if profile else provisional_key(telegram_user_id)


def upsert_sender(phone: str, verified_at: datetime | None = None) -> SenderProfile:
    """Create the sender record if absent; set verified_at when given. Returns the record."""
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO senders (phone, created_at) VALUES (?, ?)",
            (phone, datetime.now(UTC).isoformat()),
        )
        if verified_at is not None:
            conn.execute(
                "UPDATE senders SET verified_at = ? WHERE phone = ?",
                (verified_at.isoformat(), phone),
            )
    profile = lookup_sender(phone)
    assert profile is not None
    return profile


def set_token(phone: str, github_token: str, *, login: str | None = None) -> None:
    """Store the sender's own GitHub token, with the login it resolved to and the date.

    Replacing a token is the same write as adding one: there is one credential per sender per
    tool, so a second token overwrites the first rather than accumulating.
    """
    with _connect() as conn:
        updated = conn.execute(
            "UPDATE senders SET github_token = ?, github_login = ?, token_added_at = ? "
            "WHERE phone = ?",
            (github_token, login, datetime.now(UTC).isoformat(), phone),
        ).rowcount
    if updated == 0:
        raise KeyError("no sender record for that number")


def clear_token(phone: str) -> None:
    """Forget the sender's GitHub credential. The record itself stays: it is their identity."""
    with _connect() as conn:
        conn.execute(
            "UPDATE senders SET github_token = NULL, github_login = NULL, token_added_at = NULL "
            "WHERE phone = ?",
            (phone,),
        )


def adopt_telegram_contact(
    telegram_user_id: int, phone_number: str
) -> tuple[SenderProfile, str | None]:
    """Map a Telegram user id onto its number, merging any provisional record into it.

    Returns the phone-keyed profile and the provisional key that was merged, or None when
    there was nothing to merge. The token and verified_at move across only where the phone
    record has none of its own, so a number that was already onboarded by SMS wins. The
    caller migrates the provisional memory page; this function owns the store alone.
    """
    phone = e164(phone_number)
    provisional = provisional_key(telegram_user_id)
    old = lookup_sender(provisional) if provisional != phone else None
    upsert_sender(phone)
    with _connect() as conn:
        if old is not None:
            conn.execute(
                "UPDATE senders SET github_token = COALESCE(github_token, ?), "
                "github_login = COALESCE(github_login, ?), "
                "token_added_at = COALESCE(token_added_at, ?), "
                "verified_at = COALESCE(verified_at, ?) WHERE phone = ?",
                (
                    old.github_token,
                    old.github_login,
                    old.token_added_at.isoformat() if old.token_added_at else None,
                    old.verified_at.isoformat() if old.verified_at else None,
                    phone,
                ),
            )
            conn.execute("DELETE FROM senders WHERE phone = ?", (provisional,))
        conn.execute(
            "UPDATE senders SET telegram_user_id = NULL WHERE telegram_user_id = ? AND phone != ?",
            (str(telegram_user_id), phone),
        )
        conn.execute(
            "UPDATE senders SET telegram_user_id = ? WHERE phone = ?",
            (str(telegram_user_id), phone),
        )
    profile = lookup_sender(phone)
    assert profile is not None
    return profile, (provisional if old is not None else None)
