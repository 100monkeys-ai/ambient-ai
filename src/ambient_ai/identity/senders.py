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
    created_at TEXT NOT NULL
)
"""


class SenderProfile(BaseModel):
    phone: str
    verified_at: datetime | None = None
    github_token: str | None = None
    created_at: datetime | None = None

    @property
    def verified(self) -> bool:
        return self.verified_at is not None


@contextmanager
def _connect():
    conn = sqlite3.connect(db_path())
    try:
        conn.execute(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _row_to_profile(row: tuple) -> SenderProfile:
    phone, verified_at, github_token, created_at = row
    return SenderProfile(
        phone=phone,
        verified_at=datetime.fromisoformat(verified_at) if verified_at else None,
        github_token=github_token,
        created_at=datetime.fromisoformat(created_at),
    )


def lookup_sender(phone: str) -> SenderProfile | None:
    """Return the profile for a known phone number, or None for a stranger."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT phone, verified_at, github_token, created_at FROM senders WHERE phone = ?",
            (phone,),
        ).fetchone()
    return _row_to_profile(row) if row else None


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


def set_token(phone: str, github_token: str) -> None:
    """Store the sender's own GitHub personal access token on their record."""
    with _connect() as conn:
        updated = conn.execute(
            "UPDATE senders SET github_token = ? WHERE phone = ?", (github_token, phone)
        ).rowcount
    if updated == 0:
        raise KeyError("no sender record for that number")
