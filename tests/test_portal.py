import httpx
import pytest
from fastapi.testclient import TestClient

from ambient_ai.gateway import create_app
from ambient_ai.identity import lookup_sender, upsert_sender
from ambient_ai.identity.magic_link import sign
from ambient_ai.telemetry import log
from ambient_ai.tools import github

PHONE = "+15551234567"
PASTED = "ghp_pastedtokenfromtheportal01234567"


@pytest.fixture(autouse=True)
def github_accepts(monkeypatch):
    """GitHub accepts every token unless a test points the seam somewhere else."""
    monkeypatch.setattr(
        github,
        "TRANSPORT",
        httpx.MockTransport(lambda request: httpx.Response(200, json={"login": "octocat"})),
    )


def test_opening_a_valid_link_marks_the_sender_verified():
    upsert_sender(PHONE)
    client = TestClient(create_app())
    response = client.get(f"/portal/{sign(PHONE)}")
    assert response.status_code == 200
    assert "<form" in response.text
    assert "GitHub" in response.text
    assert lookup_sender(PHONE).verified_at is not None


def test_tampered_link_is_404_and_verifies_nobody():
    upsert_sender(PHONE)
    client = TestClient(create_app())
    token = sign(PHONE)
    response = client.get(f"/portal/{token[:-2]}zz")
    assert response.status_code == 404
    assert lookup_sender(PHONE).verified_at is None


def test_posting_a_token_github_accepts_stores_it_with_the_login():
    upsert_sender(PHONE)
    client = TestClient(create_app())
    token = sign(PHONE)
    response = client.post(f"/portal/{token}/github", data={"key": PASTED})
    assert response.status_code == 200
    assert "connected" in response.text.lower()
    profile = lookup_sender(PHONE)
    assert profile.github_token == PASTED
    assert profile.github_login == "octocat"
    assert PASTED not in response.text
    captured = "\n".join(f"{e.kind} {e.fields}" for e in log.events)
    assert "ghp_" not in captured
    assert "15551234567" not in captured


def test_posting_a_token_github_rejects_stores_nothing_and_says_so(monkeypatch):
    """A token stored unchecked only shows up as a failed answer, long after anyone suspects it."""
    monkeypatch.setattr(
        github,
        "TRANSPORT",
        httpx.MockTransport(lambda request: httpx.Response(401, json={"message": "Bad"})),
    )
    upsert_sender(PHONE)
    client = TestClient(create_app())
    response = client.post(f"/portal/{sign(PHONE)}/github", data={"key": PASTED})
    assert response.status_code == 200
    assert "rejected by GitHub" in response.text
    assert lookup_sender(PHONE).github_token is None
    captured = "\n".join(f"{e.kind} {e.fields}" for e in log.events)
    assert "ghp_" not in captured
