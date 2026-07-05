"""Cross-battle deterministic pattern panel construction."""
from __future__ import annotations

from typing import Any, Callable

from .review_cards import decision_review_queue
from .review_workflow import (
    auto_review_label_for_evidence,
    decorate_review_label,
    review_label_key,
    review_label_summary,
)


PATTERN_PANEL_DEFINITIONS: list[dict[str, str]] = [
    {
        "id": "hazard_status_pressure",
        "title": "Hazard / Status Pressure",
        "lens": "Positioning",
        "description": "Hazards, status, contact chip, weather, or other field effects are shaping decisions.",
        "reviewAction": "Review whether HP thresholds and field pressure changed the value of staying in, switching, or using slower value moves.",
    },
    {
        "id": "action_prevented",
        "title": "Action Prevented Before Moving",
        "lens": "Survivability",
        "description": "The planned action did not resolve because the active Pokemon fainted or was otherwise stopped.",
        "reviewAction": "Check the board state before the move: active HP, speed, priority, hazards, and whether preserving the Pokemon mattered more.",
    },
    {
        "id": "switch_recommendations_ignored",
        "title": "Switch Recommendations Ignored",
        "lens": "Preservation",
        "description": "The engine recommended a switch, but the player chose another action.",
        "reviewAction": "Treat these as preservation and matchup-positioning reviews, not automatic mistakes.",
    },
    {
        "id": "high_confidence_disagreements",
        "title": "High-Confidence Disagreements",
        "lens": "Calibration",
        "description": "The engine had high confidence, the action was followable, and the player chose a different line.",
        "reviewAction": "Replay these first as clean player-vs-engine calibration spots, especially when there is no opponent prediction miss.",
    },
    {
        "id": "opponent_prediction_misses",
        "title": "Opponent Prediction Misses",
        "lens": "Engine eval",
        "description": "The engine's predicted opponent action did not match what happened.",
        "reviewAction": "Use this as model/belief-tracker evaluation before calling a player choice wrong.",
    },
]


TurnSummaryBuilder = Callable[[dict[str, Any]], dict[str, Any]]


def pattern_panel_level(instance_count: int, battle_count: int) -> dict[str, str]:
    if instance_count >= 5 and battle_count >= 3:
        return {
            "tier": "strong",
            "label": "Strong pattern",
            "basis": "5+ instances across 3+ battles",
        }
    if instance_count >= 3 or battle_count >= 2:
        return {
            "tier": "likely",
            "label": "Likely pattern",
            "basis": "3+ instances or 2+ affected battles",
        }
    if instance_count > 0:
        return {
            "tier": "observed",
            "label": "Observed",
            "basis": "1-2 instances in the current sample",
        }
    return {
        "tier": "none",
        "label": "No signal yet",
        "basis": "No matching review cards in the current sample",
    }


def card_matches_pattern(card: dict[str, Any], pattern_id: str) -> bool:
    category = str(card.get("category") or "")
    tags = {str(tag) for tag in (card.get("tags") or [])}
    if pattern_id == "hazard_status_pressure":
        return category in {"field_pressure", "field_pressure_outcome"} or "field pressure" in tags
    if pattern_id == "action_prevented":
        return category == "action_prevented"
    if pattern_id == "switch_recommendations_ignored":
        return category == "switch_timing"
    if pattern_id == "high_confidence_disagreements":
        return category == "high_confidence_disagreement"
    if pattern_id == "opponent_prediction_misses":
        opponent = card.get("opponent") if isinstance(card.get("opponent"), dict) else {}
        return category == "engine_uncertainty" or opponent.get("pvMatchedReality") is False or "pv miss" in tags
    return False


def build_pattern_panels(
    battles: list[dict[str, Any]],
    postmortems_by_battle_id: dict[str, dict[str, Any]],
    *,
    turn_summary_builder: TurnSummaryBuilder,
    evidence_limit: int = 8,
    review_labels: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    review_labels = review_labels or {}
    pattern_evidence: dict[str, list[dict[str, Any]]] = {
        pattern["id"]: []
        for pattern in PATTERN_PANEL_DEFINITIONS
    }

    for battle in battles:
        battle_id = str(battle.get("battleId") or "")
        pm = postmortems_by_battle_id.get(battle_id)
        if not battle_id or not pm:
            continue
        turns = [
            turn_summary_builder(row)
            for row in (pm.get("turns") or [])
            if isinstance(row, dict)
        ]
        cards = decision_review_queue(battle_id, turns)
        for card in cards:
            for pattern in PATTERN_PANEL_DEFINITIONS:
                pattern_id = pattern["id"]
                if not card_matches_pattern(card, pattern_id):
                    continue
                key = review_label_key(
                    pattern_id,
                    battle_id,
                    card.get("turn"),
                    card.get("forceSwitch"),
                )
                label = decorate_review_label(review_labels.get(key))
                evidence_item = {
                    "battleId": battle_id,
                    "reviewKey": key,
                    "opponent": battle.get("opponent"),
                    "result": battle.get("result"),
                    "turn": card.get("turn"),
                    "forceSwitch": card.get("forceSwitch"),
                    "category": card.get("category"),
                    "title": card.get("title"),
                    "severity": card.get("severity"),
                    "confidence": card.get("confidence"),
                    "confidenceTier": card.get("confidenceTier"),
                    "engineAction": card.get("engineAction"),
                    "actualAction": card.get("actualAction"),
                    "opponentModel": card.get("opponent"),
                    "tags": card.get("tags") or [],
                    "verdict": card.get("verdict"),
                    "reviewQuestion": card.get("reviewQuestion"),
                }
                if label:
                    evidence_item["reviewLabel"] = label
                else:
                    auto_label = auto_review_label_for_evidence(pattern, evidence_item)
                    if auto_label:
                        evidence_item["reviewLabel"] = auto_label
                pattern_evidence[pattern_id].append(evidence_item)

    panels: list[dict[str, Any]] = []
    for pattern in PATTERN_PANEL_DEFINITIONS:
        pattern_id = pattern["id"]
        evidence = pattern_evidence[pattern_id]
        battle_ids = {
            str(item.get("battleId"))
            for item in evidence
            if item.get("battleId")
        }
        level = pattern_panel_level(len(evidence), len(battle_ids))
        labeled = [
            item.get("reviewLabel")
            for item in evidence
            if isinstance(item.get("reviewLabel"), dict)
        ]
        sorted_evidence = sorted(
            evidence,
            key=lambda item: (
                item.get("battleId") or "",
                item.get("turn") if isinstance(item.get("turn"), int) else 10_000,
            ),
            reverse=True,
        )
        panels.append({
            **pattern,
            "instances": len(evidence),
            "affectedBattles": len(battle_ids),
            "level": level,
            "reviewLabelSummary": review_label_summary(labeled),
            "summary": (
                f"{level['label']}: {len(evidence)} review card"
                f"{'' if len(evidence) == 1 else 's'} across {len(battle_ids)} battle"
                f"{'' if len(battle_ids) == 1 else 's'}."
            ),
            "evidence": sorted_evidence[:evidence_limit],
        })

    panels.sort(key=lambda panel: (
        {"strong": 0, "likely": 1, "observed": 2, "none": 3}.get(
            str((panel.get("level") or {}).get("tier")),
            4,
        ),
        -int(panel.get("instances") or 0),
        str(panel.get("title") or ""),
    ))
    return panels
