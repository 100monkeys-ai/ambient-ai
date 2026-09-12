from ambient_ai.settings import env
from ambient_ai.telemetry.events import Event, EventLog, format_line, log, redact, stream

__all__ = ["Event", "EventLog", "format_line", "log", "redact", "stream"]

if env("TELEMETRY_STREAM") != "0":
    stream(path=env("TELEMETRY_LOG_FILE"))
    """On by default so `uvicorn` output is the projector view; TELEMETRY_STREAM=0 silences it.
    TELEMETRY_LOG_FILE also appends each line to a file that `python -m ambient_ai.telemetry.tail`
    follows from another terminal."""
