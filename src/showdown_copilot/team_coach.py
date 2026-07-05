"""Deterministic team-coach context builders.

This module intentionally contains no provider/model logic. It prepares the
clean, separated evidence that the team coach agent should reason over.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .battle_turns import turn_summary
from .dashboard_archive import _effective_actual_action, _recommendation_matches


ROBUST_CONSENSUS_TIERS = {"unanimous", "strong"}
UNCERTAIN_CONSENSUS_TIERS = {"split", "fragile"}
NO_STABLE_CONFIDENCE_THRESHOLD = 0.02
ROBUST_CONFIDENCE_THRESHOLD = 65.0


def _norm(value: Any) -> str:
    return "".join(c for c in str(value or "").lower() if c.isalnum())


def _team_key(roster: list[Any] | tuple[Any, ...] | None) -> str:
    return " / ".join(str(item) for item in (roster or []) if item)


def _confidence_pct(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if numeric <= 1.0:
        numeric *= 100
    return round(numeric, 1)


def _raw_confidence(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric / 100 if numeric > 1.0 else numeric


def _result_counts(battles: list[dict[str, Any]]) -> dict[str, Any]:
    wins = sum(1 for battle in battles if battle.get("result") == "win")
    losses = sum(1 for battle in battles if battle.get("result") == "loss")
    unknown = len(battles) - wins - losses
    decided = wins + losses
    return {
        "battles": len(battles),
        "wins": wins,
        "losses": losses,
        "unknown": unknown,
        "winRate": round((wins / decided) * 100, 1) if decided else None,
    }


def _select_team_profile(
    archive: dict[str, Any],
    team_key: str | None = None,
) -> dict[str, Any] | None:
    profiles = [
        profile
        for profile in (archive.get("teamProfiles") or [])
        if isinstance(profile, dict)
    ]
    if not profiles:
        return None
    if team_key:
        wanted = _team_key(team_key.split(" / "))
        match = next(
            (profile for profile in profiles if _team_key(profile.get("team")) == wanted),
            None,
        )
        if match:
            return match
    return profiles[0]


def _matching_battles(
    archive: dict[str, Any],
    team_profile: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not team_profile:
        return []
    key = _team_key(team_profile.get("team"))
    return [
        battle
        for battle in (archive.get("battles") or [])
        if isinstance(battle, dict) and _team_key(battle.get("team")) == key
    ]


def _is_no_stable_line(my_pick: dict[str, Any]) -> bool:
    message = str(my_pick.get("message") or "")
    if "no stable line" in message.lower():
        return True
    raw_confidence = _raw_confidence(my_pick.get("confidence"))
    return raw_confidence is not None and raw_confidence > 0 and raw_confidence < NO_STABLE_CONFIDENCE_THRESHOLD


def _pimc_consensus(my_pick: dict[str, Any]) -> dict[str, Any] | None:
    consensus = my_pick.get("pimcConsensus")
    return consensus if isinstance(consensus, dict) else None


def _is_pimc_uncertain(consensus: dict[str, Any] | None) -> bool:
    if not consensus:
        return False
    tier = str(consensus.get("tier") or "")
    return bool(consensus.get("uncertain")) or tier in UNCERTAIN_CONSENSUS_TIERS


def _is_robust_consensus(consensus: dict[str, Any] | None) -> bool:
    if not consensus:
        return False
    tier = str(consensus.get("tier") or "")
    return tier in ROBUST_CONSENSUS_TIERS and not bool(consensus.get("uncertain"))


def _case_base(
    battle: dict[str, Any],
    row: dict[str, Any],
    turn: dict[str, Any],
) -> dict[str, Any]:
    my_pick = row.get("myPick") if isinstance(row.get("myPick"), dict) else {}
    actual = _effective_actual_action(row)
    consensus = _pimc_consensus(my_pick)
    return {
        "battleId": battle.get("battleId"),
        "opponent": battle.get("opponent"),
        "result": battle.get("result"),
        "turn": row.get("turn"),
        "forceSwitch": bool(row.get("forceSwitch")),
        "engineAction": turn.get("pickLabel"),
        "actualAction": turn.get("actualLabel"),
        "confidence": _confidence_pct(my_pick.get("confidence")),
        "message": my_pick.get("message"),
        "pimcConsensus": consensus,
        "pvMatchedReality": row.get("pvMatchedReality"),
        "fieldEventSummary": turn.get("fieldEventSummary") or [],
        "actualKind": actual.get("kind"),
    }


def _turn_bucket_cases(
    battles: list[dict[str, Any]],
    postmortems_by_battle_id: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {
        "robustIgnoredAdvice": [],
        "pimcSplits": [],
        "noStableLines": [],
        "fieldPressure": [],
        "pvMisses": [],
    }

    for battle in battles:
        battle_id = str(battle.get("battleId") or "")
        pm = postmortems_by_battle_id.get(battle_id)
        if not pm:
            continue
        for row in (pm.get("turns") or []):
            if not isinstance(row, dict):
                continue
            my_pick = row.get("myPick") if isinstance(row.get("myPick"), dict) else {}
            if not my_pick.get("name"):
                continue
            turn = turn_summary(row)
            case = _case_base(battle, row, turn)
            consensus = case.get("pimcConsensus") if isinstance(case.get("pimcConsensus"), dict) else None
            no_stable = _is_no_stable_line(my_pick)
            pimc_uncertain = _is_pimc_uncertain(consensus)
            confidence = case.get("confidence")
            matched = _recommendation_matches(row)

            if (
                matched is False
                and isinstance(confidence, (int, float))
                and confidence >= ROBUST_CONFIDENCE_THRESHOLD
                and _is_robust_consensus(consensus)
                and not no_stable
            ):
                buckets["robustIgnoredAdvice"].append({
                    **case,
                    "reason": "Healthy, strong-consensus engine advice was not followed.",
                })
            if pimc_uncertain:
                buckets["pimcSplits"].append({
                    **case,
                    "reason": "PIMC hypotheses disagreed, so the recommendation depends on hidden information.",
                })
            if no_stable:
                buckets["noStableLines"].append({
                    **case,
                    "reason": "Engine value was near zero or explicitly reported no stable line.",
                })
            if row.get("pvMatchedReality") is False:
                buckets["pvMisses"].append({
                    **case,
                    "reason": "Engine opponent prediction did not match the observed opponent action.",
                })
            if turn.get("fieldEventSummary"):
                buckets["fieldPressure"].append({
                    **case,
                    "reason": "Hazards, status, contact chip, or residual pressure affected the turn.",
                })

    for rows in buckets.values():
        rows.sort(key=lambda item: (
            str(item.get("result") or ""),
            item.get("turn") if isinstance(item.get("turn"), int) else 10_000,
            str(item.get("battleId") or ""),
        ))
    return buckets


def _engine_eval_buckets(
    engine_eval_archive: dict[str, Any] | None,
    battle_ids: set[str],
) -> dict[str, list[dict[str, Any]]]:
    buckets = {
        "pimcSplits": [],
        "noStableLines": [],
    }
    if not isinstance(engine_eval_archive, dict):
        return buckets

    for case in engine_eval_archive.get("cases") or []:
        if not isinstance(case, dict):
            continue
        source = case.get("source") if isinstance(case.get("source"), dict) else {}
        battle_id = str(source.get("battleId") or "")
        if battle_ids and battle_id not in battle_ids:
            continue
        replay = case.get("replay") if isinstance(case.get("replay"), dict) else {}
        terminal = replay.get("terminal") if isinstance(replay.get("terminal"), dict) else {}
        priority = case.get("priority") if isinstance(case.get("priority"), dict) else {}
        base = {
            "battleId": battle_id,
            "opponent": source.get("opponent"),
            "result": source.get("result"),
            "turn": source.get("turn"),
            "forceSwitch": bool(source.get("forceSwitch")),
            "engineAction": (case.get("positionSummary") or {}).get("engineAction"),
            "actualAction": (case.get("positionSummary") or {}).get("actualAction"),
            "confidence": _confidence_pct(terminal.get("confidence")),
            "message": terminal.get("message"),
            "pimcConsensus": terminal.get("pimcConsensus"),
            "caseId": case.get("caseId"),
        }
        pimc_uncertainty = priority.get("pimcUncertainty")
        if isinstance(pimc_uncertainty, dict):
            buckets["pimcSplits"].append({
                **base,
                "reason": "Replay-backed engine eval case has split PIMC consensus.",
            })
        raw_conf = _raw_confidence(terminal.get("confidence"))
        if (
            "no stable line" in str(terminal.get("message") or "").lower()
            or (raw_conf is not None and raw_conf > 0 and raw_conf < NO_STABLE_CONFIDENCE_THRESHOLD)
        ):
            buckets["noStableLines"].append({
                **base,
                "reason": "Replay-backed engine eval case is a near-zero/no-stable-line position.",
            })
    return buckets


def _dedupe_cases(cases: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for case in cases:
        key = (
            f"{case.get('battleId')}|{case.get('turn')}|"
            f"{'fs' if case.get('forceSwitch') else 'regular'}|{case.get('reason')}"
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(case)
        if len(deduped) >= limit:
            break
    return deduped


def _bucket_summary(cases: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    by_result = Counter(str(case.get("result") or "unknown") for case in cases)
    return {
        "count": len(cases),
        "byResult": dict(by_result.most_common()),
        "examples": _dedupe_cases(cases, limit),
    }


def build_team_coach_brief(
    archive: dict[str, Any],
    postmortems_by_battle_id: dict[str, dict[str, Any]],
    *,
    engine_eval_archive: dict[str, Any] | None = None,
    team_key: str | None = None,
    evidence_limit: int = 6,
) -> dict[str, Any]:
    """Build the deterministic source of truth for a team-level coach run."""
    profile = _select_team_profile(archive, team_key)
    battles = _matching_battles(archive, profile)
    battle_ids = {
        str(battle.get("battleId"))
        for battle in battles
        if battle.get("battleId")
    }
    turn_buckets = _turn_bucket_cases(battles, postmortems_by_battle_id)
    eval_buckets = _engine_eval_buckets(engine_eval_archive, battle_ids)

    pimc_split_cases = turn_buckets["pimcSplits"] + eval_buckets["pimcSplits"]
    no_stable_cases = turn_buckets["noStableLines"] + eval_buckets["noStableLines"]
    robust_cases = turn_buckets["robustIgnoredAdvice"]
    field_pressure_cases = turn_buckets["fieldPressure"]
    pv_miss_cases = turn_buckets["pvMisses"]

    return {
        "purpose": "team_coach_brief",
        "team": {
            "key": _team_key((profile or {}).get("team")),
            "name": (profile or {}).get("teamName"),
            "roster": (profile or {}).get("team") or [],
            "battleIds": sorted(battle_ids),
        },
        "summary": {
            **_result_counts(battles),
            "performanceBattles": (profile or {}).get("performanceBattles") or 0,
            "followRate": (profile or {}).get("followRate"),
            "topLead": (profile or {}).get("topLead"),
        },
        "pokemonProfiles": (profile or {}).get("pokemon") or [],
        "evidenceBuckets": {
            "robustIgnoredAdvice": _bucket_summary(robust_cases, evidence_limit),
            "engineUncertainty": {
                "pimcSplits": _bucket_summary(pimc_split_cases, evidence_limit),
                "pvMisses": _bucket_summary(pv_miss_cases, evidence_limit),
            },
            "noStableLines": _bucket_summary(no_stable_cases, evidence_limit),
            "fieldPressure": _bucket_summary(field_pressure_cases, evidence_limit),
        },
        "reviewPriorities": [
            *_dedupe_cases(robust_cases, max(1, evidence_limit // 2)),
            *_dedupe_cases(pimc_split_cases, max(1, evidence_limit // 2)),
            *_dedupe_cases(no_stable_cases, max(1, evidence_limit // 2)),
        ][:evidence_limit],
        "agentUsageNotes": [
            "Use robustIgnoredAdvice for player-choice calibration; these exclude split PIMC and no-stable-line turns.",
            "Use engineUncertainty.pimcSplits for hidden-information or opponent-model uncertainty, not automatic player mistakes.",
            "Use noStableLines to review earlier positioning; do not frame the doomed turn itself as the main decision error.",
            "Use fieldPressure to explain hazard/status/chip constraints and Pokemon preservation issues.",
            "Keep team-building changes as suggestions unless meta or simulator/damage-check tools are available.",
        ],
    }
