"""Coach-agent context builders for dashboard analysis."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .battle_turns import prediction_label, turn_summary
from .dashboard_archive import load_postmortem_by_battle_id, summarize_postmortem
from .engine_context import (
    field_state_context,
    find_replay_record_for_turn,
    load_engine_replay_records,
    strategic_signals,
)
from .review_cards import decision_review_queue
from .team_profiles import build_team_profiles


def build_battle_agent_context(
    battle_id: str,
    *,
    postmortem_dir: Path,
    replay_dir: Path,
) -> dict[str, Any] | None:
    pm = load_postmortem_by_battle_id(battle_id, postmortem_dir)
    if not pm:
        return None

    summary = summarize_postmortem(Path(str(pm.get("battleId"))), pm)
    replay_records = load_engine_replay_records(battle_id, replay_dir)
    turn_contexts: list[dict[str, Any]] = []
    all_signals: list[dict[str, Any]] = []

    for row in (pm.get("turns") or []):
        if not isinstance(row, dict):
            continue
        turn = turn_summary(row)
        replay = find_replay_record_for_turn(
            replay_records,
            row.get("turn"),
            (row.get("myPick") or {}).get("name") if isinstance(row.get("myPick"), dict) else None,
        )
        field_state = field_state_context(replay)
        signals = strategic_signals(row, turn, field_state)
        all_signals.extend(signals)
        turn_contexts.append({
            **turn,
            "fieldStateBeforeDecision": field_state,
            "strategicSignals": signals,
        })

    signal_counts = Counter(str(signal.get("type")) for signal in all_signals)
    review_queue = decision_review_queue(
        str(summary.get("battleId") or battle_id),
        turn_contexts,
    )
    review_counts = Counter(str(card.get("category")) for card in review_queue)
    high_priority = [
        signal for signal in all_signals
        if signal.get("severity") == "high"
    ][:20]

    return {
        "purpose": "battle_coaching_context",
        "battle": summary,
        "teamComposition": {
            "mine": summary.get("team") or [],
            "opponent": summary.get("opponentTeam") or [],
        },
        "dataCoverage": {
            "schemaVersion": pm.get("schemaVersion"),
            "postmortemTurns": len(pm.get("turns") or []),
            "engineReplayRecords": len(replay_records),
            "hasEngineState": any(t.get("fieldStateBeforeDecision") for t in turn_contexts),
            "fieldEventTurns": sum(1 for t in turn_contexts if t.get("fieldEvents")),
            "strategicSignals": len(all_signals),
        },
        "aggregateSignals": {
            "countsByType": dict(signal_counts.most_common()),
            "highPriority": high_priority,
        },
        "decisionReviewQueue": review_queue,
        "aggregateReviewCategories": dict(review_counts.most_common()),
        "turns": turn_contexts,
        "agentUsageNotes": [
            "Use postmortem recommendation/action fields to compare engine advice with player choices.",
            "Use decisionReviewQueue as the primary deterministic review order; do not rename low-confidence or prevented-action cards as high-confidence mistakes.",
            "Use fieldStateBeforeDecision for hazards, status, screens, weather, terrain, active HP, and active Pokemon context.",
            "Treat opponent_prediction_miss as model uncertainty, not automatically player error.",
            "Treat ignored_high_confidence_recommendation and critical_turn together as likely coaching moments.",
        ],
    }


def build_archive_agent_context(archive: dict[str, Any]) -> dict[str, Any]:
    battles = archive.get("battles") or []
    summary = archive.get("summary") or {}
    field_pressure = {
        "hazardsAdded": summary.get("hazardsAdded"),
        "hazardsRemoved": summary.get("hazardsRemoved"),
        "hazardResidualEvents": summary.get("hazardResidualEvents"),
        "statusResidualEvents": summary.get("statusResidualEvents"),
        "itemResidualEvents": summary.get("itemResidualEvents"),
        "contactResidualEvents": summary.get("contactResidualEvents"),
    }
    team_profiles = build_team_profiles(battles)

    return {
        "purpose": "archive_coaching_context",
        "filters": archive.get("filters"),
        "summary": archive.get("summary"),
        "fieldPressure": field_pressure,
        "teamProfiles": team_profiles,
        "patternPanels": archive.get("patternPanels"),
        "topTeamSpecies": archive.get("topTeamSpecies"),
        "topRecommendations": archive.get("topRecommendations"),
        "topDisagreements": archive.get("topDisagreements"),
        "reviewLabels": archive.get("reviewLabels"),
        "recentBattles": battles[:12],
        "agentUsageNotes": [
            "Start with archive patterns, then call /dashboard/agent-context/{battleId} for specific coaching evidence.",
            "Use patternPanels for cross-battle trends; each panel has deterministic evidence and a confidence tier.",
            "Use reviewLabels as human review labels when present; they are separate from deterministic review-card categories.",
            "Schema v7 is preferred because it has actualMyAction and repaired recommendation kinds.",
            "Separate player-choice disagreement from opponent_prediction_miss before giving coaching advice.",
            "Use fieldPressure to detect hazard/status-driven losses or positioning constraints.",
        ],
    }


def build_pattern_agent_context(
    pattern_id: str,
    archive: dict[str, Any],
) -> dict[str, Any] | None:
    panels = [
        panel for panel in (archive.get("patternPanels") or [])
        if isinstance(panel, dict)
    ]
    panel = next((item for item in panels if item.get("id") == pattern_id), None)
    if not panel:
        return None

    evidence = [
        item for item in (panel.get("evidence") or [])
        if isinstance(item, dict)
    ]
    by_category = Counter(str(item.get("category") or "unknown") for item in evidence)
    by_severity = Counter(str(item.get("severity") or "unknown") for item in evidence)
    by_confidence = Counter(str(item.get("confidenceTier") or "unknown") for item in evidence)
    by_result = Counter(str(item.get("result") or "unknown") for item in evidence)
    affected_battles = sorted({
        str(item.get("battleId"))
        for item in evidence
        if item.get("battleId")
    })

    return {
        "purpose": "pattern_coaching_context",
        "pattern": {
            "id": panel.get("id"),
            "title": panel.get("title"),
            "lens": panel.get("lens"),
            "description": panel.get("description"),
            "instances": panel.get("instances"),
            "affectedBattles": panel.get("affectedBattles"),
            "level": panel.get("level"),
            "summary": panel.get("summary"),
            "reviewAction": panel.get("reviewAction"),
        },
        "archiveSummary": archive.get("summary"),
        "peerPatterns": [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "lens": item.get("lens"),
                "instances": item.get("instances"),
                "affectedBattles": item.get("affectedBattles"),
                "level": item.get("level"),
                "summary": item.get("summary"),
            }
            for item in panels
        ],
        "evidence": evidence,
        "reviewLabelSummary": panel.get("reviewLabelSummary"),
        "evidenceBreakdown": {
            "byCategory": dict(by_category.most_common()),
            "bySeverity": dict(by_severity.most_common()),
            "byConfidence": dict(by_confidence.most_common()),
            "byResult": dict(by_result.most_common()),
            "affectedBattleIds": affected_battles[:20],
        },
        "analysisGuardrails": [
            "Pattern evidence is deterministic review-card data, not proof of a player mistake.",
            "Human review labels are user-created conclusions; do not overwrite them with model guesses.",
            "Separate player habit, engine/model uncertainty, and field/context pressure.",
            "Cite evidence as turn numbers and opponents from the evidence list.",
            "Keep team-building advice as suggestions unless external meta or simulator evidence is provided.",
        ],
    }


def compact_pattern_context_for_model(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "purpose": context.get("purpose"),
        "pattern": context.get("pattern"),
        "archiveSummary": context.get("archiveSummary"),
        "peerPatterns": context.get("peerPatterns"),
        "reviewLabelSummary": context.get("reviewLabelSummary"),
        "evidenceBreakdown": context.get("evidenceBreakdown"),
        "evidence": (context.get("evidence") or [])[:10],
        "analysisGuardrails": context.get("analysisGuardrails"),
    }


def review_auto_label_evidence_score(evidence: dict[str, Any]) -> int:
    score = 0
    category = str(evidence.get("category") or "")
    tags = {str(tag).lower() for tag in (evidence.get("tags") or [])}
    if category in {"high_confidence_disagreement", "switch_timing"}:
        score += 4
    if category in {"action_prevented", "field_pressure", "field_pressure_outcome"}:
        score += 3
    if category in {"engine_uncertainty", "opponent_prediction_miss"}:
        score += 3
    if "critical" in tags:
        score += 3
    if "field pressure" in tags or "pv miss" in tags:
        score += 2
    if evidence.get("confidenceTier") == "high":
        score += 2
    if evidence.get("result") == "loss":
        score += 1
    return score


def compact_pattern_context_for_labeler(
    context: dict[str, Any],
    *,
    label_definitions: list[dict[str, Any]],
    limit: int,
) -> dict[str, Any]:
    evidence = [
        item for item in (context.get("evidence") or [])
        if isinstance(item, dict) and not item.get("reviewLabel") and item.get("reviewKey")
    ]
    evidence.sort(key=lambda item: (
        -review_auto_label_evidence_score(item),
        item.get("turn") if isinstance(item.get("turn"), int) else 10_000,
        str(item.get("battleId") or ""),
        1 if item.get("forceSwitch") else 0,
    ))
    compact_evidence = []
    for item in evidence[:max(1, limit)]:
        compact_evidence.append({
            "reviewKey": item.get("reviewKey"),
            "battleId": item.get("battleId"),
            "turn": item.get("turn"),
            "forceSwitch": bool(item.get("forceSwitch")),
            "opponent": item.get("opponent"),
            "result": item.get("result"),
            "category": item.get("category"),
            "title": item.get("title"),
            "confidenceTier": item.get("confidenceTier"),
            "verdict": item.get("verdict"),
            "reviewQuestion": item.get("reviewQuestion"),
            "tags": item.get("tags") or [],
            "engineAction": item.get("engineAction"),
            "actualAction": item.get("actualAction"),
            "opponentModel": item.get("opponentModel"),
        })

    return {
        "purpose": "review_card_auto_label_context",
        "pattern": context.get("pattern"),
        "allowedLabels": [
            {
                "id": item["id"],
                "label": item["label"],
                "description": item["description"],
            }
            for item in label_definitions
        ],
        "evidence": compact_evidence,
        "evidenceLimit": limit,
        "totalUnreviewedEvidence": len(evidence),
        "guardrails": [
            "Return labels only for reviewKey values in evidence.",
            "Use only allowedLabels.id values.",
            "Use engine_uncertainty for opponent prediction misses or hidden-information uncertainty.",
            "Use field_pressure when hazards, status, chip, speed, or action prevention materially constrained the decision.",
            "Use player_issue only for clean player-choice calibration cards.",
            "Use team_issue for team shape, set, matchup, or preservation constraints.",
            "Use engine_issue only when the recommendation itself appears under-modeled or wrong.",
        ],
    }


def coach_turn_score(turn: dict[str, Any]) -> int:
    score = 0
    if turn.get("critical"):
        score += 4
    if turn.get("actualKind") == "prevented":
        score += 4
    if turn.get("matchedRecommendation") is False:
        score += 3
    if turn.get("pvMatchedReality") is False:
        score += 2
    if turn.get("fieldEventSummary"):
        score += 1

    for signal in turn.get("strategicSignals") or []:
        if not isinstance(signal, dict):
            continue
        severity = signal.get("severity")
        if severity == "high":
            score += 3
        elif severity == "medium":
            score += 2
        elif severity == "low":
            score += 1
    return score


def coach_turn_title(turn: dict[str, Any]) -> str:
    if turn.get("actualKind") == "prevented":
        return "Planned action was stopped"
    if turn.get("matchedRecommendation") is False and turn.get("pickKind") == "switch":
        return "Switch recommendation was declined"
    if turn.get("matchedRecommendation") is False:
        return "Engine and player diverged"
    if turn.get("pvMatchedReality") is False:
        return "Opponent prediction missed"
    if turn.get("critical"):
        return "Critical position change"
    if turn.get("fieldEventSummary"):
        return "Field pressure shaped the turn"
    return "Review this decision"


def coach_turn_recommendation(turn: dict[str, Any]) -> str:
    if turn.get("actualKind") == "prevented":
        return (
            "Check whether the active Pokemon can survive the opponent's revealed line "
            "before choosing setup, hazard, or slower value moves."
        )
    if turn.get("matchedRecommendation") is False and turn.get("pickKind") == "switch":
        return (
            "Replay the position and compare the recommended switch against the chosen "
            "action; this is likely a positioning or matchup-preservation moment."
        )
    if turn.get("matchedRecommendation") is False:
        return (
            "Review why the engine preferred its line and whether the player choice had "
            "hidden information the engine did not model."
        )
    if turn.get("pvMatchedReality") is False:
        return (
            "Treat this as opponent-model uncertainty first; inspect whether the belief "
            "tracker missed a common move, item, or set."
        )
    if turn.get("fieldEventSummary"):
        return (
            "Account for field pressure in the next decision: hazards, status, weather, "
            "or contact damage may change the value of staying in."
        )
    return "Use this turn as supporting context rather than a primary coaching moment."


def coach_turn_evidence(turn: dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    if turn.get("actualKind") == "prevented":
        evidence.append(f"Actual action: {turn.get('actualLabel')}")
    if turn.get("matchedRecommendation") is False:
        evidence.append(
            f"Engine wanted {turn.get('pickLabel')}; player chose {turn.get('actualLabel')}."
        )
    if turn.get("pvMatchedReality") is False:
        evidence.append(
            f"Opponent prediction missed: expected {prediction_label(turn.get('enginePredictedOpp'))}, "
            f"got {prediction_label(turn.get('actualOppMove'))}."
        )
    field_labels = [
        str(event.get("label"))
        for event in (turn.get("fieldEventSummary") or [])
        if isinstance(event, dict) and event.get("label")
    ]
    if field_labels:
        evidence.append(f"Field events: {', '.join(field_labels)}.")
    if turn.get("critical"):
        evidence.append("A faint or forced replacement happened on this turn.")
    return evidence


def coach_focus_items(context: dict[str, Any]) -> list[dict[str, str]]:
    battle = context.get("battle") or {}
    metrics = battle.get("metrics") or {}
    counts = ((context.get("aggregateSignals") or {}).get("countsByType") or {})
    review_counts = context.get("aggregateReviewCategories") or {}
    focus: list[dict[str, str]] = []

    if review_counts.get("high_confidence_disagreement"):
        focus.append({
            "title": "High-confidence disagreements",
            "action": "Start with the deterministic review queue's high-confidence disagreement cards; they are the cleanest player-vs-engine calibration spots.",
        })
    if review_counts.get("switch_timing"):
        focus.append({
            "title": "Switch timing",
            "action": "Study switch recommendations separately from move recommendations; they usually encode preservation, tempo, or matchup safety.",
        })
    if review_counts.get("engine_uncertainty") or counts.get("opponent_prediction_miss"):
        focus.append({
            "title": "Opponent model uncertainty",
            "action": "Check whether missed PVs came from unrevealed moves, uncommon sets, or the format alias used by the belief tracker.",
        })
    if (
        review_counts.get("field_pressure")
        or review_counts.get("field_pressure_outcome")
        or counts.get("field_pressure")
        or counts.get("active_hazard_state")
    ):
        focus.append({
            "title": "Field management",
            "action": "Track how hazards, status, contact chip, weather, and terrain changed the value of each recommendation.",
        })
    if review_counts.get("action_prevented"):
        focus.append({
            "title": "Survivability before value plays",
            "action": "Review prevented-action cards before blaming move choice; they usually ask whether the active could live the revealed line.",
        })
    if metrics.get("criticalTurns"):
        focus.append({
            "title": "Critical turns",
            "action": "Replay faint and forced-switch turns first; they usually explain the battle's momentum better than neutral turns.",
        })

    if not focus:
        focus.append({
            "title": "More sample size",
            "action": "Collect more finished games with the same team before drawing team-building conclusions.",
        })
    return focus[:5]


def coach_diagnosis(context: dict[str, Any]) -> list[dict[str, str]]:
    battle = context.get("battle") or {}
    metrics = battle.get("metrics") or {}
    counts = ((context.get("aggregateSignals") or {}).get("countsByType") or {})
    review_queue = [
        card for card in (context.get("decisionReviewQueue") or [])
        if isinstance(card, dict)
    ]
    review_counts = context.get("aggregateReviewCategories") or {}
    result = battle.get("result") or "unknown"
    diagnosis: list[dict[str, str]] = [{
        "title": "Battle profile",
        "detail": (
            f"{str(result).upper()} vs {battle.get('opponent') or 'opponent'}: "
            f"{metrics.get('followRate') if metrics.get('followRate') is not None else 'n/a'}% follow rate, "
            f"{metrics.get('pvHitRate') if metrics.get('pvHitRate') is not None else 'n/a'}% opponent-prediction hit rate, "
            f"{metrics.get('criticalTurns') or 0} critical turns."
        ),
    }]

    if review_queue:
        high_cards = sum(1 for card in review_queue if card.get("severity") == "high")
        top = review_queue[0]
        diagnosis.append({
            "title": "Decision review queue",
            "detail": (
                f"{len(review_queue)} deterministic review cards were found; "
                f"{high_cards} are high severity. Top card: turn {top.get('turn')} "
                f"{top.get('title')}."
            ),
        })
    if review_counts.get("high_confidence_disagreement"):
        diagnosis.append({
            "title": "Player-choice calibration",
            "detail": f"{review_counts['high_confidence_disagreement']} high-confidence disagreement card was found.",
        })
    if review_counts.get("engine_uncertainty") or counts.get("opponent_prediction_miss"):
        diagnosis.append({
            "title": "Model uncertainty",
            "detail": f"{counts.get('opponent_prediction_miss') or 0} turns had a missed opponent prediction, so not every disagreement should be treated as player error.",
        })
    if (
        review_counts.get("field_pressure")
        or review_counts.get("field_pressure_outcome")
        or counts.get("field_pressure")
        or counts.get("active_hazard_state")
    ):
        diagnosis.append({
            "title": "Field context",
            "detail": "Hazards, status, or residual/contact damage materially affected at least one decision.",
        })
    if result == "loss" and metrics.get("criticalTurns"):
        diagnosis.append({
            "title": "Loss shape",
            "detail": "The loss should be reviewed through the critical turns first, then checked against recommendation alignment.",
        })
    return diagnosis


def build_coach_brief(context: dict[str, Any]) -> dict[str, Any]:
    battle = context.get("battle") or {}
    review_queue = [
        card for card in (context.get("decisionReviewQueue") or [])
        if isinstance(card, dict)
    ]

    turning_points = []
    for card in review_queue[:5]:
        turning_points.append({
            "turn": card.get("turn"),
            "forceSwitch": bool(card.get("forceSwitch")),
            "score": card.get("priority"),
            "category": card.get("category"),
            "severity": card.get("severity"),
            "tags": card.get("tags") or [],
            "title": card.get("title"),
            "evidence": card.get("evidence") or [],
            "verdict": card.get("verdict"),
            "recommendation": card.get("reviewQuestion"),
        })

    return {
        "purpose": "battle_coach_brief",
        "battle": {
            "battleId": battle.get("battleId"),
            "opponent": battle.get("opponent"),
            "result": battle.get("result"),
            "format": battle.get("format"),
            "endedAtLabel": battle.get("endedAtLabel"),
            "replayUrl": battle.get("replayUrl"),
            "metrics": battle.get("metrics"),
        },
        "diagnosis": coach_diagnosis(context),
        "reviewQueue": review_queue[:8],
        "reviewCategoryCounts": context.get("aggregateReviewCategories") or {},
        "turningPoints": turning_points,
        "practiceFocus": coach_focus_items(context),
        "dataCoverage": context.get("dataCoverage"),
        "modelHandoff": {
            "suggestedTools": [
                "get_archive_context",
                "get_battle_context",
                "reviewQueue",
                "get_team_profile",
            ],
            "guardrails": [
                "Cite turn numbers and evidence.",
                "Use reviewQueue categories as authoritative deterministic labels.",
                "Separate player decisions from opponent-model misses.",
                "Treat this as post-game coaching, not live ladder assistance.",
            ],
        },
    }
