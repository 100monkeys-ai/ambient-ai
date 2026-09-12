from fastapi.testclient import TestClient

from ambient_ai.gateway import create_app
from ambient_ai.identity import lookup_sender, set_token, upsert_sender
from ambient_ai.identity.magic_link import verify
from ambient_ai.telemetry import log

PHONE = "+15551234567"
AGENT = "+15550000000"
REPLY = "Your backend repo is sms-swarm-core; latest commit abc1234 fixes the auth bug."


def post_sms(client: TestClient, body: str, sender: str = PHONE):
    return client.post("/webhook/sms", data={"Body": body, "From": sender, "To": AGENT})


def test_message_without_mention_is_dropped_with_one_event():
    client = TestClient(create_app())
    response = post_sms(client, "lunch at noon?")
    assert response.status_code == 200
    assert "<Response></Response>" in response.text
    assert len(log.events) == 1
    assert "555123" not in str(log.events[0].fields)


def test_message_without_mention_sends_nothing(outbox):
    client = TestClient(create_app())
    post_sms(client, "lunch at noon?")
    assert outbox == []


def test_unknown_sender_with_mention_is_texted_a_magic_link(outbox):
    client = TestClient(create_app())
    response = post_sms(client, "@agent hello")
    assert response.status_code == 200
    assert "<Response></Response>" in response.text
    assert len(outbox) == 1
    to, body = outbox[0]
    assert to == PHONE
    assert "http://portal.test/portal/" in body
    token = body.split("/portal/", 1)[1].split()[0]
    assert verify(token) == PHONE
    assert lookup_sender(PHONE) is not None


def test_known_unverified_sender_is_texted_a_connect_link(outbox):
    upsert_sender(PHONE)
    client = TestClient(create_app())
    post_sms(client, "@agent check my repo")
    assert len(outbox) == 1
    to, body = outbox[0]
    assert to == PHONE
    token = body.split("/portal/", 1)[1].split()[0]
    assert verify(token) == PHONE


def test_known_sender_with_token_gets_the_orchestrated_reply(outbox, monkeypatch):
    upsert_sender(PHONE)
    set_token(PHONE, "ghp_testtoken")
    seen: list[tuple[str, str | None, str]] = []

    async def fake_run_mention(profile, body):
        seen.append((profile.phone, profile.github_token, body))
        return REPLY

    monkeypatch.setattr("ambient_ai.gateway.handlers.run_mention", fake_run_mention)
    client = TestClient(create_app())
    body = "@agent look up my notes on the auth bug and check the latest commit on my backend repo"
    post_sms(client, body)
    assert seen == [(PHONE, "ghp_testtoken", body)]
    assert outbox == [(PHONE, REPLY)]


def test_no_event_carries_a_full_phone_number(outbox):
    upsert_sender(PHONE)
    set_token(PHONE, "ghp_testtoken")
    client = TestClient(create_app())
    post_sms(client, "@agent hello")
    post_sms(client, "@agent hello", sender="+15559876543")
    post_sms(client, "no mention here")
    assert len(log.events) >= 3
    captured = "\n".join(f"{e.kind} {e.fields}" for e in log.events)
    assert "15551234567" not in captured
    assert "15559876543" not in captured
    assert "ghp_" not in captured
