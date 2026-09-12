"""In-process telemetry: the event log the live dashboard will read.

Sender data never enters an event in full. Callers pass phone numbers through
redact() before emitting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class Event:
    kind: str
    fields: dict[str, Any] = field(default_factory=dict)
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


class EventLog:
    """Append-only, in-memory. Reset between tests with clear()."""

    def __init__(self) -> None:
        self._events: list[Event] = []

    def emit(self, kind: str, **fields: Any) -> Event:
        event = Event(kind=kind, fields=fields)
        self._events.append(event)
        return event

    @property
    def events(self) -> list[Event]:
        return list(self._events)

    def clear(self) -> None:
        self._events.clear()


log = EventLog()
"""The process-wide event log."""


def redact(phone: str) -> str:
    """Mask a phone number to its last four digits, e.g. '+15551234567' -> '***4567'."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 4:
        return "***"
    return "***" + digits[-4:]
