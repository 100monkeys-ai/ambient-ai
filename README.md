# Ambient AI

An AI agent that lives as a phone contact: add its number to a native SMS/MMS group chat and it stays silent until someone writes `@agent`, then acts with that sender's own memory and tools, isolated per phone number.

Pre-alpha skeleton, laid out by bounded context under `src/ambient_ai/`. Everything that talks to Twilio, an LLM, Cortex, or GitHub raises `NotImplementedError` until it is built.

Run the tests:

    python3 -m venv .venv && .venv/bin/python -m pip install -e ".[dev]"
    .venv/bin/ruff check . && .venv/bin/pytest

Architecture, decisions, and setup live in the Ambient AI workspace: https://ai-tinkerers.cortex.page/ambient-ai/ (start at `home`; commands in `operations/developer-setup`). Session protocol: `CLAUDE.md`.
