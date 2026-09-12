"""GitHub REST calls bound to one sender's token. The token lives in deps, never a global.

Each function takes the request-scoped client whose Authorization header carries the
sender's own token, and returns a compact dict the Execution Agent can read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from ambient_ai.settings import OUTBOUND_TIMEOUT_SECONDS

GITHUB_API = "https://api.github.com"


@dataclass
class GitHubDeps:
    """Per-request dependencies for the Execution Agent. Discarded with the agent."""

    client: httpx.AsyncClient
    login: str | None = field(default=None)


def github_client(
    token: str, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """An httpx client for one sender. Closed by run_mention when the request ends."""
    return httpx.AsyncClient(
        base_url=GITHUB_API,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ambient-ai",
        },
        timeout=OUTBOUND_TIMEOUT_SECONDS,
        transport=transport,
    )


async def _qualify(deps: GitHubDeps, repo: str) -> str:
    """'sms-swarm-core' -> 'login/sms-swarm-core' using the token owner's login."""
    if "/" in repo:
        return repo
    if deps.login is None:
        response = await deps.client.get("/user")
        response.raise_for_status()
        deps.login = response.json()["login"]
    return f"{deps.login}/{repo}"


async def list_repos(deps: GitHubDeps) -> list[dict[str, Any]]:
    response = await deps.client.get(
        "/user/repos", params={"sort": "pushed", "per_page": 30, "affiliation": "owner"}
    )
    response.raise_for_status()
    return [
        {"name": r["full_name"], "private": r.get("private"), "pushed_at": r.get("pushed_at")}
        for r in response.json()
    ]


async def latest_commit(deps: GitHubDeps, repo: str) -> dict[str, Any]:
    full = await _qualify(deps, repo)
    response = await deps.client.get(f"/repos/{full}/commits", params={"per_page": 1})
    if response.status_code == 404:
        return {"error": f"no repository named {full}"}
    response.raise_for_status()
    commits = response.json()
    if not commits:
        return {"repo": full, "error": "no commits"}
    c = commits[0]
    commit = c.get("commit", {})
    author = commit.get("author") or {}
    return {
        "repo": full,
        "sha": c.get("sha", "")[:7],
        "message": (commit.get("message") or "").splitlines()[0] if commit.get("message") else "",
        "author": author.get("name"),
        "date": author.get("date"),
    }


async def open_pull_requests(deps: GitHubDeps, repo: str) -> dict[str, Any]:
    full = await _qualify(deps, repo)
    response = await deps.client.get(
        f"/repos/{full}/pulls", params={"state": "open", "per_page": 10}
    )
    if response.status_code == 404:
        return {"error": f"no repository named {full}"}
    response.raise_for_status()
    return {
        "repo": full,
        "open": [
            {"number": p["number"], "title": p["title"], "state": p["state"]}
            for p in response.json()
        ],
    }
