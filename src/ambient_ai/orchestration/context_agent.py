"""Context Agent: reads one sender's memory page from Cortex over MCP.

Memory scope is one page per sender at `senders/<sha256(E.164)[:16]>` inside the workspace
the token grants (CORTEX_WORKSPACE names it explicitly when set, because the per-token
current-workspace pointer is shared). The number itself never leaves this process.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from ambient_ai.identity.senders import SenderProfile
from ambient_ai.settings import OUTBOUND_TIMEOUT_SECONDS, env
from ambient_ai.telemetry import log, redact

ToolsetFactory = Callable[[], AbstractAsyncContextManager[Any]]

PAGE_READ_TOOL = "pages.read"
PAGE_CREATE_TOOL = "pages.create"
PAGE_APPEND_TOOL = "pages.append_to_section"
"""The Cortex MCP server spells its tool names with dots and `direct_call_tool` is given the
name verbatim. Measured against the live ai-tinkerers endpoint on 2026-09-12: all 94 tools
are dotted, and nothing on the path sanitises them. Guessing a second spelling would hide a
renamed tool behind a fallback, so the name is asserted, not searched for."""

NOT_FOUND_MARKERS = ("not found", "not_found", "404", "does not exist", "no page")


SESSION_TERMINATION_NOISE = "Session termination failed"
"""The MCP client logs this at WARNING on every close: the Cortex server answers the
streamable-HTTP session DELETE with a 500. Nothing is wrong and nothing is retried, but the
line would print beside every memory event on the projector during the demo. Exactly that
message is dropped; every other warning from the client still reaches the log."""


def _drop_session_termination_noise(record: logging.LogRecord) -> bool:
    return not record.getMessage().startswith(SESSION_TERMINATION_NOISE)


def silence_session_termination_noise() -> None:
    """Install the filter once on the MCP client's logger. Idempotent."""
    logger = logging.getLogger("mcp.client.streamable_http")
    if _drop_session_termination_noise not in logger.filters:
        logger.addFilter(_drop_session_termination_noise)


def cortex_toolset() -> AbstractAsyncContextManager[Any]:
    """The one MCPToolset construction on the memory path; the writer uses it too."""
    from pydantic_ai.mcp import MCPToolset

    silence_session_termination_noise()

    url = env("CORTEX_MCP_URL")
    token = env("CORTEX_MCP_TOKEN")
    assert url is not None
    return MCPToolset(
        url,
        headers={"Authorization": f"Bearer {token}"} if token else None,
        init_timeout=OUTBOUND_TIMEOUT_SECONDS,
        read_timeout=OUTBOUND_TIMEOUT_SECONDS,
        tool_error_behavior="error",
    )


def require_tool(available: set[str], name: str) -> str:
    """Fail loudly when the server does not expose the tool we are about to call by name."""
    if name not in available:
        raise RuntimeError(f"Cortex MCP server exposes no {name} tool")
    return name


def cortex_scope() -> dict[str, Any]:
    """Every call names its workspace: the token is not instance-locked, so a slug resolved
    against the pointer can land in another product's workspace with no error."""
    workspace = env("CORTEX_WORKSPACE")
    return {"workspace": workspace} if workspace else {}


async def call_cortex(toolset: Any, name: str, args: dict[str, Any]) -> Any:
    """One MCP tool call under the outbound timeout."""
    async with asyncio.timeout(OUTBOUND_TIMEOUT_SECONDS):
        return await toolset.direct_call_tool(name, args)


def memory_path(phone: str) -> str:
    """Cortex page path for a sender's memory; never contains the number."""
    digest = hashlib.sha256(phone.encode("utf-8")).hexdigest()
    return f"senders/{digest[:16]}"


def facts_from_body(body: str) -> list[str]:
    """One fact per non-empty, non-heading line; leading list markers are stripped."""
    facts: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for marker in ("- ", "* ", "• "):
            if line.startswith(marker):
                line = line[len(marker) :].strip()
                break
        if line:
            facts.append(line)
    return facts


def _body_from_tool_result(result: Any) -> str:
    """The pages read tool returns a JSON object with body_md; tolerate str or dict."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return result
    if isinstance(result, dict):
        return str(result.get("body_md") or result.get("body") or "")
    return str(result)


async def read_memory_page(
    path: str, *, toolset_factory: ToolsetFactory | None = None
) -> str | None:
    """Read one page body over MCP. None when the page is absent; raises on any other failure."""
    args: dict[str, Any] = {"pathOrId": path, **cortex_scope()}
    async with (toolset_factory or cortex_toolset)() as toolset:
        available = {t.name for t in await toolset.list_tools()}
        require_tool(available, PAGE_READ_TOOL)
        try:
            result = await call_cortex(toolset, PAGE_READ_TOOL, args)
        except Exception as exc:
            text = str(exc).lower()
            if any(marker in text for marker in NOT_FOUND_MARKERS):
                return None
            raise
    return _body_from_tool_result(result)


async def recall(
    sender: SenderProfile, *, toolset_factory: ToolsetFactory | None = None
) -> list[str]:
    """Return the sender's remembered facts, [] when the page or Cortex is absent.

    Raises on a Cortex failure other than a missing page; run_mention turns that into the
    honest reply. With CORTEX_MCP_URL unset the path still runs and returns [].
    """
    if not env("CORTEX_MCP_URL"):
        log.emit("memory.unavailable", sender=redact(sender.phone), reason="CORTEX_MCP_URL unset")
        return []
    body = await read_memory_page(memory_path(sender.phone), toolset_factory=toolset_factory)
    if body is None:
        log.emit("memory.empty", sender=redact(sender.phone))
        return []
    return facts_from_body(body)
