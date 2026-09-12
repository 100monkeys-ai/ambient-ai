"""Ephemeral sub-agents: Context (Cortex MCP only) and Execution (the sender's own tools)."""

from typing import Any, Literal

SubAgentKind = Literal["context", "execution"]


def spawn_sub_agent(kind: SubAgentKind, sender_phone: str, instruction: str) -> Any:
    """Build a short-lived agent mounted only with what its kind allows, then run it."""
    raise NotImplementedError("sub-agent factory is not built yet")
