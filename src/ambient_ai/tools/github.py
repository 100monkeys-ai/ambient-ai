"""Per-sender tool credentials. The sender's own GitHub token lives on their sender record."""

from ambient_ai.identity import lookup_sender


def github_token_for(phone: str) -> str | None:
    """Return the token the sender pasted in the portal, or None if GitHub is not connected."""
    profile = lookup_sender(phone)
    return profile.github_token if profile else None
