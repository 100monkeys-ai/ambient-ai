"""Write extracted facts to the sender's Cortex memory page (ADR-004, ADR-005).

Same page the Context Agent reads: `memory_path(phone)` inside the workspace named by
CORTEX_WORKSPACE, over the same MCPToolset construction. The page is created on first write
and appended to after that, one dated bullet line per fact. Nothing is written when
`should_update` is false. With CORTEX_MCP_URL unset the path still runs and writes nothing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from ambient_ai.identity.senders import SenderProfile
from ambient_ai.memory.extractor import MemoryExtraction
from ambient_ai.orchestration.context_agent import (
    NOT_FOUND_MARKERS,
    PAGE_APPEND_TOOL,
    PAGE_CREATE_TOOL,
    PAGE_READ_TOOL,
    ToolsetFactory,
    call_cortex,
    cortex_scope,
    cortex_toolset,
    memory_path,
    require_tool,
)
from ambient_ai.settings import env
from ambient_ai.telemetry import log, redact

PAGE_TITLE = "Sender memory"
PAGE_HEADING = "Memory"


def memory_lines(extraction: MemoryExtraction, today: date) -> list[str]:
    """One dated bullet per fact, then per preference. Preferences are labelled as such."""
    stamp = today.isoformat()
    lines = [f"- {stamp}: {fact.strip()}" for fact in extraction.novel_facts if fact.strip()]
    lines += [
        f"- {stamp}: prefers {pref.strip()}" for pref in extraction.preferences if pref.strip()
    ]
    return lines


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
    scope: dict[str, Any] = cortex_scope()
    content = "\n".join(lines)

    async with (toolset_factory or cortex_toolset)() as toolset:
        available = {t.name for t in await toolset.list_tools()}
        require_tool(available, PAGE_READ_TOOL)
        exists = True
        try:
            await call_cortex(toolset, PAGE_READ_TOOL, {"pathOrId": path, **scope})
        except Exception as exc:
            if not any(marker in str(exc).lower() for marker in NOT_FOUND_MARKERS):
                raise
            exists = False
        if exists:
            require_tool(available, PAGE_APPEND_TOOL)
            await call_cortex(
                toolset,
                PAGE_APPEND_TOOL,
                {"pathOrId": path, "heading_text": PAGE_HEADING, "content": content, **scope},
            )
        else:
            require_tool(available, PAGE_CREATE_TOOL)
            await call_cortex(
                toolset,
                PAGE_CREATE_TOOL,
                {
                    "path": path,
                    "title": PAGE_TITLE,
                    "body_md": f"# {PAGE_HEADING}\n\n{content}\n",
                    **scope,
                },
            )
    log.emit("memory.written", sender=who, facts=len(lines), created=not exists)
    return True
