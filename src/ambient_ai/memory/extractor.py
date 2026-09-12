"""The Memory Extractor: a tool-less PydanticAI agent run after the reply is sent (ADR-005).

It reads the transcript of one exchange and emits the three-field schema. It never runs on
the request path and never touches Cortex itself; `memory.writer.remember` does the write.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

from ambient_ai.orchestration.llm import llm_model


class MemoryExtraction(BaseModel):
    novel_facts: list[str] = Field(
        default_factory=list,
        description="Durable facts about the sender stated in this exchange, one short "
        "sentence each, e.g. 'backend repo is sms-swarm-core'.",
    )
    preferences: list[str] = Field(
        default_factory=list,
        description="Standing preferences the sender expressed, e.g. 'wants short replies'.",
    )
    should_update: bool = Field(
        default=False,
        description="True only when novel_facts or preferences holds something worth keeping.",
    )


EXTRACTOR_INSTRUCTIONS = """\
You read the transcript of one SMS exchange between a sender and Ambient AI and decide what
is worth remembering about the sender for future conversations.

Keep only durable facts about the sender: names of their repositories and which role each
plays ("backend repo is sms-swarm-core"), projects they are working on, people and tools
they mention as theirs, and standing preferences about how they want replies.

Never keep transient chit-chat: greetings, thanks, the question being asked, what the agent
answered, a commit sha, or anything the sender did not state about themselves. Never keep
phone numbers, tokens, links, or secrets.

Write each fact as one short plain sentence. Do not repeat a fact that the agent's reply
merely echoed back unless the sender stated it. When nothing durable was said, return empty
lists and set should_update to false. Set should_update to true only when at least one
fact or preference is present.
"""


def build_extractor(model: Model | str | None = None) -> Agent[None, MemoryExtraction]:
    """Build the extractor agent. It has no tools; tests inspect its tool list."""
    return Agent(
        llm_model(model),
        output_type=MemoryExtraction,
        instructions=EXTRACTOR_INSTRUCTIONS,
        name="memory-extractor",
        retries=2,
    )


async def extract(transcript: str, *, model: Model | str | None = None) -> MemoryExtraction:
    """Read a transcript and return what is worth writing to the sender's Cortex memory."""
    result = await build_extractor(model).run(f"Transcript:\n{transcript}")
    return result.output
