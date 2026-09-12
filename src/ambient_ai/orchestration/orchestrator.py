"""The orchestrator: a PydanticAI agent with no tools that emits a structured Plan."""

from typing import Literal

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    agent: Literal["context", "execution"]
    instruction: str


class Plan(BaseModel):
    reasoning: str = Field(description="Why these steps, in one or two sentences.")
    steps: list[PlanStep] = Field(default_factory=list)


def plan(prompt: str, available_tools: list[str]) -> Plan:
    """Ask the orchestrator model for a Plan. It has zero tools by design."""
    raise NotImplementedError("the orchestrator is not built yet")
