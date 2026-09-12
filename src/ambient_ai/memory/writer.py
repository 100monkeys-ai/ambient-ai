"""Write extracted facts to the sender's Cortex memory page (ADR-004, ADR-005).

Same page the Context Agent reads: `memory_path(phone)` inside the workspace named by
CORTEX_WORKSPACE, over the same MCPToolset construction. The page is created on first write
and appended to after that, one dated bullet line per fact. Nothing is written when
`should_update` is false. With CORTEX_MCP_URL unset the path still runs and writes nothing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, date, datetime
from typing import Any

from ambient_ai.identity.senders import SenderProfile
from ambient_ai.memory.extractor import MemoryExtraction
from ambient_ai.orchestration.context_agent import NOT_FOUND_MARKERS, memory_path
from ambient_ai.settings import OUTBOUND_TIMEOUT_SECONDS, env
from ambient_ai.telemetry import log, redact

ToolsetFactory = Callable[[], AbstractAsyncContextManager[Any]]

PAGE_TITLE = "Sender memory"
PAGE_HEADING = "Memory"
READ_TOOLS = ("pages_read", "pages.read")
CREATE_TOOLS = ("pages_create", "pages.create")
APPEND_TOOLS = ("pages_append_to_section", "pages.append_to_section")


def cortex_toolset() -> AbstractAsyncContextManager[Any]:
    """The MCPToolset the Context Agent uses, built the same way, with the same timeouts."""
    from pydantic_ai.mcp import MCPToolset

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


def memory_lines(extraction: MemoryExtraction, today: date) -> list[str]:
    """One dated bullet per fact, then per preference. Preferences are labelled as such."""
    stamp = today.isoformat()
    lines = [f"- {stamp}: {fact.strip()}" for fact in extraction.novel_facts if fact.strip()]
    lines += [
        f"- {stamp}: prefers {pref.strip()}" for pref in extraction.preferences if pref.strip()
    ]
    return lines


def _pick(available: set[str], candidates: tuple[str, ...], what: str) -> str:
    name = next((n for n in candidates if n in available), None)
    if name is None:
        raise RuntimeError(f"Cortex MCP server exposes no {what} tool")
    return name


async def _call(toolset: Any, name: str, args: dict[str, Any]) -> Any:
    async with asyncio.timeout(OUTBOUND_TIMEOUT_SECONDS):
        return await toolset.direct_call_tool(name, args)


async def remember(
    sender: SenderProfile,
    extraction: MemoryExtraction,
    *,
    toolset_factory: ToolsetFactory | None = None,
    today: date | None = None,
) -> bool:
    """Append the extraction to the sender's memory page. Returns True when a write happened.

    `toolset_factory` is the test seam: anything with the MCPToolset context-manager surface
    (`list_tools`, `direct_call_tool`). Raises on a Cortex failure; the caller decides.
    """
    who = redact(sender.phone)
    lines = memory_lines(extraction, today or datetime.now(UTC).date())
    if not extraction.should_update or not lines:
        log.emit("memory.skipped", sender=who, should_update=extraction.should_update)
        return False
    if not env("CORTEX_MCP_URL"):
        log.emit("memory.unavailable", sender=who, reason="CORTEX_MCP_URL unset")
        return False

    path = memory_path(sender.phone)
    scope: dict[str, Any] = {}
    workspace = env("CORTEX_WORKSPACE")
    if workspace:
        scope["workspace"] = workspace
    content = "\n".join(lines)

    async with (toolset_factory or cortex_toolset)() as toolset:
        available = {t.name for t in await toolset.list_tools()}
        read_tool = _pick(available, READ_TOOLS, "pages read")
        exists = True
        try:
            await _call(toolset, read_tool, {"pathOrId": path, **scope})
        except Exception as exc:
            if not any(marker in str(exc).lower() for marker in NOT_FOUND_MARKERS):
                raise
            exists = False
        if exists:
            append_tool = _pick(available, APPEND_TOOLS, "pages append")
            await _call(
                toolset,
                append_tool,
                {"pathOrId": path, "heading_text": PAGE_HEADING, "content": content, **scope},
            )
        else:
            create_tool = _pick(available, CREATE_TOOLS, "pages create")
            await _call(
                toolset,
                create_tool,
                {
                    "path": path,
                    "title": PAGE_TITLE,
                    "body_md": f"# {PAGE_HEADING}\n\n{content}\n",
                    **scope,
                },
            )
    log.emit("memory.written", sender=who, facts=len(lines), created=not exists)
    return True
