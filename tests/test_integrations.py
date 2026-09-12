"""The integrations registry: one declaration, read by the store, the portal, and `/tools`.

Every test is offline. A fake integration stands in for the registry so the tests bind to the
mechanism — one card per entry, one credential row per entry — and not to GitHub.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from ambient_ai.gateway import create_app
from ambient_ai.identity import credentials_for, lookup_sender, set_credential, upsert_sender
from ambient_ai.identity.magic_link import sign
from ambient_ai.tools import credentials, registry

PHONE = "+15551234567"
OTHER = "+15559998888"
KEY = "ghp_akeytheserviceaccepts0123456789"


def fake(key: str, name: str, *, accepts: bool = True, login: str = "octocat"):
    async def validate(token: str, transport=None) -> str | None:
        return login if accepts else None

    return registry.Integration(
        key=key,
        name=name,
        description=f"What {name} adds.",
        how_to_get_a_key_url=f"https://example.test/{key}/keys",
        field_label="API key",
        validate=validate,
    )


@pytest.fixture
def two_integrations(monkeypatch):
    entries = (fake("alpha", "Alpha"), fake("beta", "Beta"))
    monkeypatch.setattr(registry, "INTEGRATIONS", entries)
    return entries


@pytest.fixture
def rejecting(monkeypatch):
    entries = (fake("alpha", "Alpha", accepts=False),)
    monkeypatch.setattr(registry, "INTEGRATIONS", entries)
    return entries


# --- the store ---------------------------------------------------------------------------


def test_a_store_written_before_the_credentials_table_keeps_every_token(tmp_path, monkeypatch):
    """The live store keeps its tokens in senders.github_token; migrating must lose none."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE senders (phone TEXT PRIMARY KEY, verified_at TEXT, github_token TEXT, "
        "created_at TEXT NOT NULL, telegram_user_id TEXT, github_login TEXT, token_added_at TEXT)"
    )
    conn.execute(
        "INSERT INTO senders VALUES (?, NULL, 'ghp_first', '2026-09-12T00:00:00+00:00', "
        "NULL, 'alice', '2026-09-12T01:00:00+00:00')",
        (PHONE,),
    )
    conn.execute(
        "INSERT INTO senders VALUES (?, NULL, 'ghp_second', '2026-09-12T00:00:00+00:00', "
        "NULL, 'bob', NULL)",
        (OTHER,),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("DB_PATH", str(path))

    first = lookup_sender(PHONE)
    assert first.github_token == "ghp_first"
    assert first.github_login == "alice"
    assert first.credential("github").token == "ghp_first"
    assert first.token_added_at.isoformat().startswith("2026-09-12T01:00")
    second = lookup_sender(OTHER)
    assert second.github_token == "ghp_second"
    assert second.github_login == "bob"
    assert second.token_added_at is not None


def test_one_row_per_sender_per_integration(two_integrations):
    upsert_sender(PHONE)
    set_credential(PHONE, "alpha", KEY, login="alice")
    set_credential(PHONE, "beta", "beta-key", login="bob")
    set_credential(PHONE, "alpha", "replacement", login="alice2")
    stored = credentials_for(PHONE)
    assert stored["alpha"].token == "replacement"
    assert stored["alpha"].login == "alice2"
    assert stored["beta"].token == "beta-key"


# --- the portal ---------------------------------------------------------------------------


def portal_get(client, phone=PHONE):
    return client.get(f"/portal/{sign(phone)}")


def test_the_portal_renders_one_card_per_registry_entry(two_integrations):
    upsert_sender(PHONE)
    client = TestClient(create_app())
    body = portal_get(client).text
    assert body.count('class="card"') == 2
    assert "Alpha" in body and "Beta" in body
    assert body.count("Not connected") == 2
    assert "https://example.test/alpha/keys" in body
    assert "optional" in body.lower()


def test_connecting_a_key_stores_it_under_the_integration_key(two_integrations):
    upsert_sender(PHONE)
    client = TestClient(create_app())
    token = sign(PHONE)
    response = client.post(f"/portal/{token}/beta", data={"action": "connect", "key": KEY})
    assert response.status_code == 200
    assert "Connected as octocat" in response.text
    assert KEY not in response.text
    stored = credentials_for(PHONE)
    assert stored["beta"].token == KEY and stored["beta"].login == "octocat"
    assert "alpha" not in stored


def test_a_rejected_key_stores_nothing_and_says_so(rejecting):
    upsert_sender(PHONE)
    client = TestClient(create_app())
    response = client.post(f"/portal/{sign(PHONE)}/alpha", data={"action": "connect", "key": KEY})
    assert response.status_code == 200
    assert "rejected by Alpha" in response.text
    assert credentials_for(PHONE) == {}


def test_disconnect_clears_only_that_integration(two_integrations):
    upsert_sender(PHONE)
    set_credential(PHONE, "alpha", KEY, login="alice")
    set_credential(PHONE, "beta", "beta-key", login="bob")
    client = TestClient(create_app())
    response = client.post(f"/portal/{sign(PHONE)}/beta", data={"action": "disconnect"})
    assert response.status_code == 200
    stored = credentials_for(PHONE)
    assert "beta" not in stored and stored["alpha"].token == KEY


def test_an_unknown_integration_is_404(two_integrations):
    upsert_sender(PHONE)
    client = TestClient(create_app())
    assert client.post(f"/portal/{sign(PHONE)}/gitlab", data={"key": KEY}).status_code == 404


# --- the commands --------------------------------------------------------------------------


async def test_tools_lists_every_integration_in_the_registry(two_integrations):
    upsert_sender(PHONE)
    set_credential(PHONE, "alpha", KEY, login="alice")
    text, outcome = await credentials.handle(PHONE, credentials.CredentialIntent(action="list"))
    assert outcome == "listed"
    assert "Alpha: connected as alice" in text
    assert "Beta: not connected" in text
    assert KEY not in text and KEY[-4:] in text


async def test_an_unknown_integration_gets_the_list(two_integrations):
    upsert_sender(PHONE)
    intent = credentials.CredentialIntent(action="add", tool="gitlab", token=KEY)
    text, outcome = await credentials.handle(PHONE, intent)
    assert outcome == "unknown tool"
    assert "Alpha" in text and "Beta" in text
    assert credentials_for(PHONE) == {}


async def test_tools_add_and_remove_name_the_integration(two_integrations):
    upsert_sender(PHONE)
    add = credentials.CredentialIntent(action="add", tool="beta", token=KEY)
    text, outcome = await credentials.handle(PHONE, add)
    assert outcome == "stored" and "Beta connected as octocat" in text
    remove = credentials.CredentialIntent(action="remove", tool="beta")
    text, outcome = await credentials.handle(PHONE, remove)
    assert outcome == "cleared" and "Beta disconnected" in text
    assert credentials_for(PHONE) == {}
