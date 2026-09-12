"""A Cortex MCP server in memory, spelling its tool names exactly as the live server does.

The names come from `context_agent`, so the fake and the production caller cannot drift
apart; `test_cortex_tool_names_are_the_servers_dotted_spelling` pins those constants to the
strings measured against the real ai-tinkerers endpoint.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from ambient_ai.memory.writer import PAGE_SOFT_DELETE_TOOL
from ambient_ai.orchestration.context_agent import (
    PAGE_APPEND_TOOL,
    PAGE_CREATE_TOOL,
    PAGE_READ_TOOL,
)

SERVER_TOOL_NAMES = (PAGE_READ_TOOL, PAGE_CREATE_TOOL, PAGE_APPEND_TOOL, PAGE_SOFT_DELETE_TOOL)


class FakeCortex:
    """The MCPToolset surface used on the memory path: list_tools and direct_call_tool."""

    def __init__(
        self,
        pages: dict[str, str] | None = None,
        tool_names: tuple[str, ...] = SERVER_TOOL_NAMES,
    ) -> None:
        self.pages: dict[str, str] = dict(pages or {})
        self.deleted: list[str] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.tool_names = tool_names

    async def list_tools(self):
        class T:
            def __init__(self, name: str) -> None:
                self.name = name

        return [T(n) for n in self.tool_names]

    async def direct_call_tool(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append((name, dict(args)))
        path = args["pathOrId"] if "pathOrId" in args else args["path"]
        if name == PAGE_READ_TOOL:
            if path not in self.pages:
                raise RuntimeError(f"not_found: {path}")
            return {"body_md": self.pages[path]}
        if name == PAGE_CREATE_TOOL:
            self.pages[path] = args["body_md"]
            return {"path": path}
        if name == PAGE_APPEND_TOOL:
            self.pages[path] = self.pages[path].rstrip("\n") + "\n" + args["content"] + "\n"
            return {"path": path}
        if name == PAGE_SOFT_DELETE_TOOL:
            self.pages.pop(path, None)
            self.deleted.append(path)
            return {"path": path}
        raise AssertionError(name)

    @asynccontextmanager
    async def open(self):
        yield self
