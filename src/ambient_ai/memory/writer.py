"""Write extracted facts to the sender's Cortex memory page (ADR-004, ADR-005).

Same page the Context Agent reads: `memory_path(phone)` inside the workspace named by
CORTEX_WORKSPACE, over the same MCPToolset construction. The page is created on first write
and appended to after that, one dated bullet line per fact. Nothing is written when
`should_update` is false. With CORTEX_MCP_URL unset the path still runs and writes nothing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from ambient_ai.identity.senders import SenderProfile, is_provisional
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
    facts_from_body,
    memory_path,
    read_memory_page,
    require_tool,
)
from ambient_ai.settings import env
from ambient_ai.telemetry import log, redact

PAGE_TITLE = "Sender memory"
PAGE_HEADING = "Memory"
PAGE_SOFT_DELETE_TOOL = "pages.soft_delete"
"""Dotted like the rest; see the note on PAGE_READ_TOOL. Used only when a provisional page
is folded into a phone-keyed one, so the old page stops answering reads but stays restorable
from the audit log."""


def memory_title(sender: str) -> str:
    """`Sender memory +1 503 741 9825`: the page is found by eye, in a private workspace.

    A provisional `tg:` sender has no number to show, so their page keeps the bare title
    until they share their contact and the page is merged.
    """
    if is_provisional(sender):
        return PAGE_TITLE
    digits = "".join(ch for ch in sender if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        return f"{PAGE_TITLE} +1 {digits[1:4]} {digits[4:7]} {digits[7:]}"
    return f"{PAGE_TITLE} +{digits}"


async def _append_or_create(
    toolset: Any, path: str, title: str, content: str
) -> bool:
    """Add `content` under the memory heading, creating the page when it is absent.

    Returns True when the page had to be created. One code path so the writer and the
    provisional-page merge cannot drift into two different page shapes.
    """
    available = {t.name for t in await toolset.list_tools()}
    scope: dict[str, Any] = cortex_scope()
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
            {"path": path, "title": title, "body_md": f"# {PAGE_HEADING}\n\n{content}\n", **scope},
        )
    return not exists


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
    content = "\n".join(lines)

    async with (toolset_factory or cortex_toolset)() as toolset:
        created = await _append_or_create(toolset, path, memory_title(sender.phone), content)
    log.emit("memory.written", sender=who, facts=len(lines), created=created)
    return True


async def migrate_memory_page(
    old_sender: str, new_sender: str, *, toolset_factory: ToolsetFactory | None = None
) -> bool:
    """Fold a provisional sender's memory page into their phone-keyed one, then retire it.

    The lines are appended rather than the page moved, because the phone-keyed page may
    already exist from SMS. Returns False and writes nothing when the old page is absent,
    which is the common case: most senders share their number before they say anything
    worth remembering.
    """
    if not env("CORTEX_MCP_URL"):
        log.emit("memory.unavailable", sender=redact(new_sender), reason="CORTEX_MCP_URL unset")
        return False
    old_path, new_path = memory_path(old_sender), memory_path(new_sender)
    if old_path == new_path:
        return False
    body = await read_memory_page(old_path, toolset_factory=toolset_factory)
    facts = facts_from_body(body) if body else []
    if not facts:
        log.emit("memory.nothing_to_migrate", sender=redact(new_sender))
        return False
    content = "\n".join(f"- {fact}" for fact in facts)
    async with (toolset_factory or cortex_toolset)() as toolset:
        await _append_or_create(toolset, new_path, memory_title(new_sender), content)
        require_tool({t.name for t in await toolset.list_tools()}, PAGE_SOFT_DELETE_TOOL)
        await call_cortex(
            toolset, PAGE_SOFT_DELETE_TOOL, {"pathOrId": old_path, **cortex_scope()}
        )
    log.emit("memory.migrated", sender=redact(new_sender), facts=len(facts))
    return True
