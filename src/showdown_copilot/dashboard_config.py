"""Configuration models and presets for the local dashboard."""
from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


class CoachAIRequest(BaseModel):
    presetId: str = Field(default="openai-gpt-54-mini-balanced")
    runMode: str = Field(default="fake")


COACH_MODEL_PRESETS: list[dict[str, Any]] = [
    {
        "id": "openai-gpt-54-mini-balanced",
        "provider": "openai",
        "modelLabel": "GPT-5.4 mini",
        "label": "OpenAI · GPT-5.4 mini · Balanced",
        "tier": "fast",
        "effort": "balanced",
        "toolDepth": "brief",
        "mode": "fake",
        "realProvider": "openai",
        "apiModel": os.environ.get("SHOWDOWN_OPENAI_FAST_MODEL", "gpt-5.4-mini"),
        "openaiReasoningEffort": "medium",
        "maxOutputTokens": _env_int("SHOWDOWN_OPENAI_FAST_MAX_OUTPUT_TOKENS", 1200),
        "maxToolRounds": 3,
        "timeoutSeconds": _env_int("SHOWDOWN_OPENAI_TIMEOUT_SECONDS", 45),
    },
    {
        "id": "anthropic-haiku-45-balanced",
        "provider": "anthropic",
        "modelLabel": "Claude Haiku 4.5",
        "label": "Claude · Haiku 4.5 · Balanced",
        "tier": "fast",
        "effort": "balanced",
        "toolDepth": "brief",
        "mode": "fake",
        "realProvider": "anthropic",
        "apiModel": os.environ.get("SHOWDOWN_ANTHROPIC_FAST_MODEL", "claude-haiku-4-5"),
        "maxOutputTokens": _env_int("SHOWDOWN_ANTHROPIC_FAST_MAX_OUTPUT_TOKENS", 2200),
        "maxToolRounds": 3,
        "timeoutSeconds": _env_int("SHOWDOWN_ANTHROPIC_TIMEOUT_SECONDS", 120),
    },
    {
        "id": "google-gemini-flash-balanced",
        "provider": "google",
        "modelLabel": "Gemini Flash",
        "label": "Gemini · Flash · Balanced",
        "tier": "fast",
        "effort": "balanced",
        "toolDepth": "brief",
        "mode": "fake",
    },
    {
        "id": "openai-gpt-55-high",
        "provider": "openai",
        "modelLabel": "GPT-5.5",
        "label": "OpenAI · GPT-5.5 · Advanced reasoning",
        "tier": "advanced",
        "effort": "high",
        "toolDepth": "battle",
        "mode": "fake",
        "realProvider": "openai",
        "apiModel": os.environ.get("SHOWDOWN_OPENAI_ADVANCED_MODEL", "gpt-5.5"),
        "openaiReasoningEffort": "high",
        "maxOutputTokens": _env_int("SHOWDOWN_OPENAI_ADVANCED_MAX_OUTPUT_TOKENS", 3500),
        "maxToolRounds": 5,
        "timeoutSeconds": _env_int("SHOWDOWN_OPENAI_ADVANCED_TIMEOUT_SECONDS", 180),
    },
    {
        "id": "anthropic-sonnet-46-high",
        "provider": "anthropic",
        "modelLabel": "Claude Sonnet 4.6",
        "label": "Claude · Sonnet 4.6 · Advanced reasoning",
        "tier": "advanced",
        "effort": "high",
        "toolDepth": "battle",
        "mode": "fake",
        "realProvider": "anthropic",
        "apiModel": os.environ.get("SHOWDOWN_ANTHROPIC_ADVANCED_MODEL", "claude-sonnet-4-6"),
        "maxOutputTokens": _env_int("SHOWDOWN_ANTHROPIC_ADVANCED_MAX_OUTPUT_TOKENS", 6000),
        "maxToolRounds": 5,
        "timeoutSeconds": _env_int("SHOWDOWN_ANTHROPIC_TIMEOUT_SECONDS", 120),
        "anthropicThinking": "adaptive",
        "anthropicThinkingEffort": os.environ.get("SHOWDOWN_ANTHROPIC_ADVANCED_THINKING_EFFORT", "high"),
        "anthropicThinkingDisplay": os.environ.get("SHOWDOWN_ANTHROPIC_THINKING_DISPLAY", "omitted"),
    },
    {
        "id": "anthropic-sonnet-5-high",
        "provider": "anthropic",
        "modelLabel": "Claude Sonnet 5",
        "label": "Claude · Sonnet 5 · Advanced reasoning",
        "tier": "advanced",
        "effort": "high",
        "toolDepth": "battle",
        "mode": "fake",
        "realProvider": "anthropic",
        "apiModel": os.environ.get("SHOWDOWN_ANTHROPIC_SONNET_5_MODEL", "claude-sonnet-5"),
        "maxOutputTokens": _env_int("SHOWDOWN_ANTHROPIC_SONNET_5_MAX_OUTPUT_TOKENS", 6000),
        "maxToolRounds": 5,
        "timeoutSeconds": _env_int("SHOWDOWN_ANTHROPIC_SONNET_5_TIMEOUT_SECONDS", 180),
        "anthropicThinking": "adaptive",
        "anthropicThinkingEffort": os.environ.get("SHOWDOWN_ANTHROPIC_SONNET_5_THINKING_EFFORT", "high"),
        "anthropicThinkingDisplay": os.environ.get("SHOWDOWN_ANTHROPIC_THINKING_DISPLAY", "omitted"),
    },
    {
        "id": "anthropic-fable-5-high",
        "provider": "anthropic",
        "modelLabel": "Claude Fable 5",
        "label": "Claude · Fable 5 · Advanced reasoning",
        "tier": "advanced",
        "effort": "high",
        "toolDepth": "battle",
        "mode": "fake",
        "realProvider": "anthropic",
        "apiModel": os.environ.get("SHOWDOWN_ANTHROPIC_FABLE_5_MODEL", "claude-fable-5"),
        "maxOutputTokens": _env_int("SHOWDOWN_ANTHROPIC_FABLE_5_MAX_OUTPUT_TOKENS", 8000),
        "maxToolRounds": 5,
        "timeoutSeconds": _env_int("SHOWDOWN_ANTHROPIC_FABLE_5_TIMEOUT_SECONDS", 240),
        "anthropicThinking": "adaptive",
        "anthropicThinkingEffort": os.environ.get("SHOWDOWN_ANTHROPIC_FABLE_5_THINKING_EFFORT", "high"),
        "anthropicThinkingDisplay": os.environ.get("SHOWDOWN_ANTHROPIC_THINKING_DISPLAY", "omitted"),
    },
    {
        "id": "google-gemini-pro-high",
        "provider": "google",
        "modelLabel": "Gemini Pro",
        "label": "Gemini · Pro · Advanced reasoning",
        "tier": "advanced",
        "effort": "high",
        "toolDepth": "battle",
        "mode": "fake",
    },
    {
        "id": "openai-gpt-55-pro-xhigh",
        "provider": "openai",
        "modelLabel": "GPT-5.5 Pro",
        "label": "OpenAI · GPT-5.5 Pro · Max reasoning",
        "tier": "max",
        "effort": "xhigh",
        "toolDepth": "archive",
        "mode": "fake",
        "realProvider": "openai",
        "apiModel": os.environ.get("SHOWDOWN_OPENAI_MAX_MODEL", "gpt-5.5-pro"),
        "openaiReasoningEffort": "high",
        "maxOutputTokens": _env_int("SHOWDOWN_OPENAI_MAX_OUTPUT_TOKENS", 4500),
        "maxToolRounds": _env_int("SHOWDOWN_OPENAI_MAX_TOOL_ROUNDS", 3),
        "timeoutSeconds": _env_int("SHOWDOWN_OPENAI_MAX_TIMEOUT_SECONDS", 240),
    },
    {
        "id": "anthropic-opus-48-xhigh",
        "provider": "anthropic",
        "modelLabel": "Claude Opus 4.8",
        "label": "Claude · Opus 4.8 · Max reasoning",
        "tier": "max",
        "effort": "xhigh",
        "toolDepth": "archive",
        "mode": "fake",
        "realProvider": "anthropic",
        "apiModel": os.environ.get("SHOWDOWN_ANTHROPIC_MAX_MODEL", "claude-opus-4-8"),
        "maxOutputTokens": _env_int("SHOWDOWN_ANTHROPIC_MAX_OUTPUT_TOKENS", 7500),
        "maxToolRounds": 6,
        "timeoutSeconds": _env_int("SHOWDOWN_ANTHROPIC_MAX_TIMEOUT_SECONDS", 180),
        "anthropicThinking": "adaptive",
        "anthropicThinkingEffort": os.environ.get("SHOWDOWN_ANTHROPIC_MAX_THINKING_EFFORT", "xhigh"),
        "anthropicThinkingDisplay": os.environ.get("SHOWDOWN_ANTHROPIC_THINKING_DISPLAY", "omitted"),
    },
    {
        "id": "google-gemini-pro-tools-xhigh",
        "provider": "google",
        "modelLabel": "Gemini Pro Custom Tools",
        "label": "Gemini · Pro Custom Tools · Max reasoning",
        "tier": "max",
        "effort": "xhigh",
        "toolDepth": "archive",
        "mode": "fake",
    },
]


def coach_model_presets() -> list[dict[str, Any]]:
    return [dict(preset) for preset in COACH_MODEL_PRESETS]


def coach_preset(preset_id: str) -> dict[str, Any]:
    for preset in COACH_MODEL_PRESETS:
        if preset["id"] == preset_id:
            return dict(preset)
    raise ValueError(f"unknown coach preset: {preset_id}")
