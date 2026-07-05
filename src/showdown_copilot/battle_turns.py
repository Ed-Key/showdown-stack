"""Turn-summary and battle-detail helpers for dashboard data."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .dashboard_archive import (
    _as_number,
    _effective_actual_action,
    _iter_postmortems,
    _norm,
    _recommendation_matches,
    _round_pct,
    summarize_postmortem,
)


def row_action_label(action: Any) -> str:
    if not isinstance(action, dict):
        return "Unknown"
    name = action.get("name")
    kind = action.get("kind")
    if kind == "prevented":
        return f"prevented: {action.get('reason') or 'unknown'}"
    if not name:
        return "Unknown"
    return f"{kind}: {name}" if kind else str(name)


def prediction_label(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "n/a"
    if _norm(raw) == "nomove":
        return "no move"
    return raw


def turn_field_events(row: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in row.get("hazardsAdded") or []:
        if isinstance(item, dict):
            events.append({
                "type": "hazard_added",
                "side": item.get("side"),
                "name": item.get("name"),
                "label": f"{item.get('side', 'side')} gained {item.get('name', 'hazard')}",
            })
    for item in row.get("hazardsRemoved") or []:
        if isinstance(item, dict):
            events.append({
                "type": "hazard_removed",
                "side": item.get("side"),
                "name": item.get("name"),
                "label": f"{item.get('side', 'side')} removed {item.get('name', 'hazard')}",
            })
    for item in row.get("residualEvents") or []:
        if not isinstance(item, dict):
            continue
        source = item.get("source") or "residual"
        category = item.get("category") or "other"
        target = item.get("targetSpecies") or "target"
        amount = item.get("hpPctLost")
        direction = "lost" if isinstance(amount, (int, float)) and amount >= 0 else "recovered"
        pct = abs(amount) if isinstance(amount, (int, float)) else None
        pct_label = f" {pct:g}%" if pct is not None else ""
        events.append({
            "type": "residual",
            "side": item.get("side"),
            "category": category,
            "source": source,
            "targetSpecies": target,
            "hpPctLost": amount,
            "label": f"{target} {direction}{pct_label} from {source}",
        })
    return events


def summarize_field_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("type") or "field")
        category = str(event.get("category") or "")
        if kind == "residual":
            group = category or "residual"
            key = (kind, group, str(event.get("targetSpecies") or ""))
            label_detail = event.get("label") or group
        else:
            group = str(event.get("name") or kind)
            key = (kind, group, str(event.get("side") or ""))
            label_detail = event.get("label") or group

        if key not in grouped:
            grouped[key] = {
                "type": kind,
                "category": category,
                "group": group,
                "count": 0,
                "hpPctLost": 0,
                "labels": [],
            }
        bucket = grouped[key]
        bucket["count"] += 1
        amount = event.get("hpPctLost")
        if isinstance(amount, (int, float)):
            bucket["hpPctLost"] += amount
        if label_detail:
            bucket["labels"].append(str(label_detail))

    summaries: list[dict[str, Any]] = []
    for bucket in grouped.values():
        count = bucket["count"]
        group = bucket["group"]
        if bucket["type"] == "residual":
            label = f"{group} x{count}" if count > 1 else group
            if bucket["hpPctLost"]:
                label = f"{label} ({bucket['hpPctLost']:g}%)"
        else:
            label = f"{group} x{count}" if count > 1 else group
        summaries.append({
            "type": bucket["type"],
            "category": bucket["category"],
            "group": group,
            "count": count,
            "hpPctLost": bucket["hpPctLost"],
            "label": label,
            "details": bucket["labels"],
        })
    return summaries


def turn_summary(row: dict[str, Any]) -> dict[str, Any]:
    my_pick = row.get("myPick") if isinstance(row.get("myPick"), dict) else {}
    actual = _effective_actual_action(row)
    match = _recommendation_matches(row)
    faints = row.get("faints") if isinstance(row.get("faints"), list) else []
    residual = row.get("residualEvents") if isinstance(row.get("residualEvents"), list) else []
    failures = row.get("failureMessages") if isinstance(row.get("failureMessages"), list) else []
    field_events = turn_field_events(row)

    return {
        "turn": row.get("turn"),
        "rqid": row.get("rqid"),
        "forceSwitch": bool(row.get("forceSwitch")),
        "pickKind": my_pick.get("kind"),
        "pickName": my_pick.get("name"),
        "pickLabel": row_action_label(my_pick),
        "actualKind": actual.get("kind"),
        "actualName": actual.get("name"),
        "actualReason": actual.get("reason"),
        "actualLabel": row_action_label(actual),
        "matchedRecommendation": match,
        "confidence": _round_pct(_as_number(my_pick.get("confidence"))),
        "sims": my_pick.get("sims"),
        "depth": my_pick.get("depth"),
        "pv": my_pick.get("pv") if isinstance(my_pick.get("pv"), list) else [],
        "enginePredictedOpp": row.get("enginePredictedOpp"),
        "actualOppMove": row.get("actualOppMove"),
        "pvMatchedReality": row.get("pvMatchedReality"),
        "faintedBefore": row.get("faintedBefore"),
        "faints": faints,
        "critical": bool(row.get("faintedBefore") or faints),
        "damageIDealt": row.get("damageIDealt"),
        "damageOppDealt": row.get("damageOppDealt"),
        "residualEvents": residual,
        "fieldEvents": field_events,
        "fieldEventSummary": summarize_field_events(field_events),
        "failureMessages": failures,
        "issues": [
            label for label, present in (
                ("missing recommendation", not my_pick.get("name")),
                ("actual unknown", not actual.get("name") and actual.get("kind") != "prevented"),
                ("action prevented", actual.get("kind") == "prevented"),
                ("pv miss", row.get("pvMatchedReality") is False),
                ("field pressure", bool(field_events)),
            )
            if present
        ],
    }


def battle_detail(battle_id: str, directory: Path) -> dict[str, Any] | None:
    for path, pm in _iter_postmortems(directory):
        if pm.get("battleId") != battle_id:
            continue
        summary = summarize_postmortem(path, pm)
        turns = [
            turn_summary(row)
            for row in (pm.get("turns") or [])
            if isinstance(row, dict)
        ]
        turns.sort(key=lambda row: (
            row.get("turn") if isinstance(row.get("turn"), int) else 10_000,
            1 if row.get("forceSwitch") else 0,
            row.get("rqid") if isinstance(row.get("rqid"), int) else 10_000,
        ))
        return {
            "summary": summary,
            "turns": turns,
        }
    return None
