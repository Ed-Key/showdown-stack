"""Provider-backed agent orchestration for the dashboard."""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
from fastapi import HTTPException

from .battle_turns import turn_summary
from .coach_context import (
    build_archive_agent_context,
    build_battle_agent_context,
    build_coach_brief,
    build_pattern_agent_context,
    compact_pattern_context_for_labeler,
    compact_pattern_context_for_model,
)
from .dashboard_agent_prompts import (
    ANTHROPIC_TEAM_COACH_AI_SYSTEM_PROMPT,
    ANTHROPIC_TEAM_COACH_TOOL_DESCRIPTIONS,
    AUTO_LABEL_REASONING_EFFORT,
    COACH_AI_SYSTEM_PROMPT,
    OPENAI_COACH_TOOLS,
    OPENAI_TEAM_COACH_TOOLS,
    PATTERN_AI_SYSTEM_PROMPT,
    REVIEW_AUTO_LABEL_RESPONSE_FORMAT,
    REVIEW_AUTO_LABEL_SYSTEM_PROMPT,
    SYNTHESIS_MAX_OUTPUT_TOKENS,
    TEAM_COACH_AI_SYSTEM_PROMPT,
    auto_label_output_token_budget,
    anthropic_team_coach_prompt,
    anthropic_team_coach_synthesis_prompt,
    coach_final_answer_prompt,
    coach_prompt,
    coach_synthesis_prompt,
    pattern_output_token_budget,
    pattern_synthesis_prompt,
    review_auto_label_prompt,
    review_auto_label_repair_prompt,
    team_coach_final_answer_prompt,
    team_coach_prompt,
    team_coach_synthesis_prompt,
)
from .dashboard_agent_runtime import (
    coach_agent_metrics,
    compact_pattern_tool_output_for_model,
    compact_tool_output_for_model,
    deterministic_agent_answer,
    deterministic_pattern_agent_answer,
    deterministic_team_agent_answer,
    fake_agent_answer,
    fake_pattern_agent_answer,
    fake_pattern_tool_plan,
    fake_team_agent_answer,
    fake_team_tool_plan,
    fake_tool_plan,
    merge_review_label_suggestions,
    normalize_coach_tool_args,
    normalize_team_tool_args,
    normalize_run_mode,
    pattern_agent_metrics,
    pattern_tool_output_summary,
    should_run_real_provider,
    team_agent_metrics,
    tool_output_summary,
)
from .dashboard_archive import _timestamp_label, load_postmortems_by_battle_id, summarize_archive
from .dashboard_config import CoachAIRequest, coach_preset
from .engine_context import (
    find_replay_record_for_turn,
    load_engine_replay_records,
    request_state_from_replay,
)
from .engine_eval_cases import enrich_engine_eval_cases_with_replay, prioritize_engine_eval_cases
from .llm_response import (
    looks_truncated_text,
    parse_jsonish_model_output,
    response_function_calls,
    response_incomplete,
    response_text,
    usage_from_responses,
)
from .pattern_panels import build_pattern_panels
from .review_workflow import (
    REVIEW_LABEL_DEFINITIONS,
    build_engine_eval_cases,
    load_review_labels,
    normalize_ai_review_label_suggestions,
    persist_review_label_suggestions,
    review_label_summary,
    suggest_review_labels_for_pattern,
)
from .team_coach import build_team_coach_brief

DEFAULT_MIN_SCHEMA_VERSION = 7
TOOL_OUTPUT_EVENT_PREVIEW_CHARS = 12_000
AgentEventSink = Callable[[dict[str, Any]], Awaitable[None]]


def _load_local_env_file() -> None:
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    ]
    for env_path in candidates:
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip().removeprefix("export ").strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_local_env_file()


async def _emit_agent_event(
    event_sink: AgentEventSink | None,
    event: dict[str, Any],
) -> None:
    if event_sink is not None:
        await event_sink(event)


def _tool_output_event_payload(output: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(output, ensure_ascii=False, indent=2)
    truncated = len(text) > TOOL_OUTPUT_EVENT_PREVIEW_CHARS
    if truncated:
        text = text[:TOOL_OUTPUT_EVENT_PREVIEW_CHARS] + "\n... truncated"
    return {
        "toolOutputPreview": text,
        "toolOutputBytes": len(json.dumps(output, ensure_ascii=False)),
        "toolOutputTruncated": truncated,
    }


def _coach_preset(preset_id: str) -> dict[str, Any]:
    try:
        return coach_preset(preset_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _archive_pattern_panels(
    battles: list[dict[str, Any]],
    postmortems_by_battle_id: dict[str, dict[str, Any]],
    evidence_limit: int = 8,
    review_labels: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    return build_pattern_panels(
        battles,
        postmortems_by_battle_id,
        turn_summary_builder=turn_summary,
        evidence_limit=evidence_limit,
        review_labels=review_labels,
    )


def _summarize_archive(
    directory: Path,
    *,
    min_schema_version: int | None = DEFAULT_MIN_SCHEMA_VERSION,
    pattern_evidence_limit: int = 8,
    review_labels: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return summarize_archive(
        directory,
        min_schema_version,
        pattern_evidence_limit=pattern_evidence_limit,
        review_labels=review_labels,
        pattern_panel_builder=_archive_pattern_panels,
        review_label_definitions=REVIEW_LABEL_DEFINITIONS,
        review_label_summary=review_label_summary,
    )


def _battle_agent_context(
    battle_id: str,
    *,
    postmortem_dir: Path,
    replay_dir: Path,
) -> dict[str, Any]:
    context = build_battle_agent_context(
        battle_id,
        postmortem_dir=postmortem_dir,
        replay_dir=replay_dir,
    )
    if context is None:
        raise HTTPException(status_code=404, detail=f"unknown battleId={battle_id}")
    return context


def _archive_agent_context(
    *,
    postmortem_dir: Path,
    review_labels: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    archive = _summarize_archive(
        postmortem_dir,
        review_labels=review_labels,
    )
    return build_archive_agent_context(archive)


def _team_coach_brief(
    battle_id: str,
    *,
    postmortem_dir: Path,
    replay_dir: Path,
    evidence_limit: int = 8,
) -> dict[str, Any]:
    archive = _summarize_archive(
        postmortem_dir,
        pattern_evidence_limit=200,
        review_labels=load_review_labels(),
    )
    battle = next(
        (
            item for item in (archive.get("battles") or [])
            if isinstance(item, dict) and item.get("battleId") == battle_id
        ),
        None,
    )
    if not battle:
        raise HTTPException(status_code=404, detail=f"unknown battleId={battle_id}")
    postmortems = load_postmortems_by_battle_id(
        postmortem_dir,
        DEFAULT_MIN_SCHEMA_VERSION,
    )
    engine_cases = build_engine_eval_cases(archive.get("patternPanels") or [])
    engine_cases = enrich_engine_eval_cases_with_replay(engine_cases, replay_dir)
    engine_eval_archive = {"cases": prioritize_engine_eval_cases(engine_cases)}
    team_key = " / ".join(str(item) for item in (battle.get("team") or []) if item)
    return build_team_coach_brief(
        archive,
        postmortems,
        engine_eval_archive=engine_eval_archive,
        team_key=team_key,
        evidence_limit=evidence_limit,
    )


def team_coach_brief(
    battle_id: str,
    *,
    postmortem_dir: Path,
    replay_dir: Path,
) -> dict[str, Any]:
    return _team_coach_brief(
        battle_id,
        postmortem_dir=postmortem_dir,
        replay_dir=replay_dir,
    )


def _norm_key(value: Any) -> str:
    return "".join(char for char in str(value or "").lower() if char.isalnum())


def _clamp_int(value: Any, default: int, lower: int, upper: int) -> int:
    if not isinstance(value, int):
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = default
    return max(lower, min(upper, value))


def _bucket_summary_without_examples(bucket: dict[str, Any] | None) -> dict[str, Any]:
    bucket = bucket or {}
    return {
        "count": bucket.get("count") or 0,
        "byResult": bucket.get("byResult") or {},
    }


def _compact_team_pokemon_profile(mon: dict[str, Any]) -> dict[str, Any]:
    return {
        "species": mon.get("species"),
        "battles": mon.get("battles"),
        "winRate": mon.get("winRate"),
        "leadRate": mon.get("leadRate"),
        "leadWinRate": mon.get("leadWinRate"),
        "survivalRate": mon.get("survivalRate"),
        "winWhenAlive": mon.get("winWhenAlive"),
        "avgFaintTurn": mon.get("avgFaintTurn"),
        "koShare": mon.get("koShare"),
        "avgDamageTakenPct": mon.get("avgDamageTakenPct"),
        "avgDamageDealtPct": mon.get("avgDamageDealtPct"),
        "fieldPressure": mon.get("fieldPressureBucket"),
        "engineDisagreements": mon.get("engineDisagreements"),
        "highConfidenceDisagreements": mon.get("highConfidenceDisagreements"),
        "engineWantedSwitchIntoCount": mon.get("engineWantedSwitchIntoCount"),
        "engineWantedSwitchOutCount": mon.get("engineWantedSwitchOutCount"),
    }


def _compact_team_priority(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "battleId": case.get("battleId"),
        "turn": case.get("turn"),
        "forceSwitch": bool(case.get("forceSwitch")),
        "opponent": case.get("opponent"),
        "result": case.get("result"),
        "engineAction": case.get("engineAction"),
        "actualAction": case.get("actualAction"),
        "confidence": case.get("confidence"),
        "pvMatchedReality": case.get("pvMatchedReality"),
        "fieldEventSummary": (case.get("fieldEventSummary") or [])[:2],
        "reason": case.get("reason"),
    }


def _team_bucket_from_brief(brief: dict[str, Any], bucket_name: str) -> dict[str, Any]:
    buckets = brief.get("evidenceBuckets") or {}
    uncertainty = buckets.get("engineUncertainty") or {}
    bucket_map = {
        "robustIgnoredAdvice": buckets.get("robustIgnoredAdvice"),
        "pimcSplits": uncertainty.get("pimcSplits"),
        "pvMisses": uncertainty.get("pvMisses"),
        "noStableLines": buckets.get("noStableLines"),
        "fieldPressure": buckets.get("fieldPressure"),
    }
    bucket = bucket_map.get(bucket_name)
    if not isinstance(bucket, dict):
        raise HTTPException(status_code=400, detail=f"unknown team evidence bucket: {bucket_name}")
    return bucket


def _team_overview_from_brief(brief: dict[str, Any]) -> dict[str, Any]:
    buckets = brief.get("evidenceBuckets") or {}
    uncertainty = buckets.get("engineUncertainty") or {}
    bucket_counts = {
        "robustIgnoredAdvice": (buckets.get("robustIgnoredAdvice") or {}).get("count") or 0,
        "pimcSplits": (uncertainty.get("pimcSplits") or {}).get("count") or 0,
        "pvMisses": (uncertainty.get("pvMisses") or {}).get("count") or 0,
        "noStableLines": (buckets.get("noStableLines") or {}).get("count") or 0,
        "fieldPressure": (buckets.get("fieldPressure") or {}).get("count") or 0,
    }
    team = brief.get("team") if isinstance(brief.get("team"), dict) else {}
    summary = brief.get("summary") if isinstance(brief.get("summary"), dict) else {}
    pokemon = [
        _compact_team_pokemon_profile(item)
        for item in (brief.get("pokemonProfiles") or [])
        if isinstance(item, dict)
    ]
    return {
        "purpose": "team_overview",
        "team": {
            "key": team.get("key"),
            "name": team.get("name"),
            "roster": team.get("roster") or [],
            "trackedBattleCount": len(team.get("battleIds") or []),
        },
        "summary": {
            "battles": summary.get("battles"),
            "wins": summary.get("wins"),
            "losses": summary.get("losses"),
            "unknown": summary.get("unknown"),
            "winRate": summary.get("winRate"),
            "performanceBattles": summary.get("performanceBattles"),
            "followRate": summary.get("followRate"),
            "topLead": summary.get("topLead"),
        },
        "pokemonProfiles": pokemon,
        "bucketCounts": bucket_counts,
        "reviewPriorities": [
            _compact_team_priority(item)
            for item in (brief.get("reviewPriorities") or [])[:5]
            if isinstance(item, dict)
        ],
        "agentUsageNotes": [
            "This is an overview. Use drill-down tools for Pokemon, turn, or evidence-bucket claims.",
            "Do not treat PIMC splits, PV misses, or no-stable lines as automatic player mistakes.",
        ],
    }


def _team_battle_ids(brief: dict[str, Any]) -> set[str]:
    team = brief.get("team") if isinstance(brief.get("team"), dict) else {}
    return {
        str(item)
        for item in (team.get("battleIds") or [])
        if item
    }


def _require_same_team_battle(anchor_brief: dict[str, Any], battle_id: str) -> None:
    team_battle_ids = _team_battle_ids(anchor_brief)
    if team_battle_ids and battle_id not in team_battle_ids:
        raise HTTPException(
            status_code=400,
            detail=f"battleId={battle_id} is not on the anchored team",
        )


def _compact_engine_eval_case(case: dict[str, Any]) -> dict[str, Any]:
    source = case.get("source") if isinstance(case.get("source"), dict) else {}
    position = case.get("positionSummary") if isinstance(case.get("positionSummary"), dict) else {}
    replay = case.get("replay") if isinstance(case.get("replay"), dict) else {}
    terminal = replay.get("terminal") if isinstance(replay.get("terminal"), dict) else {}
    priority = case.get("priority") if isinstance(case.get("priority"), dict) else {}
    return {
        "caseId": case.get("caseId"),
        "battleId": source.get("battleId"),
        "opponent": source.get("opponent"),
        "result": source.get("result"),
        "turn": source.get("turn"),
        "forceSwitch": bool(source.get("forceSwitch")),
        "priority": {
            "score": priority.get("score"),
            "reasons": (priority.get("reasons") or [])[:5],
            "pimcUncertainty": priority.get("pimcUncertainty"),
        },
        "position": {
            "engineAction": position.get("engineAction"),
            "actualAction": position.get("actualAction"),
            "confidence": position.get("confidence"),
            "tags": (position.get("tags") or [])[:6],
            "opponent": position.get("opponent"),
        },
        "replay": {
            "available": bool(replay.get("available")),
            "turn": replay.get("turn"),
            "forceSwitch": bool(replay.get("forceSwitch")),
        },
        "terminal": {
            "confidence": terminal.get("confidence"),
            "message": terminal.get("message"),
            "pimcConsensus": terminal.get("pimcConsensus"),
        },
    }


def _engine_case_matches_kind(case: dict[str, Any], kind: str) -> bool:
    if kind == "all":
        return True
    priority = case.get("priority") if isinstance(case.get("priority"), dict) else {}
    reasons = {str(item).lower() for item in (priority.get("reasons") or [])}
    replay = case.get("replay") if isinstance(case.get("replay"), dict) else {}
    terminal = replay.get("terminal") if isinstance(replay.get("terminal"), dict) else {}
    source = case.get("source") if isinstance(case.get("source"), dict) else {}
    position = case.get("positionSummary") if isinstance(case.get("positionSummary"), dict) else {}
    if kind == "pimc_splits":
        return bool(priority.get("pimcUncertainty"))
    if kind == "no_stable_lines":
        message = str(terminal.get("message") or "").lower()
        confidence = terminal.get("confidence")
        try:
            confidence_num = float(confidence)
        except (TypeError, ValueError):
            confidence_num = None
        return "no stable line" in message or (
            confidence_num is not None and 0 < confidence_num < 0.02
        )
    if kind == "pv_misses":
        opponent = position.get("opponent") if isinstance(position.get("opponent"), dict) else {}
        return opponent.get("pvMatchedReality") is False or "pv miss" in reasons
    if kind == "field_pressure":
        expected = case.get("expectedBehavior") if isinstance(case.get("expectedBehavior"), dict) else {}
        label = source.get("reviewLabel") if isinstance(source.get("reviewLabel"), dict) else {}
        return (
            expected.get("caseType") == "survival_positioning"
            or label.get("label") == "field_pressure"
            or "field pressure" in reasons
            or "survival/field-pressure case" in reasons
        )
    return False


def _team_engine_eval_cases(
    battle_id: str,
    kind: str,
    limit: int,
    *,
    postmortem_dir: Path,
    replay_dir: Path,
) -> dict[str, Any]:
    anchor_brief = _team_coach_brief(
        battle_id,
        postmortem_dir=postmortem_dir,
        replay_dir=replay_dir,
    )
    team_battle_ids = _team_battle_ids(anchor_brief)
    archive = _summarize_archive(
        postmortem_dir,
        pattern_evidence_limit=200,
        review_labels=load_review_labels(),
    )
    cases = build_engine_eval_cases(archive.get("patternPanels") or [])
    cases = enrich_engine_eval_cases_with_replay(cases, replay_dir)
    cases = prioritize_engine_eval_cases(cases)
    filtered = []
    for case in cases:
        source = case.get("source") if isinstance(case.get("source"), dict) else {}
        source_battle_id = str(source.get("battleId") or "")
        if team_battle_ids and source_battle_id not in team_battle_ids:
            continue
        if _engine_case_matches_kind(case, kind):
            filtered.append(case)
    clipped = filtered[:limit]
    return {
        "purpose": "team_engine_eval_cases",
        "battleId": battle_id,
        "kind": kind,
        "count": len(filtered),
        "cases": [_compact_engine_eval_case(case) for case in clipped],
        "truncated": len(filtered) > len(clipped),
    }


def _compact_signal(signal: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": signal.get("type"),
        "severity": signal.get("severity"),
        "side": signal.get("side"),
        "details": signal.get("details"),
    }


def _compact_field_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": event.get("type"),
        "category": event.get("category"),
        "group": event.get("group"),
        "count": event.get("count"),
        "hpPctLost": event.get("hpPctLost"),
        "label": event.get("label"),
        "details": (event.get("details") or [])[:2],
    }


def _compact_team_turn(turn: dict[str, Any]) -> dict[str, Any]:
    return {
        "turn": turn.get("turn"),
        "forceSwitch": bool(turn.get("forceSwitch")),
        "engine": turn.get("pickLabel"),
        "actual": turn.get("actualLabel"),
        "matched": turn.get("matchedRecommendation"),
        "confidence": turn.get("confidence"),
        "enginePredictedOpp": turn.get("enginePredictedOpp"),
        "actualOppMove": turn.get("actualOppMove"),
        "pvMatchedReality": turn.get("pvMatchedReality"),
        "critical": bool(turn.get("critical")),
        "issues": turn.get("issues") or [],
        "fieldEvents": [
            _compact_field_event(event)
            for event in (turn.get("fieldEventSummary") or [])[:4]
            if isinstance(event, dict)
        ],
        "signals": [
            _compact_signal(signal)
            for signal in (turn.get("strategicSignals") or [])[:4]
            if isinstance(signal, dict)
        ],
    }


def _compact_active(active: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(active, dict):
        return None
    return {
        "species": active.get("species"),
        "hpPct": active.get("hpPct"),
        "status": active.get("status"),
        "ability": active.get("ability"),
        "item": active.get("item"),
        "types": active.get("types") or [],
        "teraType": active.get("teraType"),
        "terastallized": bool(active.get("terastallized")),
        "moves": [
            {
                "id": move.get("id"),
                "pp": move.get("pp"),
                "disabled": bool(move.get("disabled")),
            }
            for move in (active.get("moves") or [])[:4]
            if isinstance(move, dict)
        ],
    }


def _compact_field_state(field_state: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(field_state, dict):
        return None
    mine = field_state.get("mine") if isinstance(field_state.get("mine"), dict) else {}
    opp = field_state.get("opp") if isinstance(field_state.get("opp"), dict) else {}
    mine_active = _compact_active(mine.get("active"))
    opp_active = _compact_active(opp.get("active"))
    return {
        "source": field_state.get("source"),
        "weather": field_state.get("weather"),
        "terrain": field_state.get("terrain"),
        "trickRoom": bool(field_state.get("trickRoom")),
        "mine": {
            "activeSpecies": (mine_active or {}).get("species"),
            "active": mine_active,
            "hazards": mine.get("hazards") or {},
            "screens": mine.get("screens") or {},
            "boosts": mine.get("boosts") or {},
            "lastUsedMove": mine.get("lastUsedMove"),
        },
        "opp": {
            "activeSpecies": (opp_active or {}).get("species"),
            "active": opp_active,
            "hazards": opp.get("hazards") or {},
            "screens": opp.get("screens") or {},
            "boosts": opp.get("boosts") or {},
            "lastUsedMove": opp.get("lastUsedMove"),
        },
    }


def _hp_pct_from_raw(mon: dict[str, Any]) -> float | None:
    hp = mon.get("hp")
    maxhp = mon.get("maxhp")
    if not isinstance(hp, (int, float)) or not isinstance(maxhp, (int, float)) or maxhp <= 0:
        return None
    return round((hp / maxhp) * 100, 1)


def _compact_raw_team_state(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    state = request_state_from_replay(record)
    if not isinstance(state, dict):
        return None

    def side_state(side: dict[str, Any]) -> dict[str, Any]:
        pokemon = side.get("pokemon") if isinstance(side.get("pokemon"), list) else []
        active_index = side.get("activeIndex") if isinstance(side.get("activeIndex"), int) else 0
        rows = []
        for index, mon in enumerate(pokemon):
            if not isinstance(mon, dict):
                continue
            hp_pct = _hp_pct_from_raw(mon)
            rows.append({
                "species": mon.get("species"),
                "active": index == active_index,
                "hpPct": hp_pct,
                "status": mon.get("status"),
                "fainted": hp_pct == 0,
                "item": mon.get("item"),
                "ability": mon.get("ability"),
            })
        return {
            "activeIndex": active_index,
            "pokemon": rows,
        }

    mine = state.get("sideOne") if isinstance(state.get("sideOne"), dict) else {}
    opp = state.get("sideTwo") if isinstance(state.get("sideTwo"), dict) else {}
    return {
        "mine": side_state(mine),
        "opp": side_state(opp),
    }


def _team_battle_window(
    anchor_battle_id: str,
    target_battle_id: str,
    turn: int,
    before: int,
    after: int,
    *,
    postmortem_dir: Path,
    replay_dir: Path,
) -> dict[str, Any]:
    anchor_brief = _team_coach_brief(
        anchor_battle_id,
        postmortem_dir=postmortem_dir,
        replay_dir=replay_dir,
    )
    _require_same_team_battle(anchor_brief, target_battle_id)
    context = _battle_agent_context(
        target_battle_id,
        postmortem_dir=postmortem_dir,
        replay_dir=replay_dir,
    )
    start_turn = max(1, turn - before)
    end_turn = turn + after
    turns = [
        turn_row
        for turn_row in (context.get("turns") or [])
        if isinstance(turn_row, dict)
        and isinstance(turn_row.get("turn"), int)
        and start_turn <= int(turn_row.get("turn")) <= end_turn
    ]
    return {
        "purpose": "team_battle_window",
        "anchorBattleId": anchor_battle_id,
        "battleId": target_battle_id,
        "turn": turn,
        "window": {"before": before, "after": after, "start": start_turn, "end": end_turn},
        "battle": {
            "opponent": (context.get("battle") or {}).get("opponent"),
            "result": (context.get("battle") or {}).get("result"),
            "totalTurns": (context.get("battle") or {}).get("totalTurns"),
            "replayUrl": (context.get("battle") or {}).get("replayUrl"),
        },
        "teamComposition": context.get("teamComposition"),
        "turns": [_compact_team_turn(item) for item in turns],
        "agentUsageNotes": [
            "Use this as local context around a priority turn.",
            "Do not infer information outside this window unless another tool output provides it.",
        ],
    }


def _turn_mentions_species(turn: dict[str, Any], species: str) -> bool:
    wanted = _norm_key(species)
    if not wanted:
        return False
    texts = [
        turn.get("pickName"),
        turn.get("actualName"),
        turn.get("pickLabel"),
        turn.get("actualLabel"),
        turn.get("enginePredictedOpp"),
        turn.get("actualOppMove"),
    ]
    field_state = turn.get("fieldStateBeforeDecision") if isinstance(turn.get("fieldStateBeforeDecision"), dict) else {}
    for side in ("mine", "opp"):
        active = ((field_state.get(side) or {}).get("active") or {})
        texts.append(active.get("species"))
    for item in (turn.get("faints") or []):
        if isinstance(item, dict):
            texts.extend([item.get("species"), item.get("targetSpecies")])
    for item in (turn.get("residualEvents") or []):
        if isinstance(item, dict):
            texts.extend([item.get("targetSpecies"), item.get("source")])
    for item in (turn.get("fieldEventSummary") or []):
        if isinstance(item, dict):
            texts.append(item.get("label"))
            texts.extend(item.get("details") or [])
    for item in (turn.get("strategicSignals") or []):
        if isinstance(item, dict):
            texts.append(item.get("details"))
    return any(wanted in _norm_key(text) for text in texts)


def _battle_pokemon_performance(battle: dict[str, Any], species: str) -> dict[str, Any]:
    team_performance = battle.get("teamPerformance") if isinstance(battle.get("teamPerformance"), dict) else {}
    mine = team_performance.get("mine") if isinstance(team_performance.get("mine"), dict) else {}
    pokemon = mine.get("pokemon") if isinstance(mine.get("pokemon"), dict) else {}
    for key, stats in pokemon.items():
        if isinstance(stats, dict) and _norm_key(stats.get("species") or key) == _norm_key(species):
            return {
                "species": stats.get("species") or key,
                "led": bool(stats.get("led")),
                "switchIns": stats.get("switchIns"),
                "forcedSwitchIns": stats.get("forcedSwitchIns"),
                "activeTurns": stats.get("activeTurns"),
                "fainted": bool(stats.get("fainted")),
                "faintTurn": stats.get("faintTurn"),
                "survived": bool(stats.get("survived")),
                "actionPreventedCount": stats.get("actionPreventedCount"),
                "damageTakenPct": stats.get("directDamageTakenPct"),
                "damageDealtPct": stats.get("directDamageDealtPct"),
                "kos": stats.get("kos"),
                "koCredit": stats.get("koCredit"),
                "fieldPressure": stats.get("fieldPressure"),
            }
    return {}


def _pokemon_battle_timeline(
    anchor_battle_id: str,
    target_battle_id: str,
    species: str,
    limit: int,
    *,
    postmortem_dir: Path,
    replay_dir: Path,
) -> dict[str, Any]:
    anchor_brief = _team_coach_brief(
        anchor_battle_id,
        postmortem_dir=postmortem_dir,
        replay_dir=replay_dir,
    )
    _require_same_team_battle(anchor_brief, target_battle_id)
    context = _battle_agent_context(
        target_battle_id,
        postmortem_dir=postmortem_dir,
        replay_dir=replay_dir,
    )
    relevant = [
        turn for turn in (context.get("turns") or [])
        if isinstance(turn, dict) and _turn_mentions_species(turn, species)
    ]
    relevant.sort(key=lambda item: (
        item.get("turn") if isinstance(item.get("turn"), int) else 10_000,
        1 if item.get("forceSwitch") else 0,
    ))
    clipped = relevant[:limit]
    return {
        "purpose": "pokemon_battle_timeline",
        "battleId": anchor_battle_id,
        "targetBattleId": target_battle_id,
        "species": species,
        "battle": {
            "opponent": (context.get("battle") or {}).get("opponent"),
            "result": (context.get("battle") or {}).get("result"),
            "totalTurns": (context.get("battle") or {}).get("totalTurns"),
            "replayUrl": (context.get("battle") or {}).get("replayUrl"),
        },
        "battlePerformance": _battle_pokemon_performance(context.get("battle") or {}, species),
        "turns": [_compact_team_turn(item) for item in clipped],
        "count": len(relevant),
        "truncated": len(relevant) > len(clipped),
    }


def _team_state_at_turn(
    anchor_battle_id: str,
    target_battle_id: str,
    turn: int,
    *,
    postmortem_dir: Path,
    replay_dir: Path,
) -> dict[str, Any]:
    anchor_brief = _team_coach_brief(
        anchor_battle_id,
        postmortem_dir=postmortem_dir,
        replay_dir=replay_dir,
    )
    _require_same_team_battle(anchor_brief, target_battle_id)
    context = _battle_agent_context(
        target_battle_id,
        postmortem_dir=postmortem_dir,
        replay_dir=replay_dir,
    )
    rows = [
        row for row in (context.get("turns") or [])
        if isinstance(row, dict) and row.get("turn") == turn
    ]
    row_for_record = rows[0] if rows else {}
    records = load_engine_replay_records(target_battle_id, replay_dir)
    record = find_replay_record_for_turn(
        records,
        turn,
        row_for_record.get("pickName") if isinstance(row_for_record, dict) else None,
    )
    return {
        "purpose": "team_state_at_turn",
        "battleId": anchor_battle_id,
        "targetBattleId": target_battle_id,
        "turn": turn,
        "battle": {
            "opponent": (context.get("battle") or {}).get("opponent"),
            "result": (context.get("battle") or {}).get("result"),
            "totalTurns": (context.get("battle") or {}).get("totalTurns"),
            "replayUrl": (context.get("battle") or {}).get("replayUrl"),
        },
        "teamComposition": context.get("teamComposition"),
        "rows": [_compact_team_turn(row) for row in rows],
        "fieldState": _compact_field_state(row_for_record.get("fieldStateBeforeDecision") if isinstance(row_for_record, dict) else None),
        "teamState": _compact_raw_team_state(record),
        "engineReplay": {
            "available": bool(record),
            "rqid": record.get("rqid") if isinstance(record, dict) else None,
            "forceSwitch": bool(record.get("force_switch")) if isinstance(record, dict) else None,
        },
    }


def _pattern_agent_context(
    pattern_id: str,
    *,
    postmortem_dir: Path,
    review_labels: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    archive = _summarize_archive(
        postmortem_dir,
        pattern_evidence_limit=24,
        review_labels=review_labels,
    )
    context = build_pattern_agent_context(pattern_id, archive)
    if context is None:
        raise HTTPException(status_code=404, detail=f"unknown patternId={pattern_id}")
    return context


def _coach_brief(
    battle_id: str,
    *,
    postmortem_dir: Path,
    replay_dir: Path,
) -> dict[str, Any]:
    return build_coach_brief(_battle_agent_context(
        battle_id,
        postmortem_dir=postmortem_dir,
        replay_dir=replay_dir,
    ))


def _parse_jsonish_model_output(text: str) -> Any:
    try:
        return parse_jsonish_model_output(text)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def write_coach_agent_trace(run: dict[str, Any], directory: Path) -> None:
    date = datetime.now().strftime("%Y-%m-%d")
    path = directory / f"{date}.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(run, ensure_ascii=False) + "\n")
    except Exception:
        return


async def openai_responses_create(
    payload: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not set")
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail=(
                f"OpenAI request timed out after {timeout_seconds}s. "
                "Try a faster preset or lower reasoning setting."
            ),
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"OpenAI request failed: {exc.__class__.__name__}",
        ) from exc
    if response.status_code >= 400:
        detail = response.text
        try:
            payload = response.json()
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict) and error.get("message"):
                code = error.get("code") or error.get("type") or response.status_code
                detail = f"OpenAI error ({code}): {error.get('message')}"
        except ValueError:
            pass
        raise HTTPException(status_code=502, detail=detail)
    data = response.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="OpenAI returned a non-object response")
    return data


def _anthropic_tools_from_openai(
    tools: list[dict[str, Any]],
    descriptions: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    converted = []
    for tool in tools:
        name = str(tool.get("name") or "")
        converted.append({
            "name": name,
            "description": (descriptions or {}).get(name) or tool.get("description") or "",
            "input_schema": tool.get("parameters") or {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        })
    return converted


def _anthropic_response_summaries(responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = []
    for index, response in enumerate(responses, start=1):
        content = response.get("content") or []
        content_types: list[str] = []
        text_chars = 0
        thinking_chars = 0
        thinking_blocks = 0
        tool_names: list[str] = []
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = str(block.get("type") or "unknown")
                content_types.append(block_type)
                if block_type == "text":
                    text_chars += len(str(block.get("text") or ""))
                elif block_type == "thinking":
                    thinking_blocks += 1
                    thinking_chars += len(str(block.get("thinking") or block.get("text") or ""))
                elif block_type == "tool_use":
                    tool_name = block.get("name")
                    if isinstance(tool_name, str):
                        tool_names.append(tool_name)
        summaries.append({
            "index": index,
            "id": response.get("id"),
            "stopReason": response.get("stop_reason"),
            "contentTypes": content_types,
            "textChars": text_chars,
            "thinkingBlocks": thinking_blocks,
            "thinkingChars": thinking_chars,
            "toolNames": tool_names,
        })
    return summaries


def _anthropic_thinking_payload(preset: dict[str, Any]) -> dict[str, Any]:
    thinking_mode = preset.get("anthropicThinking")
    if thinking_mode != "adaptive":
        return {}
    thinking: dict[str, Any] = {"type": "adaptive"}
    display = preset.get("anthropicThinkingDisplay")
    if isinstance(display, str) and display:
        thinking["display"] = display
    payload: dict[str, Any] = {"thinking": thinking}
    effort = preset.get("anthropicThinkingEffort")
    if isinstance(effort, str) and effort:
        payload["output_config"] = {"effort": effort}
    return payload


def _anthropic_response_text(response: dict[str, Any]) -> str:
    parts = []
    for block in response.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts).strip()


def _anthropic_refusal_reason(responses: list[dict[str, Any]]) -> str | None:
    for response in responses:
        if response.get("stop_reason") == "refusal":
            return "Anthropic returned stop_reason=refusal for this request."
    return None


def _anthropic_tool_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    calls = []
    for block in response.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        args = block.get("input")
        calls.append({
            "callId": block.get("id"),
            "name": block.get("name"),
            "args": args if isinstance(args, dict) else {},
        })
    return calls


def _anthropic_usage_from_responses(responses: list[dict[str, Any]]) -> dict[str, Any]:
    input_tokens = 0
    output_tokens = 0
    seen_usage = False
    for response in responses:
        usage = response.get("usage")
        if not isinstance(usage, dict):
            continue
        seen_usage = True
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)
    return {
        "inputTokens": input_tokens if seen_usage else None,
        "outputTokens": output_tokens if seen_usage else None,
        "totalTokens": (input_tokens + output_tokens) if seen_usage else None,
        "reasoningTokens": None,
        "costUsd": None,
        "note": "Anthropic token usage is recorded when returned; cost is left unset to avoid stale pricing assumptions.",
    }


async def anthropic_messages_create(
    payload: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY is not set")
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": os.environ.get("ANTHROPIC_VERSION", "2023-06-01"),
                    "content-type": "application/json",
                },
                json=payload,
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail=(
                f"Anthropic request timed out after {timeout_seconds}s. "
                "Try a faster preset or lower tool depth."
            ),
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Anthropic request failed: {exc.__class__.__name__}",
        ) from exc
    if response.status_code >= 400:
        detail = response.text
        try:
            payload = response.json()
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict) and error.get("message"):
                code = error.get("type") or response.status_code
                detail = f"Anthropic error ({code}): {error.get('message')}"
        except ValueError:
            pass
        raise HTTPException(status_code=502, detail=detail)
    data = response.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Anthropic returned a non-object response")
    return data


def run_coach_tool(
    name: str,
    args: dict[str, Any],
    *,
    postmortem_dir: Path,
    replay_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.perf_counter()
    if name == "get_coach_brief":
        output = _coach_brief(
            str(args.get("battleId") or ""),
            postmortem_dir=postmortem_dir,
            replay_dir=replay_dir,
        )
    elif name == "get_battle_context":
        output = _battle_agent_context(
            str(args.get("battleId") or ""),
            postmortem_dir=postmortem_dir,
            replay_dir=replay_dir,
        )
    elif name == "get_archive_context":
        output = _archive_agent_context(
            postmortem_dir=postmortem_dir,
            review_labels=load_review_labels(),
        )
    elif name == "get_team_coach_brief":
        output = _team_coach_brief(
            str(args.get("battleId") or ""),
            postmortem_dir=postmortem_dir,
            replay_dir=replay_dir,
        )
    else:
        raise HTTPException(status_code=400, detail=f"unknown coach tool: {name}")

    duration_ms = int((time.perf_counter() - started) * 1000)
    trace = {
        "name": name,
        "args": args,
        "durationMs": duration_ms,
        "outputSummary": tool_output_summary(name, output),
    }
    return output, trace


def run_team_coach_tool(
    name: str,
    args: dict[str, Any],
    *,
    postmortem_dir: Path,
    replay_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.perf_counter()
    if name == "get_team_overview":
        brief = _team_coach_brief(
            str(args.get("battleId") or ""),
            postmortem_dir=postmortem_dir,
            replay_dir=replay_dir,
        )
        output = _team_overview_from_brief(brief)
    elif name == "get_team_bucket_examples":
        limit = _clamp_int(args.get("limit"), 4, 1, 8)
        bucket_name = str(args.get("bucket") or "pimcSplits")
        brief = _team_coach_brief(
            str(args.get("battleId") or ""),
            postmortem_dir=postmortem_dir,
            replay_dir=replay_dir,
            evidence_limit=limit,
        )
        bucket = _team_bucket_from_brief(brief, bucket_name)
        examples = (bucket.get("examples") or [])[:limit]
        output = {
            "purpose": "team_bucket_examples",
            "battleId": args.get("battleId"),
            "bucket": bucket_name,
            "count": bucket.get("count") or 0,
            "byResult": bucket.get("byResult") or {},
            "examples": examples,
            "truncated": (bucket.get("count") or 0) > len(examples),
            "analysisGuardrail": (
                "Examples are evidence, not automatic proof of a player mistake; "
                "separate player issue, engine uncertainty, and field pressure."
            ),
        }
    elif name == "get_pokemon_profile":
        species = str(args.get("species") or "")
        brief = _team_coach_brief(
            str(args.get("battleId") or ""),
            postmortem_dir=postmortem_dir,
            replay_dir=replay_dir,
        )
        profile = next(
            (
                item for item in (brief.get("pokemonProfiles") or [])
                if isinstance(item, dict) and _norm_key(item.get("species")) == _norm_key(species)
            ),
            None,
        )
        output = {
            "purpose": "team_pokemon_profile",
            "battleId": args.get("battleId"),
            "species": species,
            "found": bool(profile),
            "profile": profile or {},
            "teamSummary": brief.get("summary"),
        }
    elif name == "get_pokemon_battle_timeline":
        species = str(args.get("species") or "")
        target_battle_id = str(args.get("targetBattleId") or args.get("battleId") or "")
        output = _pokemon_battle_timeline(
            str(args.get("battleId") or ""),
            target_battle_id,
            species,
            _clamp_int(args.get("limit"), 6, 1, 10),
            postmortem_dir=postmortem_dir,
            replay_dir=replay_dir,
        )
    elif name == "get_team_state_at_turn":
        target_battle_id = str(args.get("targetBattleId") or args.get("battleId") or "")
        output = _team_state_at_turn(
            str(args.get("battleId") or ""),
            target_battle_id,
            _clamp_int(args.get("turn"), 1, 1, 1000),
            postmortem_dir=postmortem_dir,
            replay_dir=replay_dir,
        )
    elif name == "get_battle_window":
        target_battle_id = str(args.get("battleId") or "")
        turn = _clamp_int(args.get("turn"), 1, 1, 1000)
        before = _clamp_int(args.get("before"), 1, 0, 3)
        after = _clamp_int(args.get("after"), 2, 0, 3)
        anchor_battle_id = str(args.get("_anchorBattleId") or target_battle_id)
        output = _team_battle_window(
            anchor_battle_id,
            target_battle_id,
            turn,
            before,
            after,
            postmortem_dir=postmortem_dir,
            replay_dir=replay_dir,
        )
    elif name == "get_engine_eval_cases":
        kind = str(args.get("kind") or "all")
        if kind not in {"all", "pimc_splits", "no_stable_lines", "pv_misses", "field_pressure"}:
            raise HTTPException(status_code=400, detail=f"unknown engine eval kind: {kind}")
        output = _team_engine_eval_cases(
            str(args.get("battleId") or ""),
            kind,
            _clamp_int(args.get("limit"), 4, 1, 8),
            postmortem_dir=postmortem_dir,
            replay_dir=replay_dir,
        )
    else:
        raise HTTPException(status_code=400, detail=f"unknown team coach tool: {name}")

    duration_ms = int((time.perf_counter() - started) * 1000)
    trace = {
        "name": name,
        "args": {key: value for key, value in args.items() if not str(key).startswith("_")},
        "durationMs": duration_ms,
        "outputSummary": tool_output_summary(name, output),
    }
    return output, trace


def run_pattern_tool(
    name: str,
    args: dict[str, Any],
    *,
    postmortem_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.perf_counter()
    if name == "get_pattern_context":
        output = _pattern_agent_context(
            str(args.get("patternId") or ""),
            postmortem_dir=postmortem_dir,
            review_labels=load_review_labels(),
        )
    elif name == "get_archive_context":
        output = _archive_agent_context(
            postmortem_dir=postmortem_dir,
            review_labels=load_review_labels(),
        )
    else:
        raise HTTPException(status_code=400, detail=f"unknown pattern tool: {name}")

    duration_ms = int((time.perf_counter() - started) * 1000)
    trace = {
        "name": name,
        "args": args,
        "durationMs": duration_ms,
        "outputSummary": pattern_tool_output_summary(name, output),
    }
    return output, trace


async def openai_coach_agent_run(
    battle_id: str,
    preset: dict[str, Any],
    *,
    postmortem_dir: Path,
    replay_dir: Path,
    trace_dir: Path,
    event_sink: AgentEventSink | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    started_at_ms = int(datetime.now().timestamp() * 1000)
    tool_calls: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    model = str(preset.get("apiModel") or preset.get("modelLabel"))
    max_output_tokens = int(preset.get("maxOutputTokens") or 1200)
    timeout_seconds = int(preset.get("timeoutSeconds") or 60)
    max_tool_rounds = int(preset.get("maxToolRounds") or 4)
    settings = {
        "api": "responses",
        "model": model,
        "reasoningEffort": preset.get("openaiReasoningEffort") or "medium",
        "maxOutputTokens": max_output_tokens,
        "maxToolRounds": max_tool_rounds,
        "timeoutSeconds": timeout_seconds,
        "toolChoice": "auto",
        "toolCount": len(OPENAI_COACH_TOOLS),
        "finalizationPass": False,
        "standaloneSynthesisPass": False,
        "deterministicFallback": False,
        "textRepairPass": False,
        "textWasTruncated": False,
        "toolLimitReached": False,
        "forcedFinalAfterToolBudget": False,
        "budgetedSynthesisPass": False,
    }

    payload: dict[str, Any] = {
        "model": model,
        "instructions": COACH_AI_SYSTEM_PROMPT,
        "input": coach_prompt(battle_id, preset),
        "tools": OPENAI_COACH_TOOLS,
        "tool_choice": "auto",
        "reasoning": {"effort": settings["reasoningEffort"]},
        "max_output_tokens": max_output_tokens,
    }
    final_text = ""
    last_response_id: str | None = None
    tool_rounds = 0
    tool_context: list[dict[str, Any]] = []
    tool_outputs_by_name: dict[str, dict[str, Any]] = {}
    last_text_response_incomplete = False
    await _emit_agent_event(event_sink, {
        "type": "run_started",
        "provider": "openai",
        "mode": "real",
        "preset": preset,
        "model": model,
        "battleId": battle_id,
    })
    while True:
        await _emit_agent_event(event_sink, {
            "type": "model_request_started",
            "provider": "openai",
            "model": model,
            "toolRound": tool_rounds + 1,
            "toolChoice": payload.get("tool_choice") or "auto",
        })
        response = await openai_responses_create(payload, timeout_seconds)
        responses.append(response)
        last_response_id = response.get("id") if isinstance(response.get("id"), str) else last_response_id
        final_text = response_text(response)
        last_text_response_incomplete = response_incomplete(response) if final_text else False
        calls = response_function_calls(response)
        await _emit_agent_event(event_sink, {
            "type": "model_response_received",
            "provider": "openai",
            "responseId": response.get("id"),
            "toolRound": tool_rounds + 1,
            "toolCallCount": len(calls),
            "hasText": bool(final_text),
        })
        if not calls:
            break
        if tool_rounds >= max_tool_rounds:
            settings["toolLimitReached"] = True
            break

        function_outputs = []
        for call in calls:
            tool_name = str(call.get("name"))
            tool_args = normalize_coach_tool_args(tool_name, call.get("args") or {}, battle_id)
            await _emit_agent_event(event_sink, {
                "type": "tool_started",
                "name": tool_name,
                "args": tool_args,
                "callId": call.get("callId"),
            })
            output, trace = run_coach_tool(
                tool_name,
                tool_args,
                postmortem_dir=postmortem_dir,
                replay_dir=replay_dir,
            )
            compact_output = compact_tool_output_for_model(tool_name, output)
            trace["callId"] = call.get("callId")
            trace["modelOutputBytes"] = len(json.dumps(compact_output, ensure_ascii=False))
            tool_calls.append(trace)
            tool_outputs_by_name[tool_name] = compact_output
            tool_context.append({
                "name": tool_name,
                "args": tool_args,
                "output": compact_output,
            })
            function_outputs.append({
                "type": "function_call_output",
                "call_id": call.get("callId"),
                "output": json.dumps(compact_output, ensure_ascii=False),
            })
            await _emit_agent_event(event_sink, {
                "type": "tool_completed",
                "toolCall": trace,
                **_tool_output_event_payload(compact_output),
            })

        tool_rounds += 1
        payload = {
            "model": model,
            "previous_response_id": last_response_id,
            "input": function_outputs,
            "tools": OPENAI_COACH_TOOLS,
            "tool_choice": "auto",
            "reasoning": {"effort": settings["reasoningEffort"]},
            "max_output_tokens": max_output_tokens,
        }

    if not final_text:
        if settings["toolLimitReached"]:
            raise HTTPException(
                status_code=502,
                detail=f"OpenAI requested more than {max_tool_rounds} tool-call rounds without returning final text",
            )
        if not last_response_id:
            raise HTTPException(status_code=502, detail="OpenAI did not return a response id or final text")
        settings["finalizationPass"] = True
        response = await openai_responses_create(
            {
                "model": model,
                "instructions": COACH_AI_SYSTEM_PROMPT,
                "previous_response_id": last_response_id,
                "input": coach_final_answer_prompt(battle_id, preset, tool_calls),
                "reasoning": {"effort": settings["reasoningEffort"]},
                "max_output_tokens": max(max_output_tokens, 2200),
            },
            timeout_seconds,
        )
        responses.append(response)
        last_response_id = response.get("id") if isinstance(response.get("id"), str) else last_response_id
        final_text = response_text(response)
        last_text_response_incomplete = response_incomplete(response) if final_text else False

    if not final_text:
        settings["standaloneSynthesisPass"] = True
        for planned in fake_tool_plan(preset, battle_id):
            name = planned["name"]
            if name in tool_outputs_by_name:
                continue
            output, trace = run_coach_tool(
                name,
                planned["args"],
                postmortem_dir=postmortem_dir,
                replay_dir=replay_dir,
            )
            compact_output = compact_tool_output_for_model(name, output)
            trace["source"] = "server_synthesis_context"
            trace["modelOutputBytes"] = len(json.dumps(compact_output, ensure_ascii=False))
            tool_calls.append(trace)
            tool_outputs_by_name[name] = compact_output
            tool_context.append({
                "name": name,
                "args": planned["args"],
                "output": compact_output,
            })

        synthesis_model = os.environ.get(
            "SHOWDOWN_OPENAI_SYNTHESIS_MODEL",
            os.environ.get("SHOWDOWN_OPENAI_FAST_MODEL", "gpt-5.4-mini"),
        )
        settings["synthesisModel"] = synthesis_model
        settings["synthesisReasoningEffort"] = "medium"
        response = await openai_responses_create(
            {
                "model": synthesis_model,
                "instructions": COACH_AI_SYSTEM_PROMPT,
                "input": coach_synthesis_prompt(battle_id, preset, tool_context),
                "reasoning": {"effort": "medium"},
                "max_output_tokens": max(max_output_tokens, SYNTHESIS_MAX_OUTPUT_TOKENS),
            },
            timeout_seconds,
        )
        responses.append(response)
        last_response_id = response.get("id") if isinstance(response.get("id"), str) else last_response_id
        final_text = response_text(response)
        last_text_response_incomplete = response_incomplete(response) if final_text else False

    if final_text and (last_text_response_incomplete or looks_truncated_text(final_text)):
        settings["textRepairPass"] = True
        settings["textWasTruncated"] = True
        for planned in fake_tool_plan(preset, battle_id):
            name = planned["name"]
            if name in tool_outputs_by_name:
                continue
            output, trace = run_coach_tool(
                name,
                planned["args"],
                postmortem_dir=postmortem_dir,
                replay_dir=replay_dir,
            )
            compact_output = compact_tool_output_for_model(name, output)
            trace["source"] = "server_repair_context"
            trace["modelOutputBytes"] = len(json.dumps(compact_output, ensure_ascii=False))
            tool_calls.append(trace)
            tool_outputs_by_name[name] = compact_output
            tool_context.append({
                "name": name,
                "args": planned["args"],
                "output": compact_output,
            })

        repair_model = os.environ.get(
            "SHOWDOWN_OPENAI_SYNTHESIS_MODEL",
            os.environ.get("SHOWDOWN_OPENAI_FAST_MODEL", "gpt-5.4-mini"),
        )
        settings["repairModel"] = repair_model
        response = await openai_responses_create(
            {
                "model": repair_model,
                "instructions": COACH_AI_SYSTEM_PROMPT,
                "input": coach_synthesis_prompt(battle_id, preset, tool_context),
                "reasoning": {"effort": "medium"},
                "max_output_tokens": max(max_output_tokens, SYNTHESIS_MAX_OUTPUT_TOKENS),
            },
            timeout_seconds,
        )
        responses.append(response)
        repaired_text = response_text(response)
        last_text_response_incomplete = response_incomplete(response) if repaired_text else False
        if repaired_text:
            final_text = repaired_text

    if not final_text or last_text_response_incomplete or looks_truncated_text(final_text):
        settings["deterministicFallback"] = True
        final_text = deterministic_agent_answer(
            preset,
            tool_outputs_by_name.get("get_coach_brief") or _coach_brief(
                battle_id,
                postmortem_dir=postmortem_dir,
                replay_dir=replay_dir,
            ),
            tool_outputs_by_name.get("get_battle_context"),
            tool_outputs_by_name.get("get_archive_context"),
            tool_outputs_by_name.get("get_team_coach_brief"),
        )

    brief = _coach_brief(battle_id, postmortem_dir=postmortem_dir, replay_dir=replay_dir)
    latency_ms = int((time.perf_counter() - started) * 1000)
    settings["responseCount"] = len(responses)
    settings["toolRounds"] = tool_rounds
    run = {
        "runId": f"coach-{started_at_ms}-{uuid.uuid4().hex[:8]}",
        "battleId": battle_id,
        "mode": "real",
        "provider": "openai",
        "preset": preset,
        "model": model,
        "startedAtMs": started_at_ms,
        "startedAtLabel": _timestamp_label(started_at_ms),
        "latencyMs": latency_ms,
        "settings": settings,
        "toolCalls": tool_calls,
        "answer": final_text,
        "comparisonMetrics": coach_agent_metrics(brief, tool_calls),
        "usage": usage_from_responses(responses),
        "responseIds": [
            response.get("id")
            for response in responses
            if isinstance(response.get("id"), str)
        ],
    }
    write_coach_agent_trace(run, directory=trace_dir)
    await _emit_agent_event(event_sink, {
        "type": "answer_ready",
        "answer": final_text,
        "usage": run.get("usage"),
    })
    return run


async def anthropic_coach_agent_run(
    battle_id: str,
    preset: dict[str, Any],
    *,
    postmortem_dir: Path,
    replay_dir: Path,
    trace_dir: Path,
    event_sink: AgentEventSink | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    started_at_ms = int(datetime.now().timestamp() * 1000)
    model = str(preset.get("apiModel") or preset.get("modelLabel"))
    max_output_tokens = int(preset.get("maxOutputTokens") or 1200)
    timeout_seconds = int(preset.get("timeoutSeconds") or 60)
    max_tool_rounds = int(preset.get("maxToolRounds") or 4)
    thinking_payload = _anthropic_thinking_payload(preset)
    tools = _anthropic_tools_from_openai(OPENAI_COACH_TOOLS)
    settings = {
        "api": "messages",
        "model": model,
        "maxOutputTokens": max_output_tokens,
        "maxToolRounds": max_tool_rounds,
        "timeoutSeconds": timeout_seconds,
        "toolChoice": "auto",
        "toolCount": len(tools),
        "standaloneSynthesisPass": False,
        "deterministicFallback": False,
        "textRepairPass": False,
        "textWasTruncated": False,
        "toolLimitReached": False,
        "fallbackReason": None,
        "thinkingMode": preset.get("anthropicThinking") or "off",
        "thinkingEffort": preset.get("anthropicThinkingEffort"),
        "thinkingDisplay": preset.get("anthropicThinkingDisplay"),
    }

    tool_calls: list[dict[str, Any]] = []
    tool_outputs_by_name: dict[str, dict[str, Any]] = {}
    tool_context: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = [{
        "role": "user",
        "content": coach_prompt(battle_id, preset),
    }]
    payload: dict[str, Any] = {
        "model": model,
        "system": COACH_AI_SYSTEM_PROMPT,
        "messages": messages,
        "tools": tools,
        "max_tokens": max_output_tokens,
    }
    payload.update(thinking_payload)

    final_text = ""
    last_text_response_incomplete = False
    tool_rounds = 0
    await _emit_agent_event(event_sink, {
        "type": "run_started",
        "provider": "anthropic",
        "mode": "real",
        "preset": preset,
        "model": model,
        "battleId": battle_id,
    })
    while True:
        await _emit_agent_event(event_sink, {
            "type": "model_request_started",
            "provider": "anthropic",
            "model": model,
            "toolRound": tool_rounds + 1,
            "toolChoice": "auto",
        })
        response = await anthropic_messages_create(payload, timeout_seconds)
        responses.append(response)
        final_text = _anthropic_response_text(response)
        last_text_response_incomplete = response.get("stop_reason") == "max_tokens"
        calls = _anthropic_tool_calls(response)
        await _emit_agent_event(event_sink, {
            "type": "model_response_received",
            "provider": "anthropic",
            "responseId": response.get("id"),
            "toolRound": tool_rounds + 1,
            "toolCallCount": len(calls),
            "hasText": bool(final_text),
            "stopReason": response.get("stop_reason"),
        })
        if not calls:
            break
        if tool_rounds >= max_tool_rounds:
            settings["toolLimitReached"] = True
            break

        messages.append({
            "role": "assistant",
            "content": response.get("content") or [],
        })
        tool_results = []
        for call in calls:
            tool_name = str(call.get("name"))
            tool_args = normalize_coach_tool_args(tool_name, call.get("args") or {}, battle_id)
            await _emit_agent_event(event_sink, {
                "type": "tool_started",
                "name": tool_name,
                "args": tool_args,
                "callId": call.get("callId"),
            })
            output, trace = run_coach_tool(
                tool_name,
                tool_args,
                postmortem_dir=postmortem_dir,
                replay_dir=replay_dir,
            )
            compact_output = compact_tool_output_for_model(tool_name, output)
            trace["callId"] = call.get("callId")
            trace["modelOutputBytes"] = len(json.dumps(compact_output, ensure_ascii=False))
            tool_calls.append(trace)
            tool_outputs_by_name[tool_name] = compact_output
            tool_context.append({
                "name": tool_name,
                "args": tool_args,
                "output": compact_output,
            })
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": call.get("callId"),
                "content": json.dumps(compact_output, ensure_ascii=False),
            })
            await _emit_agent_event(event_sink, {
                "type": "tool_completed",
                "toolCall": trace,
                **_tool_output_event_payload(compact_output),
            })

        messages.append({
            "role": "user",
            "content": tool_results,
        })
        tool_rounds += 1
        payload = {
            "model": model,
            "system": COACH_AI_SYSTEM_PROMPT,
            "messages": messages,
            "tools": tools,
            "max_tokens": max_output_tokens,
        }
        payload.update(thinking_payload)

    if (
        not final_text
        or settings["toolLimitReached"]
        or last_text_response_incomplete
        or looks_truncated_text(final_text)
    ):
        settings["standaloneSynthesisPass"] = True
        if final_text and (last_text_response_incomplete or looks_truncated_text(final_text)):
            settings["textRepairPass"] = True
            settings["textWasTruncated"] = True
        synthesis_payload = {
            "model": model,
            "system": COACH_AI_SYSTEM_PROMPT,
            "messages": [{
                "role": "user",
                "content": coach_synthesis_prompt(battle_id, preset, tool_context),
            }],
            "max_tokens": max(max_output_tokens, 1800),
        }
        synthesis_payload.update(thinking_payload)
        response = await anthropic_messages_create(synthesis_payload, timeout_seconds)
        responses.append(response)
        final_text = _anthropic_response_text(response)
        last_text_response_incomplete = response.get("stop_reason") == "max_tokens"

    refusal_reason = _anthropic_refusal_reason(responses)
    if refusal_reason:
        settings["refused"] = True
        settings["refusalReason"] = refusal_reason

    if not final_text or last_text_response_incomplete or looks_truncated_text(final_text):
        settings["deterministicFallback"] = True
        if refusal_reason:
            settings["fallbackReason"] = refusal_reason
        elif not final_text:
            settings["fallbackReason"] = "Anthropic returned no final text after tool loop and synthesis pass."
        elif last_text_response_incomplete:
            settings["fallbackReason"] = "Anthropic final text stopped at max_tokens after repair/synthesis."
        else:
            settings["fallbackReason"] = "Anthropic final text looked truncated after repair/synthesis."
        final_text = deterministic_agent_answer(
            preset,
            tool_outputs_by_name.get("get_coach_brief") or _coach_brief(
                battle_id,
                postmortem_dir=postmortem_dir,
                replay_dir=replay_dir,
            ),
            tool_outputs_by_name.get("get_battle_context"),
            tool_outputs_by_name.get("get_archive_context"),
            tool_outputs_by_name.get("get_team_coach_brief"),
        )

    brief = _coach_brief(battle_id, postmortem_dir=postmortem_dir, replay_dir=replay_dir)
    latency_ms = int((time.perf_counter() - started) * 1000)
    settings["responseCount"] = len(responses)
    settings["toolRounds"] = tool_rounds
    settings["stopReasons"] = [
        item.get("stop_reason")
        for item in responses
    ]
    settings["responseSummaries"] = _anthropic_response_summaries(responses)
    run = {
        "runId": f"coach-{started_at_ms}-{uuid.uuid4().hex[:8]}",
        "battleId": battle_id,
        "mode": "real",
        "provider": "anthropic",
        "preset": preset,
        "model": model,
        "startedAtMs": started_at_ms,
        "startedAtLabel": _timestamp_label(started_at_ms),
        "latencyMs": latency_ms,
        "settings": settings,
        "toolCalls": tool_calls,
        "answer": final_text,
        "comparisonMetrics": coach_agent_metrics(brief, tool_calls),
        "usage": _anthropic_usage_from_responses(responses),
        "responseIds": [
            item.get("id")
            for item in responses
            if isinstance(item.get("id"), str)
        ],
    }
    write_coach_agent_trace(run, directory=trace_dir)
    await _emit_agent_event(event_sink, {
        "type": "answer_ready",
        "answer": final_text,
        "usage": run.get("usage"),
    })
    return run


def coach_agent_run(
    battle_id: str,
    preset_id: str,
    *,
    postmortem_dir: Path,
    replay_dir: Path,
    trace_dir: Path,
) -> dict[str, Any]:
    preset = _coach_preset(preset_id)
    started = time.perf_counter()
    started_at_ms = int(datetime.now().timestamp() * 1000)
    tool_calls: list[dict[str, Any]] = []
    outputs: dict[str, dict[str, Any]] = {}

    for call in fake_tool_plan(preset, battle_id):
        output, trace = run_coach_tool(
            call["name"],
            call["args"],
            postmortem_dir=postmortem_dir,
            replay_dir=replay_dir,
        )
        tool_calls.append(trace)
        outputs[call["name"]] = output

    brief = outputs["get_coach_brief"]
    answer = fake_agent_answer(
        preset,
        brief,
        outputs.get("get_battle_context"),
        outputs.get("get_archive_context"),
        outputs.get("get_team_coach_brief"),
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    run = {
        "runId": f"coach-{started_at_ms}-{uuid.uuid4().hex[:8]}",
        "battleId": battle_id,
        "mode": "fake",
        "provider": preset["provider"],
        "preset": preset,
        "model": preset["modelLabel"],
        "startedAtMs": started_at_ms,
        "startedAtLabel": _timestamp_label(started_at_ms),
        "latencyMs": latency_ms,
        "toolCalls": tool_calls,
        "answer": answer,
        "comparisonMetrics": coach_agent_metrics(brief, tool_calls),
        "usage": {
            "inputTokens": None,
            "outputTokens": None,
            "totalTokens": None,
            "reasoningTokens": None,
            "costUsd": None,
            "note": "fake provider; real usage is added when provider clients are wired",
        },
    }
    write_coach_agent_trace(run, directory=trace_dir)
    return run


async def coach_agent_run_fake_streaming(
    battle_id: str,
    preset_id: str,
    *,
    postmortem_dir: Path,
    replay_dir: Path,
    trace_dir: Path,
    event_sink: AgentEventSink | None = None,
) -> dict[str, Any]:
    preset = _coach_preset(preset_id)
    started = time.perf_counter()
    started_at_ms = int(datetime.now().timestamp() * 1000)
    tool_calls: list[dict[str, Any]] = []
    outputs: dict[str, dict[str, Any]] = {}

    await _emit_agent_event(event_sink, {
        "type": "run_started",
        "provider": preset["provider"],
        "mode": "fake",
        "preset": preset,
        "model": preset["modelLabel"],
        "battleId": battle_id,
    })

    for call in fake_tool_plan(preset, battle_id):
        await _emit_agent_event(event_sink, {
            "type": "tool_started",
            "name": call["name"],
            "args": call["args"],
        })
        output, trace = run_coach_tool(
            call["name"],
            call["args"],
            postmortem_dir=postmortem_dir,
            replay_dir=replay_dir,
        )
        compact_output = compact_tool_output_for_model(call["name"], output)
        trace["modelOutputBytes"] = len(json.dumps(compact_output, ensure_ascii=False))
        tool_calls.append(trace)
        outputs[call["name"]] = output
        await _emit_agent_event(event_sink, {
            "type": "tool_completed",
            "toolCall": trace,
            **_tool_output_event_payload(compact_output),
        })

    brief = outputs["get_coach_brief"]
    answer = fake_agent_answer(
        preset,
        brief,
        outputs.get("get_battle_context"),
        outputs.get("get_archive_context"),
        outputs.get("get_team_coach_brief"),
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    run = {
        "runId": f"coach-{started_at_ms}-{uuid.uuid4().hex[:8]}",
        "battleId": battle_id,
        "mode": "fake",
        "provider": preset["provider"],
        "preset": preset,
        "model": preset["modelLabel"],
        "startedAtMs": started_at_ms,
        "startedAtLabel": _timestamp_label(started_at_ms),
        "latencyMs": latency_ms,
        "toolCalls": tool_calls,
        "answer": answer,
        "comparisonMetrics": coach_agent_metrics(brief, tool_calls),
        "usage": {
            "inputTokens": None,
            "outputTokens": None,
            "totalTokens": None,
            "reasoningTokens": None,
            "costUsd": None,
            "note": "fake provider; real usage is added when provider clients are wired",
        },
    }
    write_coach_agent_trace(run, directory=trace_dir)
    await _emit_agent_event(event_sink, {
        "type": "answer_ready",
        "answer": answer,
        "usage": run.get("usage"),
    })
    return run


async def coach_agent_run_async(
    battle_id: str,
    preset_id: str,
    *,
    postmortem_dir: Path,
    replay_dir: Path,
    trace_dir: Path,
    run_mode: str = "fake",
    event_sink: AgentEventSink | None = None,
) -> dict[str, Any]:
    preset = _coach_preset(preset_id)
    mode = normalize_run_mode(run_mode)
    if preset.get("provider") == "anthropic":
        has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        if mode == "real" and not has_key:
            raise HTTPException(
                status_code=503,
                detail="ANTHROPIC_API_KEY is not set; choose fake mode or export the key before running real Anthropic.",
            )
        if has_key and mode in {"real", "auto"}:
            return await anthropic_coach_agent_run(
                battle_id,
                preset,
                postmortem_dir=postmortem_dir,
                replay_dir=replay_dir,
                trace_dir=trace_dir,
                event_sink=event_sink,
            )
    if should_run_real_provider(preset, run_mode):
        return await openai_coach_agent_run(
            battle_id,
            preset,
            postmortem_dir=postmortem_dir,
            replay_dir=replay_dir,
            trace_dir=trace_dir,
            event_sink=event_sink,
        )
    if event_sink is not None:
        return await coach_agent_run_fake_streaming(
            battle_id,
            preset_id,
            postmortem_dir=postmortem_dir,
            replay_dir=replay_dir,
            trace_dir=trace_dir,
            event_sink=event_sink,
        )
    return coach_agent_run(
        battle_id,
        preset_id,
        postmortem_dir=postmortem_dir,
        replay_dir=replay_dir,
        trace_dir=trace_dir,
    )


def _team_planned_tool_args(
    planned: dict[str, Any],
    battle_id: str,
    outputs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    args = dict(planned["args"])
    name = str(planned["name"])
    if name == "get_pokemon_profile" and not args.get("species"):
        overview = outputs.get("get_team_overview") or {}
        roster = (overview.get("team") or {}).get("roster") or []
        args["species"] = str(roster[0]) if roster else ""
    if name in {"get_pokemon_battle_timeline", "get_team_state_at_turn"}:
        overview = outputs.get("get_team_overview") or {}
        priorities = overview.get("reviewPriorities") or []
        roster = (overview.get("team") or {}).get("roster") or []
        if name == "get_pokemon_battle_timeline" and not args.get("species"):
            args["species"] = str(roster[0]) if roster else ""
        if priorities and isinstance(priorities[0], dict):
            args["targetBattleId"] = priorities[0].get("battleId") or battle_id
            args["turn"] = priorities[0].get("turn") or args.get("turn") or 1
        else:
            args["targetBattleId"] = args.get("targetBattleId") or battle_id
    if name == "get_battle_window":
        overview = outputs.get("get_team_overview") or {}
        priorities = overview.get("reviewPriorities") or []
        if priorities and isinstance(priorities[0], dict):
            args["battleId"] = priorities[0].get("battleId") or battle_id
            args["turn"] = priorities[0].get("turn") or args.get("turn") or 1
        args["_anchorBattleId"] = battle_id
    return args


def _append_team_tool_result(
    planned_name: str,
    args: dict[str, Any],
    output: dict[str, Any],
    trace: dict[str, Any],
    *,
    tool_calls: list[dict[str, Any]],
    outputs: dict[str, dict[str, Any]],
    tool_context: list[dict[str, Any]],
) -> dict[str, Any]:
    compact_output = compact_tool_output_for_model(planned_name, output)
    trace["source"] = "server_team_context"
    trace["modelOutputBytes"] = len(json.dumps(compact_output, ensure_ascii=False))
    tool_calls.append(trace)
    outputs[planned_name] = compact_output
    tool_context.append({
        "name": planned_name,
        "args": trace["args"],
        "output": compact_output,
    })
    return compact_output


def _run_team_agent_tools(
    battle_id: str,
    preset: dict[str, Any],
    *,
    postmortem_dir: Path,
    replay_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    tool_calls: list[dict[str, Any]] = []
    outputs: dict[str, dict[str, Any]] = {}
    tool_context: list[dict[str, Any]] = []
    for planned in fake_team_tool_plan(preset, battle_id):
        args = _team_planned_tool_args(planned, battle_id, outputs)
        output, trace = run_team_coach_tool(
            planned["name"],
            args,
            postmortem_dir=postmortem_dir,
            replay_dir=replay_dir,
        )
        _append_team_tool_result(
            planned["name"],
            args,
            output,
            trace,
            tool_calls=tool_calls,
            outputs=outputs,
            tool_context=tool_context,
        )
    return tool_calls, outputs, tool_context


async def openai_team_coach_agent_run(
    battle_id: str,
    preset: dict[str, Any],
    *,
    postmortem_dir: Path,
    replay_dir: Path,
    trace_dir: Path,
    event_sink: AgentEventSink | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    started_at_ms = int(datetime.now().timestamp() * 1000)
    model = str(preset.get("apiModel") or preset.get("modelLabel"))
    max_output_tokens = int(preset.get("maxOutputTokens") or 1200)
    timeout_seconds = int(preset.get("timeoutSeconds") or 60)
    max_tool_rounds = int(preset.get("maxToolRounds") or 4)
    settings = {
        "api": "responses",
        "model": model,
        "reasoningEffort": preset.get("openaiReasoningEffort") or "medium",
        "maxOutputTokens": max_output_tokens,
        "maxToolRounds": max_tool_rounds,
        "timeoutSeconds": timeout_seconds,
        "toolChoice": "model-driven compact team tools",
        "toolCount": len(OPENAI_TEAM_COACH_TOOLS),
        "finalizationPass": False,
        "standaloneSynthesisPass": False,
        "deterministicFallback": False,
        "textRepairPass": False,
        "textWasTruncated": False,
        "toolLimitReached": False,
    }

    tool_calls: list[dict[str, Any]] = []
    tool_outputs_by_name: dict[str, dict[str, Any]] = {}
    tool_context: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    payload: dict[str, Any] = {
        "model": model,
        "instructions": TEAM_COACH_AI_SYSTEM_PROMPT,
        "input": team_coach_prompt(battle_id, preset),
        "tools": OPENAI_TEAM_COACH_TOOLS,
        "tool_choice": {"type": "function", "name": "get_team_overview"},
        "reasoning": {"effort": settings["reasoningEffort"]},
        "max_output_tokens": max_output_tokens,
    }
    final_text = ""
    last_response_id: str | None = None
    last_text_response_incomplete = False
    tool_rounds = 0
    await _emit_agent_event(event_sink, {
        "type": "run_started",
        "provider": "openai",
        "mode": "real",
        "preset": preset,
        "model": model,
        "battleId": battle_id,
    })
    while True:
        await _emit_agent_event(event_sink, {
            "type": "model_request_started",
            "provider": "openai",
            "model": model,
            "toolRound": tool_rounds + 1,
            "toolChoice": payload.get("tool_choice") or "auto",
        })
        response = await openai_responses_create(payload, timeout_seconds)
        responses.append(response)
        last_response_id = response.get("id") if isinstance(response.get("id"), str) else last_response_id
        final_text = response_text(response)
        last_text_response_incomplete = response_incomplete(response) if final_text else False
        calls = response_function_calls(response)
        await _emit_agent_event(event_sink, {
            "type": "model_response_received",
            "provider": "openai",
            "responseId": response.get("id"),
            "toolRound": tool_rounds + 1,
            "toolCallCount": len(calls),
            "hasText": bool(final_text),
        })
        if not calls:
            break
        if tool_rounds >= max_tool_rounds:
            settings["toolLimitReached"] = True
            break

        function_outputs = []
        for call in calls:
            tool_name = str(call.get("name"))
            tool_args = normalize_team_tool_args(tool_name, call.get("args") or {}, battle_id)
            if tool_name == "get_battle_window":
                tool_args["_anchorBattleId"] = battle_id
            await _emit_agent_event(event_sink, {
                "type": "tool_started",
                "name": tool_name,
                "args": tool_args,
                "callId": call.get("callId"),
            })
            output, trace = run_team_coach_tool(
                tool_name,
                tool_args,
                postmortem_dir=postmortem_dir,
                replay_dir=replay_dir,
            )
            compact_output = compact_tool_output_for_model(tool_name, output)
            trace["callId"] = call.get("callId")
            trace["modelOutputBytes"] = len(json.dumps(compact_output, ensure_ascii=False))
            tool_calls.append(trace)
            tool_outputs_by_name[tool_name] = compact_output
            tool_context.append({
                "name": tool_name,
                "args": tool_args,
                "output": compact_output,
            })
            function_outputs.append({
                "type": "function_call_output",
                "call_id": call.get("callId"),
                "output": json.dumps(compact_output, ensure_ascii=False),
            })
            await _emit_agent_event(event_sink, {
                "type": "tool_completed",
                "toolCall": trace,
                **_tool_output_event_payload(compact_output),
            })

        tool_rounds += 1
        if tool_rounds >= max_tool_rounds:
            settings["finalizationPass"] = True
            settings["forcedFinalAfterToolBudget"] = True
            settings["budgetedSynthesisPass"] = True
            payload = {
                "model": model,
                "instructions": TEAM_COACH_AI_SYSTEM_PROMPT,
                "input": team_coach_synthesis_prompt(battle_id, preset, tool_context),
                "reasoning": {"effort": settings["reasoningEffort"]},
                "max_output_tokens": max(max_output_tokens, 2200),
            }
        else:
            payload = {
                "model": model,
                "previous_response_id": last_response_id,
                "input": function_outputs,
                "tools": OPENAI_TEAM_COACH_TOOLS,
                "tool_choice": "auto",
                "reasoning": {"effort": settings["reasoningEffort"]},
                "max_output_tokens": max_output_tokens,
            }

    if not final_text and settings["toolLimitReached"]:
        settings["standaloneSynthesisPass"] = True
        response = await openai_responses_create(
            {
                "model": model,
                "instructions": TEAM_COACH_AI_SYSTEM_PROMPT,
                "input": team_coach_synthesis_prompt(battle_id, preset, tool_context),
                "reasoning": {"effort": settings["reasoningEffort"]},
                "max_output_tokens": max(max_output_tokens, 1800),
            },
            timeout_seconds,
        )
        responses.append(response)
        last_response_id = response.get("id") if isinstance(response.get("id"), str) else last_response_id
        final_text = response_text(response)
        last_text_response_incomplete = response_incomplete(response) if final_text else False

    if not final_text:
        if not last_response_id:
            raise HTTPException(status_code=502, detail="OpenAI did not return a response id or final team-coach text")
        settings["finalizationPass"] = True
        response = await openai_responses_create(
            {
                "model": model,
                "instructions": TEAM_COACH_AI_SYSTEM_PROMPT,
                "previous_response_id": last_response_id,
                "input": team_coach_final_answer_prompt(battle_id, preset, tool_calls),
                "reasoning": {"effort": settings["reasoningEffort"]},
                "max_output_tokens": max(max_output_tokens, 1800),
            },
            timeout_seconds,
        )
        responses.append(response)
        final_text = response_text(response)
        last_text_response_incomplete = response_incomplete(response) if final_text else False

    if final_text and (last_text_response_incomplete or looks_truncated_text(final_text)):
        if not last_response_id:
            raise HTTPException(status_code=502, detail="OpenAI returned truncated team-coach text without a response id")
        settings["textRepairPass"] = True
        settings["textWasTruncated"] = True
        response = await openai_responses_create(
            {
                "model": model,
                "instructions": TEAM_COACH_AI_SYSTEM_PROMPT,
                "previous_response_id": last_response_id,
                "input": (
                    "Rewrite the complete final team-coach answer using only the tool outputs already provided. "
                    "Do not call tools. End cleanly after Team-building suggestions."
                ),
                "reasoning": {"effort": settings["reasoningEffort"]},
                "max_output_tokens": max(max_output_tokens, 2200),
            },
            timeout_seconds,
        )
        responses.append(response)
        repaired_text = response_text(response)
        last_text_response_incomplete = response_incomplete(response) if repaired_text else False
        if repaired_text:
            final_text = repaired_text

    if not final_text or last_text_response_incomplete or looks_truncated_text(final_text):
        raise HTTPException(
            status_code=502,
            detail="OpenAI did not return complete final team-coach text after the tool loop and final answer pass",
        )

    team_context = tool_outputs_by_name.get("get_team_overview") or _team_overview_from_brief(_team_coach_brief(
        battle_id,
        postmortem_dir=postmortem_dir,
        replay_dir=replay_dir,
    ))

    latency_ms = int((time.perf_counter() - started) * 1000)
    settings["responseCount"] = len(responses)
    settings["toolRounds"] = tool_rounds
    run = {
        "runId": f"team-{started_at_ms}-{uuid.uuid4().hex[:8]}",
        "battleId": battle_id,
        "mode": "real",
        "provider": "openai",
        "preset": preset,
        "model": model,
        "startedAtMs": started_at_ms,
        "startedAtLabel": _timestamp_label(started_at_ms),
        "latencyMs": latency_ms,
        "settings": settings,
        "toolCalls": tool_calls,
        "answer": final_text,
        "comparisonMetrics": team_agent_metrics(team_context, tool_calls),
        "usage": usage_from_responses(responses),
        "responseIds": [
            item.get("id")
            for item in responses
            if isinstance(item.get("id"), str)
        ],
    }
    write_coach_agent_trace(run, directory=trace_dir)
    await _emit_agent_event(event_sink, {
        "type": "answer_ready",
        "answer": final_text,
        "usage": run.get("usage"),
    })
    return run


async def anthropic_team_coach_agent_run(
    battle_id: str,
    preset: dict[str, Any],
    *,
    postmortem_dir: Path,
    replay_dir: Path,
    trace_dir: Path,
    event_sink: AgentEventSink | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    started_at_ms = int(datetime.now().timestamp() * 1000)
    model = str(preset.get("apiModel") or preset.get("modelLabel"))
    max_output_tokens = int(preset.get("maxOutputTokens") or 1200)
    timeout_seconds = int(preset.get("timeoutSeconds") or 60)
    max_tool_rounds = int(preset.get("maxToolRounds") or 4)
    thinking_payload = _anthropic_thinking_payload(preset)
    tools = _anthropic_tools_from_openai(
        OPENAI_TEAM_COACH_TOOLS,
        descriptions=ANTHROPIC_TEAM_COACH_TOOL_DESCRIPTIONS,
    )
    settings = {
        "api": "messages",
        "model": model,
        "maxOutputTokens": max_output_tokens,
        "maxToolRounds": max_tool_rounds,
        "timeoutSeconds": timeout_seconds,
        "toolChoice": "model-driven compact team tools",
        "toolCount": len(tools),
        "finalizationPass": False,
        "standaloneSynthesisPass": False,
        "deterministicFallback": False,
        "textRepairPass": False,
        "textWasTruncated": False,
        "toolLimitReached": False,
        "fallbackReason": None,
        "thinkingMode": preset.get("anthropicThinking") or "off",
        "thinkingEffort": preset.get("anthropicThinkingEffort"),
        "thinkingDisplay": preset.get("anthropicThinkingDisplay"),
    }

    tool_calls: list[dict[str, Any]] = []
    tool_outputs_by_name: dict[str, dict[str, Any]] = {}
    tool_context: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = [{
        "role": "user",
        "content": anthropic_team_coach_prompt(battle_id, preset),
    }]
    payload: dict[str, Any] = {
        "model": model,
        "system": ANTHROPIC_TEAM_COACH_AI_SYSTEM_PROMPT,
        "messages": messages,
        "tools": tools,
        "max_tokens": max_output_tokens,
    }
    payload.update(thinking_payload)

    final_text = ""
    last_text_response_incomplete = False
    tool_rounds = 0
    await _emit_agent_event(event_sink, {
        "type": "run_started",
        "provider": "anthropic",
        "mode": "real",
        "preset": preset,
        "model": model,
        "battleId": battle_id,
    })
    while True:
        await _emit_agent_event(event_sink, {
            "type": "model_request_started",
            "provider": "anthropic",
            "model": model,
            "toolRound": tool_rounds + 1,
            "toolChoice": "auto",
        })
        response = await anthropic_messages_create(payload, timeout_seconds)
        responses.append(response)
        final_text = _anthropic_response_text(response)
        last_text_response_incomplete = response.get("stop_reason") == "max_tokens"
        calls = _anthropic_tool_calls(response)
        await _emit_agent_event(event_sink, {
            "type": "model_response_received",
            "provider": "anthropic",
            "responseId": response.get("id"),
            "toolRound": tool_rounds + 1,
            "toolCallCount": len(calls),
            "hasText": bool(final_text),
            "stopReason": response.get("stop_reason"),
        })
        if not calls:
            break
        if tool_rounds >= max_tool_rounds:
            settings["toolLimitReached"] = True
            break

        messages.append({
            "role": "assistant",
            "content": response.get("content") or [],
        })
        tool_results = []
        for call in calls:
            tool_name = str(call.get("name"))
            tool_args = normalize_team_tool_args(tool_name, call.get("args") or {}, battle_id)
            if tool_name == "get_battle_window":
                tool_args["_anchorBattleId"] = battle_id
            await _emit_agent_event(event_sink, {
                "type": "tool_started",
                "name": tool_name,
                "args": tool_args,
                "callId": call.get("callId"),
            })
            output, trace = run_team_coach_tool(
                tool_name,
                tool_args,
                postmortem_dir=postmortem_dir,
                replay_dir=replay_dir,
            )
            compact_output = compact_tool_output_for_model(tool_name, output)
            trace["callId"] = call.get("callId")
            trace["modelOutputBytes"] = len(json.dumps(compact_output, ensure_ascii=False))
            tool_calls.append(trace)
            tool_outputs_by_name[tool_name] = compact_output
            tool_context.append({
                "name": tool_name,
                "args": tool_args,
                "output": compact_output,
            })
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": call.get("callId"),
                "content": json.dumps(compact_output, ensure_ascii=False),
            })
            await _emit_agent_event(event_sink, {
                "type": "tool_completed",
                "toolCall": trace,
                **_tool_output_event_payload(compact_output),
            })

        messages.append({
            "role": "user",
            "content": tool_results,
        })
        tool_rounds += 1
        payload = {
            "model": model,
            "system": ANTHROPIC_TEAM_COACH_AI_SYSTEM_PROMPT,
            "messages": messages,
            "tools": tools,
            "max_tokens": max_output_tokens,
        }
        payload.update(thinking_payload)

    if (
        not final_text
        or settings["toolLimitReached"]
        or last_text_response_incomplete
        or looks_truncated_text(final_text)
    ):
        settings["standaloneSynthesisPass"] = True
        if final_text and (last_text_response_incomplete or looks_truncated_text(final_text)):
            settings["textRepairPass"] = True
            settings["textWasTruncated"] = True
        synthesis_payload = {
                "model": model,
                "system": ANTHROPIC_TEAM_COACH_AI_SYSTEM_PROMPT,
                "messages": [{
                    "role": "user",
                    "content": anthropic_team_coach_synthesis_prompt(battle_id, preset, tool_context),
                }],
                "max_tokens": max(max_output_tokens, 1800),
            }
        synthesis_payload.update(thinking_payload)
        response = await anthropic_messages_create(
            synthesis_payload,
            timeout_seconds,
        )
        responses.append(response)
        final_text = _anthropic_response_text(response)
        last_text_response_incomplete = response.get("stop_reason") == "max_tokens"

    refusal_reason = _anthropic_refusal_reason(responses)
    if refusal_reason:
        settings["refused"] = True
        settings["refusalReason"] = refusal_reason

    if not final_text or last_text_response_incomplete or looks_truncated_text(final_text):
        settings["deterministicFallback"] = True
        if refusal_reason:
            settings["fallbackReason"] = refusal_reason
        elif not final_text:
            settings["fallbackReason"] = "Anthropic returned no final text after tool loop and synthesis pass."
        elif last_text_response_incomplete:
            settings["fallbackReason"] = "Anthropic final text stopped at max_tokens after repair/synthesis."
        else:
            settings["fallbackReason"] = "Anthropic final text looked truncated after repair/synthesis."
        final_text = deterministic_team_agent_answer(
            preset,
            tool_outputs_by_name.get("get_team_overview") or _team_overview_from_brief(_team_coach_brief(
                battle_id,
                postmortem_dir=postmortem_dir,
                replay_dir=replay_dir,
            )),
            tool_outputs_by_name.get("get_engine_eval_cases"),
            tool_outputs_by_name.get("get_battle_window"),
        )

    team_context = tool_outputs_by_name.get("get_team_overview") or _team_overview_from_brief(_team_coach_brief(
        battle_id,
        postmortem_dir=postmortem_dir,
        replay_dir=replay_dir,
    ))
    latency_ms = int((time.perf_counter() - started) * 1000)
    settings["responseCount"] = len(responses)
    settings["toolRounds"] = tool_rounds
    settings["stopReasons"] = [
        item.get("stop_reason")
        for item in responses
    ]
    settings["responseSummaries"] = _anthropic_response_summaries(responses)
    run = {
        "runId": f"team-{started_at_ms}-{uuid.uuid4().hex[:8]}",
        "battleId": battle_id,
        "mode": "real",
        "provider": "anthropic",
        "preset": preset,
        "model": model,
        "startedAtMs": started_at_ms,
        "startedAtLabel": _timestamp_label(started_at_ms),
        "latencyMs": latency_ms,
        "settings": settings,
        "toolCalls": tool_calls,
        "answer": final_text,
        "comparisonMetrics": team_agent_metrics(team_context, tool_calls),
        "usage": _anthropic_usage_from_responses(responses),
        "responseIds": [
            item.get("id")
            for item in responses
            if isinstance(item.get("id"), str)
        ],
    }
    write_coach_agent_trace(run, directory=trace_dir)
    await _emit_agent_event(event_sink, {
        "type": "answer_ready",
        "answer": final_text,
        "usage": run.get("usage"),
    })
    return run


def team_coach_agent_run(
    battle_id: str,
    preset_id: str,
    *,
    postmortem_dir: Path,
    replay_dir: Path,
    trace_dir: Path,
) -> dict[str, Any]:
    preset = _coach_preset(preset_id)
    started = time.perf_counter()
    started_at_ms = int(datetime.now().timestamp() * 1000)
    tool_calls, outputs, _ = _run_team_agent_tools(
        battle_id,
        preset,
        postmortem_dir=postmortem_dir,
        replay_dir=replay_dir,
    )
    team_context = outputs["get_team_overview"]
    answer = fake_team_agent_answer(
        preset,
        team_context,
        outputs.get("get_engine_eval_cases"),
        outputs.get("get_battle_window"),
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    run = {
        "runId": f"team-{started_at_ms}-{uuid.uuid4().hex[:8]}",
        "battleId": battle_id,
        "mode": "fake",
        "provider": preset["provider"],
        "preset": preset,
        "model": preset["modelLabel"],
        "startedAtMs": started_at_ms,
        "startedAtLabel": _timestamp_label(started_at_ms),
        "latencyMs": latency_ms,
        "toolCalls": tool_calls,
        "answer": answer,
        "comparisonMetrics": team_agent_metrics(team_context, tool_calls),
        "usage": {
            "inputTokens": None,
            "outputTokens": None,
            "totalTokens": None,
            "reasoningTokens": None,
            "costUsd": None,
            "note": "fake provider; real usage is added when provider clients are wired",
        },
    }
    write_coach_agent_trace(run, directory=trace_dir)
    return run


async def team_coach_agent_run_fake_streaming(
    battle_id: str,
    preset_id: str,
    *,
    postmortem_dir: Path,
    replay_dir: Path,
    trace_dir: Path,
    event_sink: AgentEventSink | None = None,
) -> dict[str, Any]:
    preset = _coach_preset(preset_id)
    started = time.perf_counter()
    started_at_ms = int(datetime.now().timestamp() * 1000)
    tool_calls: list[dict[str, Any]] = []
    outputs: dict[str, dict[str, Any]] = {}
    tool_context: list[dict[str, Any]] = []

    await _emit_agent_event(event_sink, {
        "type": "run_started",
        "provider": preset["provider"],
        "mode": "fake",
        "preset": preset,
        "model": preset["modelLabel"],
        "battleId": battle_id,
    })

    for planned in fake_team_tool_plan(preset, battle_id):
        args = _team_planned_tool_args(planned, battle_id, outputs)
        await _emit_agent_event(event_sink, {
            "type": "tool_started",
            "name": planned["name"],
            "args": args,
        })
        output, trace = run_team_coach_tool(
            planned["name"],
            args,
            postmortem_dir=postmortem_dir,
            replay_dir=replay_dir,
        )
        compact_output = _append_team_tool_result(
            planned["name"],
            args,
            output,
            trace,
            tool_calls=tool_calls,
            outputs=outputs,
            tool_context=tool_context,
        )
        await _emit_agent_event(event_sink, {
            "type": "tool_completed",
            "toolCall": trace,
            **_tool_output_event_payload(compact_output),
        })

    team_context = outputs["get_team_overview"]
    answer = fake_team_agent_answer(
        preset,
        team_context,
        outputs.get("get_engine_eval_cases"),
        outputs.get("get_battle_window"),
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    run = {
        "runId": f"team-{started_at_ms}-{uuid.uuid4().hex[:8]}",
        "battleId": battle_id,
        "mode": "fake",
        "provider": preset["provider"],
        "preset": preset,
        "model": preset["modelLabel"],
        "startedAtMs": started_at_ms,
        "startedAtLabel": _timestamp_label(started_at_ms),
        "latencyMs": latency_ms,
        "toolCalls": tool_calls,
        "answer": answer,
        "comparisonMetrics": team_agent_metrics(team_context, tool_calls),
        "usage": {
            "inputTokens": None,
            "outputTokens": None,
            "totalTokens": None,
            "reasoningTokens": None,
            "costUsd": None,
            "note": "fake provider; real usage is added when provider clients are wired",
        },
    }
    write_coach_agent_trace(run, directory=trace_dir)
    await _emit_agent_event(event_sink, {
        "type": "answer_ready",
        "answer": answer,
        "usage": run.get("usage"),
    })
    return run


async def team_coach_agent_run_async(
    battle_id: str,
    preset_id: str,
    *,
    postmortem_dir: Path,
    replay_dir: Path,
    trace_dir: Path,
    run_mode: str = "fake",
    event_sink: AgentEventSink | None = None,
) -> dict[str, Any]:
    preset = _coach_preset(preset_id)
    mode = normalize_run_mode(run_mode)
    if preset.get("provider") == "anthropic":
        has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        if mode == "real" and not has_key:
            raise HTTPException(
                status_code=503,
                detail="ANTHROPIC_API_KEY is not set; choose fake mode or export the key before running real Anthropic.",
            )
        if has_key and mode in {"real", "auto"}:
            return await anthropic_team_coach_agent_run(
                battle_id,
                preset,
                postmortem_dir=postmortem_dir,
                replay_dir=replay_dir,
                trace_dir=trace_dir,
                event_sink=event_sink,
            )
    if should_run_real_provider(preset, run_mode):
        return await openai_team_coach_agent_run(
            battle_id,
            preset,
            postmortem_dir=postmortem_dir,
            replay_dir=replay_dir,
            trace_dir=trace_dir,
            event_sink=event_sink,
        )
    if event_sink is not None:
        return await team_coach_agent_run_fake_streaming(
            battle_id,
            preset_id,
            postmortem_dir=postmortem_dir,
            replay_dir=replay_dir,
            trace_dir=trace_dir,
            event_sink=event_sink,
        )
    return team_coach_agent_run(
        battle_id,
        preset_id,
        postmortem_dir=postmortem_dir,
        replay_dir=replay_dir,
        trace_dir=trace_dir,
    )


async def openai_pattern_agent_run(
    pattern_id: str,
    preset: dict[str, Any],
    *,
    postmortem_dir: Path,
    trace_dir: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    started_at_ms = int(datetime.now().timestamp() * 1000)
    model = str(preset.get("apiModel") or preset.get("modelLabel"))
    max_output_tokens = int(preset.get("maxOutputTokens") or 1200)
    pattern_output_tokens = pattern_output_token_budget(max_output_tokens)
    timeout_seconds = int(preset.get("timeoutSeconds") or 60)
    settings = {
        "api": "responses",
        "model": model,
        "reasoningEffort": preset.get("openaiReasoningEffort") or "medium",
        "maxOutputTokens": max_output_tokens,
        "patternMaxOutputTokens": pattern_output_tokens,
        "timeoutSeconds": timeout_seconds,
        "toolChoice": "server-side deterministic context",
        "finalizationPass": False,
        "standaloneSynthesisPass": True,
        "deterministicFallback": False,
        "textRepairPass": False,
        "textWasTruncated": False,
    }

    tool_calls: list[dict[str, Any]] = []
    tool_context: list[dict[str, Any]] = []
    tool_outputs_by_name: dict[str, dict[str, Any]] = {}
    for planned in fake_pattern_tool_plan(preset, pattern_id):
        output, trace = run_pattern_tool(
            planned["name"],
            planned["args"],
            postmortem_dir=postmortem_dir,
        )
        compact_output = compact_pattern_tool_output_for_model(planned["name"], output)
        trace["source"] = "server_pattern_context"
        trace["modelOutputBytes"] = len(json.dumps(compact_output, ensure_ascii=False))
        tool_calls.append(trace)
        tool_outputs_by_name[planned["name"]] = compact_output
        tool_context.append({
            "name": planned["name"],
            "args": planned["args"],
            "output": compact_output,
        })

    responses: list[dict[str, Any]] = []
    response = await openai_responses_create(
        {
            "model": model,
            "instructions": PATTERN_AI_SYSTEM_PROMPT,
            "input": pattern_synthesis_prompt(pattern_id, preset, tool_context),
            "reasoning": {"effort": settings["reasoningEffort"]},
            "max_output_tokens": pattern_output_tokens,
        },
        timeout_seconds,
    )
    responses.append(response)
    final_text = response_text(response)
    last_text_response_incomplete = response_incomplete(response) if final_text else False

    if final_text and (last_text_response_incomplete or looks_truncated_text(final_text)):
        settings["textRepairPass"] = True
        settings["textWasTruncated"] = True
        repair_model = os.environ.get(
            "SHOWDOWN_OPENAI_SYNTHESIS_MODEL",
            os.environ.get("SHOWDOWN_OPENAI_FAST_MODEL", "gpt-5.4-mini"),
        )
        settings["repairModel"] = repair_model
        response = await openai_responses_create(
            {
                "model": repair_model,
                "instructions": PATTERN_AI_SYSTEM_PROMPT,
                "input": pattern_synthesis_prompt(pattern_id, preset, tool_context),
                "reasoning": {"effort": "medium"},
                "max_output_tokens": pattern_output_tokens,
            },
            timeout_seconds,
        )
        responses.append(response)
        repaired_text = response_text(response)
        last_text_response_incomplete = response_incomplete(response) if repaired_text else False
        if repaired_text:
            final_text = repaired_text

    pattern_context = tool_outputs_by_name.get("get_pattern_context") or compact_pattern_context_for_model(
        _pattern_agent_context(pattern_id, postmortem_dir=postmortem_dir)
    )
    if not final_text or last_text_response_incomplete or looks_truncated_text(final_text):
        settings["deterministicFallback"] = True
        final_text = deterministic_pattern_agent_answer(
            preset,
            pattern_context,
            tool_outputs_by_name.get("get_archive_context"),
        )

    latency_ms = int((time.perf_counter() - started) * 1000)
    settings["responseCount"] = len(responses)
    run = {
        "runId": f"pattern-{started_at_ms}-{uuid.uuid4().hex[:8]}",
        "patternId": pattern_id,
        "mode": "real",
        "provider": "openai",
        "preset": preset,
        "model": model,
        "startedAtMs": started_at_ms,
        "startedAtLabel": _timestamp_label(started_at_ms),
        "latencyMs": latency_ms,
        "settings": settings,
        "toolCalls": tool_calls,
        "answer": final_text,
        "comparisonMetrics": pattern_agent_metrics(pattern_context, tool_calls),
        "usage": usage_from_responses(responses),
        "responseIds": [
            item.get("id")
            for item in responses
            if isinstance(item.get("id"), str)
        ],
    }
    write_coach_agent_trace(run, directory=trace_dir)
    return run


def pattern_agent_run(
    pattern_id: str,
    preset_id: str,
    *,
    postmortem_dir: Path,
    trace_dir: Path,
) -> dict[str, Any]:
    preset = _coach_preset(preset_id)
    started = time.perf_counter()
    started_at_ms = int(datetime.now().timestamp() * 1000)
    tool_calls: list[dict[str, Any]] = []
    outputs: dict[str, dict[str, Any]] = {}

    for call in fake_pattern_tool_plan(preset, pattern_id):
        output, trace = run_pattern_tool(
            call["name"],
            call["args"],
            postmortem_dir=postmortem_dir,
        )
        tool_calls.append(trace)
        outputs[call["name"]] = output

    pattern_context = outputs["get_pattern_context"]
    answer = fake_pattern_agent_answer(
        preset,
        pattern_context,
        outputs.get("get_archive_context"),
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    run = {
        "runId": f"pattern-{started_at_ms}-{uuid.uuid4().hex[:8]}",
        "patternId": pattern_id,
        "mode": "fake",
        "provider": preset["provider"],
        "preset": preset,
        "model": preset["modelLabel"],
        "startedAtMs": started_at_ms,
        "startedAtLabel": _timestamp_label(started_at_ms),
        "latencyMs": latency_ms,
        "toolCalls": tool_calls,
        "answer": answer,
        "comparisonMetrics": pattern_agent_metrics(pattern_context, tool_calls),
        "usage": {
            "inputTokens": None,
            "outputTokens": None,
            "totalTokens": None,
            "reasoningTokens": None,
            "costUsd": None,
            "note": "fake provider; real usage is added when provider clients are wired",
        },
    }
    write_coach_agent_trace(run, directory=trace_dir)
    return run


async def pattern_agent_run_async(
    pattern_id: str,
    preset_id: str,
    *,
    postmortem_dir: Path,
    trace_dir: Path,
    run_mode: str = "fake",
) -> dict[str, Any]:
    preset = _coach_preset(preset_id)
    if should_run_real_provider(preset, run_mode):
        return await openai_pattern_agent_run(
            pattern_id,
            preset,
            postmortem_dir=postmortem_dir,
            trace_dir=trace_dir,
        )
    return pattern_agent_run(
        pattern_id,
        preset_id,
        postmortem_dir=postmortem_dir,
        trace_dir=trace_dir,
    )


async def openai_auto_label_pattern(
    pattern_id: str,
    preset: dict[str, Any],
    pattern_context: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    model = str(preset.get("apiModel") or preset.get("modelLabel"))
    max_output_tokens = int(preset.get("maxOutputTokens") or 1200)
    label_output_tokens = auto_label_output_token_budget(max_output_tokens)
    timeout_seconds = int(preset.get("timeoutSeconds") or 60)
    compact_context = compact_pattern_context_for_labeler(
        pattern_context,
        label_definitions=REVIEW_LABEL_DEFINITIONS,
        limit=int(os.environ.get("SHOWDOWN_OPENAI_AUTO_LABEL_CARD_LIMIT", "50")),
    )
    if not compact_context.get("evidence"):
        return {
            "suggestions": [],
            "raw": {"labels": []},
            "settings": {
                "api": "responses",
                "model": model,
                "reasoningEffort": AUTO_LABEL_REASONING_EFFORT,
                "maxOutputTokens": label_output_tokens,
                "timeoutSeconds": timeout_seconds,
                "evidenceCardsSent": 0,
            },
            "usage": usage_from_responses([]),
            "latencyMs": int((time.perf_counter() - started) * 1000),
        }

    response = await openai_responses_create(
        {
            "model": model,
            "instructions": REVIEW_AUTO_LABEL_SYSTEM_PROMPT,
            "input": review_auto_label_prompt(pattern_id, preset, compact_context),
            "reasoning": {"effort": AUTO_LABEL_REASONING_EFFORT},
            "max_output_tokens": label_output_tokens,
            "text": REVIEW_AUTO_LABEL_RESPONSE_FORMAT,
        },
        timeout_seconds,
    )
    responses = [response]
    settings = {
        "api": "responses",
        "model": model,
        "reasoningEffort": AUTO_LABEL_REASONING_EFFORT,
        "maxOutputTokens": label_output_tokens,
        "timeoutSeconds": timeout_seconds,
        "evidenceCardsSent": len(compact_context.get("evidence") or []),
        "totalUnreviewedEvidence": compact_context.get("totalUnreviewedEvidence"),
        "jsonRepairPass": False,
        "parseError": None,
    }
    final_text = response_text(response)
    raw: Any = {"labels": []}
    if not final_text:
        settings["parseError"] = "OpenAI returned no auto-label JSON"
    else:
        try:
            raw = _parse_jsonish_model_output(final_text)
        except HTTPException as exc:
            settings["jsonRepairPass"] = True
            settings["parseError"] = str(exc.detail)
            repair_model = os.environ.get(
                "SHOWDOWN_OPENAI_SYNTHESIS_MODEL",
                os.environ.get("SHOWDOWN_OPENAI_FAST_MODEL", model),
            )
            settings["repairModel"] = repair_model
            repair_response = await openai_responses_create(
                {
                    "model": repair_model,
                    "instructions": REVIEW_AUTO_LABEL_SYSTEM_PROMPT,
                    "input": review_auto_label_repair_prompt(
                        pattern_id,
                        preset,
                        compact_context,
                        final_text,
                    ),
                    "reasoning": {"effort": AUTO_LABEL_REASONING_EFFORT},
                    "max_output_tokens": label_output_tokens,
                    "text": REVIEW_AUTO_LABEL_RESPONSE_FORMAT,
                },
                timeout_seconds,
            )
            responses.append(repair_response)
            repaired_text = response_text(repair_response)
            if repaired_text:
                try:
                    raw = _parse_jsonish_model_output(repaired_text)
                    settings["parseError"] = None
                except HTTPException as repair_exc:
                    settings["parseError"] = str(repair_exc.detail)

    suggestions = normalize_ai_review_label_suggestions(
        pattern_context,
        raw,
        source="openai_auto_labeler",
    )
    return {
        "suggestions": suggestions,
        "raw": raw,
        "settings": settings,
        "usage": usage_from_responses(responses),
        "responseId": response.get("id"),
        "responseIds": [
            item.get("id")
            for item in responses
            if isinstance(item.get("id"), str)
        ],
        "latencyMs": int((time.perf_counter() - started) * 1000),
    }


async def auto_label_pattern(
    pattern_id: str,
    request: CoachAIRequest | None = None,
    *,
    postmortem_dir: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    request = request or CoachAIRequest()
    preset = _coach_preset(request.presetId)
    run_mode = normalize_run_mode(request.runMode)
    pattern_context = _pattern_agent_context(
        pattern_id,
        postmortem_dir=postmortem_dir,
        review_labels=load_review_labels(),
    )
    evidence = [
        item for item in (pattern_context.get("evidence") or [])
        if isinstance(item, dict)
    ]
    unreviewed_evidence = [
        item for item in evidence
        if not item.get("reviewLabel")
    ]
    evidence_scope = {
        "loaded": len(evidence),
        "unreviewed": len(unreviewed_evidence),
        "reviewed": len(evidence) - len(unreviewed_evidence),
        "totalPatternEvidence": (pattern_context.get("pattern") or {}).get("instances"),
        "scope": "current_pattern_slice",
    }
    deterministic_suggestions = suggest_review_labels_for_pattern(pattern_context)
    suggestions = deterministic_suggestions
    provider_result: dict[str, Any] | None = None
    fallback_reason: str | None = None
    ai_suggestion_count = 0
    deterministic_backfill_count = len(deterministic_suggestions)
    source = "deterministic_auto_labeler"
    provider_mode = "fake"

    if deterministic_suggestions and should_run_real_provider(preset, run_mode):
        provider_mode = "real"
        try:
            provider_result = await openai_auto_label_pattern(pattern_id, preset, pattern_context)
            ai_suggestions = provider_result.get("suggestions") or []
            if ai_suggestions:
                suggestions = merge_review_label_suggestions(ai_suggestions, deterministic_suggestions)
                ai_suggestion_count = len(ai_suggestions)
                ai_keys = {str(item.get("reviewKey") or "") for item in ai_suggestions}
                deterministic_backfill_count = sum(
                    1 for item in deterministic_suggestions
                    if str(item.get("reviewKey") or "") not in ai_keys
                )
                source = (
                    "openai_auto_labeler"
                    if deterministic_backfill_count == 0
                    else "openai_auto_labeler_with_rule_fill"
                )
            else:
                provider_settings = provider_result.get("settings") or {}
                sent = provider_settings.get("evidenceCardsSent") or 0
                if sent:
                    parse_error = provider_settings.get("parseError")
                    if parse_error:
                        fallback_reason = f"{parse_error}; deterministic backfill used."
                    else:
                        fallback_reason = "OpenAI returned no valid labels after validation; deterministic backfill used."
        except HTTPException as exc:
            if run_mode == "real":
                raise
            fallback_reason = str(exc.detail)

    saved = persist_review_label_suggestions(suggestions)
    return {
        "patternId": pattern_id,
        "source": source,
        "mode": provider_mode,
        "provider": provider_result and "openai" or preset.get("provider"),
        "preset": preset,
        "candidateCount": len(deterministic_suggestions),
        "aiSuggestionCount": ai_suggestion_count,
        "deterministicBackfillCount": deterministic_backfill_count,
        "evidenceScope": evidence_scope,
        "suggestions": suggestions,
        "saved": saved["saved"],
        "savedCount": len(saved["saved"]),
        "summary": saved["summary"],
        "providerResult": provider_result,
        "fallbackReason": fallback_reason,
        "latencyMs": int((time.perf_counter() - started) * 1000),
    }
