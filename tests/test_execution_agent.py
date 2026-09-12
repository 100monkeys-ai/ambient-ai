"""The Execution Agent's model, its settings, its tool surface, and the GitHub calls.

Offline: no model request, httpx.MockTransport plays GitHub. Two things are pinned here.
The latency bound from ADR-009's trigger 1 — the step is measured, and the constants the
measurement chose are the ones the agent runs on. And the org-repo defect of 2026-09-12
20:16 UTC, where the agent answered that it could not find `100monkeys-ai/ambient-ai`
because the listing only asked for repositories the sender owns.
"""

from __future__ import annotations

import httpx
import pytest
from pydantic_ai import models
from pydantic_ai.models.anthropic import AnthropicModel

from ambient_ai.orchestration.execution_agent import (
    EXECUTION_INSTRUCTIONS,
    build_execution_agent,
)
from ambient_ai.orchestration.llm import EXECUTION_SETTINGS
from ambient_ai.settings import EXECUTION_MODEL_ID
from ambient_ai.tools import github_rest
from ambient_ai.tools.github_rest import GitHubDeps, github_client

models.ALLOW_MODEL_REQUESTS = False

REPO = "100monkeys-ai/ambient-ai"


@pytest.fixture(autouse=True)
def fake_anthropic_key(monkeypatch):
    """The Anthropic provider refuses to construct without a key; no request is ever made."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")


def transport(captured: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        path = request.url.path
        if path == "/user":
            return httpx.Response(200, json={"login": "demo-user"})
        if path == "/user/repos":
            return httpx.Response(
                200,
                json=[
                    {
                        "full_name": REPO,
                        "private": True,
                        "pushed_at": "2026-09-12T20:00:00Z",
                    }
                ],
            )
        if path == f"/repos/{REPO}":
            return httpx.Response(
                200,
                json={
                    "full_name": REPO,
                    "default_branch": "main",
                    "pushed_at": "2026-09-12T20:00:00Z",
                    "private": True,
                },
            )
        if path.endswith("/commits"):
            return httpx.Response(
                200,
                json=[
                    {
                        "sha": "b5e5616aaaabbbb",
                        "commit": {
                            "message": "Switch the LLM provider\n\nbody",
                            "author": {"name": "Demo", "date": "2026-09-12T20:00:00Z"},
                        },
                    }
                ],
            )
        if path.endswith("/pulls"):
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": "Not Found"})

    return httpx.MockTransport(handler)


def deps(captured: list[httpx.Request]) -> GitHubDeps:
    return GitHubDeps(client=github_client("ghp_fake", transport(captured)))


# --- the model and the settings the measurement chose ---------------------------------


def test_the_execution_agent_runs_on_its_own_model_constant():
    """Measured live on 2026-09-12: the step is the slowest of the four model calls."""
    model = build_execution_agent().model
    assert isinstance(model, AnthropicModel)
    assert model.model_name == EXECUTION_MODEL_ID == "claude-sonnet-5"


def test_the_execution_agent_carries_the_measured_settings():
    assert EXECUTION_SETTINGS["max_tokens"] == 1024
    assert EXECUTION_SETTINGS["anthropic_effort"] == "low"
    assert "anthropic_thinking" not in EXECUTION_SETTINGS
    assert "thinking" not in EXECUTION_SETTINGS
    assert build_execution_agent().model_settings == EXECUTION_SETTINGS


def test_the_instructions_bound_the_tool_calls_and_the_reply_length():
    assert "400 characters" in EXECUTION_INSTRUCTIONS
    assert "get_repo" in EXECUTION_INSTRUCTIONS
    assert "never guess a different repository" in EXECUTION_INSTRUCTIONS.lower()


# --- the org-repo defect --------------------------------------------------------------


async def test_list_repos_asks_for_organisation_and_collaborator_repositories():
    captured: list[httpx.Request] = []
    async with deps(captured).client as client:
        repos = await github_rest.list_repos(GitHubDeps(client=client))
    affiliation = captured[0].url.params["affiliation"]
    assert affiliation == "owner,collaborator,organization_member"
    assert captured[0].url.params["per_page"] == "100"
    assert captured[0].url.params["sort"] == "pushed"
    assert repos[0]["name"] == REPO


async def test_get_repo_resolves_a_full_name_the_listing_did_not_surface():
    captured: list[httpx.Request] = []
    async with deps(captured).client as client:
        info = await github_rest.get_repo(GitHubDeps(client=client), REPO)
    assert [r.url.path for r in captured] == [f"/repos/{REPO}"]
    assert info == {
        "repo": REPO,
        "default_branch": "main",
        "pushed_at": "2026-09-12T20:00:00Z",
        "private": True,
    }


async def test_get_repo_reports_a_missing_repository_instead_of_raising():
    captured: list[httpx.Request] = []
    async with deps(captured).client as client:
        info = await github_rest.get_repo(GitHubDeps(client=client), "100monkeys-ai/nope")
    assert "no repository" in info["error"]


async def test_latest_commit_takes_owner_slash_name_without_a_user_lookup():
    captured: list[httpx.Request] = []
    async with deps(captured).client as client:
        commit = await github_rest.latest_commit(GitHubDeps(client=client), REPO)
    assert commit["repo"] == REPO
    assert commit["sha"] == "b5e5616"
    assert not any(r.url.path == "/user" for r in captured)


async def test_open_pull_requests_takes_owner_slash_name_without_a_user_lookup():
    captured: list[httpx.Request] = []
    async with deps(captured).client as client:
        pulls = await github_rest.open_pull_requests(GitHubDeps(client=client), REPO)
    assert pulls == {"repo": REPO, "open": []}
    assert not any(r.url.path == "/user" for r in captured)
