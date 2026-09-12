"""GitHub REST calls bound to one sender's token. The token lives in deps, never a global.

Each function takes the request-scoped client whose Authorization header carries the
sender's own token, and returns a compact dict the Execution Agent can read.
"""

from __future__ import annotations

import base64
import binascii
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
    """Every repository the token can read, not only the ones the sender owns.

    `affiliation=owner` alone hid `100monkeys-ai/ambient-ai` on 2026-09-12 at 20:16 UTC and
    the agent answered that the repository did not exist. Organisation membership and
    collaborator access are how a real person reaches a work repository, so both are asked
    for. An empty list now means the token has no organisation access, not that the code
    looked in the wrong place.
    """
    response = await deps.client.get(
        "/user/repos",
        params={
            "sort": "pushed",
            "per_page": 100,
            "affiliation": "owner,collaborator,organization_member",
        },
    )
    response.raise_for_status()
    return [
        {"name": r["full_name"], "private": r.get("private"), "pushed_at": r.get("pushed_at")}
        for r in response.json()
    ]


async def get_repo(deps: GitHubDeps, full_name: str) -> dict[str, Any]:
    """One repository by 'owner/name', for a repo the listing did not surface."""
    full = await _qualify(deps, full_name)
    response = await deps.client.get(f"/repos/{full}")
    if response.status_code == 404:
        return {"error": f"no repository named {full}, or the token cannot see it"}
    response.raise_for_status()
    r = response.json()
    return {
        "repo": r.get("full_name", full),
        "default_branch": r.get("default_branch"),
        "pushed_at": r.get("pushed_at"),
        "private": r.get("private"),
    }


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


TREE_MAX_PATHS = 200
"""How many paths a listing returns. A large repository's whole tree would fill the
Execution Agent's context with names and leave no room for the files that answer the
question, so the listing is cut and the cut is named in the result."""

FILE_MAX_CHARS = 24 * 1024
"""How much of one file the agent reads. Enough for a README or a module; small enough that
three files still fit beside the model's own reasoning."""

BINARY_SUFFIXES = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".pdf", ".zip", ".gz",
        ".tar", ".whl", ".so", ".dylib", ".dll", ".exe", ".woff", ".woff2", ".ttf", ".mp3",
        ".mp4", ".mov", ".wasm", ".pyc", ".jar", ".class", ".db", ".sqlite",
    }
)
"""Refused before the bytes are decoded. A binary file cannot be summarised and its decoded
mojibake would spend the whole token budget saying nothing."""


async def _default_branch(deps: GitHubDeps, full: str) -> str | None:
    response = await deps.client.get(f"/repos/{full}")
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json().get("default_branch") or "main"


def _decode(payload: dict[str, Any], path: str) -> dict[str, Any]:
    """Turn a contents payload into text, or say why it cannot be read.

    GitHub answers with base64 for a file it can inline and with `encoding: none` for one
    too large for the contents endpoint; both are refused plainly rather than guessed at.
    """
    if payload.get("type") != "file":
        return {"error": f"{path} is not a file"}
    if path.lower().endswith(tuple(BINARY_SUFFIXES)):
        return {"error": f"{path} is a binary file; I only read text"}
    if payload.get("encoding") != "base64":
        return {"error": f"{path} is too large to read through the contents API"}
    try:
        raw = base64.b64decode(payload.get("content") or "")
        text = raw.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return {"error": f"{path} is a binary file; I only read text"}
    result: dict[str, Any] = {
        "path": payload.get("path", path),
        "bytes": payload.get("size", len(raw)),
        "truncated": False,
        "content": text,
    }
    if len(text) > FILE_MAX_CHARS:
        result["content"] = text[:FILE_MAX_CHARS]
        result["truncated"] = True
        result["note"] = f"truncated to the first {FILE_MAX_CHARS} characters of {len(text)}"
    return result


async def list_files(deps: GitHubDeps, repo: str, path: str = "") -> dict[str, Any]:
    """Every file on the default branch, one recursive tree call, capped at 200 paths.

    Added 2026-09-12 on the architect's ruling: a tester asked what a repository implements
    and the agent could only see commit metadata, so it answered honestly that it could not
    tell. Directories are dropped — a name with no bytes behind it answers nothing.
    """
    full = await _qualify(deps, repo)
    branch = await _default_branch(deps, full)
    if branch is None:
        return {"error": f"no repository named {full}, or the token cannot see it"}
    response = await deps.client.get(
        f"/repos/{full}/git/trees/{branch}", params={"recursive": "1"}
    )
    if response.status_code == 404:
        return {"error": f"no branch {branch} on {full}"}
    response.raise_for_status()
    prefix = path.strip("/")
    files = [
        {"path": entry["path"], "size": entry.get("size", 0)}
        for entry in response.json().get("tree", [])
        if entry.get("type") == "blob"
        and (not prefix or entry["path"].startswith(f"{prefix}/") or entry["path"] == prefix)
    ]
    result: dict[str, Any] = {
        "repo": full,
        "branch": branch,
        "files": files[:TREE_MAX_PATHS],
        "truncated": len(files) > TREE_MAX_PATHS,
    }
    if result["truncated"]:
        result["note"] = (
            f"{len(files)} files match; showing the first {TREE_MAX_PATHS}. "
            "Narrow it with the path argument."
        )
    return result


async def read_file(deps: GitHubDeps, repo: str, path: str) -> dict[str, Any]:
    """One text file from the default branch, base64-decoded and capped at 24 KB."""
    full = await _qualify(deps, repo)
    response = await deps.client.get(f"/repos/{full}/contents/{path.lstrip('/')}")
    if response.status_code == 404:
        return {"error": f"no file at {path} in {full}"}
    response.raise_for_status()
    return {"repo": full, **_decode(response.json(), path)}


async def get_readme(deps: GitHubDeps, repo: str) -> dict[str, Any]:
    """The repository's README, whatever it is named. The first thing to read about a repo."""
    full = await _qualify(deps, repo)
    response = await deps.client.get(f"/repos/{full}/readme")
    if response.status_code == 404:
        return {"error": f"no README in {full}"}
    response.raise_for_status()
    payload = response.json()
    return {"repo": full, **_decode(payload, payload.get("path", "README"))}
