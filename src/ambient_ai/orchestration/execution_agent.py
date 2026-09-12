"""Execution Agent: built per request, mounted with GitHub REST tools bound to one token.

The agent holds no token. Every tool reads the request-scoped client from ctx.deps, which
run_mention builds from the sender's own token and closes when the request ends.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.models import Model

from ambient_ai.orchestration.llm import llm_model
from ambient_ai.settings import OUTBOUND_TIMEOUT_SECONDS
from ambient_ai.tools import github_rest
from ambient_ai.tools.github_rest import GitHubDeps

EXECUTION_INSTRUCTIONS = """\
You read one person's GitHub on their behalf and report what you find, briefly.
Use the facts you are given to resolve references like "the backend repo" to a repository
name before calling a tool. If no fact names the repository, call list_repos and pick the
best match by name, saying which you picked. Call as few tools as possible.
Report findings as two or three plain sentences: repository, short sha, first line of the
commit message, author, date, and whether it answers the task. Never invent a commit.
"""


def build_execution_agent(model: Model | str | None = None) -> Agent[GitHubDeps, str]:
    agent: Agent[GitHubDeps, str] = Agent(
        llm_model(model),
        deps_type=GitHubDeps,
        output_type=str,
        instructions=EXECUTION_INSTRUCTIONS,
        name="execution",
        tool_timeout=OUTBOUND_TIMEOUT_SECONDS + 1,
        retries=1,
    )

    @agent.tool
    async def list_repos(ctx: RunContext[GitHubDeps]) -> list[dict[str, Any]]:
        """Repositories the sender owns, most recently pushed first."""
        return await github_rest.list_repos(ctx.deps)

    @agent.tool
    async def latest_commit(ctx: RunContext[GitHubDeps], repo: str) -> dict[str, Any]:
        """Latest commit on a repository ('name' or 'owner/name')."""
        return await github_rest.latest_commit(ctx.deps, repo)

    @agent.tool
    async def open_pull_requests(ctx: RunContext[GitHubDeps], repo: str) -> dict[str, Any]:
        """Open pull requests on a repository ('name' or 'owner/name')."""
        return await github_rest.open_pull_requests(ctx.deps, repo)

    return agent
