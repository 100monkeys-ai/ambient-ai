"""Memory extraction and the Cortex writer, offline: FunctionModel plays the extractor,
a fake toolset plays the Cortex MCP server. No model requests, no network.
"""

from __future__ import annotations

from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from ambient_ai.memory import MemoryExtraction, extract

models.ALLOW_MODEL_REQUESTS = False

DEMO_TRANSCRIPT = (
    "Sender: @agent my backend repo is sms-swarm-core\n"
    "Agent: Noted, sms-swarm-core is your backend repo."
)


def extractor_model() -> FunctionModel:
    """Emits a fact when the transcript names a repo, nothing durable otherwise."""

    def respond(messages, info: AgentInfo) -> ModelResponse:
        output_tool = info.output_tools[0].name
        prompt = "".join(
            str(part.content)
            for m in messages
            for part in getattr(m, "parts", [])
            if part.part_kind == "user-prompt"
        )
        if "sms-swarm-core" in prompt:
            args = {
                "novel_facts": ["backend repo is sms-swarm-core"],
                "preferences": [],
                "should_update": True,
            }
        else:
            args = {"novel_facts": [], "preferences": [], "should_update": False}
        return ModelResponse(parts=[ToolCallPart(tool_name=output_tool, args=args)])

    return FunctionModel(respond, model_name="fake-extractor")


async def test_extract_hello_is_not_worth_remembering():
    extraction = await extract("Sender: @agent hello\nAgent: Hi there!", model=extractor_model())
    assert isinstance(extraction, MemoryExtraction)
    assert extraction.should_update is False
    assert extraction.novel_facts == []


async def test_extract_repo_fact_is_worth_remembering():
    extraction = await extract(DEMO_TRANSCRIPT, model=extractor_model())
    assert extraction.should_update is True
    assert extraction.novel_facts == ["backend repo is sms-swarm-core"]


def test_extractor_agent_has_no_tools_and_the_three_field_schema():
    from pydantic_ai.models.test import TestModel

    from ambient_ai.memory.extractor import build_extractor

    agent = build_extractor(TestModel())
    assert agent._function_toolset.tools == {}
    assert set(MemoryExtraction.model_fields) == {"novel_facts", "preferences", "should_update"}


# --- writer -------------------------------------------------------------------------------

import re  # noqa: E402
from datetime import date  # noqa: E402

from ambient_ai.identity.senders import SenderProfile  # noqa: E402
from ambient_ai.memory.writer import remember  # noqa: E402
from ambient_ai.orchestration.context_agent import (  # noqa: E402
    PAGE_APPEND_TOOL,
    PAGE_CREATE_TOOL,
    PAGE_READ_TOOL,
    memory_path,
)
from ambient_ai.telemetry import log  # noqa: E402

from .fake_cortex import FakeCortex  # noqa: E402

PHONE = "+15551234567"
DATED = re.compile(r"^- \d{4}-\d{2}-\d{2}: .+$")


def two_facts() -> MemoryExtraction:
    return MemoryExtraction(
        novel_facts=["backend repo is sms-swarm-core", "working on the auth bug"],
        should_update=True,
    )


async def test_remember_creates_the_page_with_one_dated_line_per_fact(monkeypatch):
    monkeypatch.setenv("CORTEX_MCP_URL", "http://cortex.test/mcp")
    monkeypatch.setenv("CORTEX_WORKSPACE", "ws-test")
    cortex = FakeCortex()
    sender = SenderProfile(phone=PHONE)
    written = await remember(
        sender, two_facts(), toolset_factory=cortex.open, today=date(2026, 9, 12)
    )
    assert written is True
    path = memory_path(PHONE)
    assert [c[0] for c in cortex.calls] == [PAGE_READ_TOOL, PAGE_CREATE_TOOL]
    assert cortex.calls[1][1]["workspace"] == "ws-test"
    body = cortex.pages[path]
    lines = [ln for ln in body.splitlines() if ln.startswith("- ")]
    assert lines == [
        "- 2026-09-12: backend repo is sms-swarm-core",
        "- 2026-09-12: working on the auth bug",
    ]
    assert "5551234567" not in body and "5551234567" not in path
    assert any(e.kind == "memory.written" and e.fields["facts"] == 2 for e in log.events)


async def test_remember_appends_when_the_page_exists(monkeypatch):
    monkeypatch.setenv("CORTEX_MCP_URL", "http://cortex.test/mcp")
    path = memory_path(PHONE)
    cortex = FakeCortex({path: "# Memory\n\n- 2026-09-11: likes short replies\n"})
    await remember(SenderProfile(phone=PHONE), two_facts(), toolset_factory=cortex.open)
    assert [c[0] for c in cortex.calls] == [PAGE_READ_TOOL, PAGE_APPEND_TOOL]
    lines = [ln for ln in cortex.pages[path].splitlines() if ln.startswith("- ")]
    assert len(lines) == 3 and all(DATED.match(ln) for ln in lines)
    assert lines[0] == "- 2026-09-11: likes short replies"


async def test_remember_writes_nothing_when_should_update_is_false(monkeypatch):
    monkeypatch.setenv("CORTEX_MCP_URL", "http://cortex.test/mcp")
    cortex = FakeCortex()
    written = await remember(
        SenderProfile(phone=PHONE), MemoryExtraction(), toolset_factory=cortex.open
    )
    assert written is False and cortex.calls == []
    assert any(e.kind == "memory.skipped" for e in log.events)


async def test_remember_without_cortex_emits_unavailable_and_does_nothing(monkeypatch):
    monkeypatch.delenv("CORTEX_MCP_URL", raising=False)
    cortex = FakeCortex()
    written = await remember(SenderProfile(phone=PHONE), two_facts(), toolset_factory=cortex.open)
    assert written is False and cortex.calls == []
    unavailable = [e for e in log.events if e.kind == "memory.unavailable"]
    assert unavailable and "5551234567" not in str(unavailable[0].fields)
