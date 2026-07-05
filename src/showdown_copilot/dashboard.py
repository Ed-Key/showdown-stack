"""Local analytics dashboard for Showdown Copilot postmortems."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from .battle_turns import (
    battle_detail as _build_battle_detail,
    prediction_label as _build_prediction_label,
    row_action_label as _build_row_action_label,
    summarize_field_events as _build_summarize_field_events,
    turn_field_events as _build_turn_field_events,
    turn_summary as _build_turn_summary,
)
from .coach_context import (
    build_archive_agent_context as _build_archive_agent_context,
    build_battle_agent_context as _build_battle_agent_context,
    build_coach_brief as _build_coach_brief,
    build_pattern_agent_context as _build_pattern_agent_context,
    coach_diagnosis as _build_coach_diagnosis,
    coach_focus_items as _build_coach_focus_items,
    coach_turn_evidence as _build_coach_turn_evidence,
    coach_turn_recommendation as _build_coach_turn_recommendation,
    coach_turn_score as _build_coach_turn_score,
    coach_turn_title as _build_coach_turn_title,
    compact_pattern_context_for_labeler as _build_compact_pattern_context_for_labeler,
    compact_pattern_context_for_model as _build_compact_pattern_context_for_model,
    review_auto_label_evidence_score as _build_review_auto_label_evidence_score,
)
from .dashboard_agent_prompts import (
    auto_label_output_token_budget as _build_auto_label_output_token_budget,
    coach_final_answer_prompt as _build_coach_final_answer_prompt,
    coach_prompt as _build_coach_prompt,
    coach_synthesis_prompt as _build_coach_synthesis_prompt,
    pattern_output_token_budget as _build_pattern_output_token_budget,
    pattern_prompt as _build_pattern_prompt,
    pattern_synthesis_prompt as _build_pattern_synthesis_prompt,
    review_auto_label_prompt as _build_review_auto_label_prompt,
    review_auto_label_repair_prompt as _build_review_auto_label_repair_prompt,
)
from .dashboard_agent_runtime import (
    coach_agent_metrics as _build_coach_agent_metrics,
    compact_pattern_tool_output_for_model as _build_compact_pattern_tool_output_for_model,
    compact_tool_output_for_model as _build_compact_tool_output_for_model,
    compact_turn_for_model as _build_compact_turn_for_model,
    deterministic_agent_answer as _build_deterministic_agent_answer,
    deterministic_pattern_agent_answer as _build_deterministic_pattern_agent_answer,
    fake_agent_answer as _build_fake_agent_answer,
    fake_pattern_agent_answer as _build_fake_pattern_agent_answer,
    fake_pattern_tool_plan as _build_fake_pattern_tool_plan,
    fake_tool_plan as _build_fake_tool_plan,
    merge_review_label_suggestions as _build_merge_review_label_suggestions,
    normalize_coach_tool_args as _build_normalize_coach_tool_args,
    normalize_pattern_tool_args as _build_normalize_pattern_tool_args,
    normalize_team_tool_args as _build_normalize_team_tool_args,
    normalize_run_mode as _build_normalize_run_mode,
    pattern_agent_metrics as _build_pattern_agent_metrics,
    pattern_tool_output_summary as _build_pattern_tool_output_summary,
    should_run_real_provider as _build_should_run_real_provider,
    tool_output_summary as _build_tool_output_summary,
)
from .dashboard_agent_service import (
    auto_label_pattern as _service_auto_label_pattern,
    coach_agent_run as _service_coach_agent_run,
    coach_agent_run_async as _service_coach_agent_run_async,
    openai_auto_label_pattern as _service_openai_auto_label_pattern,
    openai_coach_agent_run as _service_openai_coach_agent_run,
    openai_pattern_agent_run as _service_openai_pattern_agent_run,
    openai_responses_create as _service_openai_responses_create,
    pattern_agent_run as _service_pattern_agent_run,
    pattern_agent_run_async as _service_pattern_agent_run_async,
    run_coach_tool as _service_run_coach_tool,
    run_pattern_tool as _service_run_pattern_tool,
    run_team_coach_tool as _service_run_team_coach_tool,
    team_coach_agent_run as _service_team_coach_agent_run,
    team_coach_agent_run_async as _service_team_coach_agent_run_async,
    team_coach_brief as _service_team_coach_brief,
    write_coach_agent_trace as _service_write_coach_agent_trace,
)
from .dashboard_config import (
    CoachAIRequest,
    coach_model_presets as _config_coach_model_presets,
    coach_preset as _config_coach_preset,
)
from .dashboard_archive import (
    load_postmortem_by_battle_id as _archive_load_postmortem_by_battle_id,
    summarize_archive as _build_archive_summary,
    summarize_postmortem,
)
from .engine_context import (
    active_pokemon as _build_active_pokemon,
    condition_group as _build_condition_group,
    field_state_context as _build_field_state_context,
    find_replay_record_for_turn as _build_find_replay_record_for_turn,
    hp_pct as _build_hp_pct,
    load_engine_replay_records as _build_load_engine_replay_records,
    nonzero_conditions as _build_nonzero_conditions,
    pokemon_context as _build_pokemon_context,
    request_state_from_replay as _build_request_state_from_replay,
    strategic_signals as _build_strategic_signals,
)
from .engine_eval_cases import (
    enrich_engine_eval_cases_with_replay,
    prioritize_engine_eval_cases,
)
from .llm_response import (
    looks_truncated_text,
    parse_jsonish_model_output,
    response_function_calls,
    response_incomplete,
    response_text,
    usage_from_responses,
)
from .pattern_panels import (
    PATTERN_PANEL_DEFINITIONS,
    build_pattern_panels,
    card_matches_pattern as _build_card_matches_pattern,
    pattern_panel_level as _build_pattern_panel_level,
)
from .review_cards import (
    confidence_tier as _build_confidence_tier,
    decision_review_queue as _build_decision_review_queue,
    review_card_evidence as _build_review_card_evidence,
    review_card_shape as _build_review_card_shape,
    review_card_tags as _build_review_card_tags,
)
from .review_workflow import (
    REVIEW_LABEL_DEFINITIONS,
    REVIEW_LABEL_DEFINITIONS_BY_ID,
    ReviewLabelRequest,
    build_engine_eval_cases,
    decorate_review_label as _decorate_review_label,
    engine_eval_case_summary,
    load_review_labels as _load_review_labels,
    persist_review_label,
    review_label_summary as _review_label_summary,
)

DEFAULT_POSTMORTEM_DIR = Path(
    "/Users/edkiboma/Projects/pokemon-ai/workspace/analysis/battle-postmortems"
)
POSTMORTEM_DIR = Path(
    os.environ.get("SHOWDOWN_COPILOT_POSTMORTEM_DIR", str(DEFAULT_POSTMORTEM_DIR))
)
DEFAULT_REPLAY_DIR = Path(
    "/Users/edkiboma/Projects/pokemon-ai/workspace/analysis/engine-replay"
)
REPLAY_DIR = Path(os.environ.get("SHOWDOWN_COPILOT_REPLAY_DIR", str(DEFAULT_REPLAY_DIR)))
DEFAULT_AGENT_TRACE_DIR = Path(
    "/Users/edkiboma/Projects/pokemon-ai/workspace/analysis/coach-agent-runs"
)
AGENT_TRACE_DIR = Path(
    os.environ.get("SHOWDOWN_COPILOT_AGENT_TRACE_DIR", str(DEFAULT_AGENT_TRACE_DIR))
)
DEFAULT_MIN_SCHEMA_VERSION = 7

router = APIRouter()


def _pattern_panel_level(instance_count: int, battle_count: int) -> dict[str, str]:
    return _build_pattern_panel_level(instance_count, battle_count)


def _card_matches_pattern(card: dict[str, Any], pattern_id: str) -> bool:
    return _build_card_matches_pattern(card, pattern_id)


def _archive_pattern_panels(
    battles: list[dict[str, Any]],
    postmortems_by_battle_id: dict[str, dict[str, Any]],
    evidence_limit: int = 8,
    review_labels: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    return build_pattern_panels(
        battles,
        postmortems_by_battle_id,
        turn_summary_builder=_turn_summary,
        evidence_limit=evidence_limit,
        review_labels=review_labels,
    )


def _summarize_archive(
    directory: Path = POSTMORTEM_DIR,
    min_schema_version: int | None = DEFAULT_MIN_SCHEMA_VERSION,
    pattern_evidence_limit: int = 8,
    review_labels: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return _build_archive_summary(
        directory,
        min_schema_version,
        pattern_evidence_limit=pattern_evidence_limit,
        review_labels=review_labels,
        pattern_panel_builder=_archive_pattern_panels,
        review_label_definitions=REVIEW_LABEL_DEFINITIONS,
        review_label_summary=_review_label_summary,
    )


def _load_postmortem_by_battle_id(
    battle_id: str,
    directory: Path = POSTMORTEM_DIR,
) -> dict[str, Any] | None:
    return _archive_load_postmortem_by_battle_id(battle_id, directory)


def _row_action_label(action: Any) -> str:
    return _build_row_action_label(action)


def _prediction_label(value: Any) -> str:
    return _build_prediction_label(value)


def _turn_field_events(row: dict[str, Any]) -> list[dict[str, Any]]:
    return _build_turn_field_events(row)


def _summarize_field_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _build_summarize_field_events(events)


def _turn_summary(row: dict[str, Any]) -> dict[str, Any]:
    return _build_turn_summary(row)


def _battle_detail(battle_id: str, directory: Path = POSTMORTEM_DIR) -> dict[str, Any]:
    detail = _build_battle_detail(battle_id, directory)
    if detail is not None:
        return detail
    raise HTTPException(status_code=404, detail=f"unknown battleId={battle_id}")


def _load_engine_replay_records(
    battle_id: str,
    directory: Path = REPLAY_DIR,
) -> list[dict[str, Any]]:
    return _build_load_engine_replay_records(battle_id, directory)


def _request_state_from_replay(record: dict[str, Any]) -> dict[str, Any] | None:
    return _build_request_state_from_replay(record)


def _find_replay_record_for_turn(
    records: list[dict[str, Any]],
    turn: Any,
    pick_name: Any = None,
) -> dict[str, Any] | None:
    return _build_find_replay_record_for_turn(records, turn, pick_name)


def _active_pokemon(side: dict[str, Any]) -> dict[str, Any] | None:
    return _build_active_pokemon(side)


def _hp_pct(mon: dict[str, Any]) -> float | None:
    return _build_hp_pct(mon)


def _pokemon_context(mon: dict[str, Any] | None) -> dict[str, Any] | None:
    return _build_pokemon_context(mon)


def _nonzero_conditions(side: dict[str, Any]) -> dict[str, Any]:
    return _build_nonzero_conditions(side)


def _condition_group(conditions: dict[str, Any], names: set[str]) -> dict[str, Any]:
    return _build_condition_group(conditions, names)


def _field_state_context(record: dict[str, Any] | None) -> dict[str, Any] | None:
    return _build_field_state_context(record)


def _strategic_signals(
    row: dict[str, Any],
    turn: dict[str, Any],
    field_state: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    return _build_strategic_signals(row, turn, field_state)


def _confidence_tier(confidence: Any) -> str:
    return _build_confidence_tier(confidence)


def _review_card_tags(turn: dict[str, Any], confidence_tier: str) -> list[str]:
    return _build_review_card_tags(turn, confidence_tier)


def _review_card_evidence(turn: dict[str, Any]) -> list[str]:
    return _build_review_card_evidence(turn)


def _review_card_shape(
    turn: dict[str, Any],
) -> tuple[str, str, str, int, str, str]:
    return _build_review_card_shape(turn)


def _decision_review_queue(
    battle_id: str,
    turns: list[dict[str, Any]],
    limit: int | None = None,
) -> list[dict[str, Any]]:
    return _build_decision_review_queue(battle_id, turns, limit=limit)


def _battle_agent_context(
    battle_id: str,
    directory: Path = POSTMORTEM_DIR,
) -> dict[str, Any]:
    context = _build_battle_agent_context(
        battle_id,
        postmortem_dir=directory,
        replay_dir=REPLAY_DIR,
    )
    if context is None:
        raise HTTPException(status_code=404, detail=f"unknown battleId={battle_id}")
    return context


def _archive_agent_context(
    min_schema_version: int | None = DEFAULT_MIN_SCHEMA_VERSION,
    directory: Path = POSTMORTEM_DIR,
    review_labels: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    archive = _summarize_archive(
        directory=directory,
        min_schema_version=min_schema_version,
        review_labels=review_labels,
    )
    return _build_archive_agent_context(archive)


def _team_coach_brief(
    battle_id: str,
    directory: Path = POSTMORTEM_DIR,
) -> dict[str, Any]:
    return _service_team_coach_brief(
        battle_id,
        postmortem_dir=directory,
        replay_dir=REPLAY_DIR,
    )


def _pattern_agent_context(
    pattern_id: str,
    directory: Path = POSTMORTEM_DIR,
    min_schema_version: int | None = DEFAULT_MIN_SCHEMA_VERSION,
    review_labels: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    archive = _summarize_archive(
        directory=directory,
        min_schema_version=min_schema_version,
        pattern_evidence_limit=24,
        review_labels=review_labels,
    )
    context = _build_pattern_agent_context(pattern_id, archive)
    if context is None:
        raise HTTPException(status_code=404, detail=f"unknown patternId={pattern_id}")
    return context


def _compact_pattern_context_for_model(context: dict[str, Any]) -> dict[str, Any]:
    return _build_compact_pattern_context_for_model(context)


def _review_auto_label_evidence_score(evidence: dict[str, Any]) -> int:
    return _build_review_auto_label_evidence_score(evidence)


def _compact_pattern_context_for_labeler(
    context: dict[str, Any],
    limit: int | None = None,
) -> dict[str, Any]:
    if limit is None:
        limit = int(os.environ.get("SHOWDOWN_OPENAI_AUTO_LABEL_CARD_LIMIT", "50"))
    return _build_compact_pattern_context_for_labeler(
        context,
        label_definitions=REVIEW_LABEL_DEFINITIONS,
        limit=limit,
    )


def _pattern_output_token_budget(max_output_tokens: int) -> int:
    return _build_pattern_output_token_budget(max_output_tokens)


def _auto_label_output_token_budget(max_output_tokens: int) -> int:
    return _build_auto_label_output_token_budget(max_output_tokens)


def _coach_turn_score(turn: dict[str, Any]) -> int:
    return _build_coach_turn_score(turn)


def _coach_turn_title(turn: dict[str, Any]) -> str:
    return _build_coach_turn_title(turn)


def _coach_turn_recommendation(turn: dict[str, Any]) -> str:
    return _build_coach_turn_recommendation(turn)


def _coach_turn_evidence(turn: dict[str, Any]) -> list[str]:
    return _build_coach_turn_evidence(turn)


def _coach_focus_items(context: dict[str, Any]) -> list[dict[str, str]]:
    return _build_coach_focus_items(context)


def _coach_diagnosis(context: dict[str, Any]) -> list[dict[str, str]]:
    return _build_coach_diagnosis(context)


def _coach_brief(
    battle_id: str,
    directory: Path = POSTMORTEM_DIR,
) -> dict[str, Any]:
    context = _battle_agent_context(battle_id, directory)
    return _build_coach_brief(context)


def _coach_model_presets() -> list[dict[str, Any]]:
    return _config_coach_model_presets()


def _coach_preset(preset_id: str) -> dict[str, Any]:
    try:
        return _config_coach_preset(preset_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _coach_prompt(battle_id: str, preset: dict[str, Any]) -> str:
    return _build_coach_prompt(battle_id, preset)


def _coach_final_answer_prompt(battle_id: str, preset: dict[str, Any], tool_calls: list[dict[str, Any]]) -> str:
    return _build_coach_final_answer_prompt(battle_id, preset, tool_calls)


def _coach_synthesis_prompt(
    battle_id: str,
    preset: dict[str, Any],
    tool_context: list[dict[str, Any]],
) -> str:
    return _build_coach_synthesis_prompt(battle_id, preset, tool_context)


def _pattern_prompt(pattern_id: str, preset: dict[str, Any]) -> str:
    return _build_pattern_prompt(pattern_id, preset)


def _pattern_synthesis_prompt(
    pattern_id: str,
    preset: dict[str, Any],
    tool_context: list[dict[str, Any]],
) -> str:
    return _build_pattern_synthesis_prompt(pattern_id, preset, tool_context)


def _review_auto_label_prompt(pattern_id: str, preset: dict[str, Any], context: dict[str, Any]) -> str:
    return _build_review_auto_label_prompt(pattern_id, preset, context)


def _review_auto_label_repair_prompt(
    pattern_id: str,
    preset: dict[str, Any],
    context: dict[str, Any],
    malformed_text: str,
) -> str:
    return _build_review_auto_label_repair_prompt(pattern_id, preset, context, malformed_text)


def _parse_jsonish_model_output(text: str) -> Any:
    try:
        return parse_jsonish_model_output(text)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _normalize_coach_tool_args(name: str, args: dict[str, Any], battle_id: str) -> dict[str, Any]:
    return _build_normalize_coach_tool_args(name, args, battle_id)


def _normalize_pattern_tool_args(name: str, args: dict[str, Any], pattern_id: str) -> dict[str, Any]:
    return _build_normalize_pattern_tool_args(name, args, pattern_id)


def _normalize_team_tool_args(name: str, args: dict[str, Any], battle_id: str) -> dict[str, Any]:
    return _build_normalize_team_tool_args(name, args, battle_id)


def _compact_turn_for_model(turn: dict[str, Any]) -> dict[str, Any]:
    return _build_compact_turn_for_model(turn)


def _compact_tool_output_for_model(name: str, output: dict[str, Any]) -> dict[str, Any]:
    return _build_compact_tool_output_for_model(name, output)


def _compact_pattern_tool_output_for_model(name: str, output: dict[str, Any]) -> dict[str, Any]:
    return _build_compact_pattern_tool_output_for_model(name, output)


def _tool_output_summary(name: str, output: dict[str, Any]) -> str:
    return _build_tool_output_summary(name, output)


def _pattern_tool_output_summary(name: str, output: dict[str, Any]) -> str:
    return _build_pattern_tool_output_summary(name, output)


def _run_coach_tool(
    name: str,
    args: dict[str, Any],
    directory: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return _service_run_coach_tool(
        name,
        args,
        postmortem_dir=directory,
        replay_dir=REPLAY_DIR,
    )


def _run_pattern_tool(
    name: str,
    args: dict[str, Any],
    directory: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return _service_run_pattern_tool(
        name,
        args,
        postmortem_dir=directory,
    )


def _run_team_coach_tool(
    name: str,
    args: dict[str, Any],
    directory: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return _service_run_team_coach_tool(
        name,
        args,
        postmortem_dir=directory,
        replay_dir=REPLAY_DIR,
    )


def _fake_tool_plan(preset: dict[str, Any], battle_id: str) -> list[dict[str, Any]]:
    return _build_fake_tool_plan(preset, battle_id)


def _fake_pattern_tool_plan(preset: dict[str, Any], pattern_id: str) -> list[dict[str, Any]]:
    return _build_fake_pattern_tool_plan(preset, pattern_id)


def _fake_agent_answer(
    preset: dict[str, Any],
    brief: dict[str, Any],
    battle_context: dict[str, Any] | None,
    archive_context: dict[str, Any] | None,
) -> str:
    return _build_fake_agent_answer(preset, brief, battle_context, archive_context)


def _fake_pattern_agent_answer(
    preset: dict[str, Any],
    pattern_context: dict[str, Any],
    archive_context: dict[str, Any] | None,
) -> str:
    return _build_fake_pattern_agent_answer(preset, pattern_context, archive_context)


def _deterministic_pattern_agent_answer(
    preset: dict[str, Any],
    pattern_context: dict[str, Any],
    archive_context: dict[str, Any] | None,
) -> str:
    return _build_deterministic_pattern_agent_answer(preset, pattern_context, archive_context)


def _deterministic_agent_answer(
    preset: dict[str, Any],
    brief: dict[str, Any],
    battle_context: dict[str, Any] | None,
    archive_context: dict[str, Any] | None,
) -> str:
    return _build_deterministic_agent_answer(preset, brief, battle_context, archive_context)


def _pattern_agent_metrics(
    pattern_context: dict[str, Any],
    tool_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    return _build_pattern_agent_metrics(pattern_context, tool_calls)


def _coach_agent_metrics(
    brief: dict[str, Any],
    tool_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    return _build_coach_agent_metrics(brief, tool_calls)


def _write_coach_agent_trace(run: dict[str, Any], directory: Path = AGENT_TRACE_DIR) -> None:
    _service_write_coach_agent_trace(run, directory)


def _normalize_run_mode(run_mode: str | None) -> str:
    return _build_normalize_run_mode(run_mode)


def _should_run_real_provider(preset: dict[str, Any], run_mode: str) -> bool:
    return _build_should_run_real_provider(preset, run_mode)


def _response_text(response: dict[str, Any]) -> str:
    return response_text(response)


def _response_incomplete(response: dict[str, Any]) -> bool:
    return response_incomplete(response)


def _looks_truncated_text(text: str) -> bool:
    return looks_truncated_text(text)


def _response_function_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    return response_function_calls(response)


def _usage_from_responses(responses: list[dict[str, Any]]) -> dict[str, Any]:
    return usage_from_responses(responses)


async def _openai_responses_create(
    payload: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    return await _service_openai_responses_create(payload, timeout_seconds)


async def _openai_coach_agent_run(
    battle_id: str,
    preset: dict[str, Any],
    directory: Path,
    trace_directory: Path,
) -> dict[str, Any]:
    return await _service_openai_coach_agent_run(
        battle_id,
        preset,
        postmortem_dir=directory,
        replay_dir=REPLAY_DIR,
        trace_dir=trace_directory,
    )


def _coach_agent_run(
    battle_id: str,
    preset_id: str,
    directory: Path = POSTMORTEM_DIR,
    trace_directory: Path = AGENT_TRACE_DIR,
    run_mode: str = "fake",
) -> dict[str, Any]:
    return _service_coach_agent_run(
        battle_id,
        preset_id,
        postmortem_dir=directory,
        replay_dir=REPLAY_DIR,
        trace_dir=trace_directory,
    )


async def _coach_agent_run_async(
    battle_id: str,
    preset_id: str,
    directory: Path = POSTMORTEM_DIR,
    trace_directory: Path = AGENT_TRACE_DIR,
    run_mode: str = "fake",
    event_sink: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    return await _service_coach_agent_run_async(
        battle_id,
        preset_id,
        postmortem_dir=directory,
        replay_dir=REPLAY_DIR,
        trace_dir=trace_directory,
        run_mode=run_mode,
        event_sink=event_sink,
    )


def _team_coach_agent_run(
    battle_id: str,
    preset_id: str,
    directory: Path = POSTMORTEM_DIR,
    trace_directory: Path = AGENT_TRACE_DIR,
    run_mode: str = "fake",
) -> dict[str, Any]:
    return _service_team_coach_agent_run(
        battle_id,
        preset_id,
        postmortem_dir=directory,
        replay_dir=REPLAY_DIR,
        trace_dir=trace_directory,
    )


async def _team_coach_agent_run_async(
    battle_id: str,
    preset_id: str,
    directory: Path = POSTMORTEM_DIR,
    trace_directory: Path = AGENT_TRACE_DIR,
    run_mode: str = "fake",
    event_sink: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    return await _service_team_coach_agent_run_async(
        battle_id,
        preset_id,
        postmortem_dir=directory,
        replay_dir=REPLAY_DIR,
        trace_dir=trace_directory,
        run_mode=run_mode,
        event_sink=event_sink,
    )


async def _openai_pattern_agent_run(
    pattern_id: str,
    preset: dict[str, Any],
    directory: Path,
    trace_directory: Path,
) -> dict[str, Any]:
    return await _service_openai_pattern_agent_run(
        pattern_id,
        preset,
        postmortem_dir=directory,
        trace_dir=trace_directory,
    )


def _pattern_agent_run(
    pattern_id: str,
    preset_id: str,
    directory: Path = POSTMORTEM_DIR,
    trace_directory: Path = AGENT_TRACE_DIR,
    run_mode: str = "fake",
) -> dict[str, Any]:
    return _service_pattern_agent_run(
        pattern_id,
        preset_id,
        postmortem_dir=directory,
        trace_dir=trace_directory,
    )


async def _pattern_agent_run_async(
    pattern_id: str,
    preset_id: str,
    directory: Path = POSTMORTEM_DIR,
    trace_directory: Path = AGENT_TRACE_DIR,
    run_mode: str = "fake",
) -> dict[str, Any]:
    return await _service_pattern_agent_run_async(
        pattern_id,
        preset_id,
        postmortem_dir=directory,
        trace_dir=trace_directory,
        run_mode=run_mode,
    )


def _pattern_review_card_exists(
    pattern_id: str,
    battle_id: str,
    turn: int,
    force_switch: bool,
    directory: Path = POSTMORTEM_DIR,
) -> bool:
    context = _pattern_agent_context(
        pattern_id,
        directory=directory,
        review_labels={},
    )
    for item in context.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        if (
            item.get("battleId") == battle_id
            and item.get("turn") == turn
            and bool(item.get("forceSwitch")) is bool(force_switch)
        ):
            return True
    return False


def _save_review_label(
    request: ReviewLabelRequest,
    directory: Path = POSTMORTEM_DIR,
    path: Path | None = None,
) -> dict[str, Any]:
    pattern_ids = {pattern["id"] for pattern in PATTERN_PANEL_DEFINITIONS}
    if request.patternId not in pattern_ids:
        raise HTTPException(status_code=400, detail=f"unknown patternId={request.patternId}")
    if request.label != "unreviewed" and request.label not in REVIEW_LABEL_DEFINITIONS_BY_ID:
        raise HTTPException(status_code=400, detail=f"unknown review label: {request.label}")
    if not _pattern_review_card_exists(
        request.patternId,
        request.battleId,
        request.turn,
        request.forceSwitch,
        directory=directory,
    ):
        raise HTTPException(status_code=404, detail="review card is not present in the selected pattern evidence")

    if path is not None:
        return persist_review_label(request, path=path)
    return persist_review_label(request)


def _engine_eval_case_archive(
    directory: Path = POSTMORTEM_DIR,
    min_schema_version: int | None = DEFAULT_MIN_SCHEMA_VERSION,
) -> dict[str, Any]:
    labels = _load_review_labels()
    archive = _summarize_archive(
        directory=directory,
        min_schema_version=min_schema_version,
        pattern_evidence_limit=200,
        review_labels=labels,
    )
    cases = build_engine_eval_cases(archive.get("patternPanels") or [])
    cases = enrich_engine_eval_cases_with_replay(cases, REPLAY_DIR)
    cases = prioritize_engine_eval_cases(cases)
    return {
        "summary": engine_eval_case_summary(cases),
        "cases": cases,
        "labelSummary": (archive.get("reviewLabels") or {}).get("summary"),
        "generatedAt": archive.get("generatedAt"),
        "sourceDir": archive.get("sourceDir"),
    }


def _merge_review_label_suggestions(
    primary: list[dict[str, Any]],
    fallback: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return _build_merge_review_label_suggestions(primary, fallback)


async def _openai_auto_label_pattern(
    pattern_id: str,
    preset: dict[str, Any],
    pattern_context: dict[str, Any],
) -> dict[str, Any]:
    return await _service_openai_auto_label_pattern(pattern_id, preset, pattern_context)


async def _auto_label_pattern(
    pattern_id: str,
    request: CoachAIRequest | None = None,
    directory: Path = POSTMORTEM_DIR,
) -> dict[str, Any]:
    return await _service_auto_label_pattern(
        pattern_id,
        request,
        postmortem_dir=directory,
    )


@router.get("/dashboard")
async def dashboard_page() -> RedirectResponse:
    dashboard_web_url = os.environ.get("SHOWDOWN_DASHBOARD_WEB_URL", "http://127.0.0.1:5174/")
    return RedirectResponse(dashboard_web_url, status_code=307)


@router.get("/dashboard/data")
async def dashboard_data(
    min_schema_version: int | None = Query(DEFAULT_MIN_SCHEMA_VERSION, ge=0),
) -> JSONResponse:
    return JSONResponse(_summarize_archive(
        min_schema_version=min_schema_version,
        review_labels=_load_review_labels(),
    ))


@router.get("/dashboard/review-labels")
async def dashboard_review_labels() -> JSONResponse:
    labels = _load_review_labels()
    return JSONResponse({
        "definitions": REVIEW_LABEL_DEFINITIONS,
        "labels": {
            key: _decorate_review_label(value)
            for key, value in labels.items()
            if _decorate_review_label(value)
        },
        "summary": _review_label_summary(list(labels.values())),
    })


@router.post("/dashboard/review-labels")
async def dashboard_save_review_label(request: ReviewLabelRequest) -> JSONResponse:
    return JSONResponse(_save_review_label(request))


@router.post("/dashboard/pattern-auto-label/{pattern_id}")
async def dashboard_pattern_auto_label(pattern_id: str, request: CoachAIRequest) -> JSONResponse:
    return JSONResponse(await _auto_label_pattern(pattern_id, request))


@router.get("/dashboard/engine-eval-cases")
async def dashboard_engine_eval_cases(
    min_schema_version: int | None = Query(DEFAULT_MIN_SCHEMA_VERSION, ge=0),
) -> JSONResponse:
    return JSONResponse(_engine_eval_case_archive(min_schema_version=min_schema_version))


@router.get("/dashboard/battles/{battle_id}")
async def dashboard_battle_detail(battle_id: str) -> JSONResponse:
    return JSONResponse(_battle_detail(battle_id))


@router.get("/dashboard/agent-context")
async def dashboard_agent_context(
    min_schema_version: int | None = Query(DEFAULT_MIN_SCHEMA_VERSION, ge=0),
) -> JSONResponse:
    return JSONResponse(_archive_agent_context(
        min_schema_version=min_schema_version,
        review_labels=_load_review_labels(),
    ))


@router.get("/dashboard/agent-context/{battle_id}")
async def dashboard_battle_agent_context(battle_id: str) -> JSONResponse:
    return JSONResponse(_battle_agent_context(battle_id))


@router.get("/dashboard/coach/{battle_id}")
async def dashboard_coach_brief(battle_id: str) -> JSONResponse:
    return JSONResponse(_coach_brief(battle_id))


@router.get("/dashboard/team-coach/{battle_id}")
async def dashboard_team_coach_brief(battle_id: str) -> JSONResponse:
    return JSONResponse(_team_coach_brief(battle_id))


@router.get("/dashboard/coach-ai/presets")
async def dashboard_coach_ai_presets() -> JSONResponse:
    return JSONResponse({"presets": _coach_model_presets()})


@router.post("/dashboard/coach-ai/{battle_id}")
async def dashboard_coach_ai_run(
    battle_id: str,
    request: CoachAIRequest,
) -> JSONResponse:
    return JSONResponse(await _coach_agent_run_async(
        battle_id,
        request.presetId,
        run_mode=request.runMode,
    ))


@router.get("/dashboard/coach-ai/{battle_id}/stream")
async def dashboard_coach_ai_stream(
    battle_id: str,
    presetId: str = Query("openai-gpt-54-mini-balanced"),
    runMode: str = Query("fake"),
) -> StreamingResponse:
    async def event_stream():
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def emit(event: dict[str, Any]) -> None:
            await queue.put(event)

        async def run_agent() -> None:
            try:
                run = await _coach_agent_run_async(
                    battle_id,
                    presetId,
                    run_mode=runMode,
                    event_sink=emit,
                )
                await queue.put({"type": "completed", "run": run})
            except HTTPException as exc:
                await queue.put({
                    "type": "error",
                    "message": str(exc.detail),
                    "statusCode": exc.status_code,
                })
            except Exception as exc:  # noqa: BLE001 - stream errors must reach the UI.
                await queue.put({"type": "error", "message": str(exc)})
            finally:
                await queue.put(None)

        task = asyncio.create_task(run_agent())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield _sse_payload(event)
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/dashboard/team-coach-ai/{battle_id}")
async def dashboard_team_coach_ai_run(
    battle_id: str,
    request: CoachAIRequest,
) -> JSONResponse:
    return JSONResponse(await _team_coach_agent_run_async(
        battle_id,
        request.presetId,
        run_mode=request.runMode,
    ))


def _sse_payload(event: dict[str, Any]) -> str:
    event_type = str(event.get("type") or "message")
    return f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.get("/dashboard/team-coach-ai/{battle_id}/stream")
async def dashboard_team_coach_ai_stream(
    battle_id: str,
    presetId: str = Query("openai-gpt-54-mini-balanced"),
    runMode: str = Query("fake"),
) -> StreamingResponse:
    async def event_stream():
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def emit(event: dict[str, Any]) -> None:
            await queue.put(event)

        async def run_agent() -> None:
            try:
                run = await _team_coach_agent_run_async(
                    battle_id,
                    presetId,
                    run_mode=runMode,
                    event_sink=emit,
                )
                await queue.put({"type": "completed", "run": run})
            except HTTPException as exc:
                await queue.put({
                    "type": "error",
                    "message": str(exc.detail),
                    "statusCode": exc.status_code,
                })
            except Exception as exc:  # noqa: BLE001 - stream errors must reach the UI.
                await queue.put({"type": "error", "message": str(exc)})
            finally:
                await queue.put(None)

        task = asyncio.create_task(run_agent())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield _sse_payload(event)
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/dashboard/pattern-ai/{pattern_id}")
async def dashboard_pattern_ai_run(
    pattern_id: str,
    request: CoachAIRequest,
) -> JSONResponse:
    return JSONResponse(await _pattern_agent_run_async(
        pattern_id,
        request.presetId,
        run_mode=request.runMode,
    ))
