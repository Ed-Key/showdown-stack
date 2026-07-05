"""Model repair pass for generated preview plans."""
from __future__ import annotations

import json
from typing import Any

from .llm_response import parse_jsonish_model_output, response_text, usage_from_responses


REPAIR_SYSTEM_PROMPT = """You are a Pokemon mechanics repair pass for Showdown Copilot.

You receive a JSON matchup plan plus verifier issues.
Return the same JSON schema, repaired.

Rules:
- Repair or remove only claims flagged by verifierIssues.
- Use referenceFacts in verifierIssues as the authority.
- Do not add new mechanics claims while repairing.
- Preserve useful strategic guidance when it can be rewritten accurately.
- Return JSON only. No markdown.
"""


def _anthropic_text(response: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in response.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts).strip()


def _anthropic_usage(response: dict[str, Any]) -> dict[str, Any]:
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    return {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "totalTokens": (
            int(input_tokens or 0) + int(output_tokens or 0)
            if input_tokens is not None or output_tokens is not None
            else None
        ),
        "costUsd": None,
    }


def _merge_usage(primary: dict[str, Any], repair: dict[str, Any]) -> dict[str, Any]:
    merged = dict(primary)
    merged["repair"] = repair
    for key in ("inputTokens", "outputTokens", "totalTokens"):
        if isinstance(primary.get(key), int) and isinstance(repair.get(key), int):
            merged[key] = primary[key] + repair[key]
    return merged


async def repair_preview_plan_json(
    *,
    provider: str,
    preset: dict[str, Any],
    plan: dict[str, Any],
    issues: list[dict[str, Any]],
    schema: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Repair a generated plan and return parsed JSON plus usage/raw text."""
    payload = json.dumps(
        {
            "plan": plan,
            "verifierIssues": issues,
            "requiredJsonSchema": schema,
        },
        ensure_ascii=False,
        indent=2,
    )
    model = str(preset.get("apiModel") or preset.get("modelLabel") or "")
    timeout = int(preset.get("timeoutSeconds") or 90)
    max_tokens = min(int(preset.get("maxOutputTokens") or 3000), 4000)

    if provider == "anthropic":
        from .dashboard_agent_service import anthropic_messages_create

        response = await anthropic_messages_create(
            {
                "model": model,
                "system": REPAIR_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": payload}],
                "max_tokens": max_tokens,
                "output_config": {
                    "format": {
                        "type": "json_schema",
                        "schema": schema,
                    },
                },
            },
            timeout,
        )
        text = _anthropic_text(response)
        return parse_jsonish_model_output(text), _anthropic_usage(response), text

    if provider == "openai":
        from .dashboard_agent_service import openai_responses_create

        response = await openai_responses_create(
            {
                "model": model,
                "instructions": REPAIR_SYSTEM_PROMPT,
                "input": payload,
                "reasoning": {"effort": preset.get("openaiReasoningEffort") or "medium"},
                "max_output_tokens": max_tokens,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "repaired_matchup_plan",
                        "schema": schema,
                        "strict": True,
                    },
                },
            },
            timeout,
        )
        text = response_text(response)
        return parse_jsonish_model_output(text), usage_from_responses([response]), text

    raise ValueError(f"provider {provider} is not wired for preview repair")


def merge_plan_and_repair_usage(primary: dict[str, Any], repair: dict[str, Any]) -> dict[str, Any]:
    return _merge_usage(primary, repair)
