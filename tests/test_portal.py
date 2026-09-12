from fastapi.testclient import TestClient

from ambient_ai.gateway import create_app
from ambient_ai.identity import lookup_sender, upsert_sender
from ambient_ai.identity.magic_link import sign
from ambient_ai.telemetry import log

PHONE = "+15551234567"


def test_opening_a_valid_link_marks_the_sender_verified():
    upsert_sender(PHONE)
    client = TestClient(create_app())
    response = client.get(f"/portal/{sign(PHONE)}")
    assert response.status_code == 200
    assert "<form" in response.text
    assert lookup_sender(PHONE).verified_at is not None


def test_tampered_link_is_404_and_verifies_nobody():
    upsert_sender(PHONE)
    client = TestClient(create_app())
    token = sign(PHONE)
    response = client.get(f"/portal/{token[:-2]}zz")
    assert response.status_code == 404
    assert lookup_sender(PHONE).verified_at is None


def test_posting_a_token_stores_it_on_the_sender():
    upsert_sender(PHONE)
    client = TestClient(create_app())
    token = sign(PHONE)
    response = client.post(f"/portal/{token}", data={"github_token": "ghp_pasted"})
    assert response.status_code == 200
    assert "connected" in response.text.lower()
    assert lookup_sender(PHONE).github_token == "ghp_pasted"
    captured = "\n".join(f"{e.kind} {e.fields}" for e in log.events)
    assert "ghp_" not in captured
    assert "15551234567" not in captured
