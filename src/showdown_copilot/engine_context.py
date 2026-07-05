"""Engine replay and field-state context helpers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .battle_turns import prediction_label
from .dashboard_archive import _as_number, _norm, _round_pct


def load_engine_replay_records(
    battle_id: str,
    directory: Path,
) -> list[dict[str, Any]]:
    path = directory / f"{battle_id}.jsonl"
    if not path.exists():
        return []

    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    records.append(row)
    except Exception:
        return []
    return records


def request_state_from_replay(record: dict[str, Any]) -> dict[str, Any] | None:
    req = record.get("engine_request")
    if not isinstance(req, dict):
        return None
    hypotheses = req.get("hypotheses")
    if isinstance(hypotheses, list) and hypotheses and isinstance(hypotheses[0], dict):
        return hypotheses[0]
    if isinstance(req.get("sideOne"), dict) and isinstance(req.get("sideTwo"), dict):
        return req
    return None


def find_replay_record_for_turn(
    records: list[dict[str, Any]],
    turn: Any,
    pick_name: Any = None,
) -> dict[str, Any] | None:
    if not isinstance(turn, int):
        return None
    candidates = [row for row in records if row.get("turn") == turn]
    if not candidates:
        return None
    normalized_pick = _norm(pick_name)
    if normalized_pick:
        for row in reversed(candidates):
            terminal = row.get("engine_response_terminal")
            if isinstance(terminal, dict) and _norm(terminal.get("bestMove")) == normalized_pick:
                return row
    return candidates[-1]


def active_pokemon(side: dict[str, Any]) -> dict[str, Any] | None:
    pokemon = side.get("pokemon")
    active_index = side.get("activeIndex")
    if not isinstance(pokemon, list) or not pokemon:
        return None
    if not isinstance(active_index, int) or active_index < 0 or active_index >= len(pokemon):
        active_index = 0
    active = pokemon[active_index]
    return active if isinstance(active, dict) else None


def hp_pct(mon: dict[str, Any]) -> float | None:
    hp = _as_number(mon.get("hp"))
    maxhp = _as_number(mon.get("maxhp"))
    if hp is None or maxhp is None or maxhp <= 0:
        return None
    return round((hp / maxhp) * 100, 1)


def pokemon_context(mon: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(mon, dict):
        return None
    moves = []
    for move in mon.get("moves") or []:
        if isinstance(move, dict):
            moves.append({
                "id": move.get("id"),
                "pp": move.get("pp"),
                "disabled": bool(move.get("disabled", False)),
            })
    return {
        "species": mon.get("species"),
        "hp": mon.get("hp"),
        "maxhp": mon.get("maxhp"),
        "hpPct": hp_pct(mon),
        "status": mon.get("status"),
        "ability": mon.get("ability"),
        "item": mon.get("item"),
        "types": mon.get("types") if isinstance(mon.get("types"), list) else [],
        "teraType": mon.get("teraType"),
        "terastallized": bool(mon.get("terastallized", False)),
        "moves": moves,
    }


def nonzero_conditions(side: dict[str, Any]) -> dict[str, Any]:
    conditions = side.get("sideConditions")
    if not isinstance(conditions, dict):
        return {}
    return {
        key: value
        for key, value in conditions.items()
        if isinstance(value, (int, float)) and value
    }


def condition_group(conditions: dict[str, Any], names: set[str]) -> dict[str, Any]:
    return {key: value for key, value in conditions.items() if key in names}


def field_state_context(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    state = request_state_from_replay(record)
    if not state:
        return None

    mine = state.get("sideOne") if isinstance(state.get("sideOne"), dict) else {}
    opp = state.get("sideTwo") if isinstance(state.get("sideTwo"), dict) else {}
    mine_conditions = nonzero_conditions(mine)
    opp_conditions = nonzero_conditions(opp)
    hazard_names = {"spikes", "stealthRock", "stickyWeb", "toxicSpikes"}
    screen_names = {"reflect", "lightScreen", "auroraVeil", "tailwind", "safeguard"}

    return {
        "source": {
            "turn": record.get("turn"),
            "rqid": record.get("rqid"),
            "capturedAtMs": record.get("captured_at_ms"),
        },
        "weather": state.get("weather"),
        "terrain": state.get("terrain"),
        "trickRoom": bool(state.get("trickRoom", False)),
        "mine": {
            "active": pokemon_context(active_pokemon(mine)),
            "conditions": mine_conditions,
            "hazards": condition_group(mine_conditions, hazard_names),
            "screens": condition_group(mine_conditions, screen_names),
            "boosts": mine.get("boosts") if isinstance(mine.get("boosts"), dict) else {},
            "volatileStatuses": mine.get("volatileStatuses") if isinstance(mine.get("volatileStatuses"), list) else [],
            "volatileStatusDurations": mine.get("volatileStatusDurations") if isinstance(mine.get("volatileStatusDurations"), dict) else {},
            "lastUsedMove": mine.get("lastUsedMove"),
        },
        "opp": {
            "active": pokemon_context(active_pokemon(opp)),
            "conditions": opp_conditions,
            "hazards": condition_group(opp_conditions, hazard_names),
            "screens": condition_group(opp_conditions, screen_names),
            "boosts": opp.get("boosts") if isinstance(opp.get("boosts"), dict) else {},
            "volatileStatuses": opp.get("volatileStatuses") if isinstance(opp.get("volatileStatuses"), list) else [],
            "volatileStatusDurations": opp.get("volatileStatusDurations") if isinstance(opp.get("volatileStatusDurations"), dict) else {},
            "lastUsedMove": opp.get("lastUsedMove"),
        },
    }


def strategic_signals(
    row: dict[str, Any],
    turn: dict[str, Any],
    field_state: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    my_pick = row.get("myPick") if isinstance(row.get("myPick"), dict) else {}
    confidence = _as_number(my_pick.get("confidence"))
    pick = turn.get("pickName")
    actual = turn.get("actualName")

    if turn.get("matchedRecommendation") is False and confidence is not None and confidence >= 0.65:
        signals.append({
            "type": "ignored_high_confidence_recommendation",
            "severity": "high",
            "turn": row.get("turn"),
            "details": f"Engine wanted {pick}; player chose {actual}.",
            "confidence": _round_pct(confidence),
        })
    if turn.get("pickKind") == "switch" and turn.get("matchedRecommendation") is False:
        signals.append({
            "type": "ignored_switch_recommendation",
            "severity": "medium",
            "turn": row.get("turn"),
            "details": f"Engine wanted a switch to {pick}; player chose {actual}.",
        })
    if row.get("pvMatchedReality") is False:
        signals.append({
            "type": "opponent_prediction_miss",
            "severity": "medium",
            "turn": row.get("turn"),
            "details": f"Expected {prediction_label(row.get('enginePredictedOpp'))}; opponent used {prediction_label(row.get('actualOppMove'))}.",
        })
    pimc_consensus = my_pick.get("pimcConsensus") if isinstance(my_pick.get("pimcConsensus"), dict) else {}
    if pimc_consensus.get("uncertain") or pimc_consensus.get("tier") in {"split", "fragile"}:
        share = pimc_consensus.get("topMoveShare")
        share_text = f"{round(float(share) * 100)}%" if isinstance(share, (int, float)) else "unknown share"
        signals.append({
            "type": "pimc_hidden_info_split",
            "severity": "medium",
            "turn": row.get("turn"),
            "details": f"PIMC hypotheses split on {pimc_consensus.get('topMove') or 'the top move'} ({share_text} agreement).",
            "consensus": pimc_consensus,
        })
    for event in turn.get("fieldEvents") or []:
        category = event.get("category")
        event_type = event.get("type")
        if event_type in {"hazard_added", "hazard_removed"} or category in {"hazard", "status"}:
            signals.append({
                "type": "field_pressure",
                "severity": "medium" if category in {"hazard", "status"} else "low",
                "turn": row.get("turn"),
                "details": event.get("label"),
                "event": event,
            })
    if turn.get("critical"):
        signals.append({
            "type": "critical_turn",
            "severity": "high",
            "turn": row.get("turn"),
            "details": "A faint or forced replacement happened on this turn.",
        })

    if field_state:
        for side_name in ("mine", "opp"):
            active = ((field_state.get(side_name) or {}).get("active") or {})
            status = active.get("status")
            if status and status != "None":
                signals.append({
                    "type": "active_status_constraint",
                    "severity": "medium",
                    "turn": row.get("turn"),
                    "side": side_name,
                    "details": f"{side_name} active {active.get('species')} is statused: {status}.",
                })
            hazards = (field_state.get(side_name) or {}).get("hazards") or {}
            if hazards:
                signals.append({
                    "type": "active_hazard_state",
                    "severity": "medium",
                    "turn": row.get("turn"),
                    "side": side_name,
                    "details": f"{side_name} side has hazards active: {', '.join(hazards.keys())}.",
                    "hazards": hazards,
                })
        weather = field_state.get("weather") if isinstance(field_state.get("weather"), dict) else {}
        terrain = field_state.get("terrain") if isinstance(field_state.get("terrain"), dict) else {}
        weather_type = weather.get("weatherType")
        terrain_type = terrain.get("terrainType")
        if weather_type and weather_type != "none":
            signals.append({
                "type": "weather_context",
                "severity": "low",
                "turn": row.get("turn"),
                "details": f"Weather active: {weather_type}.",
                "weather": weather,
            })
        if terrain_type and terrain_type != "none":
            signals.append({
                "type": "terrain_context",
                "severity": "low",
                "turn": row.get("turn"),
                "details": f"Terrain active: {terrain_type}.",
                "terrain": terrain,
            })
        if field_state.get("trickRoom"):
            signals.append({
                "type": "trick_room_context",
                "severity": "medium",
                "turn": row.get("turn"),
                "details": "Trick Room is active.",
            })

    return signals
