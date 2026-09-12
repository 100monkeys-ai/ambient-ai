"""Sender identity. The phone number is the primary key for everything downstream.

Records live in a local SQLite file (settings.db_path()). Credentials live in their own
table, one row per sender per integration, so a second integration is a row and not a
column. Tokens and numbers are never logged; callers emit telemetry with redact(phone) only.
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
    created_at TEXT NOT NULL,
    telegram_user_id TEXT
)
"""

CREDENTIALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS credentials (
    phone TEXT NOT NULL,
    integration TEXT NOT NULL,
    token TEXT NOT NULL,
    login TEXT,
    added_at TEXT NOT NULL,
    PRIMARY KEY (phone, integration)
)
"""

ADDED_COLUMNS = ("ALTER TABLE senders ADD COLUMN telegram_user_id TEXT",)
TELEGRAM_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS senders_telegram_user_id ON senders (telegram_user_id)"
)
"""The live store predates every column after `created_at`, so each is added on open and a
duplicate is tolerated. Uniqueness is an index and not a column constraint because SQLite
refuses `ADD COLUMN ... UNIQUE` outright: writing it inline would work on a fresh file and
fail on the one the demo is running against. Columns are only ever added; provisional `tg:`
records are merged onto a number by adopt_telegram_contact when a sender shares a contact."""

COLUMNS = "phone, verified_at, created_at, telegram_user_id"

GITHUB = "github"
"""The one integration the store itself names, and only to migrate the columns it replaced."""

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


class Credential(BaseModel):
    """One sender's key for one integration, with the account it resolved to."""

    integration: str
    token: str
    login: str | None = None
    added_at: datetime | None = None


class SenderProfile(BaseModel):
    phone: str
    verified_at: datetime | None = None
    github_token: str | None = None
    created_at: datetime | None = None
    telegram_user_id: str | None = None
    github_login: str | None = None
    token_added_at: datetime | None = None
    credentials: dict[str, Credential] = {}
    """Every connected integration by key. The three `github_*` fields above are the same row
    read through the name the reply path already uses; they are filled from here on load."""

    @property
    def verified(self) -> bool:
        return self.verified_at is not None

    def credential(self, integration: str) -> Credential | None:
        return self.credentials.get(integration)


def _migrate(conn: sqlite3.Connection) -> None:
    """Open any store this product has ever written, and leave it on the current schema.

    The live store carries four senders with GitHub tokens in `senders.github_token`. Those
    rows move into `credentials` before the columns go, so migrating loses no token.
    """
    conn.execute(SCHEMA)
    for statement in ADDED_COLUMNS:
        try:
            conn.execute(statement)
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc):
                raise
    conn.execute(TELEGRAM_INDEX)
    conn.execute(CREDENTIALS_SCHEMA)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(senders)")}
    if "github_token" not in columns:
        return
    login = "github_login" if "github_login" in columns else "NULL"
    added = "token_added_at" if "token_added_at" in columns else "NULL"
    conn.execute(
        "INSERT OR IGNORE INTO credentials (phone, integration, token, login, added_at) "
        f"SELECT phone, '{GITHUB}', github_token, {login}, COALESCE({added}, created_at) "
        "FROM senders WHERE github_token IS NOT NULL AND github_token != ''"
    )
    for column in ("github_token", "github_login", "token_added_at"):
        if column in columns:
            try:
                conn.execute(f"ALTER TABLE senders DROP COLUMN {column}")
            except sqlite3.OperationalError:
                pass  # An older SQLite keeps the column; nothing reads it any more.


@contextmanager
def _connect():
    conn = sqlite3.connect(db_path())
    try:
        _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _credentials(conn: sqlite3.Connection, phone: str) -> dict[str, Credential]:
    rows = conn.execute(
        "SELECT integration, token, login, added_at FROM credentials WHERE phone = ?", (phone,)
    ).fetchall()
    return {
        integration: Credential(
            integration=integration,
            token=token,
            login=login,
            added_at=datetime.fromisoformat(added_at) if added_at else None,
        )
        for integration, token, login, added_at in rows
    }


def _row_to_profile(row: tuple, credentials: dict[str, Credential]) -> SenderProfile:
    phone, verified_at, created_at, telegram_user_id = row
    github = credentials.get(GITHUB)
    return SenderProfile(
        phone=phone,
        verified_at=datetime.fromisoformat(verified_at) if verified_at else None,
        github_token=github.token if github else None,
        created_at=datetime.fromisoformat(created_at),
        telegram_user_id=telegram_user_id,
        github_login=github.login if github else None,
        token_added_at=github.added_at if github else None,
        credentials=credentials,
    )


def lookup_sender(phone: str) -> SenderProfile | None:
    """Return the profile for a known phone number, or None for a stranger."""
    with _connect() as conn:
        row = conn.execute(f"SELECT {COLUMNS} FROM senders WHERE phone = ?", (phone,)).fetchone()
        return _row_to_profile(row, _credentials(conn, row[0])) if row else None


def lookup_by_telegram_id(telegram_user_id: int | str) -> SenderProfile | None:
    """Return the phone-keyed profile a Telegram user id maps to, or None when unmapped."""
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {COLUMNS} FROM senders WHERE telegram_user_id = ?",
            (str(telegram_user_id),),
        ).fetchone()
        return _row_to_profile(row, _credentials(conn, row[0])) if row else None


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


def credentials_for(phone: str) -> dict[str, Credential]:
    """Every integration this sender has connected, by key."""
    with _connect() as conn:
        return _credentials(conn, phone)


def credential_for(phone: str, integration: str) -> Credential | None:
    return credentials_for(phone).get(integration)


def set_credential(phone: str, integration: str, token: str, *, login: str | None = None) -> None:
    """Store one sender's key for one integration, with the account it resolved to.

    Replacing a key is the same write as adding one: there is one credential per sender per
    integration, so a second key overwrites the first rather than accumulating.
    """
    with _connect() as conn:
        if conn.execute("SELECT 1 FROM senders WHERE phone = ?", (phone,)).fetchone() is None:
            raise KeyError("no sender record for that number")
        conn.execute(
            "INSERT INTO credentials (phone, integration, token, login, added_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(phone, integration) DO UPDATE SET "
            "token = excluded.token, login = excluded.login, added_at = excluded.added_at",
            (phone, integration, token, login, datetime.now(UTC).isoformat()),
        )


def clear_credential(phone: str, integration: str) -> bool:
    """Forget one integration's key. The sender record stays: it is their identity."""
    with _connect() as conn:
        deleted = conn.execute(
            "DELETE FROM credentials WHERE phone = ? AND integration = ?", (phone, integration)
        ).rowcount
    return deleted > 0


def set_token(phone: str, github_token: str, *, login: str | None = None) -> None:
    """GitHub through the registry-agnostic store. Kept so the reply path reads one name."""
    set_credential(phone, GITHUB, github_token, login=login)


def clear_token(phone: str) -> None:
    clear_credential(phone, GITHUB)


def adopt_telegram_contact(
    telegram_user_id: int, phone_number: str
) -> tuple[SenderProfile, str | None]:
    """Map a Telegram user id onto its number, merging any provisional record into it.

    Returns the phone-keyed profile and the provisional key that was merged, or None when
    there was nothing to merge. Credentials and verified_at move across only where the phone
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
                "INSERT OR IGNORE INTO credentials (phone, integration, token, login, added_at) "
                "SELECT ?, integration, token, login, added_at FROM credentials WHERE phone = ?",
                (phone, provisional),
            )
            conn.execute("DELETE FROM credentials WHERE phone = ?", (provisional,))
            conn.execute(
                "UPDATE senders SET verified_at = COALESCE(verified_at, ?) WHERE phone = ?",
                (old.verified_at.isoformat() if old.verified_at else None, phone),
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
