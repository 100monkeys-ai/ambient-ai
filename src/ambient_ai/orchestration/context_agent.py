"""Context Agent: reads one sender's memory page from Cortex over MCP.

Memory scope is one page per sender at `senders/<sha256(E.164)[:16]>` inside the workspace
the token grants (CORTEX_WORKSPACE names it explicitly when set, because the per-token
current-workspace pointer is shared). The number itself never leaves this process.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ambient_ai.identity.senders import SenderProfile
from ambient_ai.settings import OUTBOUND_TIMEOUT_SECONDS, env
from ambient_ai.telemetry import log, redact

PAGE_READ_TOOL_NAMES = ("pages_read", "pages.read")
NOT_FOUND_MARKERS = ("not found", "not_found", "404", "does not exist", "no page")


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


async def read_memory_page(path: str) -> str | None:
    """Read one page body over MCP. None when the page is absent; raises on any other failure."""
    from pydantic_ai.mcp import MCPToolset

    url = env("CORTEX_MCP_URL")
    token = env("CORTEX_MCP_TOKEN")
    assert url is not None
    headers = {"Authorization": f"Bearer {token}"} if token else None
    toolset = MCPToolset(
        url,
        headers=headers,
        init_timeout=OUTBOUND_TIMEOUT_SECONDS,
        read_timeout=OUTBOUND_TIMEOUT_SECONDS,
        tool_error_behavior="error",
    )
    args: dict[str, Any] = {"pathOrId": path}
    workspace = env("CORTEX_WORKSPACE")
    if workspace:
        args["workspace"] = workspace
    async with toolset:
        available = {t.name for t in await toolset.list_tools()}
        tool_name = next((n for n in PAGE_READ_TOOL_NAMES if n in available), None)
        if tool_name is None:
            raise RuntimeError("Cortex MCP server exposes no pages read tool")
        try:
            result = await toolset.direct_call_tool(tool_name, args)
        except Exception as exc:
            text = str(exc).lower()
            if any(marker in text for marker in NOT_FOUND_MARKERS):
                return None
            raise
    return _body_from_tool_result(result)


async def recall(sender: SenderProfile) -> list[str]:
    """Return the sender's remembered facts, [] when the page or Cortex is absent.

    Raises on a Cortex failure other than a missing page; run_mention turns that into the
    honest reply. With CORTEX_MCP_URL unset the path still runs and returns [].
    """
    if not env("CORTEX_MCP_URL"):
        log.emit("memory.unavailable", sender=redact(sender.phone), reason="CORTEX_MCP_URL unset")
        return []
    body = await read_memory_page(memory_path(sender.phone))
    if body is None:
        log.emit("memory.empty", sender=redact(sender.phone))
        return []
    return facts_from_body(body)
