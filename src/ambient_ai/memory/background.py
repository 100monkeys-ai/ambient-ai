"""The fire-and-forget step the gateway schedules after a reply is sent (message flow C).

`schedule_extraction` starts a daemon thread and returns at once; the request path never
waits on it and nothing raised inside it reaches the caller. Every outcome is an event.
"""

from __future__ import annotations

import asyncio
import threading

from pydantic_ai.models import Model

from ambient_ai.identity.senders import SenderProfile
from ambient_ai.memory.extractor import extract
from ambient_ai.memory.writer import ToolsetFactory, remember
from ambient_ai.telemetry import log, redact
from ambient_ai.tools.credentials import contains_token


def transcript_of(inbound: str, reply: str) -> str:
    return f"Sender: {inbound}\nAgent: {reply}"


async def extract_and_remember(
    sender: SenderProfile,
    transcript: str,
    *,
    model: Model | str | None = None,
    toolset_factory: ToolsetFactory | None = None,
) -> bool:
    """Run the extractor, then the writer. Returns True when Cortex was written. Never raises."""
    who = redact(sender.phone)
    if contains_token(transcript):
        log.emit("memory.extract", sender=who, status="skipped", reason="credential material")
        return False
    log.emit("memory.extract", sender=who, status="started", chars=len(transcript))
    try:
        extraction = await extract(transcript, model=model)
    except Exception as exc:
        log.emit("memory.extract", sender=who, status="failed", error=type(exc).__name__)
        return False
    log.emit(
        "memory.extract",
        sender=who,
        status="ok",
        should_update=extraction.should_update,
        facts=len(extraction.novel_facts),
        preferences=len(extraction.preferences),
    )
    try:
        return await remember(sender, extraction, toolset_factory=toolset_factory)
    except Exception as exc:
        log.emit("memory.write", sender=who, status="failed", error=type(exc).__name__)
        return False


def schedule_extraction(sender: SenderProfile, transcript: str) -> threading.Thread:
    """Start extract_and_remember in a daemon thread and return immediately."""

    def run() -> None:
        try:
            asyncio.run(extract_and_remember(sender, transcript))
        except Exception as exc:  # noqa: BLE001 - a background step must never surface
            log.emit("memory.failed", sender=redact(sender.phone), error=type(exc).__name__)

    thread = threading.Thread(target=run, name="memory-extractor", daemon=True)
    thread.start()
    return thread
