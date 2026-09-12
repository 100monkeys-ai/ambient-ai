"""The integrations registry: the one place an integration is declared.

Every integration is optional. The agent answers with none connected and gains one ability
per connection, so the portal, the `/tools` commands, and the sender store read this list
instead of naming GitHub. Adding an integration is one entry here plus its `validate`; a
place that special-cases a key instead of reading this list is the bug this module prevents.

Read the list through `integrations()`, never by importing the tuple: a caller holding the
tuple cannot see a registry a test or a later release changed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ambient_ai.tools.github import GITHUB, validate_github_token


@dataclass(frozen=True)
class Integration:
    """One connectable service. `validate` returns the account name, or None for a bad key."""

    key: str
    name: str
    description: str
    how_to_get_a_key_url: str
    field_label: str
    validate: Callable[[str], Awaitable[str | None]]


INTEGRATIONS: tuple[Integration, ...] = (
    Integration(
        key=GITHUB,
        name="GitHub",
        description="Ask about your repositories, commits, and open pull requests.",
        how_to_get_a_key_url="https://github.com/settings/tokens",
        field_label="Personal access token",
        validate=validate_github_token,
    ),
)


def integrations() -> tuple[Integration, ...]:
    return INTEGRATIONS


def find(key: str) -> Integration | None:
    """The integration with that key, or None — an unknown key is answered with the list."""
    for integration in integrations():
        if integration.key == key.lower():
            return integration
    return None


def keys() -> tuple[str, ...]:
    return tuple(integration.key for integration in integrations())
