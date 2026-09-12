"""Execution Agent: built per request, mounted with GitHub REST tools bound to one token.

The agent holds no token. Every tool reads the request-scoped client from ctx.deps, which
run_mention builds from the sender's own token and closes when the request ends.

This is the slowest of the four model calls on the reply path — it runs a tool loop — so it
carries its own model constant and its own settings, both chosen by live measurement. See
`llm.EXECUTION_SETTINGS` and `settings.EXECUTION_MODEL_ID`.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.models import Model

from ambient_ai.orchestration.llm import EXECUTION_SETTINGS, llm_model
from ambient_ai.settings import EXECUTION_MODEL_ID, OUTBOUND_TIMEOUT_SECONDS
from ambient_ai.tools import github_rest
from ambient_ai.tools.github_rest import GitHubDeps

EXECUTION_INSTRUCTIONS = """\
You read one person's GitHub on their behalf and report what you find, briefly.

Make the fewest tool calls that answer the task, normally two at most: one to resolve the
repository and one to read it. Do not call a tool to confirm what a previous tool returned.
- If a fact or the task names the repository, call latest_commit or open_pull_requests on
  that name directly. No lookup first.
- If the sender names an organisation or says a repository is "under X", call
  get_repo("X/<name>") before anything else.
- Only when no name is available, call list_repos and pick the best match, saying which.
  If list_repos comes back empty, say the token cannot see any organisation.
Never guess a different repository than the one asked for, and never invent a commit.

Answer in under 400 characters, two or three plain sentences: repository, short sha, first
line of the commit message, author, date, and whether it answers the task. If you could not
find something, say plainly what you could not find and why.
"""


def build_execution_agent(model: Model | str | None = None) -> Agent[GitHubDeps, str]:
    agent: Agent[GitHubDeps, str] = Agent(
        llm_model(model, model_id=EXECUTION_MODEL_ID),
        deps_type=GitHubDeps,
        output_type=str,
        instructions=EXECUTION_INSTRUCTIONS,
        name="execution",
        tool_timeout=OUTBOUND_TIMEOUT_SECONDS + 1,
        retries=1,
        model_settings=EXECUTION_SETTINGS,
    )

    @agent.tool
    async def list_repos(ctx: RunContext[GitHubDeps]) -> list[dict[str, Any]]:
        """Every repository the sender's token can read — owned, collaborator, and org."""
        return await github_rest.list_repos(ctx.deps)

    @agent.tool
    async def get_repo(ctx: RunContext[GitHubDeps], full_name: str) -> dict[str, Any]:
        """One repository by 'owner/name', for a repo the listing did not surface."""
        return await github_rest.get_repo(ctx.deps, full_name)

    @agent.tool
    async def latest_commit(ctx: RunContext[GitHubDeps], repo: str) -> dict[str, Any]:
        """Latest commit on a repository ('name' or 'owner/name')."""
        return await github_rest.latest_commit(ctx.deps, repo)

    @agent.tool
    async def open_pull_requests(ctx: RunContext[GitHubDeps], repo: str) -> dict[str, Any]:
        """Open pull requests on a repository ('name' or 'owner/name')."""
        return await github_rest.open_pull_requests(ctx.deps, repo)

    return agent
