# Ambient AI — repository bootstrap

Ambient AI is an AI agent that lives as an ordinary phone contact: added to a native SMS/MMS group chat, it stays silent until someone writes `@agent`, then acts with that sender's own memory and tools, isolated per phone number.

## The canonical workspace

Knowledge lives in the **Ambient AI** workspace on the `ai-tinkerers` cortex instance, UUID `7b24f6d1-0e46-4218-b987-ff4b95d9cb93`. The repository holds code, not knowledge.

Pass `workspace: "7b24f6d1-0e46-4218-b987-ff4b95d9cb93"` on every cortex call, reads and writes alike. The MCP token's current-workspace pointer is shared across sessions; a call that omits the argument can land in another product's workspace and report success.

## Session protocol

1. `cortex.ground` with `{ instance: "ai-tinkerers", workspace: "ambient-ai" }` and read the grounding it returns in full.
2. Read the workspace `home` page for navigation.
3. Read the decision record before implementing it: the record itself, not its row on an index.
4. Read `operations/developer-setup` before touching the toolchain.

How work is done (commits, ADRs, autonomy, tone) is governed by the process library at https://100monkeys-ai.cortex.page/project-management/p/home. Link it; never copy it here.

## Layout

One package per bounded context under `src/ambient_ai/`:

| Package | Owns |
|---|---|
| `gateway/` | FastAPI app factory, `POST /webhook/sms` (empty TwiML `<Response>` at once; `@agent` messages go to a background task), `handle_mention()` in `handlers.py`, `send_sms()` in `sms.py`, and the portal at `/portal/{token}`. |
| `identity/` | SQLite sender store keyed by E.164 number, and the signed magic link that proves a number. |
| `orchestration/` | The tool-less orchestrator that emits a `Plan`, and the ephemeral sub-agent factory (Context, Execution). |
| `memory/` | The asynchronous memory extractor: `novel_facts`, `preferences`, `should_update`. |
| `tools/` | Per-sender tool credentials: the GitHub token the sender pasted in the portal. |
| `telemetry/` | In-process event log for the live dashboard, and `redact()`. |
| `settings.py` | Environment variable names, `LLM_PROVIDER`, `LLM_MODEL_ID`, `MENTION`, `DB_PATH`/`PORTAL_BASE_URL` defaults. |

`tests/` mirrors the packages. `SOURCES/` holds the two source PDFs that define the product.

## Commands

`uv` is not installed on the build machine; the toolchain is a plain venv.

```
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/ruff check .
.venv/bin/pytest
.venv/bin/uvicorn ambient_ai.gateway.app:create_app --factory --reload
```

CI (`.github/workflows/ci.yml`) runs the same install, `ruff check .`, and `pytest` on Python 3.11 for every push and pull request.

## What is real and what is stubbed

Real: the app factory, the `@agent` filter on the webhook and its background hand-off, the SQLite sender store, the signed magic link and portal, `send_sms()` over the Twilio Messages API (10 s timeout; `FAKE_SMS_OUTBOX=<file>` records sends to a file instead), the telemetry event log, `redact()`, the pydantic models (`Plan`, `PlanStep`, `MemoryExtraction`, `SenderProfile`).

Stubbed: everything that talks to an LLM, Cortex, or GitHub; `handle_mention()` answers a connected sender with a fixed acknowledgement until orchestration lands. Every unimplemented function raises `NotImplementedError` with a one-line message. Do not replace a stub with a placeholder that looks like real behaviour; replace it with the real thing or leave it raising.

## Lifecycle rules

- **Pre-alpha, one surface.** No backward-compatibility shims, no deprecated code paths, no migrations for internal formats. Change the code, its callers, and its tests in one commit.
- **Sender data is the one carve-out.** Phone numbers, magic-link state, tool tokens, and extracted memories are never logged in full, never shown on the dashboard, and never committed. Pass phone numbers through `telemetry.redact()` before they enter an event. `.env` and `ambient.db` are git-ignored; `.env.example` carries names only.
- **The LLM is OpenAI** (ruled by the architect 2026-09-12 19:30 UTC). The model id is the single constant `LLM_MODEL_ID` in `settings.py`; the key is `OPENAI_API_KEY`.
- **Stage by explicit path.** Never `git add -A`; never `git stash`.
- **Human-only floor.** Deployments, the live Twilio number, the public webhook host, shared secrets, repository visibility, and releases belong to Jeshua. Stop and report rather than work around them.

## Conventions

- Python 3.11+, ruff for lint and import order, pytest with `asyncio_mode = "auto"`.
- Tests are watched red before they go green: a new test fails against the unimplemented state first.
- Commit subject in the imperative, body naming what is real and what is stubbed.
