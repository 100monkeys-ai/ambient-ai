"""In-process telemetry: the event log and the redacted line stream the projector tails.

Sender data never enters an event in full. Callers pass phone numbers through
redact() before emitting; format_line() redacts again so a renderer cannot regress it.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TextIO

E164 = re.compile(r"\+?[1-9][0-9]{7,14}")
"""Anything that looks like a full phone number; masked wherever it appears in a line."""

PHONE_KEYS = ("sender", "to", "from", "phone")
"""Field names whose value is a phone number and always goes through redact()."""

SECRET_KEY_PARTS = ("token", "link", "secret", "key", "sid", "otp")
"""A field whose name contains one of these never prints its value."""

BODY_MAX_CHARS = 60
"""Longest string value a line may carry; message bodies are cut here."""


@dataclass(frozen=True)
class Event:
    kind: str
    fields: dict[str, Any] = field(default_factory=dict)
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


Subscriber = Callable[[Event], None]


class EventLog:
    """Append-only, in-memory, with subscribers notified on every emit. Reset with clear()."""

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._subscribers: list[Subscriber] = []

    def emit(self, kind: str, **fields: Any) -> Event:
        event = Event(kind=kind, fields=fields)
        self._events.append(event)
        for subscriber in list(self._subscribers):
            try:
                subscriber(event)
            except Exception:  # noqa: BLE001 - a renderer must never break the emitter
                pass
        return event

    def subscribe(self, subscriber: Subscriber) -> Callable[[], None]:
        """Register a subscriber; returns the function that removes it."""
        self._subscribers.append(subscriber)

        def stop() -> None:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

        return stop

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


def _render_value(key: str, value: Any) -> str:
    lowered = key.lower()
    if any(part in lowered for part in SECRET_KEY_PARTS):
        return "[redacted]"
    if lowered in PHONE_KEYS:
        return redact(str(value))
    text = str(value)
    if len(text) > BODY_MAX_CHARS:
        text = text[:BODY_MAX_CHARS] + "…"
    text = " ".join(text.split())
    return E164.sub(lambda m: redact(m.group(0)), text)


def format_line(event: Event) -> str:
    """`HH:MM:SS  <kind>  k=v k=v`, with every value redacted where the line is built."""
    stamp = event.at.astimezone(UTC).strftime("%H:%M:%S")
    pairs = " ".join(f"{k}={_render_value(k, v)}" for k, v in event.fields.items())
    return f"{stamp}  {event.kind}  {pairs}".rstrip()


def stream(out: TextIO | None = None, *, path: str | None = None) -> Callable[[], None]:
    """Print every event as one line to `out` (default stdout) and to `path` when given.

    Returns the function that stops the stream. Under uvicorn, stdout is the projector view.
    """
    target = out or sys.stdout

    def render(event: Event) -> None:
        line = format_line(event)
        print(line, file=target, flush=True)
        if path:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    return log.subscribe(render)
