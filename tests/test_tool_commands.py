"""Credential management: private conversations only, and the token never leaves the path.

Every test here is offline. `httpx.MockTransport` plays GitHub's `/user`, `FunctionModel`
plays the orchestrator, and `models.ALLOW_MODEL_REQUESTS` is false, so a test that reaches a
model provider fails rather than calls one.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from ambient_ai.gateway import create_app
from ambient_ai.gateway.telegram_webhook import set_bot_identity
from ambient_ai.identity import lookup_sender, set_token, upsert_sender
from ambient_ai.identity.senders import SenderProfile
from ambient_ai.orchestration.run import CREDENTIALS_PRIVATE_ONLY_REPLY, run_mention
from ambient_ai.telemetry import log
from ambient_ai.tools import credentials

USER_ID = 123456789
PHONE = "+15551234567"
AGENT = "+15550000000"
GROUP_ID = -1001234567890
SECRET = "test-telegram-secret"
TOKEN = "ghp_realisticlookingtoken0123456789"
OTHER_TOKEN = "ghp_asecondtoken9876543210abcdefgh"


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


class Deletions(list):
    """The deleteMessage calls made, with `ok` deciding what Telegram answers."""

    ok = True


@pytest.fixture
def deletions(monkeypatch):
    calls = Deletions()

    def fake_delete(chat_id: int, message_id: int) -> bool:
        calls.append((chat_id, message_id))
        return calls.ok

    monkeypatch.setattr("ambient_ai.gateway.telegram_webhook.delete_message", fake_delete)
    return calls


def github_user(monkeypatch, *, status: int = 200, login: str = "octocat") -> list[str]:
    """Point the credential check at a fake GitHub; returns the bearer tokens it saw."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization", ""))
        if status != 200:
            return httpx.Response(status, json={"message": "Bad credentials"})
        return httpx.Response(200, json={"login": login})

    monkeypatch.setattr(credentials, "TRANSPORT", httpx.MockTransport(handler))
    return seen


def telegram_update(text: str, *, private: bool = True, message_id: int = 42) -> dict:
    chat = (
        {"id": USER_ID, "type": "private"}
        if private
        else {"id": GROUP_ID, "type": "supergroup", "title": "Demo"}
    )
    return {
        "update_id": 7,
        "message": {
            "message_id": message_id,
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


def connected_sender(phone: str = PHONE) -> None:
    upsert_sender(phone)
    set_token(phone, TOKEN, login="octocat")


def all_event_text() -> str:
    return " ".join(f"{e.kind} {e.fields}" for e in log.events)


# --- private Telegram chat: each command ---------------------------------------------------


def test_tools_lists_nothing_connected_in_a_private_chat(sends):
    client = TestClient(create_app())
    post_telegram(client, "/tools")
    assert "No tools connected" in sends[0][1]


def test_tools_add_stores_the_token_after_github_accepts_it(sends, deletions, monkeypatch):
    seen = github_user(monkeypatch, login="jeshua")
    upsert_sender(f"tg:{USER_ID}")
    client = TestClient(create_app())
    post_telegram(client, f"/tools add github {TOKEN}")
    assert seen == [f"Bearer {TOKEN}"]
    assert lookup_sender(f"tg:{USER_ID}").github_token == TOKEN
    assert "jeshua" in sends[0][1]
    assert TOKEN not in sends[0][1]


def test_tools_add_stores_nothing_when_github_rejects_the_token(sends, deletions, monkeypatch):
    github_user(monkeypatch, status=401)
    upsert_sender(f"tg:{USER_ID}")
    client = TestClient(create_app())
    post_telegram(client, f"/tools add github {TOKEN}")
    assert lookup_sender(f"tg:{USER_ID}").github_token is None
    assert "rejected" in sends[0][1].lower()


def test_tools_replace_swaps_the_stored_token(sends, deletions, monkeypatch):
    github_user(monkeypatch)
    connected_sender(f"tg:{USER_ID}")
    client = TestClient(create_app())
    post_telegram(client, f"/tools replace github {OTHER_TOKEN}")
    assert lookup_sender(f"tg:{USER_ID}").github_token == OTHER_TOKEN


def test_tools_list_masks_the_stored_token(sends):
    connected_sender(f"tg:{USER_ID}")
    client = TestClient(create_app())
    post_telegram(client, "/tools")
    body = sends[0][1]
    assert TOKEN not in body
    assert TOKEN[-4:] in body
    assert "connected" in body.lower()


def test_tools_remove_clears_the_token(sends):
    connected_sender(f"tg:{USER_ID}")
    client = TestClient(create_app())
    post_telegram(client, "/tools remove github")
    assert lookup_sender(f"tg:{USER_ID}").github_token is None
    assert "disconnected" in sends[0][1].lower()


# --- groups refuse ---------------------------------------------------------------------------


def test_tools_in_a_group_is_refused_and_changes_nothing(sends):
    connected_sender(f"tg:{USER_ID}")
    client = TestClient(create_app())
    post_telegram(client, "@agent /tools remove github", private=False)
    assert lookup_sender(f"tg:{USER_ID}").github_token == TOKEN
    assert sends[0][0] == GROUP_ID
    assert "private chat" in sends[0][1]


def test_a_token_pasted_in_a_group_is_refused_stored_nowhere_and_never_logged(sends, deletions):
    upsert_sender(f"tg:{USER_ID}")
    client = TestClient(create_app())
    post_telegram(client, f"@agent here is my token {TOKEN}", private=False)
    assert lookup_sender(f"tg:{USER_ID}").github_token is None
    assert "private chat" in sends[0][1]
    assert TOKEN not in all_event_text()
    assert deletions == []


# --- the token never reaches a model, the extractor, or an event -----------------------------


def test_a_pasted_token_is_handled_without_any_model_call(sends, deletions, monkeypatch):
    """ALLOW_MODEL_REQUESTS is false, so a model call would raise; the plan must not run."""
    github_user(monkeypatch, login="jeshua")
    connected_sender(f"tg:{USER_ID}")
    client = TestClient(create_app())
    post_telegram(client, f"here you go: {OTHER_TOKEN}")
    assert lookup_sender(f"tg:{USER_ID}").github_token == OTHER_TOKEN
    assert [e for e in log.events if e.kind == "orchestration.plan"] == []
    assert TOKEN not in all_event_text()
    assert OTHER_TOKEN not in all_event_text()


def test_extraction_is_skipped_for_a_credential_message(sends, deletions, monkeypatch):
    github_user(monkeypatch)
    connected_sender(f"tg:{USER_ID}")
    scheduled: list[str] = []
    monkeypatch.setattr(
        "ambient_ai.gateway.handlers.schedule_extraction",
        lambda profile, transcript: scheduled.append(transcript),
    )
    client = TestClient(create_app())
    post_telegram(client, f"/tools add github {OTHER_TOKEN}")
    assert scheduled == []


def test_the_extractor_refuses_a_transcript_carrying_a_token():
    from ambient_ai.memory.background import extract_and_remember

    profile = SenderProfile(phone=PHONE)
    ran = asyncio_run(extract_and_remember(profile, f"Sender: my token is {TOKEN}\nAgent: ok"))
    assert ran is False
    assert TOKEN not in all_event_text()


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


def test_the_body_is_scrubbed_before_it_reaches_the_model():
    """Nothing token-shaped may reach the plan even on a path that is not a credential one."""
    prompts: list[str] = []

    def respond(messages, info: AgentInfo) -> ModelResponse:
        prompts.append(str(messages[-1].parts[-1].content))
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=info.output_tools[0].name,
                    args={
                        "reasoning": "small talk",
                        "needs_memory": False,
                        "needs_github": False,
                        "github_task": None,
                        "direct_reply": "Hi.",
                        "credentials_action": "none",
                        "credentials_tool": None,
                    },
                )
            ]
        )

    profile = SenderProfile(phone=PHONE, github_token=TOKEN)
    reply = asyncio_run(
        run_mention(profile, f"hello {OTHER_TOKEN}", model=FunctionModel(respond))
    )
    assert reply == "Hi."
    assert OTHER_TOKEN not in prompts[0]
    assert "[token]" in prompts[0]


# --- the plan's own routing -------------------------------------------------------------------


def plan_model(action: str) -> FunctionModel:
    def respond(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=info.output_tools[0].name,
                    args={
                        "reasoning": "the sender asked about their tools",
                        "needs_memory": False,
                        "needs_github": False,
                        "github_task": None,
                        "direct_reply": None,
                        "credentials_action": action,
                        "credentials_tool": "github",
                    },
                )
            ]
        )

    return FunctionModel(respond)


def test_a_plan_routed_credential_intent_is_refused_when_the_chat_is_not_private():
    profile = SenderProfile(phone=PHONE, github_token=TOKEN)
    calls: list[tuple[str, str]] = []

    async def credentials_fn(action: str, tool: str) -> str:
        calls.append((action, tool))
        return "never"

    reply = asyncio_run(
        run_mention(
            profile,
            "what tools do I have connected?",
            model=plan_model("list"),
            private=False,
            credentials_fn=credentials_fn,
        )
    )
    assert reply == CREDENTIALS_PRIVATE_ONLY_REPLY
    assert calls == []


def test_a_plan_routed_credential_intent_runs_in_a_private_chat():
    profile = SenderProfile(phone=PHONE, github_token=TOKEN)

    async def credentials_fn(action: str, tool: str) -> str:
        return f"handled {action} {tool}"

    reply = asyncio_run(
        run_mention(
            profile,
            "what tools do I have connected?",
            model=plan_model("list"),
            private=True,
            credentials_fn=credentials_fn,
        )
    )
    assert reply == "handled list github"


# --- Telegram deletes the message that carried the secret -------------------------------------


def test_the_message_carrying_a_token_is_deleted_by_message_id(sends, deletions, monkeypatch):
    github_user(monkeypatch)
    upsert_sender(f"tg:{USER_ID}")
    client = TestClient(create_app())
    post_telegram(client, f"/tools add github {TOKEN}", message_id=99)
    assert deletions == [(USER_ID, 99)]


def test_a_failed_deletion_is_said_out_loud(sends, deletions, monkeypatch):
    github_user(monkeypatch)
    upsert_sender(f"tg:{USER_ID}")
    deletions.ok = False
    client = TestClient(create_app())
    post_telegram(client, f"/tools add github {TOKEN}")
    assert "delete" in sends[0][1].lower()


def test_a_message_without_a_token_deletes_nothing(sends, deletions):
    connected_sender(f"tg:{USER_ID}")
    client = TestClient(create_app())
    post_telegram(client, "/tools")
    assert deletions == []


# --- SMS is one-to-one, so the same commands work there ---------------------------------------


def test_the_same_command_works_over_sms(outbox, monkeypatch):
    github_user(monkeypatch, login="jeshua")
    upsert_sender(PHONE)
    client = TestClient(create_app())
    post_sms(client, f"@agent /tools add github {TOKEN}")
    assert lookup_sender(PHONE).github_token == TOKEN
    to, body = outbox[0]
    assert to == PHONE
    assert TOKEN not in body
    assert "jeshua" in body


def test_no_event_ever_carries_a_token_over_sms(outbox, monkeypatch):
    github_user(monkeypatch)
    upsert_sender(PHONE)
    client = TestClient(create_app())
    post_sms(client, f"@agent {TOKEN}")
    assert TOKEN not in all_event_text()
    assert lookup_sender(PHONE).github_token == TOKEN


# --- the store's new columns -------------------------------------------------------------------


def test_a_store_written_before_the_columns_existed_still_opens(tmp_path, monkeypatch):
    """The live store predates github_login and token_added_at; opening it must migrate it."""
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE senders (phone TEXT PRIMARY KEY, verified_at TEXT, "
        "github_token TEXT, created_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO senders (phone, created_at) VALUES (?, '2026-09-12T00:00:00+00:00')",
        (PHONE,),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("DB_PATH", str(path))
    set_token(PHONE, TOKEN, login="octocat")
    profile = lookup_sender(PHONE)
    assert profile.github_login == "octocat"
    assert profile.token_added_at is not None
