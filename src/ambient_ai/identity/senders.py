"""Sender identity. The phone number is the primary key for everything downstream."""

from pydantic import BaseModel


class SenderProfile(BaseModel):
    phone: str
    github_token: str | None = None


def lookup_sender(phone: str) -> SenderProfile | None:
    """Return the profile for a known phone number, or None for a stranger."""
    raise NotImplementedError("sender lookup is not built yet")


def start_onboarding(phone: str) -> str:
    """Begin JIT onboarding (Twilio Verify OTP) and return the portal link to text back."""
    raise NotImplementedError("JIT onboarding is not built yet")
