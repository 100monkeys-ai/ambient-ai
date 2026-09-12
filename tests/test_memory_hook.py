"""The gateway hook: extraction is scheduled after the reply is sent and never touches it."""

from __future__ import annotations

import threading

from fastapi.testclient import TestClient

from ambient_ai.gateway import create_app
from ambient_ai.identity import set_token, upsert_sender
from ambient_ai.telemetry import log

PHONE = "+15551234567"
AGENT = "+15550000000"
BODY = "@agent my backend repo is sms-swarm-core"
REPLY = "Noted: sms-swarm-core is your backend repo."


def post_sms(client: TestClient, body: str, sender: str = PHONE):
    return client.post("/webhook/sms", data={"Body": body, "From": sender, "To": AGENT})


def known_sender_with_token() -> None:
    upsert_sender(PHONE)
    set_token(PHONE, "ghp_testtoken")


def test_extraction_is_scheduled_after_the_reply_with_the_full_transcript(outbox, monkeypatch):
    known_sender_with_token()
    order: list[str] = []
    scheduled: list[tuple[str, str]] = []

    async def fake_run_mention(profile, body, **_):
        return REPLY

    real_send = outbox

    def recording_send(to, body):
        order.append("reply")
        real_send.append((to, body))
        return "SMfake"

    def fake_schedule(profile, transcript):
        order.append("extract")
        scheduled.append((profile.phone, transcript))
        return threading.Thread()

    monkeypatch.setattr("ambient_ai.gateway.handlers.run_mention", fake_run_mention)
    monkeypatch.setattr("ambient_ai.gateway.handlers.send_sms", recording_send)
    monkeypatch.setattr("ambient_ai.gateway.handlers.schedule_extraction", fake_schedule)
    response = post_sms(TestClient(create_app()), BODY)
    assert response.status_code == 200
    assert order == ["reply", "extract"]
    assert outbox == [(PHONE, REPLY)]
    phone, transcript = scheduled[0]
    assert phone == PHONE and BODY in transcript and REPLY in transcript


def test_onboarding_and_connect_branches_do_not_schedule_extraction(outbox, monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(
        "ambient_ai.gateway.handlers.schedule_extraction",
        lambda profile, transcript: called.append(profile.phone),
    )
    client = TestClient(create_app())
    post_sms(client, "@agent hello")  # unknown sender: magic link
    post_sms(client, "@agent hello")  # known, no token: connect link
    assert len(outbox) == 2 and called == []


def test_a_raising_extractor_never_changes_the_reply_or_the_response(outbox, monkeypatch):
    known_sender_with_token()
    threads: list[threading.Thread] = []

    async def fake_run_mention(profile, body, **_):
        return REPLY

    async def exploding_extract(transcript, *, model=None):
        raise RuntimeError("extractor exploded")

    from ambient_ai.memory import background

    real_schedule = background.schedule_extraction

    def tracked_schedule(profile, transcript):
        thread = real_schedule(profile, transcript)
        threads.append(thread)
        return thread

    monkeypatch.setattr("ambient_ai.gateway.handlers.run_mention", fake_run_mention)
    monkeypatch.setattr("ambient_ai.memory.background.extract", exploding_extract)
    monkeypatch.setattr("ambient_ai.gateway.handlers.schedule_extraction", tracked_schedule)
    response = post_sms(TestClient(create_app()), BODY)
    for t in threads:
        t.join(timeout=5)
    assert response.status_code == 200 and "<Response></Response>" in response.text
    assert outbox == [(PHONE, REPLY)]
    assert len(threads) == 1 and not threads[0].is_alive()
    failed = [
        e
        for e in log.events
        if e.kind == "memory.extract" and e.fields.get("status") == "failed"
    ]
    assert failed and failed[0].fields["error"] == "RuntimeError"
    assert not any(e.kind == "handler.failed" for e in log.events)
    assert "5551234567" not in "\n".join(f"{e.kind} {e.fields}" for e in log.events)
