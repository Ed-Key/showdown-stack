"""Deterministic decision review-card generation."""
from __future__ import annotations

from typing import Any


def _norm(value: Any) -> str:
    return "".join(c for c in str(value or "").lower() if c.isalnum())


def _as_number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _prediction_label(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "n/a"
    if _norm(raw) == "nomove":
        return "no move"
    return raw


def confidence_tier(confidence: Any) -> str:
    value = _as_number(confidence)
    if value is None:
        return "unknown"
    if value >= 65:
        return "high"
    if value >= 50:
        return "medium"
    return "low"


def review_card_tags(turn: dict[str, Any], tier: str) -> list[str]:
    tags: list[str] = []
    if tier != "unknown":
        tags.append(f"{tier} confidence")
    if turn.get("matchedRecommendation") is False:
        tags.append("player-engine diff")
    if turn.get("pickKind") == "switch":
        tags.append("switch rec")
    if turn.get("actualKind") == "prevented":
        tags.append("action prevented")
    if turn.get("pvMatchedReality") is False:
        tags.append("pv miss")
    if turn.get("fieldEventSummary"):
        tags.append("field pressure")
    if turn.get("critical"):
        tags.append("critical")

    seen: set[str] = set()
    deduped: list[str] = []
    for tag in tags:
        if tag in seen:
            continue
        seen.add(tag)
        deduped.append(tag)
    return deduped


def review_card_evidence(turn: dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    confidence = turn.get("confidence")
    engine_action = turn.get("pickLabel") or "Unknown"
    actual_action = turn.get("actualLabel") or "Unknown"
    if confidence is not None:
        evidence.append(f"Engine: {engine_action} at {confidence}% confidence.")
    else:
        evidence.append(f"Engine: {engine_action}.")
    evidence.append(f"Player: {actual_action}.")

    predicted = _prediction_label(turn.get("enginePredictedOpp"))
    actual_opp = _prediction_label(turn.get("actualOppMove"))
    if predicted != "n/a" or actual_opp != "n/a":
        if turn.get("pvMatchedReality") is False:
            evidence.append(f"Opponent prediction missed: expected {predicted}; actual {actual_opp}.")
        else:
            evidence.append(f"Opponent model: expected {predicted}; actual {actual_opp}.")

    field_labels = [
        str(event.get("label"))
        for event in (turn.get("fieldEventSummary") or [])
        if isinstance(event, dict) and event.get("label")
    ]
    if field_labels:
        evidence.append(f"Field pressure: {', '.join(field_labels)}.")

    faints = [
        f"{faint.get('side') or 'side'} {faint.get('species') or 'Pokemon'}"
        for faint in (turn.get("faints") or [])
        if isinstance(faint, dict)
    ]
    if faints:
        evidence.append(f"Faints: {', '.join(faints)}.")
    return evidence


def review_card_shape(
    turn: dict[str, Any],
) -> tuple[str, str, str, int, str, str]:
    tier = confidence_tier(turn.get("confidence"))
    match = turn.get("matchedRecommendation")
    prevented = turn.get("actualKind") == "prevented"
    pv_miss = turn.get("pvMatchedReality") is False
    field_pressure = bool(turn.get("fieldEventSummary"))
    critical = bool(turn.get("critical"))
    switch_recommendation = turn.get("pickKind") == "switch"

    if prevented:
        return (
            "action_prevented",
            "Planned action was stopped",
            "high",
            95,
            "Not a clean player-choice miss; the planned action never resolved.",
            "Could the active Pokemon survive the revealed line after hazard, status, or tempo pressure?",
        )
    if match is False and switch_recommendation:
        severity = "high" if tier == "high" else "medium"
        priority = 86 if tier == "high" else 72
        return (
            "switch_timing",
            "Switch timing review",
            severity,
            priority,
            "The engine recommended preserving or repositioning, while the player stayed with another line.",
            "Was preserving the active Pokemon or switching into a better matchup more valuable than the chosen action?",
        )
    if match is False and tier == "high":
        return (
            "high_confidence_disagreement",
            "High-confidence disagreement",
            "high",
            82,
            "This is a strong calibration candidate, unless opponent-model or field-pressure tags explain the gap.",
            "What information made the player line better than the engine line in this position?",
        )
    if match is False and tier == "medium":
        return (
            "medium_confidence_disagreement",
            "Medium-confidence disagreement",
            "medium",
            67,
            "Worth reviewing after the high-confidence spots; the engine had a preference but not an overwhelming one.",
            "Was the player choosing a strategic line the engine undervalued, or was this a missed tactical recommendation?",
        )
    if match is False:
        return (
            "low_confidence_outcome_review",
            "Low-confidence outcome review",
            "medium" if critical else "low",
            58 if critical else 44,
            "Do not grade this as an engine-correct mistake by default; use it as positioning and outcome review.",
            "Did the chosen line lose position, or was the engine also uncertain enough that this needs more evidence?",
        )
    if critical and field_pressure:
        return (
            "field_pressure_outcome",
            "Field-pressure outcome",
            "high",
            62,
            "Hazards, status, contact chip, or residual damage shaped a critical turn.",
            "How did field pressure change the survival math or switch value before this decision?",
        )
    if critical:
        return (
            "critical_outcome",
            "Critical outcome review",
            "medium",
            58,
            "A faint or forced replacement changed the battle state even without a clear recommendation mismatch.",
            "Was this the turn where preserving a win condition mattered more than immediate tempo?",
        )
    if pv_miss:
        return (
            "engine_uncertainty",
            "Opponent prediction review",
            "medium",
            52,
            "The engine's opponent forecast missed, so this is model uncertainty before it is a player-choice issue.",
            "Was the opponent's actual line visible from preview, common sets, or earlier turns?",
        )
    if field_pressure:
        return (
            "field_pressure",
            "Field pressure context",
            "low",
            42,
            "Field effects were present and may explain later tactical constraints.",
            "Did hazards, status, weather, terrain, or contact chip change the next decision?",
        )
    return ("", "", "", 0, "", "")


def decision_review_queue(
    battle_id: str,
    turns: list[dict[str, Any]],
    limit: int | None = None,
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        category, title, severity, priority, verdict, question = review_card_shape(turn)
        if not category:
            continue

        tier = confidence_tier(turn.get("confidence"))
        tags = review_card_tags(turn, tier)
        if turn.get("pvMatchedReality") is False and category not in {
            "engine_uncertainty",
            "action_prevented",
        }:
            verdict = f"{verdict} Opponent prediction also missed, so do not treat it as pure player error."
            priority += 3
        if turn.get("fieldEventSummary") and category not in {
            "field_pressure",
            "field_pressure_outcome",
        }:
            priority += 2
        if turn.get("critical") and category not in {
            "action_prevented",
            "critical_outcome",
            "field_pressure_outcome",
        }:
            priority += 2

        card = {
            "id": (
                f"{battle_id}:turn:{turn.get('turn')}"
                f"{':fs' if turn.get('forceSwitch') else ''}"
                f":{len(cards) + 1}"
            ),
            "battleId": battle_id,
            "turn": turn.get("turn"),
            "forceSwitch": bool(turn.get("forceSwitch")),
            "category": category,
            "title": title,
            "severity": severity,
            "priority": priority,
            "confidence": turn.get("confidence"),
            "confidenceTier": tier,
            "engineAction": turn.get("pickLabel"),
            "actualAction": turn.get("actualLabel"),
            "matchedRecommendation": turn.get("matchedRecommendation"),
            "opponent": {
                "predicted": _prediction_label(turn.get("enginePredictedOpp")),
                "actual": _prediction_label(turn.get("actualOppMove")),
                "pvMatchedReality": turn.get("pvMatchedReality"),
            },
            "tags": tags,
            "evidence": review_card_evidence(turn),
            "verdict": verdict,
            "reviewQuestion": question,
        }
        cards.append(card)

    cards.sort(key=lambda card: (
        -int(card.get("priority") or 0),
        card.get("turn") if isinstance(card.get("turn"), int) else 10_000,
        1 if card.get("forceSwitch") else 0,
    ))
    for index, card in enumerate(cards, start=1):
        card["rank"] = index
    if limit is not None:
        return cards[:limit]
    return cards
