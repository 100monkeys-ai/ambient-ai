"""POST /webhook/telegram: the second transport. Same seam, same events, transport=telegram."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ambient_ai.gateway import create_app
from ambient_ai.gateway.telegram import TelegramRefused
from ambient_ai.gateway.telegram_webhook import set_bot_identity
from ambient_ai.identity import lookup_sender, set_token, upsert_sender
from ambient_ai.identity.magic_link import verify
from ambient_ai.telemetry import log

USER_ID = 123456789
SENDER = f"tg:{USER_ID}"
GROUP_ID = -1001234567890
BOT_USERNAME = "ambient_demo_bot"
BOT_ID = 987654321
SECRET = "test-telegram-secret"
REPLY = "Your backend repo is sms-swarm-core; latest commit abc1234 fixes the auth bug."


@pytest.fixture(autouse=True)
def telegram_settings(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)
    set_bot_identity(username=BOT_USERNAME, bot_id=BOT_ID)


@pytest.fixture
def sends(monkeypatch):
    """Replace send_telegram with a fake that records (chat_id, text) pairs."""
    calls: list[tuple[int, str]] = []

    def fake_send(chat_id: int, text: str) -> int:
        calls.append((chat_id, text))
        return 1

    monkeypatch.setattr("ambient_ai.gateway.telegram_webhook.send_telegram", fake_send)
    return calls


def update(text: str, *, chat_type: str = "supergroup", reply_to_bot: bool = False) -> dict:
    chat = {"id": USER_ID, "type": "private"} if chat_type == "private" else {
        "id": GROUP_ID, "type": chat_type, "title": "Demo"
    }
    message = {
        "message_id": 42,
        "from": {"id": USER_ID, "is_bot": False, "first_name": "Alice"},
        "chat": chat,
        "date": 1757700000,
        "text": text,
    }
    if reply_to_bot:
        message["reply_to_message"] = {"message_id": 41, "from": {"id": BOT_ID, "is_bot": True}}
    return {"update_id": 7, "message": message}


def post(client: TestClient, payload: dict, secret: str = SECRET):
    return client.post(
        "/webhook/telegram", json=payload, headers={"X-Telegram-Bot-Api-Secret-Token": secret}
    )


def test_group_message_without_mention_is_ignored(sends):
    response = post(TestClient(create_app()), update("lunch at noon?"))
    assert response.status_code == 200
    assert sends == []
    assert [e.kind for e in log.events] == ["webhook.ignored"]
    assert log.events[0].fields["transport"] == "telegram"


def test_wrong_secret_header_is_refused(sends):
    response = post(TestClient(create_app()), update(f"@{BOT_USERNAME} hi"), secret="nope")
    assert response.status_code == 403
    assert sends == []


def test_unknown_user_mention_gets_one_private_portal_link(sends):
    response = post(TestClient(create_app()), update(f"@{BOT_USERNAME} hello"))
    assert response.status_code == 200
    assert len(sends) == 1
    chat_id, text = sends[0]
    assert chat_id == USER_ID
    assert "http://portal.test/portal/" in text
    token = text.split("/portal/", 1)[1].split()[0]
    assert verify(token) == SENDER
    assert lookup_sender(SENDER) is not None


def test_private_link_falls_back_to_the_group_when_telegram_refuses(monkeypatch):
    calls: list[tuple[int, str]] = []

    def refusing_send(chat_id: int, text: str) -> int:
        if chat_id == USER_ID:
            raise TelegramRefused("Forbidden: bot can't initiate conversation with a user")
        calls.append((chat_id, text))
        return 1

    monkeypatch.setattr("ambient_ai.gateway.telegram_webhook.send_telegram", refusing_send)
    post(TestClient(create_app()), update(f"@{BOT_USERNAME} hello"))
    assert len(calls) == 1
    chat_id, text = calls[0]
    assert chat_id == GROUP_ID
    assert "/portal/" in text and "privately" in text


def test_known_user_with_token_gets_the_reply_in_the_group(sends, monkeypatch):
    upsert_sender(SENDER)
    set_token(SENDER, "ghp_testtoken")
    seen: list[tuple[str, str | None, str]] = []
    scheduled: list[str] = []

    async def fake_run_mention(profile, body):
        seen.append((profile.phone, profile.github_token, body))
        return REPLY

    monkeypatch.setattr("ambient_ai.gateway.handlers.run_mention", fake_run_mention)
    monkeypatch.setattr(
        "ambient_ai.gateway.handlers.schedule_extraction",
        lambda profile, transcript: scheduled.append(transcript),
    )
    body = f"@{BOT_USERNAME} check the latest commit on my backend repo"
    post(TestClient(create_app()), update(body))
    assert seen == [(SENDER, "ghp_testtoken", body)]
    assert sends == [(GROUP_ID, REPLY)]
    assert len(scheduled) == 1 and REPLY in scheduled[0]


def test_reply_to_the_bot_counts_as_a_mention(sends):
    post(TestClient(create_app()), update("and the second one?", reply_to_bot=True))
    assert len(sends) == 1 and sends[0][0] == USER_ID


def test_private_chat_message_counts_as_addressed(sends):
    post(TestClient(create_app()), update("hello there", chat_type="private"))
    assert len(sends) == 1 and sends[0][0] == USER_ID


def test_no_event_carries_the_full_telegram_id(sends):
    post(TestClient(create_app()), update(f"@{BOT_USERNAME} hello"))
    captured = "\n".join(f"{e.kind} {e.fields}" for e in log.events)
    assert str(USER_ID) not in captured and str(GROUP_ID) not in captured
    assert "6789" in captured
