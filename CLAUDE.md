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
| `gateway/` | FastAPI app factory, `POST /webhook/sms` (empty TwiML `<Response>` at once; `@agent` messages go to a background task), `handle_mention()` in `handlers.py` (credential management first, then onboarding, then `run_mention`), `send_sms()` in `sms.py`, and the portal at `/portal/{token}`. |
| `gateway/telegram*.py` | The second transport. `POST /webhook/telegram` in `telegram_webhook.py` (403 on a bad `X-Telegram-Bot-Api-Secret-Token`, 200 at once, mention detection: `@<bot_username>` from `getMe`, `@agent`, a reply to the bot, or any private-chat message), the Bot API client in `telegram.py` (`send_telegram`, `get_me`, `set_webhook`, `FAKE_TELEGRAM_OUTBOX`). `deleteMessage` takes a pasted token off the sender's screen. Sender key is the E.164 number once the sender shares their contact, `tg:<user_id>` until; portal links go to the user privately and fall back to the group when Telegram refuses. Startup sets the webhook only when `TELEGRAM_BOT_TOKEN` is set; the bot needs BotFather privacy mode disabled to see group messages. |
| `identity/` | SQLite sender store keyed by E.164 number, and the signed magic link that proves a number. |
| `orchestration/` | `run_mention()` in `run.py`, the request loop the gateway calls; `orchestrator.py`, the tool-less agent that emits a flat `Plan` (`needs_memory`, `needs_github`, `github_task`, `direct_reply`); `context_agent.py`, `recall()` reading the sender's page `senders/<sha256(E.164)[:16]>` from Cortex over MCP; `execution_agent.py`, the per-request agent with three GitHub REST tools bound to the sender's token through `deps`; `llm.py`, the one place the provider is named. |
| `memory/` | The asynchronous memory extractor: `novel_facts`, `preferences`, `should_update`. |
| `tools/` | The integrations registry (`registry.py`: one frozen entry per connectable service — name, description, where its key is issued, and its validate; the portal, the `/tools` commands, and the sender store all read it, and every integration is optional). Per-sender credentials, one row per sender per integration (`github.py`: the token the sender pasted in the portal; `credentials.py`: managing that token from the conversation — `parse_intent` reads `/tools [add\|replace\|remove] <integration> <key>` or a bare token-shaped string before any model call, `scrub` masks anything token-shaped in every body that goes on to the plan, the extractor, telemetry, or a reply, and a token is validated against `GET /user` before it is stored) and the GitHub REST calls (`github_rest.py`: `list_repos`, `get_repo`, `latest_commit`, `open_pull_requests`, 10 s timeout, compact dicts). |
| `telemetry/` | In-process event log for the live dashboard, and `redact()`. |
| `settings.py` | Environment variable names, `LLM_PROVIDER`, `LLM_MODEL_ID`, `MENTION`, `SMS_REPLY_MAX_CHARS`, `OUTBOUND_TIMEOUT_SECONDS`, `DB_PATH`/`PORTAL_BASE_URL` defaults. `CORTEX_WORKSPACE` is optional: when set it is passed on every Cortex read so the shared token pointer cannot redirect it. |

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

Real: the app factory, the `@agent` filter on the webhook and its background hand-off, the SQLite sender store, the signed magic link and portal, `send_sms()` over the Twilio Messages API (10 s timeout; `FAKE_SMS_OUTBOX=<file>` records sends to a file instead), the whole orchestration path (`run_mention()`: plan, recall, execute, synthesize, reply, one telemetry event per step, and the honest short reply from ADR-009 on any recall, execute, or model failure), the Cortex MCP client in `recall()` (returns `[]` with a `memory.unavailable` event while `CORTEX_MCP_URL` is unset), the GitHub REST tools, the telemetry event log, `redact()`, the pydantic models (`Plan`, `MemoryExtraction`, `SenderProfile`). Also real: the memory extractor (`memory.extract()`, a tool-less agent with `output_type=MemoryExtraction`), the Cortex writer (`memory.remember()`, one dated bullet per fact on the sender's page, `memory.unavailable` while `CORTEX_MCP_URL` is unset), the fire-and-forget `schedule_extraction()` hooked after `send_sms` in `handle_mention`, and the redacted line stream (`telemetry.stream()`, on by default so `uvicorn` output is the projector view; `TELEMETRY_LOG_FILE` plus `python -m ambient_ai.telemetry.tail` follows it from another terminal). Tests never reach a model: `tests/conftest.py` sets `pydantic_ai.models.ALLOW_MODEL_REQUESTS = False`.

Stubbed: nothing on the demo path. Not yet exercised live: a real Cortex MCP server (no credential issued), real GitHub. Every unimplemented function raises `NotImplementedError` with a one-line message. Do not replace a stub with a placeholder that looks like real behaviour; replace it with the real thing or leave it raising.

## Lifecycle rules

- **Pre-alpha, one surface.** No backward-compatibility shims, no deprecated code paths, no migrations for internal formats. Change the code, its callers, and its tests in one commit.
- **Sender data is the one carve-out.** Phone numbers, magic-link state, tool tokens, and extracted memories are never logged in full, never shown on the dashboard, and never committed. **Credential management happens only where the conversation is one-to-one** — a Telegram private chat, or SMS; in a group any credential intent is refused with nothing stored, logged, or extracted, because a room is the wrong place for one person's token. Pass phone numbers through `telemetry.redact()` before they enter an event. `.env` and `ambient.db` are git-ignored; `.env.example` carries names only.
- **The LLM is Anthropic** (ruled by the architect 2026-09-12 20:10 UTC, after the OpenAI account returned `credit_balance_exhausted` and could not be funded before the demo). Three constants in `settings.py`, and nowhere else: `LLM_MODEL_ID = "claude-opus-5"` for the plan and synthesis, `EXECUTION_MODEL_ID = "claude-sonnet-5"` for the GitHub Execution Agent (measured the fastest of three configurations on 2026-09-12), and `EXTRACTOR_MODEL_ID = "claude-haiku-4-5"` for the memory extractor, which is a bulk job off the reply path. The key is `ANTHROPIC_API_KEY`. `orchestration/llm.py` also holds the per-call model settings that keep the reply inside ADR-009's 30 seconds across four sequential calls: a small `max_tokens` on each (plan 1024, execution 1024, synthesis 512, extractor 1024) and `anthropic_effort="low"` on plan, execution, and synthesis. Thinking is never configured, so it stays adaptive; `budget_tokens` is rejected on these models.
- **Stage by explicit path.** Never `git add -A`; never `git stash`.
- **Human-only floor.** Deployments, the live Twilio number, the public webhook host, shared secrets, repository visibility, and releases belong to Jeshua. Stop and report rather than work around them.

## Conventions

- Python 3.11+, ruff for lint and import order, pytest with `asyncio_mode = "auto"`.
- Tests are watched red before they go green: a new test fails against the unimplemented state first.
- Commit subject in the imperative, body naming what is real and what is stubbed.
