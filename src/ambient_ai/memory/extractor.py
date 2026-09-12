"""Asynchronous memory extraction, run after the reply is sent."""

from pydantic import BaseModel, Field


class MemoryExtraction(BaseModel):
    novel_facts: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    should_update: bool = False


def extract(transcript: str) -> MemoryExtraction:
    """Read a transcript and return what is worth writing to the sender's Cortex memory."""
    raise NotImplementedError("memory extractor is not built yet")
