"""Per-sender tool credentials. For the hackathon, GitHub is a mock token behind a button."""


class MockGitHubTokenStore:
    """Holds one mock GitHub token per phone number. Tokens are never logged."""

    def get(self, phone: str) -> str | None:
        raise NotImplementedError("token store lookup is not built yet")

    def put(self, phone: str, token: str) -> None:
        raise NotImplementedError("token store write is not built yet")
