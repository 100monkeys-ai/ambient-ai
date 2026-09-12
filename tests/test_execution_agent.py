"""The Execution Agent's model, its settings, its tool surface, and the GitHub calls.

Offline: no model request, httpx.MockTransport plays GitHub. Two things are pinned here.
The latency bound from ADR-009's trigger 1 — the step is measured, and the constants the
measurement chose are the ones the agent runs on. And the org-repo defect of 2026-09-12
20:16 UTC, where the agent answered that it could not find `100monkeys-ai/ambient-ai`
because the listing only asked for repositories the sender owns.
"""

from __future__ import annotations

import base64

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
README_TEXT = "# Ambient AI\n\nAn agent that lives as a phone contact.\n"
SETTINGS_TEXT = "MENTION = '@agent'\n" * 4
BIG_TEXT = "x" * 40_000
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
TREE = {
    "sha": "treesha",
    "truncated": False,
    "tree": (
        [{"path": "README.md", "type": "blob", "size": 42}, {"path": "src", "type": "tree"}]
        + [
            {"path": f"src/ambient_ai/mod{i}.py", "type": "blob", "size": 100 + i}
            for i in range(250)
        ]
    ),
}


def contents_json(path: str, body: str | bytes) -> dict:
    raw = body.encode() if isinstance(body, str) else body
    return {
        "name": path.rsplit("/", 1)[-1],
        "path": path,
        "type": "file",
        "size": len(raw),
        "encoding": "base64",
        "content": base64.b64encode(raw).decode(),
    }


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
        if path == f"/repos/{REPO}/git/trees/main":
            return httpx.Response(200, json=TREE)
        if path == f"/repos/{REPO}/readme":
            return httpx.Response(200, json=contents_json("README.md", README_TEXT))
        if path == f"/repos/{REPO}/contents/src/ambient_ai/settings.py":
            return httpx.Response(
                200, json=contents_json("src/ambient_ai/settings.py", SETTINGS_TEXT)
            )
        if path == f"/repos/{REPO}/contents/big.py":
            return httpx.Response(200, json=contents_json("big.py", BIG_TEXT))
        if path == f"/repos/{REPO}/contents/logo.png":
            return httpx.Response(200, json=contents_json("logo.png", PNG_BYTES))
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
    assert EXECUTION_SETTINGS["max_tokens"] == 2048
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


# --- reading a repository's contents (architect's ruling, 2026-09-12 21:05 UTC) ---------
# A tester asked for a summary of what a repository implements and the bot answered that it
# only sees commit metadata. Three read-only tools close that gap.


async def test_list_files_walks_the_default_branch_tree_recursively():
    captured: list[httpx.Request] = []
    async with deps(captured).client as client:
        listing = await github_rest.list_files(GitHubDeps(client=client), REPO)
    tree = [r for r in captured if "/git/trees/" in r.url.path][0]
    assert tree.url.path == f"/repos/{REPO}/git/trees/main"
    assert tree.url.params["recursive"] == "1"
    assert listing["repo"] == REPO
    assert listing["branch"] == "main"
    assert {"path": "README.md", "size": 42} in listing["files"]
    assert all(f["path"] != "src" for f in listing["files"]), "directories are not files"


async def test_list_files_caps_the_listing_at_two_hundred_paths_with_a_note():
    captured: list[httpx.Request] = []
    async with deps(captured).client as client:
        listing = await github_rest.list_files(GitHubDeps(client=client), REPO)
    assert len(listing["files"]) == 200
    assert listing["truncated"] is True
    assert "251" in listing["note"] and "200" in listing["note"]


async def test_list_files_filters_by_path_prefix():
    captured: list[httpx.Request] = []
    async with deps(captured).client as client:
        listing = await github_rest.list_files(
            GitHubDeps(client=client), REPO, path="src/ambient_ai"
        )
    assert listing["files"]
    assert all(f["path"].startswith("src/ambient_ai/") for f in listing["files"])


async def test_read_file_decodes_the_base64_contents():
    captured: list[httpx.Request] = []
    async with deps(captured).client as client:
        got = await github_rest.read_file(
            GitHubDeps(client=client), REPO, "src/ambient_ai/settings.py"
        )
    assert got["repo"] == REPO
    assert got["path"] == "src/ambient_ai/settings.py"
    assert got["content"] == SETTINGS_TEXT
    assert got["truncated"] is False


async def test_read_file_caps_a_large_file_at_twenty_four_kilobytes():
    captured: list[httpx.Request] = []
    async with deps(captured).client as client:
        got = await github_rest.read_file(GitHubDeps(client=client), REPO, "big.py")
    assert len(got["content"]) <= github_rest.FILE_MAX_CHARS
    assert got["truncated"] is True
    assert "truncated" in got["note"].lower()


async def test_read_file_refuses_a_binary_file_instead_of_returning_mojibake():
    captured: list[httpx.Request] = []
    async with deps(captured).client as client:
        got = await github_rest.read_file(GitHubDeps(client=client), REPO, "logo.png")
    assert "content" not in got
    assert "binary" in got["error"].lower()


async def test_read_file_reports_a_missing_path_instead_of_raising():
    captured: list[httpx.Request] = []
    async with deps(captured).client as client:
        got = await github_rest.read_file(GitHubDeps(client=client), REPO, "nope.py")
    assert "no file" in got["error"].lower()


async def test_get_readme_reads_the_readme_endpoint():
    captured: list[httpx.Request] = []
    async with deps(captured).client as client:
        got = await github_rest.get_readme(GitHubDeps(client=client), REPO)
    assert captured[-1].url.path == f"/repos/{REPO}/readme"
    assert got["content"] == README_TEXT
    assert got["path"] == "README.md"


def test_the_instructions_bound_a_content_question_to_eight_tool_calls():
    assert "eight tool calls" in EXECUTION_INSTRUCTIONS
    assert "get_readme" in EXECUTION_INSTRUCTIONS
    assert "three" in EXECUTION_INSTRUCTIONS
