"""Engine-eval case ranking and replay enrichment."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .engine_context import find_replay_record_for_turn, load_engine_replay_records


def _engine_action_name(action: Any) -> str:
    if isinstance(action, dict):
        return str(action.get("name") or action.get("move") or "")
    text = str(action or "")
    if ":" in text:
        return text.split(":", 1)[1].strip()
    return text.strip()


def _terminal_summary(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    terminal = record.get("engine_response_terminal")
    if not isinstance(terminal, dict):
        return None
    pimc_breakdown = terminal.get("pimcBreakdown") if isinstance(terminal.get("pimcBreakdown"), list) else []
    pimc_consensus = (
        terminal.get("pimcConsensus")
        if isinstance(terminal.get("pimcConsensus"), dict)
        else _pimc_consensus_from_breakdown(pimc_breakdown)
    )
    return {
        "bestMove": terminal.get("bestMove"),
        "confidence": terminal.get("confidence"),
        "sims": terminal.get("sims"),
        "depth": terminal.get("depth"),
        "message": terminal.get("message"),
        "pimcConsensus": pimc_consensus,
        "pimcBreakdown": pimc_breakdown,
        "pv": terminal.get("pv") if isinstance(terminal.get("pv"), list) else [],
        "alternatives": terminal.get("alternatives")
        if isinstance(terminal.get("alternatives"), list)
        else [],
    }


def _round4(value: float) -> float:
    return round(value, 4)


def _pimc_consensus_from_breakdown(breakdown: list[Any]) -> dict[str, Any] | None:
    rows = [item for item in breakdown if isinstance(item, dict) and item.get("top_move")]
    if not rows:
        return None

    groups: dict[str, dict[str, float | int | str]] = {}
    values: list[float] = []
    for row in rows:
        move = str(row.get("top_move"))
        value = row.get("value")
        visit_share = row.get("visit_share")
        value_num = float(value) if isinstance(value, (int, float)) else 0.0
        visit_num = float(visit_share) if isinstance(visit_share, (int, float)) else 0.0
        values.append(value_num)
        group = groups.setdefault(move, {"topMove": move, "votes": 0, "valueSum": 0.0, "visitShareSum": 0.0})
        group["votes"] = int(group["votes"]) + 1
        group["valueSum"] = float(group["valueSum"]) + value_num
        group["visitShareSum"] = float(group["visitShareSum"]) + visit_num

    hypothesis_count = len(rows)
    votes = []
    for group in groups.values():
        count = int(group["votes"])
        votes.append({
            "topMove": group["topMove"],
            "votes": count,
            "share": _round4(count / hypothesis_count),
            "avgValue": _round4(float(group["valueSum"]) / count),
            "avgVisitShare": _round4(float(group["visitShareSum"]) / count),
        })
    votes.sort(key=lambda item: (-int(item["votes"]), -float(item["avgValue"]), str(item["topMove"])))
    top = votes[0]
    top_move_share = float(top["share"])
    distinct_top_moves = len(votes)
    if top_move_share >= 1.0:
        tier = "unanimous"
    elif top_move_share >= 0.75:
        tier = "strong"
    elif top_move_share >= 0.5:
        tier = "split"
    else:
        tier = "fragile"
    return {
        "hypothesisCount": hypothesis_count,
        "topMove": top["topMove"],
        "topMoveVotes": top["votes"],
        "topMoveShare": top_move_share,
        "distinctTopMoves": distinct_top_moves,
        "valueSpread": _round4(max(values) - min(values)) if values else 0.0,
        "tier": tier,
        "uncertain": top_move_share < 0.75 or distinct_top_moves > 2,
        "votes": votes,
    }


def terminal_pimc_uncertainty(terminal: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(terminal, dict):
        return None
    consensus = terminal.get("pimcConsensus")
    if not isinstance(consensus, dict):
        return None
    tier = str(consensus.get("tier") or "")
    share = consensus.get("topMoveShare")
    distinct = consensus.get("distinctTopMoves")
    uncertain = bool(consensus.get("uncertain"))
    if not uncertain and tier not in {"split", "fragile"}:
        return None
    return {
        "tier": tier or "unknown",
        "topMove": consensus.get("topMove"),
        "topMoveShare": share,
        "distinctTopMoves": distinct,
        "hypothesisCount": consensus.get("hypothesisCount"),
    }


def _request_summary(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    request = record.get("engine_request")
    if not isinstance(request, dict):
        return None
    hypotheses = request.get("hypotheses")
    return {
        "battleId": request.get("battleId"),
        "turn": request.get("turn") or record.get("turn"),
        "rqid": request.get("rqid") or record.get("rqid"),
        "forceSwitch": bool(request.get("forceSwitch") or record.get("force_switch")),
        "timeLimit": request.get("timeLimit"),
        "hypotheses": len(hypotheses) if isinstance(hypotheses, list) else 1,
        "hasState": bool(
            isinstance(request.get("sideOne"), dict)
            or (isinstance(hypotheses, list) and bool(hypotheses))
        ),
    }


def enrich_engine_eval_cases_with_replay(
    cases: list[dict[str, Any]],
    replay_dir: Path,
) -> list[dict[str, Any]]:
    """Attach captured replay metadata needed to rerun a case against engine variants."""
    records_by_battle: dict[str, list[dict[str, Any]]] = {}
    enriched: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        source = case.get("source") if isinstance(case.get("source"), dict) else {}
        position = case.get("positionSummary") if isinstance(case.get("positionSummary"), dict) else {}
        battle_id = str(source.get("battleId") or "")
        turn = source.get("turn")
        if battle_id not in records_by_battle:
            records_by_battle[battle_id] = load_engine_replay_records(battle_id, replay_dir)
        pick_name = _engine_action_name(position.get("engineAction"))
        record = find_replay_record_for_turn(records_by_battle[battle_id], turn, pick_name)
        replay = {
            "available": bool(record),
            "recordPath": str(replay_dir / f"{battle_id}.jsonl") if battle_id else "",
            "turn": record.get("turn") if isinstance(record, dict) else turn,
            "rqid": record.get("rqid") if isinstance(record, dict) else None,
            "forceSwitch": bool(record.get("force_switch")) if isinstance(record, dict) else bool(source.get("forceSwitch")),
            "terminal": _terminal_summary(record),
            "request": _request_summary(record),
        }
        enriched.append({
            **case,
            "replay": replay,
        })
    return enriched


def engine_eval_case_priority(case: dict[str, Any]) -> dict[str, Any]:
    source = case.get("source") if isinstance(case.get("source"), dict) else {}
    position = case.get("positionSummary") if isinstance(case.get("positionSummary"), dict) else {}
    expected = case.get("expectedBehavior") if isinstance(case.get("expectedBehavior"), dict) else {}
    label = source.get("reviewLabel") if isinstance(source.get("reviewLabel"), dict) else {}
    opponent = position.get("opponent") if isinstance(position.get("opponent"), dict) else {}
    tags = {str(tag) for tag in (position.get("tags") or [])}

    score = 0
    reasons: list[str] = []
    if source.get("result") == "loss":
        score += 35
        reasons.append("loss case")
    if expected.get("caseType") == "survival_positioning":
        score += 25
        reasons.append("survival/field-pressure case")
    if expected.get("caseType") == "confidence_calibration":
        score += 15
        reasons.append("opponent-model calibration")
    if label.get("label") == "engine_uncertainty":
        score += 15
        reasons.append("engine uncertainty")
    if label.get("label") == "field_pressure":
        score += 12
        reasons.append("field pressure")
    if opponent.get("pvMatchedReality") is False:
        score += 18
        reasons.append("PV miss")
    if "action prevented" in tags or "critical" in tags:
        score += 12
        reasons.append("critical/action-prevention tag")
    confidence = position.get("confidence")
    if isinstance(confidence, (int, float)) and confidence >= 65:
        score += 10
        reasons.append("high confidence")
    if (position.get("actualAction") or "").startswith("prevented:"):
        score += 20
        reasons.append("action failed before moving")
    replay = case.get("replay") if isinstance(case.get("replay"), dict) else {}
    if replay.get("available"):
        score += 8
        reasons.append("replay input captured")
    pimc_uncertainty = terminal_pimc_uncertainty(replay.get("terminal"))
    if pimc_uncertainty:
        score += 18
        reasons.append("PIMC split")

    return {
        "score": score,
        "reasons": reasons,
        "pimcUncertainty": pimc_uncertainty,
    }


def prioritize_engine_eval_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        priority = engine_eval_case_priority(case)
        ranked.append({
            **case,
            "priority": priority,
        })
    ranked.sort(
        key=lambda item: (
            (item.get("priority") or {}).get("score") or 0,
            str((item.get("source") or {}).get("battleId") or ""),
            -int((item.get("source") or {}).get("turn") or 0),
        ),
        reverse=True,
    )
    return ranked
