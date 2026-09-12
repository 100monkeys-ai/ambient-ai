from ambient_ai.telemetry import redact


def test_redact_keeps_only_last_four_digits():
    masked = redact("+15551234567")
    assert "4567" in masked
    assert "555123" not in masked
