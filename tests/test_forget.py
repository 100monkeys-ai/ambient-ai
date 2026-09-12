"""Being forgotten: private chats only, one confirmation, and then nothing of the sender left.

Every test is offline. `FunctionModel` plays the orchestrator, `models.ALLOW_MODEL_REQUESTS`
is false, and `FakeCortex` plays the memory workspace, so a test that reaches a provider or
the live workspace fails rather than calls one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from ambient_ai.gateway import create_app
from ambient_ai.gateway.telegram_webhook import set_bot_identity
from ambient_ai.identity import (
    adopt_telegram_contact,
    lookup_sender,
    mark_forget_requested,
    set_token,
    upsert_sender,
)
from ambient_ai.identity.senders import credentials_for, provisional_key
from ambient_ai.orchestration.context_agent import memory_path
from ambient_ai.telemetry import log
from ambient_ai.tools import account

from .fake_cortex import FakeCortex

USER_ID = 123456789
PHONE = "+15551234567"
AGENT = "+15550000000"
GROUP_ID = -1001234567890
SECRET = "test-telegram-secret"
TOKEN = "ghp_realisticlookingtoken0123456789"


@pytest.fixture(autouse=True)
def telegram_settings(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)
    set_bot_identity(username="ambient_demo_bot", bot_id=987654321)


@pytest.fixture
def sends(monkeypatch):
    calls: list[tuple[int, str, dict | None]] = []

    def fake_send(chat_id: int, text: str, *, reply_markup: dict | None = None) -> int:
        calls.append((chat_id, text, reply_markup))
        return 1

    monkeypatch.setattr("ambient_ai.gateway.telegram_webhook.send_telegram", fake_send)
    return calls


@pytest.fixture
def cortex(monkeypatch):
    """A Cortex holding this sender's memory page, wired under the writer's own factory."""
    monkeypatch.setenv("CORTEX_MCP_URL", "http://cortex.test/mcp")
    fake = FakeCortex({memory_path(PHONE): "# Memory\n\n- 2026-09-12: likes short replies\n"})
    monkeypatch.setattr("ambient_ai.memory.writer.cortex_toolset", fake.open)
    return fake


def telegram_update(text: str, *, private: bool = True) -> dict:
    chat = (
        {"id": USER_ID, "type": "private"}
        if private
        else {"id": GROUP_ID, "type": "supergroup", "title": "Demo"}
    )
    return {
        "update_id": 7,
        "message": {
            "message_id": 42,
            "from": {"id": USER_ID, "is_bot": False, "first_name": "Alice"},
            "chat": chat,
            "date": 1757700000,
            "text": text,
        },
    }


def post_telegram(client: TestClient, text: str, **kw):
    return client.post(
        "/webhook/telegram",
        json=telegram_update(text, **kw),
        headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
    )


def post_sms(client: TestClient, body: str):
    return client.post("/webhook/sms", data={"Body": body, "From": PHONE, "To": AGENT})


def known_telegram_sender() -> None:
    """A Telegram sender who has shared their number and connected a tool: the full record."""
    upsert_sender(provisional_key(USER_ID))
    adopt_telegram_contact(USER_ID, PHONE)
    set_token(PHONE, TOKEN, login="octocat")


def all_event_text() -> str:
    return " ".join(f"{e.kind} {e.fields}" for e in log.events)


# --- a group is the wrong room --------------------------------------------------------------


def test_forget_in_a_group_is_refused_and_changes_nothing(sends):
    known_telegram_sender()
    client = TestClient(create_app())
    post_telegram(client, "@agent /forget", private=False)
    profile = lookup_sender(PHONE)
    assert profile is not None
    assert profile.forget_requested_at is None
    assert profile.github_token == TOKEN
    assert sends[0][0] == GROUP_ID
    assert sends[0][1] == account.GROUP_REFUSAL


# --- the confirmation ------------------------------------------------------------------------


def test_the_first_request_asks_for_confirmation_and_deletes_nothing(sends):
    known_telegram_sender()
    client = TestClient(create_app())
    post_telegram(client, "/forget")
    profile = lookup_sender(PHONE)
    assert profile is not None
    assert profile.forget_requested_at is not None
    assert profile.github_token == TOKEN
    assert "/forget confirm" in sends[0][1]


def test_confirming_within_the_window_deletes_memory_credentials_and_the_sender(sends, cortex):
    known_telegram_sender()
    mark_forget_requested(PHONE, datetime.now(UTC) - timedelta(minutes=1))
    client = TestClient(create_app())
    post_telegram(client, "/forget confirm")
    assert lookup_sender(PHONE) is None
    assert credentials_for(PHONE) == {}
    assert memory_path(PHONE) in cortex.deleted
    assert memory_path(provisional_key(USER_ID)) in cortex.deleted
    assert sends[0][1] == account.DONE_TEXT
    assert [e for e in log.events if e.kind == "account.forgotten"]


def test_confirming_after_the_window_refuses_and_deletes_nothing(sends, cortex):
    known_telegram_sender()
    mark_forget_requested(PHONE, datetime.now(UTC) - timedelta(minutes=6))
    client = TestClient(create_app())
    post_telegram(client, "/forget confirm")
    profile = lookup_sender(PHONE)
    assert profile is not None
    assert profile.github_token == TOKEN
    assert profile.forget_requested_at is None
    assert cortex.deleted == []
    assert sends[0][1] == account.EXPIRED_TEXT


def test_confirming_without_ever_having_asked_deletes_nothing(sends, cortex):
    known_telegram_sender()
    client = TestClient(create_app())
    post_telegram(client, "/forget confirm")
    assert lookup_sender(PHONE) is not None
    assert cortex.deleted == []
    assert sends[0][1] == account.EXPIRED_TEXT


# --- afterwards the person is a stranger again ------------------------------------------------


def test_a_forgotten_telegram_sender_is_onboarded_afresh_on_the_next_mention(sends, cortex):
    known_telegram_sender()
    mark_forget_requested(PHONE)
    client = TestClient(create_app())
    post_telegram(client, "/forget confirm")
    post_telegram(client, "@agent hello again")
    assert lookup_sender(PHONE) is None
    onboarding = sends[-1]
    assert "authenticate" in onboarding[1]
    assert onboarding[2] is not None, "the share-number keyboard must come back too"


# --- the plan's own route reaches the same handler --------------------------------------------


def forget_plan_model() -> FunctionModel:
    def respond(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=info.output_tools[0].name,
                    args={
                        "reasoning": "the sender asked to be forgotten",
                        "needs_memory": False,
                        "needs_github": False,
                        "github_task": None,
                        "direct_reply": None,
                        "credentials_action": "none",
                        "credentials_tool": None,
                        "account_action": "forget",
                    },
                )
            ]
        )

    return FunctionModel(respond)


def test_the_natural_language_route_reaches_the_same_handler(outbox, monkeypatch):
    """"forget everything about me" only ever asks for the confirmation the command asks for."""
    monkeypatch.setattr(
        "ambient_ai.orchestration.run.build_orchestrator",
        lambda model=None: __import__(
            "ambient_ai.orchestration.orchestrator", fromlist=["build_orchestrator"]
        ).build_orchestrator(forget_plan_model()),
    )
    upsert_sender(PHONE)
    set_token(PHONE, TOKEN, login="octocat")
    client = TestClient(create_app())
    post_sms(client, "@agent forget everything about me")
    profile = lookup_sender(PHONE)
    assert profile is not None, "the plan route asks first; it never deletes on its own"
    assert profile.forget_requested_at is not None
    assert "/forget confirm" in outbox[0][1]


def test_the_natural_language_route_is_refused_in_a_group(sends, monkeypatch):
    monkeypatch.setattr(
        "ambient_ai.orchestration.run.build_orchestrator",
        lambda model=None: __import__(
            "ambient_ai.orchestration.orchestrator", fromlist=["build_orchestrator"]
        ).build_orchestrator(forget_plan_model()),
    )
    known_telegram_sender()
    client = TestClient(create_app())
    post_telegram(client, "@agent forget everything about me", private=False)
    profile = lookup_sender(PHONE)
    assert profile is not None
    assert profile.forget_requested_at is None
    assert sends[0][1] == account.GROUP_REFUSAL


# --- nothing forgotten is spoken out loud -----------------------------------------------------


def test_no_event_from_a_forget_carries_the_number_or_the_token(sends, cortex):
    known_telegram_sender()
    mark_forget_requested(PHONE)
    client = TestClient(create_app())
    post_telegram(client, "/forget")
    post_telegram(client, "/forget confirm")
    assert PHONE not in all_event_text()
    assert PHONE[1:] not in all_event_text()
    assert TOKEN not in all_event_text()


def test_both_refusal_sentences_are_the_same_words():
    """The command path and the plan path refuse in one voice; the strings cannot drift."""
    from ambient_ai.orchestration.run import ACCOUNT_PRIVATE_ONLY_REPLY

    assert ACCOUNT_PRIVATE_ONLY_REPLY == account.GROUP_REFUSAL


# --- the store the demo is running against predates the column --------------------------------


def test_a_store_written_before_the_forget_column_existed_still_opens(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE senders (phone TEXT PRIMARY KEY, verified_at TEXT, "
        "created_at TEXT NOT NULL, telegram_user_id TEXT)"
    )
    conn.execute(
        "INSERT INTO senders (phone, created_at) VALUES (?, ?)", (PHONE, "2026-09-11T00:00:00")
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("DB_PATH", str(path))

    profile = lookup_sender(PHONE)
    assert profile is not None
    assert profile.forget_requested_at is None
    mark_forget_requested(PHONE)
    assert lookup_sender(PHONE).forget_requested_at is not None
