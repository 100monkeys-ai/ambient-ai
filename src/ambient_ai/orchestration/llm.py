"""The one place a model provider is named. Every agent takes its model from here."""

from __future__ import annotations

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel

from ambient_ai.settings import LLM_MODEL_ID


def llm_model(model: Model | str | None = None) -> Model | str:
    """Return the model an agent should run on: the caller's override, else OpenAI LLM_MODEL_ID.

    The OpenAI client reads OPENAI_API_KEY from the environment; the value is never handled here.
    """
    if model is not None:
        return model
    return OpenAIChatModel(LLM_MODEL_ID)
