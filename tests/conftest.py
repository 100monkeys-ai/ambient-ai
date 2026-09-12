import pytest

from ambient_ai.telemetry import log


@pytest.fixture(autouse=True)
def clear_telemetry():
    log.clear()
    yield
    log.clear()


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Every test gets its own SQLite file and a test-only signing secret."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("APP_SECRET", "test-secret-not-real")
    monkeypatch.setenv("PORTAL_BASE_URL", "http://portal.test")
    monkeypatch.delenv("TWILIO_PHONE_NUMBER", raising=False)


@pytest.fixture
def outbox(monkeypatch):
    """Replace send_sms with a fake that records (to, body) pairs."""
    calls: list[tuple[str, str]] = []

    def fake_send_sms(to: str, body: str) -> str:
        calls.append((to, body))
        return "SMfake"

    monkeypatch.setattr("ambient_ai.gateway.handlers.send_sms", fake_send_sms)
    return calls
