from fastapi.testclient import TestClient

from ambient_ai.gateway import create_app
from ambient_ai.telemetry import log


def test_message_without_mention_is_dropped_with_one_event():
    client = TestClient(create_app())
    response = client.post(
        "/webhook/sms",
        data={"Body": "lunch at noon?", "From": "+15551234567", "To": "+15550000000"},
    )
    assert response.status_code == 200
    assert "<Response></Response>" in response.text
    assert len(log.events) == 1
    assert "555123" not in str(log.events[0].fields)
