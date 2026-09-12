"""POST /webhook/telegram: the second transport. Same seam, same events, transport=telegram."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ambient_ai.gateway import create_app
from ambient_ai.gateway.telegram import CONTACT_KEYBOARD, TelegramRefused
from ambient_ai.gateway.telegram_webhook import set_bot_identity
from ambient_ai.identity import lookup_sender, set_token, upsert_sender
from ambient_ai.identity.magic_link import verify
from ambient_ai.identity.senders import adopt_telegram_contact
from ambient_ai.orchestration.run import (
    GITHUB_NOT_CONNECTED_REPLY,
    GITHUB_UNAVAILABLE_REPLY,
)
from ambient_ai.telemetry import log

USER_ID = 123456789
SENDER = f"tg:{USER_ID}"
PHONE = "+15551234567"
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
    """Replace send_telegram with a fake recording (chat_id, text, reply_markup) triples."""
    calls: list[tuple[int, str, dict | None]] = []

    def fake_send(chat_id: int, text: str, *, reply_markup: dict | None = None) -> int:
        calls.append((chat_id, text, reply_markup))
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


def contact_update(*, phone: str = PHONE, owner_id: int | None = None) -> dict:
    """A shared contact arriving in the user's private chat."""
    return {
        "update_id": 8,
        "message": {
            "message_id": 43,
            "from": {"id": USER_ID, "is_bot": False, "first_name": "Alice"},
            "chat": {"id": USER_ID, "type": "private"},
            "date": 1757700001,
            "contact": {
                "phone_number": phone.lstrip("+"),
                "first_name": "Alice",
                "user_id": USER_ID if owner_id is None else owner_id,
            },
        },
    }


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
    chat_id, text, _ = sends[0]
    assert chat_id == USER_ID
    assert "http://portal.test/portal/" in text
    token = text.split("/portal/", 1)[1].split()[0]
    assert verify(token) == SENDER
    assert lookup_sender(SENDER) is not None


def test_unknown_user_gets_the_contact_keyboard_in_the_private_reply(sends):
    post(TestClient(create_app()), update(f"@{BOT_USERNAME} hello"))
    chat_id, text, markup = sends[0]
    assert chat_id == USER_ID
    assert markup == CONTACT_KEYBOARD
    assert "number" in text.lower()


def test_a_sender_with_a_number_is_never_asked_for_it_again(sends, monkeypatch):
    adopt_telegram_contact(USER_ID, PHONE)
    set_token(PHONE, "ghp_testtoken")

    async def fake_run_mention(profile, body, **_):
        return REPLY

    monkeypatch.setattr("ambient_ai.gateway.handlers.run_mention", fake_run_mention)
    monkeypatch.setattr(
        "ambient_ai.gateway.handlers.schedule_extraction", lambda profile, transcript: None
    )
    post(TestClient(create_app()), update(f"@{BOT_USERNAME} hello"))
    assert sends == [(GROUP_ID, REPLY, None)]


def test_private_link_falls_back_to_the_group_when_telegram_refuses(monkeypatch):
    calls: list[tuple[int, str, dict | None]] = []

    def refusing_send(chat_id: int, text: str, *, reply_markup: dict | None = None) -> int:
        if chat_id == USER_ID:
            raise TelegramRefused("Forbidden: bot can't initiate conversation with a user")
        calls.append((chat_id, text, reply_markup))
        return 1

    monkeypatch.setattr("ambient_ai.gateway.telegram_webhook.send_telegram", refusing_send)
    post(TestClient(create_app()), update(f"@{BOT_USERNAME} hello"))
    assert len(calls) == 1
    chat_id, text, markup = calls[0]
    assert chat_id == GROUP_ID
    assert "/portal/" in text and "privately" in text
    assert markup is None, "a reply keyboard cannot be shown in a group"


def test_contact_shared_for_self_maps_the_id_and_merges_the_provisional_record(sends):
    upsert_sender(SENDER)
    set_token(SENDER, "ghp_testtoken")
    response = post(TestClient(create_app()), contact_update())
    assert response.status_code == 200
    phone_record = lookup_sender(PHONE)
    assert phone_record is not None
    assert phone_record.github_token == "ghp_testtoken"
    assert phone_record.telegram_user_id == str(USER_ID)
    assert lookup_sender(SENDER) is None, "the provisional record is merged away, not left behind"
    assert "identity.number_linked" in [e.kind for e in log.events]


def test_contact_shared_for_someone_else_is_ignored_with_an_event(sends):
    post(TestClient(create_app()), contact_update(phone="+15559998888", owner_id=42))
    assert lookup_sender("+15559998888") is None
    kinds = [e.kind for e in log.events]
    assert "identity.contact_ignored" in kinds


def test_a_mapped_user_mention_resolves_to_the_phone_record_and_its_token(sends, monkeypatch):
    adopt_telegram_contact(USER_ID, PHONE)
    set_token(PHONE, "ghp_phonetoken")
    seen: list[tuple[str, str | None, str]] = []

    async def fake_run_mention(profile, body, **_):
        seen.append((profile.phone, profile.github_token, body))
        return REPLY

    monkeypatch.setattr("ambient_ai.gateway.handlers.run_mention", fake_run_mention)
    monkeypatch.setattr(
        "ambient_ai.gateway.handlers.schedule_extraction", lambda profile, transcript: None
    )
    body = f"@{BOT_USERNAME} check the latest commit on my backend repo"
    post(TestClient(create_app()), update(body))
    assert seen == [(PHONE, "ghp_phonetoken", body)]
    assert sends == [(GROUP_ID, REPLY, None)]


def test_known_user_with_token_gets_the_reply_in_the_group(sends, monkeypatch):
    upsert_sender(SENDER)
    set_token(SENDER, "ghp_testtoken")
    seen: list[tuple[str, str | None, str]] = []
    scheduled: list[str] = []

    async def fake_run_mention(profile, body, **_):
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
    assert sends[0] == (GROUP_ID, REPLY, None)
    assert len(scheduled) == 1 and REPLY in scheduled[0]
    assert sends[1][0] == USER_ID and sends[1][2] == CONTACT_KEYBOARD


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


def test_no_event_carries_the_full_phone_number(sends):
    upsert_sender(SENDER)
    post(TestClient(create_app()), contact_update())
    captured = "\n".join(f"{e.kind} {e.fields}" for e in log.events)
    assert PHONE not in captured and PHONE.lstrip("+") not in captured
    assert "***4567" in captured


def test_a_fallback_reply_schedules_no_extraction(sends, monkeypatch):
    """Telegram shares the hook, so the guard must hold on this transport too."""
    upsert_sender(SENDER)
    set_token(SENDER, "ghp_testtoken")
    scheduled: list[str] = []

    async def fake_run_mention(profile, body, **_):
        return GITHUB_UNAVAILABLE_REPLY

    monkeypatch.setattr("ambient_ai.gateway.handlers.run_mention", fake_run_mention)
    monkeypatch.setattr(
        "ambient_ai.gateway.handlers.schedule_extraction",
        lambda profile, transcript: scheduled.append(transcript),
    )
    post(TestClient(create_app()), update(f"@{BOT_USERNAME} what is my latest commit?"))
    assert sends[0] == (GROUP_ID, GITHUB_UNAVAILABLE_REPLY, None)
    assert scheduled == []


def test_telegram_gets_a_longer_reply_limit_than_sms(sends, monkeypatch):
    """Telegram has no 160-character segment, so a repository summary is not clipped to an
    SMS. The transport chooses the limit; the SMS webhook still passes 480."""
    adopt_telegram_contact(USER_ID, PHONE)
    set_token(PHONE, "ghp_testtoken")
    limits: list[int] = []

    async def fake_run_mention(profile, body, **kwargs):
        limits.append(kwargs["max_reply_chars"])
        return REPLY

    monkeypatch.setattr("ambient_ai.gateway.handlers.run_mention", fake_run_mention)
    monkeypatch.setattr(
        "ambient_ai.gateway.handlers.schedule_extraction", lambda profile, transcript: None
    )
    post(TestClient(create_app()), update(f"@{BOT_USERNAME} summarize ambient-ai"))
    assert limits == [1500]


def test_a_user_with_no_credentials_is_answered_normally_on_telegram(sends, monkeypatch):
    """Keys are optional on every transport: no connect link for a question needing none."""
    upsert_sender(SENDER)
    seen: list[tuple[str, str | None, str]] = []

    async def fake_run_mention(profile, body, **_):
        seen.append((profile.phone, profile.github_token, body))
        return REPLY

    monkeypatch.setattr("ambient_ai.gateway.handlers.run_mention", fake_run_mention)
    monkeypatch.setattr(
        "ambient_ai.gateway.handlers.schedule_extraction", lambda profile, transcript: None
    )
    body = f"@{BOT_USERNAME} what do you remember about me?"
    post(TestClient(create_app()), update(body))
    assert seen == [(SENDER, None, body)]
    assert sends[0] == (GROUP_ID, REPLY, None)
    assert "/portal/" not in sends[0][1]


def test_a_github_question_without_a_token_gets_the_link_privately(sends, monkeypatch):
    """The link is one person's, so a group question sends it to the private chat."""
    upsert_sender(SENDER)

    async def fake_run_mention(profile, body, **_):
        return GITHUB_NOT_CONNECTED_REPLY

    monkeypatch.setattr("ambient_ai.gateway.handlers.run_mention", fake_run_mention)
    post(TestClient(create_app()), update(f"@{BOT_USERNAME} latest commit on my backend repo"))
    assert len(sends) == 1
    chat_id, text, _ = sends[0]
    assert chat_id == USER_ID
    assert "/portal/" in text
