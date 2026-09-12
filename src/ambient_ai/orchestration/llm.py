"""The one place a model provider is named. Every agent takes its model from here.

The provider is Anthropic (ADR-009's reply path, architect's direction of 2026-09-12 20:10
UTC, after the OpenAI account ran out of credit). Three model ids: `LLM_MODEL_ID` for the
orchestrator and synthesis; `EXECUTION_MODEL_ID` for the Execution Agent; `EXTRACTOR_MODEL_ID`
for the bulk memory extractor, which is cheap, high-volume, and off the reply path.

The per-call settings below exist for the 30-second reply bound in ADR-009's trigger 1. The
reply path makes up to four sequential model calls, so each one carries a small `max_tokens`
and, where the model supports it, `anthropic_effort="low"`. Thinking is left unconfigured so
it stays adaptive: `budget_tokens` is rejected outright on these models.

The Execution Agent is the slowest step because it runs a tool loop: it took 14 s of the
21 s first end-to-end run at 20:16 UTC. Measured live on 2026-09-12 on the demo message with
a faked GitHub, one run each, on the tightened instructions: `claude-opus-5` with no settings
3.49 s, `claude-opus-5` at effort low and 1024 tokens 3.64 s, `claude-sonnet-5` at the same
settings 2.82 s. All three named the commit and said honestly that it did not fix the auth
bug, so the fastest wins and `EXECUTION_MODEL_ID` is `claude-sonnet-5`. The old instructions
on `claude-opus-5` with no settings took 4.30 s for the same answer.
"""

from __future__ import annotations

from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings

from ambient_ai.settings import LLM_MODEL_ID

PLAN_SETTINGS = AnthropicModelSettings(max_tokens=1024, anthropic_effort="low")
"""Orchestrator: one small structured Plan, no prose. Effort low; the decision is shallow."""

SYNTHESIS_SETTINGS = AnthropicModelSettings(max_tokens=512, anthropic_effort="low")
"""Synthesis: at most 480 characters of SMS. 512 output tokens is more than three segments."""

EXECUTION_SETTINGS = AnthropicModelSettings(max_tokens=1024, anthropic_effort="low")
"""Execution Agent: two tool calls and a short finding. The slowest step; measured below."""

EXTRACTOR_SETTINGS = AnthropicModelSettings(max_tokens=1024)
"""Extractor: a short list of facts. `claude-haiku-4-5` takes no effort setting."""


def llm_model(model: Model | str | None = None, *, model_id: str = LLM_MODEL_ID) -> Model | str:
    """Return the model an agent should run on: the caller's override, else an Anthropic model.

    The Anthropic client reads ANTHROPIC_API_KEY from the environment; the value is never
    handled here. `model_id` defaults to LLM_MODEL_ID; the extractor passes EXTRACTOR_MODEL_ID.
    """
    if model is not None:
        return model
    return AnthropicModel(model_id)
