"""The projector view: one redacted line per event, printed as it happens (ADR-008)."""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime

from pydantic_ai import models

from ambient_ai.telemetry import Event, format_line, log, stream

E164 = re.compile(r"\+[1-9][0-9]{7,14}")


def test_stream_line_for_webhook_received_carries_last_four_digits_only():
    event = Event(
        kind="webhook.received",
        fields={"sender": "+15551234567", "to": "+15550000000", "chars": 42},
        at=datetime(2026, 9, 12, 21, 5, 9, tzinfo=UTC),
    )
    line = format_line(event)
    assert line.startswith("21:05:09  webhook.received  ")
    assert "sender=***4567" in line
    assert "15551234567" not in line and "5551234567" not in line
    assert E164.search(line) is None


def test_stream_line_never_carries_a_token_a_link_or_a_long_body():
    event = Event(
        kind="identity.connect",
        fields={
            "sender": "***4567",
            "github_token": "ghp_secretvalue",
            "link": "http://portal.test/portal/eyJhbGciOi.magic",
            "body": "x" * 200,
        },
    )
    line = format_line(event)
    assert "ghp_" not in line and "eyJ" not in line and "portal/" not in line
    assert "x" * 61 not in line and "x" * 60 in line


def test_stream_prints_each_event_as_it_is_emitted():
    out = io.StringIO()
    stop = stream(out)
    try:
        log.emit("webhook.mention", sender="***4567", to="***0000", chars=12)
        log.emit("memory.extract", sender="***4567", status="ok", should_update=True)
    finally:
        stop()
    lines = out.getvalue().splitlines()
    assert len(lines) == 2
    assert re.match(
        r"^\d{2}:\d{2}:\d{2}  webhook\.mention  sender=\*\*\*4567 to=\*\*\*0000 chars=12$",
        lines[0],
    )
    assert lines[1].endswith("memory.extract  sender=***4567 status=ok should_update=True")
    log.emit("after.stop", sender="***4567")
    assert len(out.getvalue().splitlines()) == 2


def test_no_test_may_reach_a_model_provider():
    assert models.ALLOW_MODEL_REQUESTS is False
