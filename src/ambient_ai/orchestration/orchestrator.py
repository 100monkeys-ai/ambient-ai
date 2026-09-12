"""The orchestrator: a PydanticAI agent with no tools that emits a structured Plan."""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

from ambient_ai.orchestration.llm import llm_model


class Plan(BaseModel):
    reasoning: str = Field(description="Why this plan, in one sentence.")
    needs_memory: bool = Field(
        description="True when the sender's stored notes are needed to answer or to resolve "
        "a reference such as 'my backend repo'."
    )
    needs_github: bool = Field(description="True when the answer needs a read from GitHub.")
    github_task: str | None = Field(
        default=None,
        description="One-line instruction for the GitHub step, e.g. 'latest commit on the "
        "backend repo'. Required when needs_github is true.",
    )
    direct_reply: str | None = Field(
        default=None,
        description="The full SMS reply when no memory and no tool is needed (greetings, "
        "questions about the agent itself). Null otherwise.",
    )


ORCHESTRATOR_INSTRUCTIONS = """\
You are Ambient AI, a phone contact in an SMS group chat. Someone just @mentioned you.
You have no tools. You only decide what has to happen and emit a plan.

Rules:
- Replies are SMS-length: at most three short sentences, plain text, no markdown.
- If the message is a greeting, small talk, or a question about you, answer it yourself in
  direct_reply and set needs_memory and needs_github to false.
- If the message refers to the sender's notes, memory, or anything you have been told before,
  set needs_memory to true.
- If the message refers to a repository by role ("my backend repo", "the API repo") or by a
  name you cannot be sure of, set needs_memory to true. Never guess which repository is meant
  when memory can answer.
- If the message asks about commits, pull requests, repositories, or whether something was
  fixed or merged, set needs_github to true and write github_task as one line for a GitHub
  reader (for example "latest commit on the backend repo; say whether it fixes the auth bug").
- When needs_memory or needs_github is true, leave direct_reply null.
"""


def build_orchestrator(model: Model | str | None = None) -> Agent[None, Plan]:
    """Build the tool-less orchestrator. Tests inspect its tool list; it must stay empty."""
    return Agent(
        llm_model(model),
        output_type=Plan,
        instructions=ORCHESTRATOR_INSTRUCTIONS,
        name="orchestrator",
        retries=2,
    )
