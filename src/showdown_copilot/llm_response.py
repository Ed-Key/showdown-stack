"""Provider response parsing helpers used by dashboard agents."""
from __future__ import annotations

import json
from typing import Any


def parse_jsonish_model_output(text: str) -> Any:
    raw = text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    starts = [index for index in (raw.find("{"), raw.find("[")) if index >= 0]
    if not starts:
        raise ValueError("model did not return JSON")
    start = min(starts)
    end = raw.rfind("}") if raw[start] == "{" else raw.rfind("]")
    if end <= start:
        raise ValueError("model returned malformed JSON")
    try:
        return json.loads(raw[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("model returned malformed JSON") from exc


def response_text(response: dict[str, Any]) -> str:
    output_text = response.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    parts: list[str] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n".join(parts).strip()


def response_incomplete(response: dict[str, Any]) -> bool:
    if response.get("status") == "incomplete":
        return True
    if isinstance(response.get("incomplete_details"), dict):
        return True
    for item in response.get("output") or []:
        if isinstance(item, dict) and item.get("status") == "incomplete":
            return True
    return False


def looks_truncated_text(text: str) -> bool:
    stripped = text.rstrip()
    if not stripped:
        return False
    if stripped.count("**") % 2 == 1:
        return True
    if stripped.count("```") % 2 == 1:
        return True
    last_line = stripped.splitlines()[-1].strip()
    if last_line in {"-", "*"} or last_line.endswith(("**", "`", " to", " with", " by", " because", " and", " or")):
        return True
    return len(stripped) > 400 and stripped[-1] not in ".!?)]\"'"


def response_function_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "function_call":
            continue
        args = item.get("arguments") or "{}"
        if isinstance(args, str):
            try:
                parsed_args = json.loads(args)
            except json.JSONDecodeError:
                parsed_args = {}
        elif isinstance(args, dict):
            parsed_args = args
        else:
            parsed_args = {}
        calls.append({
            "callId": item.get("call_id") or item.get("id"),
            "name": item.get("name"),
            "args": parsed_args,
        })
    return calls


def usage_from_responses(responses: list[dict[str, Any]]) -> dict[str, Any]:
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    reasoning_tokens = 0
    seen_usage = False
    for response in responses:
        usage = response.get("usage")
        if not isinstance(usage, dict):
            continue
        seen_usage = True
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)
        total_tokens += int(usage.get("total_tokens") or 0)
        details = usage.get("output_tokens_details")
        if isinstance(details, dict):
            reasoning_tokens += int(details.get("reasoning_tokens") or 0)
    return {
        "inputTokens": input_tokens if seen_usage else None,
        "outputTokens": output_tokens if seen_usage else None,
        "totalTokens": total_tokens if seen_usage else None,
        "reasoningTokens": reasoning_tokens if seen_usage else None,
        "costUsd": None,
        "note": "OpenAI token usage is recorded when returned; cost is left unset to avoid stale pricing assumptions.",
    }
