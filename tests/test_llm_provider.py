"""The provider every agent runs on, offline: construction only, no model request.

ADR-009's trigger 1 bounds the reply at 30 seconds, so these tests also pin the per-call
`max_tokens` each agent carries. No test may reach Anthropic; the key below is a fake.
"""

from __future__ import annotations

import pytest
from pydantic_ai import models
from pydantic_ai.models.anthropic import AnthropicModel

from ambient_ai.memory.extractor import build_extractor
from ambient_ai.orchestration.llm import (
    EXTRACTOR_SETTINGS,
    PLAN_SETTINGS,
    SYNTHESIS_SETTINGS,
    llm_model,
)
from ambient_ai.orchestration.orchestrator import build_orchestrator
from ambient_ai.orchestration.run import build_synthesizer
from ambient_ai.settings import EXTRACTOR_MODEL_ID, LLM_MODEL_ID, LLM_PROVIDER

models.ALLOW_MODEL_REQUESTS = False


@pytest.fixture(autouse=True)
def fake_anthropic_key(monkeypatch):
    """The Anthropic provider refuses to construct without a key; no request is ever made."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")


def test_no_test_reaches_a_model_provider():
    assert models.ALLOW_MODEL_REQUESTS is False


def test_llm_model_is_anthropic_on_the_named_constant():
    model = llm_model()
    assert isinstance(model, AnthropicModel)
    assert model.system == "anthropic"
    assert model.model_name == LLM_MODEL_ID == "claude-opus-5"
    assert LLM_PROVIDER == "anthropic"


def test_the_extractor_runs_on_the_cheaper_extractor_model():
    assert EXTRACTOR_MODEL_ID == "claude-haiku-4-5"
    assert EXTRACTOR_MODEL_ID != LLM_MODEL_ID
    model = build_extractor().model
    assert isinstance(model, AnthropicModel)
    assert model.model_name == EXTRACTOR_MODEL_ID


def test_reply_path_agents_run_on_the_orchestrator_model():
    assert build_orchestrator().model.model_name == LLM_MODEL_ID
    assert build_synthesizer().model.model_name == LLM_MODEL_ID


def test_every_call_carries_a_small_output_bound_and_adaptive_thinking():
    """ADR-009 trigger 1: a reply within 30 seconds over four sequential calls."""
    assert PLAN_SETTINGS["max_tokens"] == 1024
    assert SYNTHESIS_SETTINGS["max_tokens"] == 1024
    assert EXTRACTOR_SETTINGS["max_tokens"] == 1024
    # Effort is only offered where the model profile supports it.
    assert PLAN_SETTINGS["anthropic_effort"] == "low"
    assert SYNTHESIS_SETTINGS["anthropic_effort"] == "low"
    assert "anthropic_effort" not in EXTRACTOR_SETTINGS
    # Thinking stays adaptive: budget_tokens is rejected on these models, so nothing is sent.
    for settings in (PLAN_SETTINGS, SYNTHESIS_SETTINGS, EXTRACTOR_SETTINGS):
        assert "anthropic_thinking" not in settings
        assert "thinking" not in settings


def test_the_agents_carry_their_settings():
    assert build_orchestrator().model_settings == PLAN_SETTINGS
    assert build_synthesizer().model_settings == SYNTHESIS_SETTINGS
    assert build_extractor().model_settings == EXTRACTOR_SETTINGS
