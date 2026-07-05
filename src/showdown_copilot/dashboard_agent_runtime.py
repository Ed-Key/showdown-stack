"""Runtime helpers for dashboard AI-agent runs."""
from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException

from .battle_turns import prediction_label
from .coach_context import coach_turn_score, compact_pattern_context_for_model


def normalize_coach_tool_args(name: str, args: dict[str, Any], battle_id: str) -> dict[str, Any]:
    safe_args = dict(args or {})
    if name in {"get_coach_brief", "get_battle_context", "get_team_coach_brief"}:
        safe_args["battleId"] = battle_id
    return safe_args


def normalize_team_tool_args(name: str, args: dict[str, Any], battle_id: str) -> dict[str, Any]:
    safe_args = dict(args or {})
    if name in {
        "get_team_overview",
        "get_team_bucket_examples",
        "get_pokemon_profile",
        "get_pokemon_battle_timeline",
        "get_team_state_at_turn",
        "get_engine_eval_cases",
    }:
        safe_args["battleId"] = battle_id
    elif name == "get_battle_window" and not safe_args.get("battleId"):
        safe_args["battleId"] = battle_id
    return safe_args


def normalize_pattern_tool_args(name: str, args: dict[str, Any], pattern_id: str) -> dict[str, Any]:
    safe_args = dict(args or {})
    if name == "get_pattern_context":
        safe_args["patternId"] = pattern_id
    return safe_args


def compact_turn_for_model(turn: dict[str, Any]) -> dict[str, Any]:
    return {
        "turn": turn.get("turn"),
        "forceSwitch": turn.get("forceSwitch"),
        "pickLabel": turn.get("pickLabel"),
        "actualLabel": turn.get("actualLabel"),
        "matchedRecommendation": turn.get("matchedRecommendation"),
        "confidence": turn.get("confidence"),
        "enginePredictedOpp": prediction_label(turn.get("enginePredictedOpp")),
        "actualOppMove": prediction_label(turn.get("actualOppMove")),
        "critical": turn.get("critical"),
        "issues": turn.get("issues") or [],
        "fieldEventSummary": turn.get("fieldEventSummary") or [],
        "strategicSignals": [
            {
                "type": signal.get("type"),
                "severity": signal.get("severity"),
                "details": signal.get("details"),
            }
            for signal in (turn.get("strategicSignals") or [])[:5]
            if isinstance(signal, dict)
        ],
    }


def compact_tool_output_for_model(name: str, output: dict[str, Any]) -> dict[str, Any]:
    if name == "get_coach_brief":
        return output
    if name == "get_battle_context":
        turns = [
            turn for turn in (output.get("turns") or [])
            if isinstance(turn, dict)
        ]
        ranked = sorted(
            turns,
            key=lambda turn: (
                -coach_turn_score(turn),
                turn.get("turn") if isinstance(turn.get("turn"), int) else 10_000,
                1 if turn.get("forceSwitch") else 0,
            ),
        )
        return {
            "purpose": output.get("purpose"),
            "battle": output.get("battle"),
            "teamComposition": output.get("teamComposition"),
            "dataCoverage": output.get("dataCoverage"),
            "aggregateSignals": output.get("aggregateSignals"),
            "decisionReviewQueue": (output.get("decisionReviewQueue") or [])[:10],
            "aggregateReviewCategories": output.get("aggregateReviewCategories"),
            "highSignalTurns": [compact_turn_for_model(turn) for turn in ranked[:10]],
            "agentUsageNotes": output.get("agentUsageNotes"),
        }
    if name == "get_archive_context":
        return {
            "purpose": output.get("purpose"),
            "summary": output.get("summary"),
            "fieldPressure": output.get("fieldPressure"),
            "teamProfiles": (output.get("teamProfiles") or [])[:6],
            "patternPanels": [
                {
                    "id": panel.get("id"),
                    "title": panel.get("title"),
                    "lens": panel.get("lens"),
                    "instances": panel.get("instances"),
                    "affectedBattles": panel.get("affectedBattles"),
                    "level": panel.get("level"),
                    "summary": panel.get("summary"),
                    "reviewAction": panel.get("reviewAction"),
                    "reviewLabelSummary": panel.get("reviewLabelSummary"),
                    "evidence": (panel.get("evidence") or [])[:4],
                }
                for panel in (output.get("patternPanels") or [])[:5]
                if isinstance(panel, dict)
            ],
            "reviewLabels": output.get("reviewLabels"),
            "topTeamSpecies": output.get("topTeamSpecies"),
            "topRecommendations": output.get("topRecommendations"),
            "topDisagreements": output.get("topDisagreements"),
            "recentBattles": (output.get("recentBattles") or [])[:8],
            "agentUsageNotes": output.get("agentUsageNotes"),
        }
    if name == "get_team_coach_brief":
        buckets = output.get("evidenceBuckets") or {}
        uncertainty = buckets.get("engineUncertainty") or {}
        return {
            "purpose": output.get("purpose"),
            "team": output.get("team"),
            "summary": output.get("summary"),
            "pokemonProfiles": (output.get("pokemonProfiles") or [])[:6],
            "evidenceBuckets": {
                "robustIgnoredAdvice": buckets.get("robustIgnoredAdvice"),
                "engineUncertainty": {
                    "pimcSplits": uncertainty.get("pimcSplits"),
                    "pvMisses": uncertainty.get("pvMisses"),
                },
                "noStableLines": buckets.get("noStableLines"),
                "fieldPressure": buckets.get("fieldPressure"),
            },
            "reviewPriorities": (output.get("reviewPriorities") or [])[:8],
            "agentUsageNotes": output.get("agentUsageNotes"),
        }
    if name in {
        "get_team_overview",
        "get_team_bucket_examples",
        "get_pokemon_profile",
        "get_pokemon_battle_timeline",
        "get_team_state_at_turn",
        "get_battle_window",
        "get_engine_eval_cases",
    }:
        return output
    return output


def compact_pattern_tool_output_for_model(name: str, output: dict[str, Any]) -> dict[str, Any]:
    if name == "get_pattern_context":
        return compact_pattern_context_for_model(output)
    if name == "get_archive_context":
        return compact_tool_output_for_model(name, output)
    return output


def tool_output_summary(name: str, output: dict[str, Any]) -> str:
    if name == "get_coach_brief":
        return (
            f"{len(output.get('turningPoints') or [])} turning points, "
            f"{len(output.get('reviewQueue') or [])} review cards, "
            f"{len(output.get('practiceFocus') or [])} practice items"
        )
    if name == "get_battle_context":
        coverage = output.get("dataCoverage") or {}
        signal_count = coverage.get("strategicSignals") or 0
        turns = coverage.get("postmortemTurns") or len(output.get("turns") or [])
        cards = len(output.get("decisionReviewQueue") or [])
        return f"{turns} turn rows, {signal_count} strategic signals, {cards} review cards"
    if name == "get_archive_context":
        summary = output.get("summary") or {}
        return (
            f"{summary.get('finishedBattles') or 0} battles, "
            f"{summary.get('winRate') if summary.get('winRate') is not None else 'n/a'}% win rate"
        )
    if name == "get_team_coach_brief":
        team = output.get("team") or {}
        summary = output.get("summary") or {}
        buckets = output.get("evidenceBuckets") or {}
        uncertainty = buckets.get("engineUncertainty") or {}
        return (
            f"{team.get('key') or 'team'}, {summary.get('battles') or 0} battles, "
            f"{(buckets.get('robustIgnoredAdvice') or {}).get('count') or 0} robust ignored, "
            f"{(uncertainty.get('pimcSplits') or {}).get('count') or 0} PIMC splits"
        )
    if name == "get_team_overview":
        team = output.get("team") or {}
        summary = output.get("summary") or {}
        buckets = output.get("bucketCounts") or {}
        return (
            f"{team.get('key') or 'team'}, {summary.get('battles') or 0} battles, "
            f"{buckets.get('robustIgnoredAdvice') or 0} robust ignored, "
            f"{buckets.get('pimcSplits') or 0} PIMC splits"
        )
    if name == "get_team_bucket_examples":
        return (
            f"{output.get('bucket') or 'bucket'}, "
            f"{len(output.get('examples') or [])} examples of {output.get('count') or 0}"
        )
    if name == "get_pokemon_profile":
        profile = output.get("profile") or {}
        return (
            f"{profile.get('species') or output.get('species') or 'Pokemon'}, "
            f"lead {profile.get('leadRate', 'n/a')}%, survival {profile.get('survivalRate', 'n/a')}%"
        )
    if name == "get_pokemon_battle_timeline":
        return (
            f"{output.get('species') or 'Pokemon'}, "
            f"{len(output.get('turns') or [])} relevant turns in {output.get('targetBattleId') or output.get('battleId')}"
        )
    if name == "get_team_state_at_turn":
        return (
            f"{output.get('targetBattleId') or output.get('battleId') or 'battle'} T{output.get('turn')}, "
            f"{len(output.get('rows') or [])} row(s), active {((output.get('fieldState') or {}).get('mine') or {}).get('activeSpecies') or 'n/a'}"
        )
    if name == "get_battle_window":
        return (
            f"{output.get('battleId') or 'battle'}, "
            f"{len(output.get('turns') or [])} turns around T{output.get('turn')}"
        )
    if name == "get_engine_eval_cases":
        return (
            f"{output.get('kind') or 'engine cases'}, "
            f"{len(output.get('cases') or [])} cases of {output.get('count') or 0}"
        )
    return "tool output captured"


def pattern_tool_output_summary(name: str, output: dict[str, Any]) -> str:
    if name == "get_pattern_context":
        pattern = output.get("pattern") or {}
        breakdown = output.get("evidenceBreakdown") or {}
        return (
            f"{pattern.get('title') or pattern.get('id') or 'pattern'}, "
            f"{pattern.get('instances') or 0} cards across {pattern.get('affectedBattles') or 0} battles, "
            f"{len((breakdown.get('affectedBattleIds') or []))} battle ids inspected"
        )
    return tool_output_summary(name, output)


def fake_tool_plan(preset: dict[str, Any], battle_id: str) -> list[dict[str, Any]]:
    plan = [{"name": "get_coach_brief", "args": {"battleId": battle_id}}]
    if preset.get("toolDepth") in {"battle", "archive"}:
        plan.append({"name": "get_battle_context", "args": {"battleId": battle_id}})
    if preset.get("toolDepth") == "archive":
        plan.append({"name": "get_archive_context", "args": {}})
        plan.append({"name": "get_team_coach_brief", "args": {"battleId": battle_id}})
    return plan


def fake_pattern_tool_plan(preset: dict[str, Any], pattern_id: str) -> list[dict[str, Any]]:
    plan = [{"name": "get_pattern_context", "args": {"patternId": pattern_id}}]
    if preset.get("toolDepth") == "archive":
        plan.append({"name": "get_archive_context", "args": {}})
    return plan


def fake_team_tool_plan(preset: dict[str, Any], battle_id: str) -> list[dict[str, Any]]:
    plan = [{"name": "get_team_overview", "args": {"battleId": battle_id}}]
    if preset.get("toolDepth") in {"battle", "archive"}:
        plan.append({
            "name": "get_team_bucket_examples",
            "args": {"battleId": battle_id, "bucket": "pimcSplits", "limit": 4},
        })
        plan.append({
            "name": "get_battle_window",
            "args": {"battleId": battle_id, "turn": 1, "before": 1, "after": 2},
        })
    if preset.get("toolDepth") == "archive":
        plan.append({
            "name": "get_pokemon_profile",
            "args": {"battleId": battle_id, "species": ""},
        })
        plan.append({
            "name": "get_pokemon_battle_timeline",
            "args": {"battleId": battle_id, "species": "", "targetBattleId": "", "limit": 6},
        })
        plan.append({
            "name": "get_team_state_at_turn",
            "args": {"battleId": battle_id, "targetBattleId": "", "turn": 1},
        })
        plan.append({
            "name": "get_engine_eval_cases",
            "args": {"battleId": battle_id, "kind": "pimc_splits", "limit": 4},
        })
    return plan


def _review_turn_label(item: dict[str, Any]) -> str:
    return f"T{item.get('turn')}{' FS' if item.get('forceSwitch') else ''}"


def _card_action_line(item: dict[str, Any]) -> str:
    engine = item.get("engineAction") or "engine recommendation unavailable"
    actual = item.get("actualAction") or "actual action unavailable"
    opponent = item.get("opponent") if isinstance(item.get("opponent"), dict) else {}
    opp_note = ""
    predicted = opponent.get("predicted")
    actual_opp = opponent.get("actual")
    if predicted or actual_opp:
        opp_note = f"; opponent expected {predicted or 'n/a'}, actual {actual_opp or 'n/a'}"
    return f"{_review_turn_label(item)}: engine wanted {engine}; player clicked {actual}{opp_note}."


def _key_review_cards(review_queue: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    priority = {
        "high_confidence_disagreement": 0,
        "switch_timing": 1,
        "action_prevented": 2,
        "medium_confidence_disagreement": 3,
        "low_confidence_outcome_review": 4,
    }
    cards = [item for item in review_queue if isinstance(item, dict)]
    cards.sort(key=lambda item: (
        priority.get(str(item.get("category") or ""), 8),
        -(int(item.get("priority") or 0)),
        item.get("turn") if isinstance(item.get("turn"), int) else 10_000,
    ))
    return cards[:limit]


def _opponent_answer_chart(
    battle_context: dict[str, Any] | None,
    team_context: dict[str, Any] | None,
) -> list[str]:
    opponent = []
    if battle_context:
        composition = battle_context.get("teamComposition") or {}
        opponent = [
            str(item)
            for item in (composition.get("opponent") or [])
            if item
        ]
    roster: list[str] = []
    if team_context:
        team = team_context.get("team") or {}
        roster = [
            str(item)
            for item in (team.get("roster") or [])
            if item
        ]
    if not opponent:
        return [
            "- Opponent roster was not available in the loaded tool context; use the turn evidence above first."
        ]

    by_species: dict[str, dict[str, Any]] = {}
    for turn in (battle_context or {}).get("turns") or []:
        if not isinstance(turn, dict):
            continue
        field = turn.get("fieldStateBeforeDecision") or {}
        opp_active = (((field.get("opp") or {}).get("active") or {}).get("species") or "").strip()
        mine_active = (((field.get("mine") or {}).get("active") or {}).get("species") or "").strip()
        if not opp_active:
            continue
        key = "".join(ch for ch in opp_active.lower() if ch.isalnum())
        bucket = by_species.setdefault(key, {
            "species": opp_active,
            "mineSeen": [],
            "examples": [],
        })
        if mine_active and mine_active not in bucket["mineSeen"]:
            bucket["mineSeen"].append(mine_active)
        if len(bucket["examples"]) < 2:
            bucket["examples"].append(
                f"T{turn.get('turn')}: engine {turn.get('pickLabel') or 'n/a'}, player {turn.get('actualLabel') or 'n/a'}"
            )

    lines: list[str] = []
    fallback_answer = ", ".join(roster[:2]) if roster else "a preserved matchup piece"
    for species in opponent[:6]:
        key = "".join(ch for ch in str(species).lower() if ch.isalnum())
        bucket = by_species.get(key)
        if bucket:
            seen = ", ".join(bucket["mineSeen"][:3]) or fallback_answer
            examples = "; ".join(bucket["examples"])
            lines.append(
                f"- **{species}:** reviewed mainly through {seen}. Key evidence: {examples}. Treat the exact answer as tentative until damage/meta tools are connected."
            )
        else:
            lines.append(
                f"- **{species}:** no active-turn matchup evidence found in the compact context; review preservation of {fallback_answer} if this Pokemon mattered."
            )
    return lines


def fake_agent_answer(
    preset: dict[str, Any],
    brief: dict[str, Any],
    battle_context: dict[str, Any] | None,
    archive_context: dict[str, Any] | None,
    team_context: dict[str, Any] | None = None,
) -> str:
    battle = brief.get("battle") or {}
    diagnosis = brief.get("diagnosis") or []
    turning_points = brief.get("turningPoints") or []
    review_queue = brief.get("reviewQueue") or []
    focus = brief.get("practiceFocus") or []
    opener = (
        f"{preset['label']} fake run: {str(battle.get('result') or 'unknown').upper()} "
        f"vs {battle.get('opponent') or 'opponent'}."
    )
    lines = [opener]
    if diagnosis:
        lines.append("### Where the battle slipped\n" + str(diagnosis[0].get("detail") or "Review the highest-priority turns first."))
    elif turning_points:
        top = turning_points[0]
        lines.append(
            "### Where the battle slipped\n"
            f"{_review_turn_label(top)} was the first loaded turning point: {top.get('title') or 'review this turn'}."
        )

    click_cards = _key_review_cards(review_queue)
    if click_cards:
        click_lines = []
        for item in click_cards[:4]:
            note = str(item.get("verdict") or item.get("reviewQuestion") or "").strip()
            click_lines.append(f"- {_card_action_line(item)} {note}".strip())
        lines.append("### What I should have clicked\n" + "\n".join(click_lines))
    elif turning_points:
        turn_lines = [
            f"- {_review_turn_label(item)}: {item.get('title') or 'review turn'} - {item.get('verdict') or item.get('recommendation') or 'compare engine line to actual line'}"
            for item in turning_points[:4]
        ]
        lines.append("### What I should have clicked\n" + "\n".join(turn_lines))

    lines.append(
        "### Opponent answer chart\n"
        + "\n".join(_opponent_answer_chart(battle_context, team_context))
    )

    if battle_context:
        coverage = battle_context.get("dataCoverage") or {}
        lines.append(
            "### Evidence coverage\nBattle-context tool inspected "
            f"{coverage.get('strategicSignals') or 0} strategic signals across "
            f"{coverage.get('postmortemTurns') or 0} postmortem rows."
        )
    if archive_context:
        summary = archive_context.get("summary") or {}
        lines.append(
            "Archive-context tool compared this battle against "
            f"{summary.get('finishedBattles') or 0} finished battles."
        )
    if team_context:
        team = team_context.get("team") or {}
        summary = team_context.get("summary") or {}
        buckets = team_context.get("evidenceBuckets") or {}
        robust = buckets.get("robustIgnoredAdvice") or {}
        lines.append(
            "Team-coach tool reviewed "
            f"{summary.get('battles') or 0} games for {team.get('key') or 'this team'} "
            f"and found {robust.get('count') or 0} robust ignored-advice cases."
        )

    if focus:
        lines.append(f"### Next-time game plan\n{focus[0].get('title')} - {focus[0].get('action')}")
    else:
        lines.append("### Next-time game plan\nReplay the listed turns and compare the engine-backed line against what actually happened.")
    lines.append(
        "### Engine/model uncertainty\nThis is a simulated provider response; inspect the tool trace and evidence coverage before trusting any matchup claim."
    )
    return "\n\n".join(lines)


def fake_pattern_agent_answer(
    preset: dict[str, Any],
    pattern_context: dict[str, Any],
    archive_context: dict[str, Any] | None,
) -> str:
    pattern = pattern_context.get("pattern") or {}
    breakdown = pattern_context.get("evidenceBreakdown") or {}
    evidence = pattern_context.get("evidence") or []
    lines = [
        (
            f"{preset['label']} fake pattern run: {pattern.get('title') or pattern.get('id')} "
            f"is {((pattern.get('level') or {}).get('label') or 'unclassified').lower()} "
            f"with {pattern.get('instances') or 0} review cards across "
            f"{pattern.get('affectedBattles') or 0} battles."
        )
    ]
    if breakdown.get("byCategory"):
        top_categories = ", ".join(
            f"{name}: {count}"
            for name, count in list((breakdown.get("byCategory") or {}).items())[:3]
        )
        lines.append(f"Top deterministic categories: {top_categories}.")
    if evidence:
        turn_bits = []
        for item in evidence[:4]:
            turn_bits.append(
                f"T{item.get('turn')}{' FS' if item.get('forceSwitch') else ''} vs {item.get('opponent') or 'unknown'}: {item.get('title')}"
            )
        lines.append("Evidence to inspect: " + "; ".join(turn_bits) + ".")
    lines.append(f"Practice focus: {pattern.get('reviewAction') or 'Review the highest-evidence turns first.'}")
    if archive_context:
        summary = archive_context.get("summary") or {}
        lines.append(
            "Archive context compared this pattern against "
            f"{summary.get('finishedBattles') or 0} finished battles."
        )
    lines.append(
        "This is a simulated provider response; the useful part to inspect now is the pattern tool trace and evidence coverage."
    )
    return "\n\n".join(lines)


def _team_bucket_count(team_context: dict[str, Any], path: list[str]) -> int:
    bucket_counts = team_context.get("bucketCounts") if isinstance(team_context, dict) else None
    if isinstance(bucket_counts, dict):
        legacy_path_map = {
            "evidenceBuckets.robustIgnoredAdvice": "robustIgnoredAdvice",
            "evidenceBuckets.engineUncertainty.pimcSplits": "pimcSplits",
            "evidenceBuckets.engineUncertainty.pvMisses": "pvMisses",
            "evidenceBuckets.noStableLines": "noStableLines",
            "evidenceBuckets.fieldPressure": "fieldPressure",
            "bucketCounts.robustIgnoredAdvice": "robustIgnoredAdvice",
            "bucketCounts.pimcSplits": "pimcSplits",
            "bucketCounts.pvMisses": "pvMisses",
            "bucketCounts.noStableLines": "noStableLines",
            "bucketCounts.fieldPressure": "fieldPressure",
        }
        count_key = legacy_path_map.get(".".join(path))
        if count_key:
            try:
                return int(bucket_counts.get(count_key) or 0)
            except (TypeError, ValueError):
                return 0

    node: Any = team_context
    for key in path:
        if not isinstance(node, dict):
            return 0
        node = node.get(key)
    if isinstance(node, dict):
        return int(node.get("count") or 0)
    return 0


def fake_team_agent_answer(
    preset: dict[str, Any],
    team_context: dict[str, Any],
    archive_context: dict[str, Any] | None,
    battle_context: dict[str, Any] | None,
) -> str:
    team = team_context.get("team") or {}
    summary = team_context.get("summary") or {}
    pokemon = team_context.get("pokemonProfiles") or []
    lines = [
        (
            f"{preset['label']} fake team run: {team.get('key') or 'selected team'} "
            f"over {summary.get('battles') or 0} tracked battles."
        )
    ]
    lines.append(
        "Team performance read: "
        f"{summary.get('wins') or 0}W / {summary.get('losses') or 0}L, "
        f"{summary.get('winRate') if summary.get('winRate') is not None else 'n/a'}% win rate, "
        f"{summary.get('followRate') if summary.get('followRate') is not None else 'n/a'}% follow rate."
    )
    lines.append(
        "What is losing games: "
        f"{_team_bucket_count(team_context, ['evidenceBuckets', 'fieldPressure'])} field-pressure cases, "
        f"{_team_bucket_count(team_context, ['evidenceBuckets', 'noStableLines'])} no-stable-line cases."
    )
    lines.append(
        "Engine uncertainty vs player-choice issues: "
        f"{_team_bucket_count(team_context, ['evidenceBuckets', 'engineUncertainty', 'pimcSplits'])} PIMC splits, "
        f"{_team_bucket_count(team_context, ['evidenceBuckets', 'engineUncertainty', 'pvMisses'])} PV misses, "
        f"{_team_bucket_count(team_context, ['evidenceBuckets', 'robustIgnoredAdvice'])} clean player-calibration cases."
    )
    if pokemon:
        notes = []
        for mon in pokemon[:3]:
            notes.append(
                f"{mon.get('species')}: lead {mon.get('leadRate', 'n/a')}%, "
                f"survival {mon.get('survivalRate', 'n/a')}%, KO share {mon.get('koShare', 'n/a')}%"
            )
        lines.append("Pokemon-specific notes: " + "; ".join(notes) + ".")
    if archive_context:
        if archive_context.get("purpose") == "team_engine_eval_cases":
            lines.append(
                "Engine-eval context: inspected "
                f"{len(archive_context.get('cases') or [])} compact cases out of "
                f"{archive_context.get('count') or 0} matching candidates."
            )
        else:
            archive_summary = archive_context.get("summary") or {}
            lines.append(
                "Archive context: compared against "
                f"{archive_summary.get('finishedBattles') or 0} finished local battles."
            )
    if battle_context:
        if battle_context.get("purpose") == "team_battle_window":
            lines.append(
                "Battle-window context: inspected "
                f"{len(battle_context.get('turns') or [])} turns around "
                f"T{battle_context.get('turn') or 'n/a'} in {battle_context.get('battleId') or 'a team battle'}."
            )
        else:
            coverage = battle_context.get("dataCoverage") or {}
            lines.append(
                "Anchor battle context: inspected "
                f"{coverage.get('postmortemTurns') or 0} postmortem rows and "
                f"{coverage.get('strategicSignals') or 0} strategic signals."
            )
    lines.append(
        "This is a simulated provider response; inspect the tool trace to confirm the team coach used team context first."
    )
    return "\n\n".join(lines)


def deterministic_pattern_agent_answer(
    preset: dict[str, Any],
    pattern_context: dict[str, Any],
    archive_context: dict[str, Any] | None,
) -> str:
    pattern = pattern_context.get("pattern") or {}
    breakdown = pattern_context.get("evidenceBreakdown") or {}
    evidence = pattern_context.get("evidence") or []
    lines = [
        (
            "## Deterministic pattern fallback\n\n"
            f"**{pattern.get('title') or pattern.get('id')}** is "
            f"{((pattern.get('level') or {}).get('label') or 'unclassified').lower()} "
            f"with {pattern.get('instances') or 0} review cards across "
            f"{pattern.get('affectedBattles') or 0} battles using {preset.get('label') or 'the selected preset'}."
        )
    ]
    lines.append(f"### Pattern read\n{pattern.get('summary') or pattern.get('description') or 'No summary available.'}")
    if breakdown.get("byCategory"):
        category_lines = [
            f"- **{name}:** {count}"
            for name, count in (breakdown.get("byCategory") or {}).items()
        ]
        lines.append("### Breakdown\n" + "\n".join(category_lines[:6]))
    if evidence:
        evidence_lines = [
            f"- **T{item.get('turn')}{' FS' if item.get('forceSwitch') else ''} vs {item.get('opponent') or 'unknown'}:** {item.get('title')} - {item.get('verdict')}"
            for item in evidence[:6]
        ]
        lines.append("### Evidence to review\n" + "\n".join(evidence_lines))
    lines.append(f"### Practice drill\n{pattern.get('reviewAction') or 'Review the highest-evidence turns first.'}")
    if archive_context:
        summary = archive_context.get("summary") or {}
        lines.append(
            "### Data scope\n"
            f"Compared against {summary.get('finishedBattles') or 0} finished local battles."
        )
    lines.append(
        "### Model uncertainty\n"
        "The provider did not return visible final text, so this answer was rendered from the same deterministic pattern context."
    )
    return "\n\n".join(lines)


def deterministic_agent_answer(
    preset: dict[str, Any],
    brief: dict[str, Any],
    battle_context: dict[str, Any] | None,
    archive_context: dict[str, Any] | None,
    team_context: dict[str, Any] | None = None,
) -> str:
    battle = brief.get("battle") or {}
    diagnosis = brief.get("diagnosis") or []
    turning_points = brief.get("turningPoints") or []
    review_queue = brief.get("reviewQueue") or []
    focus = brief.get("practiceFocus") or []
    lines = [
        (
            "## Deterministic coach fallback\n\n"
            f"{str(battle.get('result') or 'unknown').upper()} vs {battle.get('opponent') or 'opponent'} "
            f"using {preset.get('label') or 'the selected preset'}."
        )
    ]
    if diagnosis:
        lines.append(f"### Where the battle slipped\n{diagnosis[0].get('detail')}")
    if review_queue:
        review_lines = []
        for item in _key_review_cards(review_queue, limit=5):
            review_lines.append(
                f"- **{_review_turn_label(item)}:** {_card_action_line(item)} {item.get('verdict') or item.get('reviewQuestion') or ''}".strip()
            )
        lines.append("### What I should have clicked\n" + "\n".join(review_lines))
    if turning_points:
        turn_lines = []
        for item in turning_points[:5]:
            turn_lines.append(
                f"- **{_review_turn_label(item)}:** {item.get('title')} - {item.get('verdict') or item.get('recommendation')}"
            )
        lines.append("### Where to replay\n" + "\n".join(turn_lines))
    lines.append(
        "### Opponent answer chart\n"
        + "\n".join(_opponent_answer_chart(battle_context, team_context))
    )
    if focus:
        focus_lines = [
            f"- **{item.get('title')}:** {item.get('action')}"
            for item in focus[:3]
        ]
        lines.append("### Next-time game plan\n" + "\n".join(focus_lines))
    if battle_context:
        coverage = battle_context.get("dataCoverage") or {}
        lines.append(
            "### Evidence coverage\n"
            f"Battle context inspected {coverage.get('postmortemTurns') or 0} turn rows and "
            f"{coverage.get('strategicSignals') or 0} strategic signals."
        )
    if archive_context:
        summary = archive_context.get("summary") or {}
        lines.append(
            "### Archive context\n"
            f"Compared against {summary.get('finishedBattles') or 0} finished local battles."
        )
    if team_context:
        team = team_context.get("team") or {}
        summary = team_context.get("summary") or {}
        buckets = team_context.get("evidenceBuckets") or {}
        robust = buckets.get("robustIgnoredAdvice") or {}
        uncertainty = (buckets.get("engineUncertainty") or {}).get("pimcSplits") or {}
        lines.append(
            "### Team context\n"
            f"{team.get('key') or 'Selected team'} has {summary.get('battles') or 0} tracked battles, "
            f"{robust.get('count') or 0} robust ignored-advice cases, and "
            f"{uncertainty.get('count') or 0} PIMC hidden-info split cases."
        )
    lines.append(
        "### Model uncertainty\n"
        "The provider did not return visible final text for this run, so the dashboard rendered this "
        "deterministic fallback from the same local coach tools instead of failing the panel."
    )
    return "\n\n".join(lines)


def deterministic_team_agent_answer(
    preset: dict[str, Any],
    team_context: dict[str, Any],
    archive_context: dict[str, Any] | None,
    battle_context: dict[str, Any] | None,
) -> str:
    team = team_context.get("team") or {}
    summary = team_context.get("summary") or {}
    pokemon = team_context.get("pokemonProfiles") or []
    lines = [
        (
            "## Deterministic team-coach fallback\n\n"
            f"**{team.get('key') or 'Selected team'}** using {preset.get('label') or 'the selected preset'}."
        )
    ]
    lines.append(
        "### Team performance read\n"
        f"{summary.get('wins') or 0}W / {summary.get('losses') or 0}L across "
        f"{summary.get('battles') or 0} tracked battles. Win rate: "
        f"{summary.get('winRate') if summary.get('winRate') is not None else 'n/a'}%. "
        f"Follow rate: {summary.get('followRate') if summary.get('followRate') is not None else 'n/a'}%."
    )
    lines.append(
        "### What is losing games\n"
        f"Field-pressure cases: {_team_bucket_count(team_context, ['evidenceBuckets', 'fieldPressure'])}. "
        f"No-stable-line cases: {_team_bucket_count(team_context, ['evidenceBuckets', 'noStableLines'])}."
    )
    lines.append(
        "### Engine uncertainty vs player-choice issues\n"
        f"PIMC splits: {_team_bucket_count(team_context, ['evidenceBuckets', 'engineUncertainty', 'pimcSplits'])}. "
        f"PV misses: {_team_bucket_count(team_context, ['evidenceBuckets', 'engineUncertainty', 'pvMisses'])}. "
        f"Clean player-calibration cases: {_team_bucket_count(team_context, ['evidenceBuckets', 'robustIgnoredAdvice'])}."
    )
    if pokemon:
        mon_lines = [
            f"- **{mon.get('species')}:** lead {mon.get('leadRate', 'n/a')}%, survival {mon.get('survivalRate', 'n/a')}%, KO share {mon.get('koShare', 'n/a')}%."
            for mon in pokemon[:6]
        ]
        lines.append("### Pokemon-specific notes\n" + "\n".join(mon_lines))
    lines.append(
        "### Practice focus\n"
        "Review no-stable-line and field-pressure examples backward from the collapse turn, then separate PIMC/PV uncertainty from clean player-choice calibration."
    )
    lines.append(
        "### Team-building suggestions\n"
        "Treat any set or roster changes as suggestions until meta, simulator, or damage-check tools are connected."
    )
    if archive_context or battle_context:
        lines.append(
            "### Evidence coverage\n"
            f"Archive context: {'yes' if archive_context else 'no'}. Anchor battle context: {'yes' if battle_context else 'no'}."
        )
    return "\n\n".join(lines)


def pattern_agent_metrics(
    pattern_context: dict[str, Any],
    tool_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    pattern = pattern_context.get("pattern") or {}
    evidence = pattern_context.get("evidence") or []
    called_tools = {str(call.get("name")) for call in tool_calls}
    return {
        "requiredToolsCalled": "get_pattern_context" in called_tools,
        "toolCallCount": len(tool_calls),
        "turnCitations": min(len(evidence), 8),
        "reviewCards": pattern.get("instances") or len(evidence),
        "highSeverityReviewCards": sum(
            1 for item in evidence
            if isinstance(item, dict) and item.get("severity") == "high"
        ),
        "hasModelUncertaintySeparation": pattern.get("lens") == "Engine eval" or any(
            isinstance(item, dict) and item.get("category") == "engine_uncertainty"
            for item in evidence
        ),
        "hallucinationGuard": "deterministic pattern run over local pattern context",
    }


def coach_agent_metrics(
    brief: dict[str, Any],
    tool_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    turning_points = brief.get("turningPoints") or []
    review_queue = brief.get("reviewQueue") or []
    diagnosis_titles = {
        str(item.get("title") or "")
        for item in (brief.get("diagnosis") or [])
        if isinstance(item, dict)
    }
    required_tools = {"get_coach_brief"}
    called_tools = {str(call.get("name")) for call in tool_calls}
    return {
        "requiredToolsCalled": required_tools.issubset(called_tools),
        "toolCallCount": len(tool_calls),
        "turnCitations": len(turning_points),
        "reviewCards": len(review_queue),
        "highSeverityReviewCards": sum(
            1 for card in review_queue
            if isinstance(card, dict) and card.get("severity") == "high"
        ),
        "hasModelUncertaintySeparation": "Model uncertainty" in diagnosis_titles,
        "hallucinationGuard": "deterministic fake run over local tool output",
    }


def team_agent_metrics(
    team_context: dict[str, Any],
    tool_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    called_tools = [str(call.get("name")) for call in tool_calls]
    return {
        "requiredToolsCalled": bool(called_tools) and called_tools[0] == "get_team_overview",
        "toolCallCount": len(tool_calls),
        "teamContextFirst": bool(called_tools) and called_tools[0] == "get_team_overview",
        "pokemonProfiles": len(team_context.get("pokemonProfiles") or []),
        "reviewPriorities": len(team_context.get("reviewPriorities") or []),
        "hasModelUncertaintySeparation": (
            _team_bucket_count(team_context, ["evidenceBuckets", "engineUncertainty", "pimcSplits"]) > 0
            or _team_bucket_count(team_context, ["evidenceBuckets", "engineUncertainty", "pvMisses"]) > 0
        ),
        "hallucinationGuard": "team-first deterministic run over local team coach context",
    }


def normalize_run_mode(run_mode: str | None) -> str:
    mode = str(run_mode or "fake").lower()
    if mode not in {"fake", "auto", "real"}:
        raise HTTPException(status_code=400, detail=f"unknown coach run mode: {run_mode}")
    return mode


def should_run_real_provider(preset: dict[str, Any], run_mode: str) -> bool:
    mode = normalize_run_mode(run_mode)
    if mode == "fake":
        return False
    if preset.get("provider") != "openai":
        if mode == "real":
            raise HTTPException(
                status_code=501,
                detail=f"real provider not wired yet: {preset.get('provider')}",
            )
        return False
    has_key = bool(os.environ.get("OPENAI_API_KEY"))
    if mode == "real" and not has_key:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY is not set; choose fake mode or export the key before running real OpenAI.",
        )
    return has_key


def merge_review_label_suggestions(
    primary: list[dict[str, Any]],
    fallback: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen = {str(item.get("reviewKey") or "") for item in primary}
    return primary + [
        item for item in fallback
        if str(item.get("reviewKey") or "") not in seen
    ]
