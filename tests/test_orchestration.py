"""Orchestration path, offline: no model requests, no network.

FunctionModel plays every agent; httpx.MockTransport plays GitHub; recall is stubbed.
"""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from ambient_ai.identity.senders import SenderProfile
from ambient_ai.orchestration.execution_agent import build_execution_agent
from ambient_ai.orchestration.orchestrator import Plan, build_orchestrator
from ambient_ai.orchestration.run import GITHUB_UNAVAILABLE_REPLY, run_mention
from ambient_ai.settings import SMS_REPLY_MAX_CHARS
from ambient_ai.telemetry import log

models.ALLOW_MODEL_REQUESTS = False

DEMO_MESSAGE = (
    "@agent, look up my notes on the auth bug and check the latest commit "
    "on my backend repo to see if I fixed it."
)
FAKE_SHA = "abc1234def5678"
FAKE_COMMIT_MESSAGE = "Fix auth bug: rotate the session secret\n\nLonger body."


def github_plan_model() -> FunctionModel:
    """A model that plans a GitHub step, drives latest_commit, then writes the reply."""

    def respond(messages, info: AgentInfo) -> ModelResponse:
        output_tool = info.output_tools[0].name if info.output_tools else None
        tool_names = {t.name for t in info.function_tools}
        if output_tool and "needs_github" in json.dumps(
            info.output_tools[0].parameters_json_schema
        ):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name=output_tool,
                        args={
                            "reasoning": "memory names the repo, GitHub has the commit",
                            "needs_memory": True,
                            "needs_github": True,
                            "github_task": "latest commit on the backend repo",
                            "direct_reply": None,
                        },
                    )
                ]
            )
        if "latest_commit" in tool_names:
            already_called = any(
                part.part_kind == "tool-return"
                for m in messages
                for part in getattr(m, "parts", [])
            )
            if not already_called:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="latest_commit", args={"repo": "sms-swarm-core"}
                        )
                    ]
                )
            returned = [
                part.content
                for m in messages
                for part in getattr(m, "parts", [])
                if part.part_kind == "tool-return"
            ]
            return ModelResponse(parts=[TextPart(f"findings: {json.dumps(returned)}")])
        # synthesis: echo whatever findings were given, so the reply names the commit
        prompt = "".join(
            str(part.content)
            for m in messages
            for part in getattr(m, "parts", [])
            if part.part_kind == "user-prompt"
        )
        return ModelResponse(parts=[TextPart(f"Reply built from: {prompt[:400]}")])

    return FunctionModel(respond, model_name="fake-github-plan")


def github_transport(captured: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        path = request.url.path
        if path == "/user":
            return httpx.Response(200, json={"login": "demo-user"})
        if path.endswith("/commits"):
            return httpx.Response(
                200,
                json=[
                    {
                        "sha": FAKE_SHA,
                        "commit": {
                            "message": FAKE_COMMIT_MESSAGE,
                            "author": {"name": "Demo User", "date": "2026-09-12T18:00:00Z"},
                        },
                    }
                ],
            )
        if path.endswith("/pulls"):
            return httpx.Response(200, json=[])
        if path == "/user/repos":
            return httpx.Response(200, json=[{"full_name": "demo-user/sms-swarm-core"}])
        return httpx.Response(404, json={"message": "Not Found"})

    return httpx.MockTransport(handler)


def failing_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("GitHub is down", request=request)

    return httpx.MockTransport(handler)


async def stub_recall(sender: SenderProfile) -> list[str]:
    return ["backend repo is sms-swarm-core"]


def test_orchestrator_has_zero_tools():
    agent = build_orchestrator(TestModel())
    assert agent._function_toolset.tools == {}
    assert agent.output_type is Plan


def test_execution_agent_has_exactly_the_three_github_tools():
    agent = build_execution_agent(TestModel())
    assert set(agent._function_toolset.tools) == {
        "list_repos",
        "latest_commit",
        "open_pull_requests",
    }


async def test_plan_with_github_drives_execution_and_reply_names_the_fake_commit():
    captured: list[httpx.Request] = []
    sender = SenderProfile(phone="+15551230001", github_token="ghp_sender_a")
    reply = await run_mention(
        sender,
        DEMO_MESSAGE,
        model=github_plan_model(),
        recall_fn=stub_recall,
        transport=github_transport(captured),
    )
    assert FAKE_SHA[:7] in reply
    assert "Fix auth bug" in reply
    assert any(r.url.path.endswith("/commits") for r in captured)
    kinds = [e.kind for e in log.events]
    for step in ("plan", "recall", "execute", "synthesize", "reply"):
        assert any(k == f"orchestration.{step}" for k in kinds), kinds


async def test_sender_a_request_never_carries_sender_b_token():
    a = SenderProfile(phone="+15551230001", github_token="ghp_token_for_A")
    b = SenderProfile(phone="+15551230002", github_token="ghp_token_for_B")
    seen_a: list[httpx.Request] = []
    seen_b: list[httpx.Request] = []
    await run_mention(
        a, DEMO_MESSAGE, model=github_plan_model(), recall_fn=stub_recall,
        transport=github_transport(seen_a),
    )
    await run_mention(
        b, DEMO_MESSAGE, model=github_plan_model(), recall_fn=stub_recall,
        transport=github_transport(seen_b),
    )
    assert seen_a and seen_b
    assert all(r.headers["authorization"] == "Bearer ghp_token_for_A" for r in seen_a)
    assert all(r.headers["authorization"] == "Bearer ghp_token_for_B" for r in seen_b)
    # the token never enters telemetry
    assert "ghp_token_for" not in str([e.fields for e in log.events])
    assert "5551230001" not in str([e.fields for e in log.events])


async def test_failing_github_yields_honest_reply_and_no_exception():
    sender = SenderProfile(phone="+15551230001", github_token="ghp_sender_a")
    reply = await run_mention(
        sender,
        DEMO_MESSAGE,
        model=github_plan_model(),
        recall_fn=stub_recall,
        transport=failing_transport(),
    )
    assert reply == GITHUB_UNAVAILABLE_REPLY
    assert "Traceback" not in reply
    failed = [e for e in log.events if e.kind == "orchestration.execute"]
    assert failed and failed[-1].fields["error"] == "ConnectError"


async def test_reply_is_under_the_sms_limit():
    captured: list[httpx.Request] = []
    sender = SenderProfile(phone="+15551230001", github_token="ghp_sender_a")
    reply = await run_mention(
        sender,
        DEMO_MESSAGE * 20,
        model=github_plan_model(),
        recall_fn=stub_recall,
        transport=github_transport(captured),
    )
    assert len(reply) <= SMS_REPLY_MAX_CHARS
    assert "*" not in reply and "#" not in reply


async def test_recall_without_cortex_returns_empty_and_emits_memory_unavailable(monkeypatch):
    monkeypatch.delenv("CORTEX_MCP_URL", raising=False)
    from ambient_ai.orchestration.context_agent import recall

    facts = await recall(SenderProfile(phone="+15551230001"))
    assert facts == []
    assert any(e.kind == "memory.unavailable" for e in log.events)


@pytest.mark.parametrize("phone", ["+15551230001", "+15551230002"])
def test_memory_path_is_hashed_not_the_number(phone):
    from ambient_ai.orchestration.context_agent import memory_path

    path = memory_path(phone)
    assert path.startswith("senders/")
    assert "5551230" not in path
    assert len(path) == len("senders/") + 16


# --- the Cortex MCP tool names, as the live server spells them -----------------------------


def test_cortex_tool_names_are_the_servers_dotted_spelling():
    """The ai-tinkerers MCP server names its tools with dots; `direct_call_tool` needs that
    exact string. Measured live on 2026-09-12 at 20:04 UTC: 94 tools, all dotted."""
    from ambient_ai.orchestration import context_agent as ca

    assert ca.PAGE_READ_TOOL == "pages.read"
    assert ca.PAGE_CREATE_TOOL == "pages.create"
    assert ca.PAGE_APPEND_TOOL == "pages.append_to_section"


async def test_read_memory_page_calls_pages_read_by_its_dotted_name():
    from ambient_ai.orchestration.context_agent import PAGE_READ_TOOL, read_memory_page

    from .fake_cortex import FakeCortex

    cortex = FakeCortex({"senders/abc": "# Memory\n\n- 2026-09-12: a fact\n"})
    body = await read_memory_page("senders/abc", toolset_factory=cortex.open)
    assert cortex.calls[0][0] == PAGE_READ_TOOL == "pages.read"
    assert "a fact" in body


async def test_recall_reads_the_senders_page_and_strips_the_bullets(monkeypatch):
    monkeypatch.setenv("CORTEX_MCP_URL", "http://cortex.test/mcp")
    monkeypatch.setenv("CORTEX_WORKSPACE", "ws-test")
    from ambient_ai.orchestration.context_agent import memory_path, recall

    from .fake_cortex import FakeCortex

    sender = SenderProfile(phone="+15551230001")
    cortex = FakeCortex({memory_path(sender.phone): "# Memory\n\n- 2026-09-12: a fact\n"})
    assert await recall(sender, toolset_factory=cortex.open) == ["2026-09-12: a fact"]
    assert cortex.calls[0][1]["workspace"] == "ws-test"


async def test_read_memory_page_raises_when_the_server_has_no_pages_read_tool():
    from ambient_ai.orchestration.context_agent import read_memory_page

    from .fake_cortex import FakeCortex

    cortex = FakeCortex(tool_names=("pages_read", "pages_create"))
    with pytest.raises(RuntimeError, match="pages.read"):
        await read_memory_page("senders/abc", toolset_factory=cortex.open)
