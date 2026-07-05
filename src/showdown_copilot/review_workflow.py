"""Review-label and eval-case helpers for the dashboard workflow."""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

DEFAULT_REVIEW_LABELS_PATH = Path(
    "/Users/edkiboma/Projects/pokemon-ai/workspace/analysis/dashboard-review-labels.json"
)
REVIEW_LABELS_PATH = Path(
    os.environ.get("SHOWDOWN_COPILOT_REVIEW_LABELS_PATH", str(DEFAULT_REVIEW_LABELS_PATH))
)


class ReviewLabelRequest(BaseModel):
    patternId: str = Field(min_length=1, max_length=80)
    battleId: str = Field(min_length=1, max_length=140)
    turn: int = Field(ge=0, le=1000)
    forceSwitch: bool = Field(default=False)
    label: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=240)


REVIEW_LABEL_DEFINITIONS: list[dict[str, str]] = [
    {
        "id": "player_issue",
        "label": "Player issue",
        "description": "The player choice is the main thing to review.",
        "tone": "red",
    },
    {
        "id": "field_pressure",
        "label": "Field pressure",
        "description": "Hazards, status, chip, speed, or tempo constrained the decision.",
        "tone": "gold",
    },
    {
        "id": "engine_uncertainty",
        "label": "Engine uncertainty",
        "description": "Opponent prediction, hidden information, or low confidence made the engine shaky.",
        "tone": "blue",
    },
    {
        "id": "team_issue",
        "label": "Team issue",
        "description": "The position points to team structure, matchup, or set constraints.",
        "tone": "purple",
    },
    {
        "id": "engine_issue",
        "label": "Engine issue",
        "description": "The recommendation itself looks wrong or under-modeled.",
        "tone": "orange",
    },
    {
        "id": "unclear",
        "label": "Unclear",
        "description": "Worth revisiting after replay or more data.",
        "tone": "neutral",
    },
]
REVIEW_LABEL_DEFINITIONS_BY_ID = {
    item["id"]: item
    for item in REVIEW_LABEL_DEFINITIONS
}

ENGINE_EVAL_LABELS = {"engine_issue", "engine_uncertainty", "field_pressure"}


def review_label_key(
    pattern_id: str,
    battle_id: str,
    turn: Any,
    force_switch: Any,
) -> str:
    turn_value = turn if isinstance(turn, int) else str(turn or "unknown")
    suffix = "fs" if bool(force_switch) else "regular"
    return f"{pattern_id}|{battle_id}|{turn_value}|{suffix}"


def decorate_review_label(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    label_id = str(entry.get("label") or "")
    definition = REVIEW_LABEL_DEFINITIONS_BY_ID.get(label_id)
    if not definition:
        return None
    return {
        "key": str(entry.get("key") or ""),
        "patternId": str(entry.get("patternId") or ""),
        "battleId": str(entry.get("battleId") or ""),
        "turn": entry.get("turn"),
        "forceSwitch": bool(entry.get("forceSwitch")),
        "label": label_id,
        "labelTitle": definition["label"],
        "description": definition["description"],
        "tone": definition["tone"],
        "note": str(entry.get("note") or ""),
        "createdAtMs": entry.get("createdAtMs"),
        "updatedAtMs": entry.get("updatedAtMs"),
    }


def load_review_labels(path: Path = REVIEW_LABELS_PATH) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw_labels = data.get("labels") if isinstance(data, dict) else {}
    if not isinstance(raw_labels, dict):
        return {}

    labels: dict[str, dict[str, Any]] = {}
    for key, value in raw_labels.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        if str(value.get("label") or "") not in REVIEW_LABEL_DEFINITIONS_BY_ID:
            continue
        entry = {
            "key": key,
            "patternId": str(value.get("patternId") or ""),
            "battleId": str(value.get("battleId") or ""),
            "turn": value.get("turn"),
            "forceSwitch": bool(value.get("forceSwitch")),
            "label": str(value.get("label") or ""),
            "note": str(value.get("note") or "")[:240],
            "createdAtMs": value.get("createdAtMs"),
            "updatedAtMs": value.get("updatedAtMs"),
        }
        if entry["patternId"] and entry["battleId"]:
            labels[key] = entry
    return labels


def write_review_labels(
    labels: dict[str, dict[str, Any]],
    path: Path = REVIEW_LABELS_PATH,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
        "labels": labels,
    }
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def review_label_summary(labels: list[dict[str, Any] | None]) -> dict[str, Any]:
    decorated = [
        label for label in (decorate_review_label(item) for item in labels)
        if label
    ]
    counts = Counter(str(label["label"]) for label in decorated)
    return {
        "totalLabeled": len(decorated),
        "counts": dict(counts.most_common()),
        "byLabel": [
            {
                **definition,
                "count": counts[definition["id"]],
            }
            for definition in REVIEW_LABEL_DEFINITIONS
            if counts[definition["id"]]
        ],
    }


def persist_review_label(
    request: ReviewLabelRequest,
    path: Path = REVIEW_LABELS_PATH,
) -> dict[str, Any]:
    labels = load_review_labels(path)
    key = review_label_key(
        request.patternId,
        request.battleId,
        request.turn,
        request.forceSwitch,
    )
    if request.label == "unreviewed":
        labels.pop(key, None)
        write_review_labels(labels, path)
        return {
            "reviewKey": key,
            "reviewLabel": None,
            "definitions": REVIEW_LABEL_DEFINITIONS,
            "summary": review_label_summary(list(labels.values())),
        }

    now_ms = int(datetime.now().timestamp() * 1000)
    existing = labels.get(key) or {}
    labels[key] = {
        "key": key,
        "patternId": request.patternId,
        "battleId": request.battleId,
        "turn": request.turn,
        "forceSwitch": request.forceSwitch,
        "label": request.label,
        "note": request.note[:240],
        "createdAtMs": existing.get("createdAtMs") or now_ms,
        "updatedAtMs": now_ms,
    }
    write_review_labels(labels, path)
    return {
        "reviewKey": key,
        "reviewLabel": decorate_review_label(labels[key]),
        "definitions": REVIEW_LABEL_DEFINITIONS,
        "summary": review_label_summary(list(labels.values())),
    }


def suggest_review_label_for_evidence(
    pattern: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    pattern_id = str(pattern.get("id") or evidence.get("patternId") or "")
    category = str(evidence.get("category") or "")
    tags = {str(tag) for tag in (evidence.get("tags") or [])}
    confidence_tier = str(evidence.get("confidenceTier") or "")
    opponent_model = evidence.get("opponentModel") if isinstance(evidence.get("opponentModel"), dict) else {}
    pv_miss = opponent_model.get("pvMatchedReality") is False or "pv miss" in tags
    field_pressure = (
        category in {"field_pressure", "field_pressure_outcome", "action_prevented"}
        or "field pressure" in tags
    )

    label = "unclear"
    confidence = 0.45
    reason = "Evidence is mixed; keep this as an unclear review card until a replay pass confirms the main cause."

    if category == "engine_uncertainty" or pattern_id == "opponent_prediction_misses" or pv_miss:
        label = "engine_uncertainty"
        confidence = 0.82
        reason = "Opponent prediction or hidden-information uncertainty is explicit in the deterministic evidence."
    elif field_pressure:
        label = "field_pressure"
        confidence = 0.78
        reason = "Hazards, status, chip, contact damage, or action prevention shaped the decision context."
    elif category == "switch_timing":
        if confidence_tier == "high":
            label = "player_issue"
            confidence = 0.66
            reason = "The engine made a high-confidence preservation recommendation and the player chose another line."
        else:
            label = "team_issue"
            confidence = 0.58
            reason = "The switch recommendation likely reflects matchup preservation or team-positioning constraints."
    elif category == "high_confidence_disagreement":
        label = "player_issue"
        confidence = 0.74
        reason = "This is a clean player-vs-engine calibration card unless replay context shows hidden information."
    elif category == "critical_outcome":
        label = "team_issue"
        confidence = 0.52
        reason = "A critical outcome happened without a clean recommendation mismatch; treat it as preservation or team-shape review."

    definition = REVIEW_LABEL_DEFINITIONS_BY_ID[label]
    return {
        "reviewKey": evidence.get("reviewKey") or review_label_key(
            pattern_id,
            str(evidence.get("battleId") or ""),
            evidence.get("turn"),
            evidence.get("forceSwitch"),
        ),
        "patternId": pattern_id,
        "battleId": evidence.get("battleId"),
        "turn": evidence.get("turn"),
        "forceSwitch": bool(evidence.get("forceSwitch")),
        "label": label,
        "labelTitle": definition["label"],
        "confidence": round(confidence, 2),
        "reason": reason,
        "source": "deterministic_auto_labeler",
    }


def suggest_review_labels_for_pattern(
    pattern_context: dict[str, Any],
    overwrite_existing: bool = False,
) -> list[dict[str, Any]]:
    pattern = pattern_context.get("pattern") or {}
    suggestions: list[dict[str, Any]] = []
    for evidence in pattern_context.get("evidence") or []:
        if not isinstance(evidence, dict):
            continue
        existing_label = evidence.get("reviewLabel") if isinstance(evidence.get("reviewLabel"), dict) else None
        if existing_label and not existing_label.get("autoGenerated") and not overwrite_existing:
            continue
        suggestion = suggest_review_label_for_evidence(pattern, evidence)
        if suggestion.get("battleId") and isinstance(suggestion.get("turn"), int):
            suggestions.append(suggestion)
    return suggestions


def auto_review_label_for_evidence(
    pattern: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any] | None:
    suggestion = suggest_review_label_for_evidence(pattern, evidence)
    label_id = str(suggestion.get("label") or "")
    definition = REVIEW_LABEL_DEFINITIONS_BY_ID.get(label_id)
    if not definition:
        return None
    return {
        "key": str(suggestion.get("reviewKey") or ""),
        "patternId": str(suggestion.get("patternId") or ""),
        "battleId": str(suggestion.get("battleId") or ""),
        "turn": suggestion.get("turn"),
        "forceSwitch": bool(suggestion.get("forceSwitch")),
        "label": label_id,
        "labelTitle": definition["label"],
        "description": definition["description"],
        "tone": definition["tone"],
        "note": str(suggestion.get("reason") or ""),
        "source": str(suggestion.get("source") or "deterministic_auto_labeler"),
        "confidence": suggestion.get("confidence"),
        "autoGenerated": True,
    }


def _coerce_confidence(value: Any, default: float = 0.55) -> float:
    if isinstance(value, str):
        text = value.strip().rstrip("%")
        try:
            value = float(text)
        except ValueError:
            value = default
    if isinstance(value, (int, float)):
        confidence = float(value)
        if confidence > 1:
            confidence = confidence / 100
        return round(max(0.0, min(1.0, confidence)), 2)
    return default


def _compact_note(value: Any, fallback: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        text = fallback
    return text[:240]


def _ai_label_items(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        labels = raw.get("labels")
    else:
        labels = raw
    if not isinstance(labels, list):
        return []
    return [item for item in labels if isinstance(item, dict)]


def normalize_ai_review_label_suggestions(
    pattern_context: dict[str, Any],
    raw: Any,
    source: str = "ai_auto_labeler",
) -> list[dict[str, Any]]:
    """Validate model-created review labels against current deterministic evidence."""
    pattern = pattern_context.get("pattern") if isinstance(pattern_context.get("pattern"), dict) else {}
    pattern_id = str(pattern.get("id") or "")
    evidence_by_key: dict[str, dict[str, Any]] = {}
    for evidence in pattern_context.get("evidence") or []:
        if not isinstance(evidence, dict):
            continue
        existing_label = evidence.get("reviewLabel") if isinstance(evidence.get("reviewLabel"), dict) else None
        if existing_label and not existing_label.get("autoGenerated"):
            continue
        key = str(evidence.get("reviewKey") or "")
        if key:
            evidence_by_key[key] = evidence

    suggestions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _ai_label_items(raw):
        review_key = str(item.get("reviewKey") or item.get("key") or "")
        if review_key in seen:
            continue
        evidence = evidence_by_key.get(review_key)
        if not evidence:
            continue

        label_id = str(item.get("label") or item.get("labelId") or "")
        definition = REVIEW_LABEL_DEFINITIONS_BY_ID.get(label_id)
        if not definition:
            continue

        seen.add(review_key)
        suggestions.append({
            "reviewKey": review_key,
            "patternId": pattern_id,
            "battleId": evidence.get("battleId"),
            "turn": evidence.get("turn"),
            "forceSwitch": bool(evidence.get("forceSwitch")),
            "label": label_id,
            "labelTitle": definition["label"],
            "confidence": _coerce_confidence(item.get("confidence")),
            "reason": _compact_note(
                item.get("reason"),
                f"AI auto-label selected {definition['label']} from the current pattern evidence.",
            ),
            "source": source,
        })
    return suggestions


def persist_review_label_suggestions(
    suggestions: list[dict[str, Any]],
    path: Path = REVIEW_LABELS_PATH,
    overwrite_existing: bool = False,
) -> dict[str, Any]:
    labels = load_review_labels(path)
    saved: list[dict[str, Any]] = []
    now_ms = int(datetime.now().timestamp() * 1000)
    for suggestion in suggestions:
        label_id = str(suggestion.get("label") or "")
        if label_id not in REVIEW_LABEL_DEFINITIONS_BY_ID:
            continue
        key = str(suggestion.get("reviewKey") or review_label_key(
            str(suggestion.get("patternId") or ""),
            str(suggestion.get("battleId") or ""),
            suggestion.get("turn"),
            suggestion.get("forceSwitch"),
        ))
        if key in labels and not overwrite_existing:
            continue
        existing = labels.get(key) or {}
        labels[key] = {
            "key": key,
            "patternId": str(suggestion.get("patternId") or ""),
            "battleId": str(suggestion.get("battleId") or ""),
            "turn": suggestion.get("turn"),
            "forceSwitch": bool(suggestion.get("forceSwitch")),
            "label": label_id,
            "note": str(suggestion.get("reason") or "")[:240],
            "createdAtMs": existing.get("createdAtMs") or now_ms,
            "updatedAtMs": now_ms,
        }
        decorated = decorate_review_label(labels[key])
        if decorated:
            saved.append({
                "reviewKey": key,
                "reviewLabel": decorated,
                "confidence": suggestion.get("confidence"),
                "reason": suggestion.get("reason"),
                "source": suggestion.get("source"),
            })
    write_review_labels(labels, path)
    return {
        "saved": saved,
        "summary": review_label_summary(list(labels.values())),
    }


def _source_label(panel: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any] | None:
    label = evidence.get("reviewLabel") if isinstance(evidence.get("reviewLabel"), dict) else None
    if label:
        return label
    return auto_review_label_for_evidence(panel, evidence)


def _eval_expectation(label_id: str, evidence: dict[str, Any]) -> dict[str, Any] | None:
    category = str(evidence.get("category") or "")
    tags = {str(tag) for tag in (evidence.get("tags") or [])}
    if label_id == "engine_issue":
        return {
            "caseType": "regression",
            "evaluationTarget": "recommendation_quality",
            "expectedBehavior": "Avoid repeating a recommendation that the review marked as an engine issue.",
            "scoringChecks": [
                "new recommendation differs from the flagged old recommendation when the same state is replayed",
                "explanation identifies why the old line was risky or under-modeled",
                "confidence does not increase unless the new PV explains the improvement",
            ],
        }
    if label_id == "engine_uncertainty":
        return {
            "caseType": "confidence_calibration",
            "evaluationTarget": "opponent_model",
            "expectedBehavior": "Lower confidence or broaden hypotheses when opponent prediction is unstable.",
            "scoringChecks": [
                "confidence drops when the opponent action is ambiguous",
                "PV or explanation names the hidden-information risk",
                "engine does not present a fragile opponent read as a certain line",
            ],
        }
    if label_id == "field_pressure":
        pressure = "field pressure" in tags or category in {"field_pressure", "field_pressure_outcome", "action_prevented"}
        return {
            "caseType": "survival_positioning",
            "evaluationTarget": "field_pressure",
            "expectedBehavior": "Account for hazard, status, chip, speed, and tempo constraints before recommending slow value lines.",
            "scoringChecks": [
                "field effects are included in the pre-action survival calculation",
                "actions likely to fail before moving are heavily penalized",
                "switch or preserve lines are considered when field pressure threatens a key Pokemon",
            ],
            "fieldPressureObserved": pressure,
        }
    return None


def build_engine_eval_cases(pattern_panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for panel in pattern_panels:
        if not isinstance(panel, dict):
            continue
        pattern_id = str(panel.get("id") or "")
        for evidence in panel.get("evidence") or []:
            if not isinstance(evidence, dict):
                continue
            label = _source_label(panel, evidence)
            label_id = str((label or {}).get("label") or "")
            if label_id not in ENGINE_EVAL_LABELS:
                continue
            expectation = _eval_expectation(label_id, evidence)
            if not expectation:
                continue
            dedupe_key = (
                str(evidence.get("battleId") or ""),
                str(evidence.get("turn") or ""),
                "fs" if evidence.get("forceSwitch") else "regular",
                label_id,
                str(expectation.get("evaluationTarget") or ""),
            )
            if "|".join(dedupe_key) in seen:
                continue
            seen.add("|".join(dedupe_key))

            case_id = (
                f"{pattern_id}:{evidence.get('battleId')}:"
                f"turn:{evidence.get('turn')}:"
                f"{'fs' if evidence.get('forceSwitch') else 'regular'}:{label_id}"
            )
            cases.append({
                "caseId": case_id,
                "source": {
                    "patternId": pattern_id,
                    "patternTitle": panel.get("title"),
                    "battleId": evidence.get("battleId"),
                    "opponent": evidence.get("opponent"),
                    "result": evidence.get("result"),
                    "turn": evidence.get("turn"),
                    "forceSwitch": bool(evidence.get("forceSwitch")),
                    "category": evidence.get("category"),
                    "reviewLabel": label,
                },
                "positionSummary": {
                    "title": evidence.get("title"),
                    "verdict": evidence.get("verdict"),
                    "reviewQuestion": evidence.get("reviewQuestion"),
                    "confidence": evidence.get("confidence"),
                    "confidenceTier": evidence.get("confidenceTier"),
                    "engineAction": evidence.get("engineAction"),
                    "actualAction": evidence.get("actualAction"),
                    "opponent": evidence.get("opponentModel"),
                    "tags": evidence.get("tags") or [],
                },
                "expectedBehavior": expectation,
                "status": "candidate",
            })
    cases.sort(key=lambda item: (
        str(item["expectedBehavior"].get("caseType") or ""),
        str(item["source"].get("battleId") or ""),
        item["source"].get("turn") if isinstance(item["source"].get("turn"), int) else 10_000,
    ))
    return cases


def engine_eval_case_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    by_type = Counter(str((case.get("expectedBehavior") or {}).get("caseType") or "unknown") for case in cases)
    by_label = Counter(
        str((((case.get("source") or {}).get("reviewLabel") or {}).get("label")) or "unknown")
        for case in cases
    )
    pimc_splits = sum(
        1
        for case in cases
        if isinstance((case.get("priority") or {}).get("pimcUncertainty"), dict)
    )
    return {
        "totalCases": len(cases),
        "byType": dict(by_type.most_common()),
        "byLabel": dict(by_label.most_common()),
        "pimcSplits": pimc_splits,
    }
