import pytest

from ambient_ai.telemetry import log


@pytest.fixture(autouse=True)
def clear_telemetry():
    log.clear()
    yield
    log.clear()
