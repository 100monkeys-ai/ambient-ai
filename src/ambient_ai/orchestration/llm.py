"""The one place a model provider is named. Every agent takes its model from here.

The provider is Anthropic (ADR-009's reply path, architect's direction of 2026-09-12 20:10
UTC, after the OpenAI account ran out of credit). Two model ids: `LLM_MODEL_ID` for the
orchestrator, the Execution Agent, and synthesis; `EXTRACTOR_MODEL_ID` for the bulk memory
extractor, which is cheap, high-volume, and off the reply path.

The per-call settings below exist for the 30-second reply bound in ADR-009's trigger 1. The
reply path makes up to four sequential model calls, so each one carries a small `max_tokens`
and, where the model supports it, `anthropic_effort="low"`. Thinking is left unconfigured so
it stays adaptive: `budget_tokens` is rejected outright on these models.
"""

from __future__ import annotations

from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings

from ambient_ai.settings import LLM_MODEL_ID

PLAN_SETTINGS = AnthropicModelSettings(max_tokens=1024, anthropic_effort="low")
"""Orchestrator: one small structured Plan, no prose. Effort low; the decision is shallow."""

SYNTHESIS_SETTINGS = AnthropicModelSettings(max_tokens=512, anthropic_effort="low")
"""Synthesis: at most 480 characters of SMS. 512 output tokens is more than three segments."""

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
