"""Archive loading and aggregate summary helpers for the dashboard."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .team_profiles import build_team_profiles


PatternPanelBuilder = Callable[
    [list[dict[str, Any]], dict[str, dict[str, Any]], int, dict[str, dict[str, Any]] | None],
    list[dict[str, Any]],
]
ReviewLabelSummary = Callable[[list[dict[str, Any]]], dict[str, Any]]


def _norm(value: Any) -> str:
    return "".join(c for c in str(value or "").lower() if c.isalnum())


def _round_pct(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value * 100, 1)


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _as_int(value: Any, default: int = 0) -> int:
    return value if isinstance(value, int) else default


def _as_number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _schema_version(pm: dict[str, Any]) -> int:
    value = pm.get("schemaVersion")
    return value if isinstance(value, int) else 0


def _passes_schema_filter(
    pm: dict[str, Any],
    min_schema_version: int | None,
) -> bool:
    if min_schema_version is None or min_schema_version <= 0:
        return True
    return _schema_version(pm) >= min_schema_version


def _timestamp_label(ms: int | None) -> str:
    if not ms:
        return "Unknown"
    return datetime.fromtimestamp(ms / 1000).strftime("%b %d, %I:%M %p")


def _day_key(ms: int | None) -> str:
    if not ms:
        return "unknown"
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _iter_postmortems(directory: Path) -> list[tuple[Path, dict[str, Any]]]:
    if not directory.exists():
        return []

    rows: list[tuple[Path, dict[str, Any]]] = []
    for path in directory.glob("*.json"):
        pm = _read_json_file(path)
        if pm is None:
            continue
        if not isinstance(pm.get("battleId"), str):
            continue
        if not isinstance(pm.get("turns"), list):
            continue
        rows.append((path, pm))
    return rows


def _is_finished_battle(pm: dict[str, Any]) -> bool:
    ended_ms = _as_number(pm.get("endedAtMs"))
    has_winner = isinstance(pm.get("winner"), str) and bool(pm.get("winner"))
    has_turns = len(pm.get("turns") or []) >= 2
    return has_turns and (has_winner or bool(ended_ms))


def _recorded_sort_key(path: Path, summary: dict[str, Any]) -> tuple[int, str]:
    ended_ms = _as_int(summary.get("endedAtMs"), 0)
    if ended_ms:
        return (ended_ms, path.name)
    try:
        modified_ms = int(path.stat().st_mtime * 1000)
    except OSError:
        modified_ms = 0
    return (modified_ms, path.name)


def _derive_result(pm: dict[str, Any]) -> str:
    explicit = pm.get("result")
    if isinstance(explicit, str) and explicit.strip():
        lowered = explicit.lower()
        if lowered.startswith("win"):
            return "win"
        if lowered.startswith("loss") or lowered.startswith("lose"):
            return "loss"

    winner = _norm(pm.get("winner"))
    mine = _norm(pm.get("myUsername"))
    if not winner:
        return "unknown"
    if mine and winner == mine:
        return "win"
    return "loss"


def _effective_actual_action(row: dict[str, Any]) -> dict[str, Any]:
    actual = row.get("actualMyAction")
    if isinstance(actual, dict):
        if actual.get("kind") != "unknown" or actual.get("name"):
            return actual
    else:
        actual = {"kind": "unknown", "name": None}

    faints = row.get("faints")
    mine_faint = None
    if isinstance(faints, list):
        for faint in faints:
            if isinstance(faint, dict) and faint.get("side") == "mine":
                mine_faint = faint
                break
    if mine_faint:
        reason = "fainted before action"
        damage_opp = row.get("damageOppDealt")
        if isinstance(damage_opp, dict) and damage_opp.get("move"):
            reason = f"fainted before action ({damage_opp['move']})"
        else:
            for event in row.get("residualEvents") or []:
                if not isinstance(event, dict) or event.get("side") != "mine":
                    continue
                if _norm(event.get("targetSpecies")) == _norm(mine_faint.get("species")):
                    reason = f"fainted before action ({event.get('source') or 'residual'})"
                    break
        return {"kind": "prevented", "name": None, "reason": reason}

    return actual


def _recommendation_matches(row: dict[str, Any]) -> bool | None:
    my_pick = row.get("myPick")
    actual = _effective_actual_action(row)
    if not isinstance(my_pick, dict) or not isinstance(actual, dict):
        return None
    pick_name = my_pick.get("name")
    actual_name = actual.get("name")
    if actual.get("kind") == "prevented":
        return None
    if not pick_name or not actual_name:
        return None
    return _norm(pick_name) == _norm(actual_name)


def _confidence_values(rows: list[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    for row in rows:
        my_pick = row.get("myPick")
        if not isinstance(my_pick, dict):
            continue
        confidence = _as_number(my_pick.get("confidence"))
        if confidence is not None:
            values.append(confidence)
    return values


def _detect_duplicate_regular_turns(turns: list[dict[str, Any]]) -> list[int]:
    counts: Counter[int] = Counter()
    for row in turns:
        if not isinstance(row, dict) or row.get("forceSwitch"):
            continue
        turn = row.get("turn")
        if isinstance(turn, int):
            counts[turn] += 1
    return sorted(turn for turn, count in counts.items() if count > 1)


def _field_event_counts(turns: list[dict[str, Any]]) -> dict[str, int]:
    hazards_added = 0
    hazards_removed = 0
    residual_events = 0
    residual_by_category: Counter[str] = Counter()
    for row in turns:
        hazards_added += len(row.get("hazardsAdded") or [])
        hazards_removed += len(row.get("hazardsRemoved") or [])
        for event in row.get("residualEvents") or []:
            if not isinstance(event, dict):
                continue
            residual_events += 1
            residual_by_category[str(event.get("category") or "other")] += 1

    return {
        "hazardsAdded": hazards_added,
        "hazardsRemoved": hazards_removed,
        "residualEvents": residual_events,
        "hazardResidualEvents": residual_by_category["hazard"],
        "statusResidualEvents": residual_by_category["status"],
        "itemResidualEvents": residual_by_category["item"],
        "contactResidualEvents": residual_by_category["contact"],
        "otherResidualEvents": sum(
            count
            for category, count in residual_by_category.items()
            if category not in {"hazard", "status", "item", "contact"}
        ),
    }


def summarize_postmortem(path: Path, pm: dict[str, Any]) -> dict[str, Any]:
    turns = [row for row in (pm.get("turns") or []) if isinstance(row, dict)]
    regular_rows = [row for row in turns if not row.get("forceSwitch")]
    force_rows = [row for row in turns if row.get("forceSwitch")]

    followable = [
        row for row in regular_rows
        if isinstance(row.get("myPick"), dict)
        and row["myPick"].get("name")
        and isinstance(row.get("actualMyAction"), dict)
        and row["actualMyAction"].get("name")
    ]
    followed = sum(1 for row in followable if _recommendation_matches(row))

    move_recs = [
        row for row in regular_rows
        if isinstance(row.get("myPick"), dict) and row["myPick"].get("kind") == "move"
    ]
    switch_recs = [
        row for row in regular_rows
        if isinstance(row.get("myPick"), dict) and row["myPick"].get("kind") == "switch"
    ]
    switch_followable = [
        row for row in switch_recs
        if isinstance(row.get("actualMyAction"), dict) and row["actualMyAction"].get("name")
    ]
    switch_followed = sum(1 for row in switch_followable if _recommendation_matches(row))

    pv_rows = [
        row for row in regular_rows
        if row.get("enginePredictedOpp") and row.get("actualOppMove")
    ]
    pv_hits = sum(1 for row in pv_rows if row.get("pvMatchedReality") is True)
    critical_rows = [
        row for row in turns
        if row.get("faintedBefore")
        or (isinstance(row.get("faints"), list) and bool(row["faints"]))
    ]

    confidence_values = _confidence_values(regular_rows)
    confidence_avg = (
        sum(confidence_values) / len(confidence_values)
        if confidence_values
        else None
    )
    ended_ms = _as_int(pm.get("endedAtMs"), 0)
    result = _derive_result(pm)
    duplicate_turns = _detect_duplicate_regular_turns(turns)
    field_events = _field_event_counts(turns)
    missing_actual = [
        row for row in regular_rows
        if not _effective_actual_action(row).get("name")
        and _effective_actual_action(row).get("kind") != "prevented"
    ]

    data_issues: list[str] = []
    if result == "unknown":
        data_issues.append("missing result")
    if not pm.get("replayUrl"):
        data_issues.append("missing replay")
    if duplicate_turns:
        data_issues.append("duplicate turns")
    if missing_actual:
        data_issues.append("actual unknown")

    return {
        "battleId": pm.get("battleId"),
        "file": path.name,
        "format": pm.get("format") or "Unknown format",
        "myUsername": pm.get("myUsername") or "Unknown",
        "opponent": pm.get("opponentUsername") or pm.get("opponent") or "Unknown",
        "winner": pm.get("winner"),
        "result": result,
        "teamName": pm.get("teamName") if isinstance(pm.get("teamName"), str) else None,
        "endedAtMs": ended_ms,
        "endedAtLabel": _timestamp_label(ended_ms),
        "day": _day_key(ended_ms),
        "totalTurns": pm.get("totalTurns") or len(regular_rows),
        "schemaVersion": pm.get("schemaVersion"),
        "replayUrl": pm.get("replayUrl"),
        "team": ((pm.get("teamPreview") or {}).get("mine") or []),
        "opponentTeam": ((pm.get("teamPreview") or {}).get("opp") or []),
        "teamPerformance": pm.get("teamPerformance") if isinstance(pm.get("teamPerformance"), dict) else None,
        "metrics": {
            "rows": len(turns),
            "regularRows": len(regular_rows),
            "forceSwitchRows": len(force_rows),
            "followable": len(followable),
            "followed": followed,
            "followRate": _round_pct(_rate(followed, len(followable))),
            "moveRecommendations": len(move_recs),
            "switchRecommendations": len(switch_recs),
            "switchFollowed": switch_followed,
            "switchFollowRate": _round_pct(_rate(switch_followed, len(switch_followable))),
            "pvKnown": len(pv_rows),
            "pvHits": pv_hits,
            "pvHitRate": _round_pct(_rate(pv_hits, len(pv_rows))),
            "criticalTurns": len(critical_rows),
            "avgConfidence": _round_pct(confidence_avg),
            "missingActualActions": len(missing_actual),
            "duplicateRegularTurns": duplicate_turns,
            **field_events,
        },
        "dataIssues": data_issues,
    }


def summarize_archive(
    directory: Path,
    min_schema_version: int | None,
    *,
    pattern_evidence_limit: int = 8,
    review_labels: dict[str, dict[str, Any]] | None = None,
    pattern_panel_builder: PatternPanelBuilder | None = None,
    review_label_definitions: list[dict[str, Any]] | None = None,
    review_label_summary: ReviewLabelSummary | None = None,
) -> dict[str, Any]:
    raw = _iter_postmortems(directory)
    finished: list[dict[str, Any]] = []
    recorded: list[tuple[tuple[int, str], dict[str, Any]]] = []
    postmortems_by_battle_id: dict[str, dict[str, Any]] = {}
    schema_versions: Counter[str] = Counter()
    schema_skipped = 0
    skipped = 0
    for path, pm in raw:
        schema_versions[str(_schema_version(pm) or "unknown")] += 1
        battle_id = pm.get("battleId")
        if isinstance(battle_id, str):
            postmortems_by_battle_id[battle_id] = pm
        if not _passes_schema_filter(pm, min_schema_version):
            schema_skipped += 1
            continue
        summary = summarize_postmortem(path, pm)
        recorded.append((_recorded_sort_key(path, summary), summary))
        if not _is_finished_battle(pm):
            skipped += 1
            continue
        finished.append(summary)

    finished.sort(key=lambda row: (row.get("endedAtMs") or 0, row.get("file") or ""), reverse=True)
    recorded.sort(key=lambda row: row[0], reverse=True)
    latest_recorded_battle = recorded[0][1] if recorded else None

    wins = sum(1 for row in finished if row.get("result") == "win")
    losses = sum(1 for row in finished if row.get("result") == "loss")
    unknown = len(finished) - wins - losses

    followed = sum(row["metrics"]["followed"] for row in finished)
    followable = sum(row["metrics"]["followable"] for row in finished)
    pv_hits = sum(row["metrics"]["pvHits"] for row in finished)
    pv_known = sum(row["metrics"]["pvKnown"] for row in finished)
    switch_recs = sum(row["metrics"]["switchRecommendations"] for row in finished)
    move_recs = sum(row["metrics"]["moveRecommendations"] for row in finished)
    force_rows = sum(row["metrics"]["forceSwitchRows"] for row in finished)
    critical_turns = sum(row["metrics"]["criticalTurns"] for row in finished)
    hazards_added = sum(row["metrics"]["hazardsAdded"] for row in finished)
    hazards_removed = sum(row["metrics"]["hazardsRemoved"] for row in finished)
    residual_events = sum(row["metrics"]["residualEvents"] for row in finished)
    hazard_residual_events = sum(row["metrics"]["hazardResidualEvents"] for row in finished)
    status_residual_events = sum(row["metrics"]["statusResidualEvents"] for row in finished)
    item_residual_events = sum(row["metrics"]["itemResidualEvents"] for row in finished)
    contact_residual_events = sum(row["metrics"]["contactResidualEvents"] for row in finished)

    confidence_values = [
        row["metrics"]["avgConfidence"] / 100
        for row in finished
        if row["metrics"]["avgConfidence"] is not None
    ]
    avg_confidence = (
        sum(confidence_values) / len(confidence_values)
        if confidence_values
        else None
    )

    team_counter: Counter[str] = Counter()
    recommendation_counter: Counter[str] = Counter()
    disagreement_counter: Counter[str] = Counter()
    day_rows: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "day": "",
        "battles": 0,
        "wins": 0,
        "losses": 0,
        "followed": 0,
        "followable": 0,
    })

    for row in finished:
        for species in row.get("team") or []:
            team_counter[str(species)] += 1
        day = str(row.get("day") or "unknown")
        day_rows[day]["day"] = day
        day_rows[day]["battles"] += 1
        day_rows[day]["wins"] += 1 if row.get("result") == "win" else 0
        day_rows[day]["losses"] += 1 if row.get("result") == "loss" else 0
        day_rows[day]["followed"] += row["metrics"]["followed"]
        day_rows[day]["followable"] += row["metrics"]["followable"]

        full = postmortems_by_battle_id.get(str(row.get("battleId")))
        if not full:
            continue
        for turn in full.get("turns") or []:
            if not isinstance(turn, dict) or turn.get("forceSwitch"):
                continue
            my_pick = turn.get("myPick")
            if not isinstance(my_pick, dict) or not my_pick.get("name"):
                continue
            recommendation_counter[str(my_pick["name"])] += 1
            match = _recommendation_matches(turn)
            if match is False:
                disagreement_counter[str(my_pick["name"])] += 1

    timeline = sorted(day_rows.values(), key=lambda row: row["day"])
    for row in timeline:
        row["followRate"] = _round_pct(_rate(row["followed"], row["followable"]))

    pattern_panels = []
    if pattern_panel_builder is not None:
        pattern_panels = pattern_panel_builder(
            finished,
            postmortems_by_battle_id,
            pattern_evidence_limit,
            review_labels,
        )
    labels = list((review_labels or {}).values())
    label_summary = (
        review_label_summary(labels)
        if review_label_summary is not None
        else {"totalLabeled": len(labels), "counts": {}, "byLabel": []}
    )

    return {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "sourceDir": str(directory),
        "filters": {
            "minSchemaVersion": min_schema_version,
        },
        "summary": {
            "totalFiles": len(raw),
            "finishedBattles": len(finished),
            "skippedFiles": skipped,
            "schemaSkippedFiles": schema_skipped,
            "schemaVersions": dict(sorted(schema_versions.items())),
            "wins": wins,
            "losses": losses,
            "unknownResults": unknown,
            "winRate": _round_pct(_rate(wins, wins + losses)),
            "followed": followed,
            "followable": followable,
            "followRate": _round_pct(_rate(followed, followable)),
            "pvHits": pv_hits,
            "pvKnown": pv_known,
            "pvHitRate": _round_pct(_rate(pv_hits, pv_known)),
            "switchRecommendations": switch_recs,
            "moveRecommendations": move_recs,
            "switchRecommendationRate": _round_pct(_rate(switch_recs, switch_recs + move_recs)),
            "forceSwitchRows": force_rows,
            "criticalTurns": critical_turns,
            "avgConfidence": _round_pct(avg_confidence),
            "hazardsAdded": hazards_added,
            "hazardsRemoved": hazards_removed,
            "residualEvents": residual_events,
            "hazardResidualEvents": hazard_residual_events,
            "statusResidualEvents": status_residual_events,
            "itemResidualEvents": item_residual_events,
            "contactResidualEvents": contact_residual_events,
        },
        "latestRecordedBattle": latest_recorded_battle,
        "battles": finished,
        "timeline": timeline,
        "topTeamSpecies": [
            {"name": name, "count": count}
            for name, count in team_counter.most_common(12)
        ],
        "topRecommendations": [
            {"name": name, "count": count}
            for name, count in recommendation_counter.most_common(10)
        ],
        "topDisagreements": [
            {"name": name, "count": count}
            for name, count in disagreement_counter.most_common(10)
        ],
        "teamProfiles": build_team_profiles(finished),
        "patternPanels": pattern_panels,
        "reviewLabels": {
            "definitions": review_label_definitions or [],
            "summary": label_summary,
        },
    }


def load_postmortem_by_battle_id(
    battle_id: str,
    directory: Path,
) -> dict[str, Any] | None:
    for _, pm in _iter_postmortems(directory):
        if pm.get("battleId") == battle_id:
            return pm
    return None


def load_postmortems_by_battle_id(
    directory: Path,
    min_schema_version: int | None = None,
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for _, pm in _iter_postmortems(directory):
        battle_id = pm.get("battleId")
        if not isinstance(battle_id, str):
            continue
        if not _passes_schema_filter(pm, min_schema_version):
            continue
        rows[battle_id] = pm
    return rows
