import json
import os
from pathlib import Path

import pytest

from showdown_copilot.dashboard import (
    _battle_detail,
    _coach_agent_run_async,
    _coach_agent_run,
    _coach_final_answer_prompt,
    _coach_preset,
    _coach_brief,
    _coach_model_presets,
    _coach_synthesis_prompt,
    _compact_pattern_context_for_labeler,
    _field_state_context,
    _looks_truncated_text,
    _normalize_coach_tool_args,
    _normalize_pattern_tool_args,
    _normalize_team_tool_args,
    _run_team_coach_tool,
    _parse_jsonish_model_output,
    _pattern_agent_context,
    _pattern_agent_run,
    _pattern_output_token_budget,
    _pattern_synthesis_prompt,
    _response_function_calls,
    _response_incomplete,
    _response_text,
    _save_review_label,
    _should_run_real_provider,
    _summarize_archive,
    _team_coach_agent_run,
    _team_coach_agent_run_async,
    _team_coach_brief,
    _turn_summary,
    _usage_from_responses,
    summarize_postmortem,
)
from showdown_copilot.review_workflow import (
    ReviewLabelRequest,
    build_engine_eval_cases,
    load_review_labels as _load_review_labels,
    normalize_ai_review_label_suggestions,
    persist_review_label_suggestions,
    suggest_review_labels_for_pattern,
)
from showdown_copilot.engine_eval_cases import (
    enrich_engine_eval_cases_with_replay,
    prioritize_engine_eval_cases,
    terminal_pimc_uncertainty,
)
from showdown_copilot.team_coach import build_team_coach_brief
from showdown_copilot.team_profiles import build_team_profiles


def test_summarize_postmortem_computes_dashboard_metrics() -> None:
    pm = {
        "battleId": "battle-test-1",
        "format": "[Gen 9] National Dex",
        "myUsername": "Mariga",
        "opponentUsername": "Rival",
        "winner": "Mariga",
        "endedAtMs": 1781891298095,
        "totalTurns": 2,
        "schemaVersion": 7,
        "replayUrl": "https://replay.pokemonshowdown.com/test",
        "teamPreview": {
            "mine": ["Garchomp", "Volcarona"],
            "opp": ["Clodsire", "Greninja"],
        },
        "turns": [
            {
                "turn": 1,
                "forceSwitch": False,
                "rqid": 1,
                "myPick": {
                    "kind": "move",
                    "name": "Earthquake",
                    "confidence": 0.75,
                },
                "actualMyAction": {"kind": "move", "name": "Earthquake"},
                "enginePredictedOpp": "Recover",
                "actualOppMove": "Recover",
                "pvMatchedReality": True,
                "faints": [],
            },
            {
                "turn": 2,
                "forceSwitch": False,
                "rqid": 2,
                "myPick": {
                    "kind": "switch",
                    "name": "Volcarona",
                    "confidence": 0.5,
                },
                "actualMyAction": {"kind": "move", "name": "Dragon Claw"},
                "enginePredictedOpp": "Toxic",
                "actualOppMove": "Stealth Rock",
                "pvMatchedReality": False,
                "hazardsAdded": [{"side": "mine", "name": "Stealth Rock"}],
                "residualEvents": [
                    {
                        "side": "mine",
                        "source": "Spikes",
                        "category": "hazard",
                        "hpPctLost": 12,
                        "targetSpecies": "Garchomp",
                    },
                    {
                        "side": "opp",
                        "source": "psn",
                        "category": "status",
                        "hpPctLost": 6,
                        "targetSpecies": "Clodsire",
                    },
                ],
                "faints": [{"side": "opp", "species": "Clodsire"}],
            },
            {
                "turn": 2,
                "forceSwitch": True,
                "rqid": 3,
                "myPick": {"kind": "switch", "name": "Garchomp"},
                "actualMyAction": {"kind": "switch", "name": "Garchomp"},
                "faintedBefore": {"species": "Volcarona", "cause": "Toxic"},
            },
        ],
    }

    summary = summarize_postmortem(Path("sample.json"), pm)

    assert summary["result"] == "win"
    assert summary["opponent"] == "Rival"
    assert summary["metrics"]["regularRows"] == 2
    assert summary["metrics"]["forceSwitchRows"] == 1
    assert summary["metrics"]["followed"] == 1
    assert summary["metrics"]["followable"] == 2
    assert summary["metrics"]["followRate"] == 50.0
    assert summary["metrics"]["switchRecommendations"] == 1
    assert summary["metrics"]["switchFollowRate"] == 0.0
    assert summary["metrics"]["pvHitRate"] == 50.0
    assert summary["metrics"]["criticalTurns"] == 2
    assert summary["metrics"]["avgConfidence"] == 62.5
    assert summary["metrics"]["hazardsAdded"] == 1
    assert summary["metrics"]["hazardResidualEvents"] == 1
    assert summary["metrics"]["statusResidualEvents"] == 1
    assert summary["dataIssues"] == []


def test_build_team_profiles_groups_rosters_and_rates() -> None:
    profiles = build_team_profiles([
        {
            "team": ["Volcarona", "Garchomp"],
            "result": "win",
            "metrics": {"followed": 3, "followable": 4},
        },
        {
            "team": ["Volcarona", "Garchomp"],
            "result": "loss",
            "metrics": {"followed": 1, "followable": 4},
        },
        {
            "team": ["Iron Valiant"],
            "result": "win",
            "metrics": {"followed": 0, "followable": 0},
        },
    ])

    assert {
        key: profiles[0][key]
        for key in ("team", "battles", "wins", "losses", "winRate", "followRate")
    } == {
        "team": ["Volcarona", "Garchomp"],
        "battles": 2,
        "wins": 1,
        "losses": 1,
        "winRate": 50.0,
        "followRate": 50.0,
    }
    assert profiles[0]["hasPerformance"] is False
    assert profiles[1]["team"] == ["Iron Valiant"]
    assert profiles[1]["followRate"] is None


def test_build_team_profiles_aggregates_v11_team_performance() -> None:
    profiles = build_team_profiles([
        {
            "team": ["Volcarona", "Garchomp"],
            "result": "win",
            "metrics": {"followed": 3, "followable": 4},
            "teamPerformance": {
                "mine": {
                    "lead": "Volcarona",
                    "pokemon": {
                        "Volcarona": {
                            "species": "Volcarona",
                            "led": True,
                            "survived": True,
                            "fainted": False,
                            "switchIns": 1,
                            "fieldPressure": {"totalHpLost": 6, "hazardHpLost": 6},
                            "koCredit": {"directKos": 1, "pressureKos": 1},
                            "kos": 1,
                        },
                        "Garchomp": {
                            "species": "Garchomp",
                            "led": False,
                            "survived": False,
                            "fainted": True,
                            "faintTurn": 18,
                            "fieldPressure": {"totalHpLost": 24, "hazardHpLost": 12, "statusHpLost": 12},
                            "koCredit": {"hazardKos": 1},
                        },
                    },
                }
            },
        },
        {
            "team": ["Volcarona", "Garchomp"],
            "result": "loss",
            "metrics": {"followed": 1, "followable": 4},
            "teamPerformance": {
                "mine": {
                    "lead": "Garchomp",
                    "pokemon": {
                        "Volcarona": {
                            "species": "Volcarona",
                            "led": False,
                            "survived": False,
                            "fainted": True,
                            "faintTurn": 10,
                            "fieldPressure": {"totalHpLost": 40, "hazardHpLost": 30, "statusHpLost": 10},
                            "koCredit": {},
                        },
                        "Garchomp": {
                            "species": "Garchomp",
                            "led": True,
                            "survived": True,
                            "fainted": False,
                            "fieldPressure": {"totalHpLost": 0},
                            "koCredit": {"directKos": 2},
                            "kos": 2,
                        },
                    },
                }
            },
        },
    ])

    profile = profiles[0]
    assert profile["performanceBattles"] == 2
    assert profile["topLead"] == {
        "species": "Volcarona",
        "count": 1,
        "rate": 50.0,
        "winRate": 100.0,
    }

    by_species = {mon["species"]: mon for mon in profile["pokemon"]}
    assert by_species["Volcarona"]["leadRate"] == 50.0
    assert by_species["Volcarona"]["survivalRate"] == 50.0
    assert by_species["Volcarona"]["winWhenAlive"] == 100.0
    assert by_species["Volcarona"]["avgFaintTurn"] == 10.0
    assert by_species["Volcarona"]["koCreditTotal"] == 2
    assert by_species["Volcarona"]["koShare"] == 40.0
    assert by_species["Volcarona"]["fieldPressureBucket"] == "medium"
    assert by_species["Garchomp"]["survivalRate"] == 50.0
    assert by_species["Garchomp"]["avgFaintTurn"] == 18.0
    assert by_species["Garchomp"]["koCreditTotal"] == 3
    assert by_species["Garchomp"]["fieldPressureBucket"] == "low"


def test_build_team_coach_brief_separates_player_uncertainty_and_no_stable_cases() -> None:
    pm = {
        "battleId": "battle-team-coach",
        "format": "[Gen 9] National Dex",
        "myUsername": "Mariga",
        "opponentUsername": "Rival",
        "winner": "Rival",
        "endedAtMs": 1781891298095,
        "schemaVersion": 11,
        "teamPreview": {
            "mine": ["Volcarona", "Garchomp"],
            "opp": ["Clodsire"],
        },
        "teamPerformance": {
            "mine": {
                "lead": "Volcarona",
                "pokemon": {
                    "Volcarona": {
                        "species": "Volcarona",
                        "led": True,
                        "survived": False,
                        "fainted": True,
                        "faintTurn": 4,
                        "fieldPressure": {"totalHpLost": 20},
                        "koCredit": {"directKos": 1},
                    },
                    "Garchomp": {
                        "species": "Garchomp",
                        "led": False,
                        "survived": True,
                        "fainted": False,
                        "fieldPressure": {"totalHpLost": 0},
                        "koCredit": {},
                    },
                },
            },
        },
        "turns": [
            {
                "turn": 1,
                "forceSwitch": False,
                "myPick": {
                    "kind": "move",
                    "name": "Quiver Dance",
                    "confidence": 0.82,
                    "pimcConsensus": {
                        "tier": "unanimous",
                        "uncertain": False,
                        "topMove": "QUIVERDANCE",
                        "topMoveShare": 1.0,
                    },
                },
                "actualMyAction": {"kind": "move", "name": "Fire Blast"},
                "enginePredictedOpp": "Recover",
                "actualOppMove": "Recover",
                "pvMatchedReality": True,
            },
            {
                "turn": 2,
                "forceSwitch": False,
                "myPick": {
                    "kind": "switch",
                    "name": "Garchomp",
                    "confidence": 0.72,
                    "pimcConsensus": {
                        "tier": "split",
                        "uncertain": True,
                        "topMove": "GARCHOMP",
                        "topMoveShare": 0.5,
                    },
                },
                "actualMyAction": {"kind": "move", "name": "Make It Rain"},
                "enginePredictedOpp": "Earthquake",
                "actualOppMove": "Hidden Power",
                "pvMatchedReality": False,
            },
            {
                "turn": 3,
                "forceSwitch": False,
                "myPick": {
                    "kind": "move",
                    "name": "Dragon Tail",
                    "confidence": 0.001,
                    "message": "No stable line found; treat this as damage control, not a normal recommendation.",
                    "pimcConsensus": {
                        "tier": "unanimous",
                        "uncertain": False,
                        "topMove": "DRAGONTAIL",
                        "topMoveShare": 1.0,
                    },
                },
                "actualMyAction": {
                    "kind": "prevented",
                    "name": None,
                    "reason": "fainted before action (Earthquake)",
                },
                "enginePredictedOpp": "Earthquake",
                "actualOppMove": "Earthquake",
                "pvMatchedReality": True,
                "faints": [{"side": "mine", "species": "Volcarona"}],
            },
            {
                "turn": 4,
                "forceSwitch": False,
                "myPick": {
                    "kind": "move",
                    "name": "Recover",
                    "confidence": 0.6,
                    "pimcConsensus": {
                        "tier": "strong",
                        "uncertain": False,
                        "topMove": "RECOVER",
                        "topMoveShare": 0.75,
                    },
                },
                "actualMyAction": {"kind": "move", "name": "Recover"},
                "enginePredictedOpp": "Toxic",
                "actualOppMove": "Toxic",
                "pvMatchedReality": True,
                "residualEvents": [
                    {
                        "side": "mine",
                        "source": "Stealth Rock",
                        "category": "hazard",
                        "hpPctLost": 12,
                        "targetSpecies": "Volcarona",
                    },
                ],
            },
        ],
    }
    battle = summarize_postmortem(Path("team-coach.json"), pm)
    archive = {
        "summary": {"finishedBattles": 1},
        "battles": [battle],
        "teamProfiles": build_team_profiles([battle]),
    }

    brief = build_team_coach_brief(archive, {"battle-team-coach": pm})
    buckets = brief["evidenceBuckets"]

    assert brief["purpose"] == "team_coach_brief"
    assert brief["team"]["roster"] == ["Volcarona", "Garchomp"]
    assert [mon["species"] for mon in brief["pokemonProfiles"]] == ["Volcarona", "Garchomp"]
    assert buckets["robustIgnoredAdvice"]["count"] == 1
    assert buckets["robustIgnoredAdvice"]["examples"][0]["turn"] == 1
    assert buckets["engineUncertainty"]["pimcSplits"]["count"] == 1
    assert buckets["engineUncertainty"]["pimcSplits"]["examples"][0]["turn"] == 2
    assert buckets["engineUncertainty"]["pvMisses"]["count"] == 1
    assert buckets["engineUncertainty"]["pvMisses"]["examples"][0]["turn"] == 2
    assert buckets["noStableLines"]["count"] == 1
    assert buckets["noStableLines"]["examples"][0]["turn"] == 3
    assert buckets["fieldPressure"]["count"] == 1
    assert buckets["fieldPressure"]["examples"][0]["turn"] == 4
    assert [case["turn"] for case in brief["reviewPriorities"]] == [1, 2, 3]


def test_turn_summary_promotes_field_events() -> None:
    row = {
        "turn": 4,
        "forceSwitch": False,
        "myPick": {"kind": "move", "name": "Recover"},
        "actualMyAction": {"kind": "move", "name": "Recover"},
        "hazardsRemoved": [{"side": "mine", "name": "Spikes"}],
        "residualEvents": [
            {
                "side": "mine",
                "source": "brn",
                "category": "status",
                "hpPctLost": 6,
                "targetSpecies": "Volcarona",
            }
        ],
    }

    summary = _turn_summary(row)

    assert [event["type"] for event in summary["fieldEvents"]] == [
        "hazard_removed",
        "residual",
    ]
    assert summary["fieldEventSummary"][0]["label"] == "Spikes"
    assert summary["fieldEventSummary"][1]["label"] == "status (6%)"
    assert "field pressure" in summary["issues"]


def test_turn_summary_groups_repeated_contact_events() -> None:
    row = {
        "turn": 1,
        "forceSwitch": False,
        "myPick": {"kind": "move", "name": "Stealth Rock"},
        "actualMyAction": {
            "kind": "prevented",
            "name": None,
            "reason": "fainted before action (Triple Axel)",
        },
        "residualEvents": [
            {
                "side": "opp",
                "source": "Rough Skin",
                "category": "contact",
                "hpPctLost": 12,
                "targetSpecies": "Meowscarada",
            },
            {
                "side": "opp",
                "source": "Rocky Helmet",
                "category": "contact",
                "hpPctLost": 16,
                "targetSpecies": "Meowscarada",
            },
            {
                "side": "opp",
                "source": "Rough Skin",
                "category": "contact",
                "hpPctLost": 12,
                "targetSpecies": "Meowscarada",
            },
            {
                "side": "opp",
                "source": "Rocky Helmet",
                "category": "contact",
                "hpPctLost": 17,
                "targetSpecies": "Meowscarada",
            },
            {
                "side": "opp",
                "source": "Rough Skin",
                "category": "contact",
                "hpPctLost": 12,
                "targetSpecies": "Meowscarada",
            },
            {
                "side": "opp",
                "source": "Rocky Helmet",
                "category": "contact",
                "hpPctLost": 17,
                "targetSpecies": "Meowscarada",
            },
        ],
    }

    summary = _turn_summary(row)

    assert len(summary["fieldEventSummary"]) == 1
    assert summary["fieldEventSummary"][0]["label"] == "contact x6 (86%)"
    assert summary["fieldEventSummary"][0]["count"] == 6
    assert "field pressure" in summary["issues"]


def test_prevented_action_is_not_a_data_gap() -> None:
    pm = {
        "battleId": "battle-prevented",
        "format": "[Gen 9] National Dex",
        "myUsername": "Mariga",
        "opponentUsername": "Rival",
        "winner": "Rival",
        "endedAtMs": 1781891298095,
        "schemaVersion": 8,
        "replayUrl": "https://replay.pokemonshowdown.com/test",
        "turns": [
            {
                "turn": 1,
                "forceSwitch": False,
                "myPick": {"kind": "move", "name": "Recover"},
                "actualMyAction": {
                    "kind": "prevented",
                    "name": None,
                    "reason": "flinch",
                },
            },
            {
                "turn": 2,
                "forceSwitch": False,
                "myPick": {"kind": "move", "name": "Recover"},
                "actualMyAction": {"kind": "move", "name": "Recover"},
            },
        ],
    }

    summary = summarize_postmortem(Path("prevented.json"), pm)
    turn = _turn_summary(pm["turns"][0])

    assert summary["metrics"]["missingActualActions"] == 0
    assert summary["dataIssues"] == []
    assert turn["actualLabel"] == "prevented: flinch"
    assert "action prevented" in turn["issues"]


def test_unknown_action_with_mine_faint_is_inferred_as_prevented() -> None:
    pm = {
        "battleId": "battle-fainted-before-action",
        "format": "[Gen 9] National Dex",
        "myUsername": "Mariga",
        "opponentUsername": "Rival",
        "winner": "Rival",
        "endedAtMs": 1781891298095,
        "schemaVersion": 8,
        "replayUrl": "https://replay.pokemonshowdown.com/test",
        "turns": [
            {
                "turn": 1,
                "forceSwitch": False,
                "myPick": {"kind": "move", "name": "Stealth Rock"},
                "actualMyAction": {"kind": "unknown", "name": None},
                "damageOppDealt": {"move": "Triple Axel"},
                "faints": [{"side": "mine", "species": "Garchomp"}],
            },
            {
                "turn": 1,
                "forceSwitch": True,
                "myPick": {"kind": "switch", "name": "Volcarona"},
                "actualMyAction": {"kind": "switch", "name": "Volcarona"},
            },
        ],
    }

    summary = summarize_postmortem(Path("faint.json"), pm)
    turn = _turn_summary(pm["turns"][0])

    assert summary["metrics"]["missingActualActions"] == 0
    assert summary["dataIssues"] == []
    assert turn["actualLabel"] == "prevented: fainted before action (Triple Axel)"
    assert "action prevented" in turn["issues"]


def test_summarize_archive_defaults_to_schema_v7(tmp_path: Path) -> None:
    base = {
        "battleId": "battle-v7",
        "winner": "Mariga",
        "myUsername": "Mariga",
        "endedAtMs": 1781891298095,
        "turns": [
            {
                "turn": 1,
                "forceSwitch": False,
                "myPick": {"kind": "move", "name": "Recover"},
                "actualMyAction": {"kind": "move", "name": "Recover"},
            },
            {
                "turn": 2,
                "forceSwitch": False,
                "myPick": {"kind": "move", "name": "Earthquake"},
                "actualMyAction": {"kind": "move", "name": "Earthquake"},
            }
        ],
    }
    old = {**base, "battleId": "battle-v6", "schemaVersion": 6}
    new = {**base, "schemaVersion": 7}
    (tmp_path / "old.json").write_text(json.dumps(old), encoding="utf-8")
    (tmp_path / "new.json").write_text(json.dumps(new), encoding="utf-8")

    summary = _summarize_archive(directory=tmp_path)

    assert summary["summary"]["finishedBattles"] == 1
    assert summary["summary"]["schemaSkippedFiles"] == 1
    assert summary["battles"][0]["battleId"] == "battle-v7"


def test_summarize_archive_builds_pattern_panels_from_review_cards(tmp_path: Path) -> None:
    battle_one = {
        "battleId": "battle-pattern-1",
        "winner": "Rival",
        "myUsername": "Mariga",
        "opponentUsername": "Rival",
        "endedAtMs": 1781891298095,
        "schemaVersion": 9,
        "replayUrl": "https://replay.pokemonshowdown.com/test-1",
        "turns": [
            {
                "turn": 1,
                "forceSwitch": False,
                "myPick": {"kind": "move", "name": "Swords Dance", "confidence": 0.82},
                "actualMyAction": {"kind": "move", "name": "Ivy Cudgel"},
                "enginePredictedOpp": "Toxic",
                "actualOppMove": "Stealth Rock",
                "pvMatchedReality": False,
                "hazardsAdded": [{"side": "mine", "name": "Stealth Rock"}],
            },
            {
                "turn": 2,
                "forceSwitch": False,
                "myPick": {"kind": "switch", "name": "Gholdengo", "confidence": 0.55},
                "actualMyAction": {"kind": "move", "name": "Knock Off"},
                "enginePredictedOpp": "Recover",
                "actualOppMove": "Recover",
                "pvMatchedReality": True,
            },
        ],
    }
    battle_two = {
        "battleId": "battle-pattern-2",
        "winner": "Rival",
        "myUsername": "Mariga",
        "opponentUsername": "Rival2",
        "endedAtMs": 1781891398095,
        "schemaVersion": 9,
        "replayUrl": "https://replay.pokemonshowdown.com/test-2",
        "turns": [
            {
                "turn": 1,
                "forceSwitch": False,
                "myPick": {"kind": "move", "name": "Stealth Rock", "confidence": 0.7},
                "actualMyAction": {
                    "kind": "prevented",
                    "name": None,
                    "reason": "fainted before action (Triple Axel)",
                },
                "enginePredictedOpp": "Corviknight",
                "actualOppMove": "Triple Axel",
                "pvMatchedReality": False,
                "faints": [{"side": "mine", "species": "Garchomp"}],
                "residualEvents": [
                    {
                        "side": "mine",
                        "source": "Stealth Rock",
                        "category": "hazard",
                        "hpPctLost": 12,
                        "targetSpecies": "Garchomp",
                    }
                ],
            },
            {
                "turn": 2,
                "forceSwitch": False,
                "myPick": {"kind": "move", "name": "Recover", "confidence": 0.4},
                "actualMyAction": {"kind": "move", "name": "Recover"},
                "enginePredictedOpp": "Toxic",
                "actualOppMove": "Toxic",
                "pvMatchedReality": True,
            },
        ],
    }
    (tmp_path / "one.json").write_text(json.dumps(battle_one), encoding="utf-8")
    (tmp_path / "two.json").write_text(json.dumps(battle_two), encoding="utf-8")

    summary = _summarize_archive(directory=tmp_path)
    panels = {panel["id"]: panel for panel in summary["patternPanels"]}

    assert panels["hazard_status_pressure"]["instances"] == 2
    assert panels["hazard_status_pressure"]["affectedBattles"] == 2
    assert panels["hazard_status_pressure"]["level"]["tier"] == "likely"
    assert panels["action_prevented"]["instances"] == 1
    assert panels["switch_recommendations_ignored"]["instances"] == 1
    assert panels["high_confidence_disagreements"]["instances"] == 1
    assert panels["opponent_prediction_misses"]["instances"] == 2
    assert panels["opponent_prediction_misses"]["lens"] == "Engine eval"


def test_react_dashboard_archive_contract_exposes_command_center_data(tmp_path: Path) -> None:
    base_turns = [
        {
            "turn": 1,
            "forceSwitch": False,
            "rqid": 1,
            "myPick": {"kind": "move", "name": "Quiver Dance", "confidence": 0.72},
            "actualMyAction": {"kind": "move", "name": "Quiver Dance"},
            "enginePredictedOpp": "Toxic",
            "actualOppMove": "Toxic",
            "pvMatchedReality": True,
        },
        {
            "turn": 2,
            "forceSwitch": False,
            "rqid": 2,
            "myPick": {"kind": "switch", "name": "Gholdengo", "confidence": 0.61},
            "actualMyAction": {"kind": "move", "name": "Fire Blast"},
            "enginePredictedOpp": "Stealth Rock",
            "actualOppMove": "Stealth Rock",
            "pvMatchedReality": True,
            "hazardsAdded": [{"side": "mine", "name": "Stealth Rock"}],
            "residualEvents": [
                {
                    "side": "mine",
                    "source": "Stealth Rock",
                    "category": "hazard",
                    "hpPctLost": 12,
                    "targetSpecies": "Volcarona",
                }
            ],
        },
    ]
    common = {
        "format": "[Gen 9] National Dex",
        "myUsername": "Mariga",
        "teamName": "Terapagos Balance",
        "schemaVersion": 11,
        "teamPreview": {
            "mine": ["Volcarona", "Garchomp", "Gholdengo"],
            "opp": ["Gliscor", "Toxapex"],
        },
        "teamPerformance": {
            "mine": {
                "lead": "Volcarona",
                "pokemon": {
                    "Volcarona": {
                        "species": "Volcarona",
                        "led": True,
                        "survived": True,
                        "fainted": False,
                        "fieldPressure": {"totalHpLost": 12, "hazardHpLost": 12},
                        "koCredit": {"directKos": 1},
                        "kos": 1,
                    },
                    "Garchomp": {
                        "species": "Garchomp",
                        "led": False,
                        "survived": False,
                        "fainted": True,
                        "faintTurn": 12,
                        "fieldPressure": {"totalHpLost": 0},
                        "koCredit": {},
                    },
                    "Gholdengo": {
                        "species": "Gholdengo",
                        "led": False,
                        "survived": True,
                        "fainted": False,
                        "fieldPressure": {"totalHpLost": 0},
                        "koCredit": {},
                    },
                },
            }
        },
        "turns": base_turns,
    }
    win = {
        **common,
        "battleId": "battle-react-contract-win",
        "opponentUsername": "Rival",
        "winner": "Mariga",
        "endedAtMs": 1781891298095,
        "replayUrl": "https://replay.pokemonshowdown.com/react-contract-win",
    }
    loss = {
        **common,
        "battleId": "battle-react-contract-loss",
        "opponentUsername": "Rival2",
        "winner": "Rival2",
        "endedAtMs": 1781891398095,
        "replayUrl": "https://replay.pokemonshowdown.com/react-contract-loss",
    }
    (tmp_path / "win.json").write_text(json.dumps(win), encoding="utf-8")
    (tmp_path / "loss.json").write_text(json.dumps(loss), encoding="utf-8")
    latest_capture = {
        **common,
        "battleId": "battle-react-contract-latest-capture",
        "opponentUsername": "RecentRival",
        "winner": None,
        "endedAtMs": 0,
        "replayUrl": "https://replay.pokemonshowdown.com/react-contract-latest",
    }
    latest_path = tmp_path / "latest.json"
    latest_path.write_text(json.dumps(latest_capture), encoding="utf-8")
    os.utime(latest_path, (2_000_000_000, 2_000_000_000))

    archive = _summarize_archive(directory=tmp_path, min_schema_version=7)

    assert set(archive) >= {
        "generatedAt",
        "sourceDir",
        "filters",
        "summary",
        "latestRecordedBattle",
        "battles",
        "timeline",
        "teamProfiles",
        "patternPanels",
        "reviewLabels",
    }
    assert archive["filters"] == {"minSchemaVersion": 7}
    assert archive["summary"]["finishedBattles"] == 2
    assert archive["summary"]["winRate"] == 50.0
    assert archive["summary"]["followRate"] == 50.0
    assert len(archive["battles"]) == 2
    assert archive["latestRecordedBattle"]["battleId"] == "battle-react-contract-latest-capture"
    assert archive["latestRecordedBattle"]["result"] == "unknown"
    assert "missing result" in archive["latestRecordedBattle"]["dataIssues"]

    latest_battle = archive["battles"][0]
    assert set(latest_battle) >= {
        "battleId",
        "opponent",
        "result",
        "teamName",
        "endedAtLabel",
        "schemaVersion",
        "team",
        "metrics",
        "dataIssues",
    }
    assert set(latest_battle["metrics"]) >= {
        "followRate",
        "pvHitRate",
        "switchRecommendations",
        "criticalTurns",
        "hazardResidualEvents",
    }

    profile = archive["teamProfiles"][0]
    assert profile["teamName"] == "Terapagos Balance"
    assert profile["team"] == ["Volcarona", "Garchomp", "Gholdengo"]
    assert profile["battles"] == 2
    assert profile["hasPerformance"] is True
    assert profile["performanceBattles"] == 2
    assert profile["topLead"]["species"] == "Volcarona"
    assert profile["pokemon"][0]["species"] == "Volcarona"
    assert set(profile["pokemon"][0]) >= {
        "leadRate",
        "survivalRate",
        "winWhenAlive",
        "avgFaintTurn",
        "fieldPressureBucket",
        "koShare",
        "koCredit",
        "koCreditTotal",
    }
    assert profile["pokemon"][0]["koCredit"]["directKos"] == 2


def test_react_dashboard_battle_detail_contract_exposes_turn_rows(tmp_path: Path) -> None:
    pm = {
        "battleId": "battle-detail-contract",
        "format": "[Gen 9] National Dex",
        "myUsername": "Mariga",
        "opponentUsername": "Rival",
        "winner": "Rival",
        "endedAtMs": 1781891298095,
        "schemaVersion": 11,
        "replayUrl": "https://replay.pokemonshowdown.com/detail-contract",
        "teamPreview": {
            "mine": ["Volcarona", "Garchomp"],
            "opp": ["Gliscor", "Toxapex"],
        },
        "turns": [
            {
                "turn": 1,
                "forceSwitch": False,
                "rqid": 1,
                "myPick": {"kind": "move", "name": "Quiver Dance", "confidence": 0.72},
                "actualMyAction": {"kind": "move", "name": "Fire Blast"},
                "enginePredictedOpp": "Toxic",
                "actualOppMove": "Stealth Rock",
                "pvMatchedReality": False,
                "hazardsAdded": [{"side": "mine", "name": "Stealth Rock"}],
            },
            {
                "turn": 1,
                "forceSwitch": True,
                "rqid": 2,
                "myPick": {"kind": "switch", "name": "Garchomp"},
                "actualMyAction": {"kind": "switch", "name": "Garchomp"},
            },
        ],
    }
    (tmp_path / "detail.json").write_text(json.dumps(pm), encoding="utf-8")

    detail = _battle_detail("battle-detail-contract", directory=tmp_path)

    assert set(detail) == {"summary", "turns"}
    assert detail["summary"]["battleId"] == "battle-detail-contract"
    assert len(detail["turns"]) == 2
    turn = detail["turns"][0]
    assert set(turn) >= {
        "turn",
        "rqid",
        "forceSwitch",
        "pickLabel",
        "actualLabel",
        "matchedRecommendation",
        "confidence",
        "enginePredictedOpp",
        "actualOppMove",
        "fieldEvents",
        "fieldEventSummary",
        "issues",
    }
    assert turn["matchedRecommendation"] is False
    assert "pv miss" in turn["issues"]
    assert "field pressure" in turn["issues"]


def test_react_dashboard_team_coach_contract_exposes_team_first_brief(tmp_path: Path) -> None:
    pm = {
        "battleId": "battle-team-coach-contract",
        "format": "[Gen 9] National Dex",
        "myUsername": "Mariga",
        "opponentUsername": "Rival",
        "winner": "Rival",
        "endedAtMs": 1781891298095,
        "schemaVersion": 11,
        "replayUrl": "https://replay.pokemonshowdown.com/team-coach-contract",
        "teamName": "Terapagos Balance",
        "teamPreview": {
            "mine": ["Volcarona", "Garchomp", "Gholdengo"],
            "opp": ["Gliscor", "Toxapex"],
        },
        "teamPerformance": {
            "mine": {
                "lead": "Volcarona",
                "pokemon": {
                    "Volcarona": {
                        "species": "Volcarona",
                        "led": True,
                        "survived": False,
                        "fainted": True,
                        "faintTurn": 8,
                        "fieldPressure": {"totalHpLost": 24, "hazardHpLost": 24},
                        "koCredit": {},
                    },
                    "Garchomp": {
                        "species": "Garchomp",
                        "led": False,
                        "survived": True,
                        "fainted": False,
                        "fieldPressure": {"totalHpLost": 0},
                        "koCredit": {"directKos": 1},
                        "kos": 1,
                    },
                    "Gholdengo": {
                        "species": "Gholdengo",
                        "led": False,
                        "survived": True,
                        "fainted": False,
                        "fieldPressure": {"totalHpLost": 0},
                        "koCredit": {},
                    },
                },
            }
        },
        "turns": [
            {
                "turn": 1,
                "forceSwitch": False,
                "rqid": 1,
                "myPick": {"kind": "move", "name": "Quiver Dance", "confidence": 0.82},
                "actualMyAction": {"kind": "move", "name": "Fire Blast"},
                "enginePredictedOpp": "Toxic",
                "actualOppMove": "Stealth Rock",
                "pvMatchedReality": False,
                "hazardsAdded": [{"side": "mine", "name": "Stealth Rock"}],
            },
            {
                "turn": 2,
                "forceSwitch": False,
                "rqid": 2,
                "myPick": {"kind": "switch", "name": "Gholdengo", "confidence": 0.64},
                "actualMyAction": {"kind": "move", "name": "Bug Buzz"},
                "enginePredictedOpp": "Recover",
                "actualOppMove": "Recover",
                "pvMatchedReality": True,
            },
        ],
    }
    (tmp_path / "team-coach.json").write_text(json.dumps(pm), encoding="utf-8")

    brief = _team_coach_brief("battle-team-coach-contract", directory=tmp_path)

    assert brief["purpose"] == "team_coach_brief"
    assert brief["team"]["name"] == "Terapagos Balance"
    assert brief["team"]["roster"] == ["Volcarona", "Garchomp", "Gholdengo"]
    assert brief["summary"]["battles"] == 1
    assert brief["summary"]["performanceBattles"] == 1
    assert isinstance(brief["pokemonProfiles"], list)
    assert brief["pokemonProfiles"][0]["species"] == "Volcarona"
    assert set(brief["evidenceBuckets"]) == {
        "robustIgnoredAdvice",
        "engineUncertainty",
        "noStableLines",
        "fieldPressure",
    }
    assert set(brief["evidenceBuckets"]["engineUncertainty"]) == {"pimcSplits", "pvMisses"}
    assert "agentUsageNotes" in brief


def test_pattern_agent_context_and_fake_run_use_deterministic_pattern_evidence(tmp_path: Path) -> None:
    pm = {
        "battleId": "battle-pattern-agent",
        "winner": "Rival",
        "myUsername": "Mariga",
        "opponentUsername": "Rival",
        "endedAtMs": 1781891298095,
        "schemaVersion": 9,
        "replayUrl": "https://replay.pokemonshowdown.com/test",
        "turns": [
            {
                "turn": 1,
                "forceSwitch": False,
                "myPick": {"kind": "move", "name": "Swords Dance", "confidence": 0.82},
                "actualMyAction": {"kind": "move", "name": "Ivy Cudgel"},
                "enginePredictedOpp": "Toxic",
                "actualOppMove": "Stealth Rock",
                "pvMatchedReality": False,
                "hazardsAdded": [{"side": "mine", "name": "Stealth Rock"}],
            },
            {
                "turn": 2,
                "forceSwitch": False,
                "myPick": {"kind": "switch", "name": "Gholdengo", "confidence": 0.55},
                "actualMyAction": {"kind": "move", "name": "Knock Off"},
                "enginePredictedOpp": "Recover",
                "actualOppMove": "Recover",
                "pvMatchedReality": True,
            },
        ],
    }
    (tmp_path / "pattern-agent.json").write_text(json.dumps(pm), encoding="utf-8")
    trace_dir = tmp_path / "traces"

    context = _pattern_agent_context("high_confidence_disagreements", directory=tmp_path)
    run = _pattern_agent_run(
        "high_confidence_disagreements",
        "openai-gpt-54-mini-balanced",
        directory=tmp_path,
        trace_directory=trace_dir,
    )
    max_run = _pattern_agent_run(
        "high_confidence_disagreements",
        "anthropic-opus-48-xhigh",
        directory=tmp_path,
        trace_directory=trace_dir,
    )

    assert context["purpose"] == "pattern_coaching_context"
    assert context["pattern"]["id"] == "high_confidence_disagreements"
    assert context["pattern"]["instances"] == 1
    assert context["evidenceBreakdown"]["byCategory"]["high_confidence_disagreement"] == 1
    assert [call["name"] for call in run["toolCalls"]] == ["get_pattern_context"]
    assert run["comparisonMetrics"]["requiredToolsCalled"] is True
    assert "fake pattern run" in run["answer"]
    assert [call["name"] for call in max_run["toolCalls"]] == [
        "get_pattern_context",
        "get_archive_context",
    ]
    assert list(trace_dir.glob("*.jsonl"))


def test_pattern_synthesis_prompt_and_tool_arg_guard() -> None:
    preset = _coach_preset("openai-gpt-55-high")
    prompt = _pattern_synthesis_prompt(
        "hazard_status_pressure",
        preset,
        [{"name": "get_pattern_context", "args": {"patternId": "hazard_status_pressure"}, "output": {"ok": True}}],
    )
    args = _normalize_pattern_tool_args(
        "get_pattern_context",
        {"patternId": "opponent_prediction_misses"},
        "hazard_status_pressure",
    )

    assert "Local pattern evidence JSON" in prompt
    assert "hazard_status_pressure" in prompt
    assert "opponent_prediction_misses" not in json.dumps(args)
    assert args["patternId"] == "hazard_status_pressure"


def test_pattern_output_token_budget_is_bounded() -> None:
    assert _pattern_output_token_budget(500) == 900
    assert _pattern_output_token_budget(1200) == 1200
    assert _pattern_output_token_budget(5000) == 2200


def test_ai_review_label_suggestions_are_validated_against_pattern_context() -> None:
    pattern_context = {
        "pattern": {"id": "hazard_status_pressure", "title": "Hazard/status pressure"},
        "evidence": [
            {
                "reviewKey": "hazard_status_pressure|battle-a|3|regular",
                "battleId": "battle-a",
                "turn": 3,
                "forceSwitch": False,
                "category": "field_pressure",
                "tags": ["field pressure"],
                "title": "Field pressure shaped the turn",
            },
            {
                "reviewKey": "hazard_status_pressure|battle-b|4|fs",
                "battleId": "battle-b",
                "turn": 4,
                "forceSwitch": True,
                "category": "switch_timing",
                "reviewLabel": {"label": "player_issue"},
            },
        ],
    }
    raw = {
        "labels": [
            {
                "reviewKey": "hazard_status_pressure|battle-a|3|regular",
                "label": "field_pressure",
                "confidence": "78%",
                "reason": "Hazards and chip constrained the decision.",
            },
            {
                "reviewKey": "hazard_status_pressure|battle-b|4|fs",
                "label": "engine_issue",
                "confidence": 0.9,
                "reason": "Already reviewed, so this should be ignored.",
            },
            {
                "reviewKey": "hazard_status_pressure|battle-c|9|regular",
                "label": "player_issue",
            },
            {
                "reviewKey": "hazard_status_pressure|battle-a|3|regular",
                "label": "not_a_real_label",
            },
        ],
    }

    suggestions = normalize_ai_review_label_suggestions(pattern_context, raw, source="test_model")
    compact = _compact_pattern_context_for_labeler(pattern_context, limit=1)

    assert len(suggestions) == 1
    assert suggestions[0]["label"] == "field_pressure"
    assert suggestions[0]["confidence"] == 0.78
    assert suggestions[0]["source"] == "test_model"
    assert compact["totalUnreviewedEvidence"] == 1
    assert compact["evidence"][0]["reviewKey"] == "hazard_status_pressure|battle-a|3|regular"
    assert {item["id"] for item in compact["allowedLabels"]} >= {"field_pressure", "engine_uncertainty"}


def test_parse_jsonish_model_output_accepts_fenced_json() -> None:
    parsed = _parse_jsonish_model_output(
        '```json\n{"labels":[{"reviewKey":"k","label":"unclear"}]}\n```'
    )

    assert parsed["labels"][0]["label"] == "unclear"


def test_engine_eval_cases_attach_replay_and_priority(tmp_path: Path) -> None:
    battle_id = "battle-engine-eval"
    replay_dir = tmp_path / "replay"
    replay_dir.mkdir()
    (replay_dir / f"{battle_id}.jsonl").write_text(
        json.dumps({
            "battle_id": battle_id,
            "turn": 7,
            "rqid": 11,
            "force_switch": False,
            "engine_request": {
                "battleId": battle_id,
                "turn": 7,
                "rqid": 11,
                "timeLimit": 500,
                "sideOne": {},
                "sideTwo": {},
            },
            "engine_response_terminal": {
                "bestMove": "DRAGONTAIL",
                "confidence": 0.72,
                "sims": 1000,
                "depth": 6,
                "message": "Hidden-info split.",
                "pimcBreakdown": [
                    {"top_move": "DRAGONTAIL", "value": 0.7, "visit_share": 0.6},
                    {"top_move": "DRAGONTAIL", "value": 0.65, "visit_share": 0.55},
                    {"top_move": "RECOVER", "value": 0.6, "visit_share": 0.5},
                    {"top_move": "GARCHOMP", "value": 0.4, "visit_share": 0.2},
                ],
                "pv": ["DRAGONTAIL"],
            },
        }) + "\n",
        encoding="utf-8",
    )
    cases = [{
        "caseId": "case-1",
        "source": {
            "battleId": battle_id,
            "opponent": "Rival",
            "result": "loss",
            "turn": 7,
            "forceSwitch": False,
            "reviewLabel": {"label": "engine_uncertainty"},
        },
        "positionSummary": {
            "confidence": 72,
            "engineAction": "move: DRAGONTAIL",
            "actualAction": "switch: Garchomp",
            "opponent": {"pvMatchedReality": False},
            "tags": ["critical"],
        },
        "expectedBehavior": {
            "caseType": "confidence_calibration",
            "evaluationTarget": "opponent_model",
        },
    }]

    enriched = enrich_engine_eval_cases_with_replay(cases, replay_dir)
    ranked = prioritize_engine_eval_cases(enriched)

    assert ranked[0]["replay"]["available"] is True
    assert ranked[0]["replay"]["terminal"]["bestMove"] == "DRAGONTAIL"
    assert ranked[0]["replay"]["terminal"]["pimcConsensus"]["tier"] == "split"
    assert ranked[0]["priority"]["score"] > 0
    assert "PV miss" in ranked[0]["priority"]["reasons"]
    assert "PIMC split" in ranked[0]["priority"]["reasons"]
    assert ranked[0]["priority"]["pimcUncertainty"]["topMoveShare"] == 0.5


def test_terminal_pimc_uncertainty_only_flags_split_or_fragile_consensus() -> None:
    split = {
        "pimcConsensus": {
            "hypothesisCount": 4,
            "topMove": "GARCHOMP",
            "topMoveShare": 0.5,
            "distinctTopMoves": 3,
            "tier": "split",
            "uncertain": True,
        }
    }
    unanimous = {
        "pimcConsensus": {
            "hypothesisCount": 4,
            "topMove": "MOONBLAST",
            "topMoveShare": 1.0,
            "distinctTopMoves": 1,
            "tier": "unanimous",
            "uncertain": False,
        }
    }

    assert terminal_pimc_uncertainty(split)["tier"] == "split"
    assert terminal_pimc_uncertainty(unanimous) is None


def test_review_labels_persist_and_attach_to_pattern_context(tmp_path: Path) -> None:
    pm = {
        "battleId": "battle-review-label",
        "winner": "Rival",
        "myUsername": "Mariga",
        "opponentUsername": "Rival",
        "endedAtMs": 1781891298095,
        "schemaVersion": 9,
        "replayUrl": "https://replay.pokemonshowdown.com/test",
        "turns": [
            {
                "turn": 1,
                "forceSwitch": False,
                "myPick": {"kind": "move", "name": "Swords Dance", "confidence": 82},
                "actualMyAction": {"kind": "move", "name": "Ivy Cudgel"},
                "enginePredictedOpp": "Toxic",
                "actualOppMove": "Stealth Rock",
                "pvMatchedReality": False,
            },
            {
                "turn": 2,
                "forceSwitch": False,
                "myPick": {"kind": "move", "name": "Recover", "confidence": 40},
                "actualMyAction": {"kind": "move", "name": "Recover"},
                "enginePredictedOpp": "Toxic",
                "actualOppMove": "Toxic",
                "pvMatchedReality": True,
            },
        ],
    }
    labels_path = tmp_path / "labels.json"
    (tmp_path / "review-label.json").write_text(json.dumps(pm), encoding="utf-8")

    result = _save_review_label(
        ReviewLabelRequest(
            patternId="high_confidence_disagreements",
            battleId="battle-review-label",
            turn=1,
            forceSwitch=False,
            label="player_issue",
        ),
        directory=tmp_path,
        path=labels_path,
    )
    labels = _load_review_labels(labels_path)
    summary = _summarize_archive(directory=tmp_path, review_labels=labels)
    context = _pattern_agent_context(
        "high_confidence_disagreements",
        directory=tmp_path,
        review_labels=labels,
    )
    panel = next(
        item for item in summary["patternPanels"]
        if item["id"] == "high_confidence_disagreements"
    )

    assert result["reviewLabel"]["label"] == "player_issue"
    assert len(labels) == 1
    assert panel["reviewLabelSummary"]["counts"]["player_issue"] == 1
    assert panel["evidence"][0]["reviewLabel"]["labelTitle"] == "Player issue"
    assert context["reviewLabelSummary"]["counts"]["player_issue"] == 1
    assert context["evidence"][0]["reviewLabel"]["label"] == "player_issue"

    unlabeled_context = _pattern_agent_context(
        "opponent_prediction_misses",
        directory=tmp_path,
        review_labels={},
    )
    suggestions = suggest_review_labels_for_pattern(unlabeled_context)
    auto_saved = persist_review_label_suggestions(suggestions, path=labels_path)
    labels = _load_review_labels(labels_path)
    summary = _summarize_archive(
        directory=tmp_path,
        pattern_evidence_limit=50,
        review_labels=labels,
    )
    cases = build_engine_eval_cases(summary["patternPanels"])

    assert suggestions[0]["label"] == "engine_uncertainty"
    assert auto_saved["saved"][0]["reviewLabel"]["label"] == "engine_uncertainty"
    assert len(cases) == 1
    assert cases[0]["expectedBehavior"]["caseType"] == "confidence_calibration"

    _save_review_label(
        ReviewLabelRequest(
            patternId="opponent_prediction_misses",
            battleId="battle-review-label",
            turn=1,
            forceSwitch=False,
            label="unreviewed",
        ),
        directory=tmp_path,
        path=labels_path,
    )

    engine_result = _save_review_label(
        ReviewLabelRequest(
            patternId="opponent_prediction_misses",
            battleId="battle-review-label",
            turn=1,
            forceSwitch=False,
            label="engine_uncertainty",
        ),
        directory=tmp_path,
        path=labels_path,
    )
    labels = _load_review_labels(labels_path)
    summary = _summarize_archive(
        directory=tmp_path,
        pattern_evidence_limit=50,
        review_labels=labels,
    )
    cases = build_engine_eval_cases(summary["patternPanels"])

    assert engine_result["reviewLabel"]["label"] == "engine_uncertainty"
    assert len(cases) == 1
    assert cases[0]["expectedBehavior"]["caseType"] == "confidence_calibration"
    assert cases[0]["source"]["reviewLabel"]["label"] == "engine_uncertainty"

    summary = _summarize_archive(
        directory=tmp_path,
        pattern_evidence_limit=50,
        review_labels={},
    )
    auto_cases = build_engine_eval_cases(summary["patternPanels"])

    assert len(auto_cases) == 1
    assert auto_cases[0]["source"]["reviewLabel"]["label"] == "engine_uncertainty"
    assert auto_cases[0]["source"]["reviewLabel"]["autoGenerated"] is True

    cleared = _save_review_label(
        ReviewLabelRequest(
            patternId="high_confidence_disagreements",
            battleId="battle-review-label",
            turn=1,
            forceSwitch=False,
            label="unreviewed",
        ),
        directory=tmp_path,
        path=labels_path,
    )

    assert cleared["reviewLabel"] is None
    assert len(_load_review_labels(labels_path)) == 1

    _save_review_label(
        ReviewLabelRequest(
            patternId="opponent_prediction_misses",
            battleId="battle-review-label",
            turn=1,
            forceSwitch=False,
            label="unreviewed",
        ),
        directory=tmp_path,
        path=labels_path,
    )

    assert _load_review_labels(labels_path) == {}


def test_field_state_context_extracts_engine_replay_board_state() -> None:
    state = _field_state_context({
        "turn": 3,
        "rqid": 10,
        "captured_at_ms": 123,
        "engine_request": {
            "hypotheses": [{
                "weather": {"weatherType": "rain", "turnsRemaining": 3},
                "terrain": {"terrainType": "none", "turnsRemaining": -1},
                "trickRoom": False,
                "sideOne": {
                    "activeIndex": 0,
                    "sideConditions": {"spikes": 1, "reflect": 2},
                    "pokemon": [{
                        "species": "garchomp",
                        "hp": 150,
                        "maxhp": 300,
                        "status": "Burn",
                        "moves": [{"id": "earthquake", "pp": 10}],
                    }],
                },
                "sideTwo": {
                    "activeIndex": 0,
                    "sideConditions": {"stealthRock": 1},
                    "pokemon": [{
                        "species": "clodsire",
                        "hp": 200,
                        "maxhp": 400,
                        "status": "None",
                    }],
                },
            }],
        },
    })

    assert state is not None
    assert state["mine"]["active"]["species"] == "garchomp"
    assert state["mine"]["active"]["hpPct"] == 50.0
    assert state["mine"]["hazards"] == {"spikes": 1}
    assert state["mine"]["screens"] == {"reflect": 2}
    assert state["opp"]["hazards"] == {"stealthRock": 1}


def test_coach_brief_surfaces_turning_points_and_focus(tmp_path: Path) -> None:
    pm = {
        "battleId": "battle-coach",
        "format": "[Gen 9] National Dex",
        "myUsername": "Mariga",
        "opponentUsername": "Rival",
        "winner": "Rival",
        "endedAtMs": 1781891298095,
        "schemaVersion": 9,
        "replayUrl": "https://replay.pokemonshowdown.com/test",
        "teamPreview": {
            "mine": ["Garchomp", "Volcarona"],
            "opp": ["Meowscarada", "Corviknight"],
        },
        "turns": [
            {
                "turn": 1,
                "forceSwitch": False,
                "myPick": {
                    "kind": "move",
                    "name": "Stealth Rock",
                    "confidence": 0.71,
                },
                "actualMyAction": {
                    "kind": "prevented",
                    "name": None,
                    "reason": "fainted before action (Triple Axel)",
                },
                "enginePredictedOpp": "Corviknight",
                "actualOppMove": "Triple Axel",
                "pvMatchedReality": False,
                "faints": [{"side": "mine", "species": "Garchomp"}],
                "residualEvents": [
                    {
                        "side": "opp",
                        "source": "Rough Skin",
                        "category": "contact",
                        "hpPctLost": 12,
                        "targetSpecies": "Meowscarada",
                    },
                ],
            },
            {
                "turn": 2,
                "forceSwitch": False,
                "myPick": {
                    "kind": "switch",
                    "name": "Volcarona",
                    "confidence": 0.8,
                },
                "actualMyAction": {"kind": "move", "name": "Dragon Claw"},
                "enginePredictedOpp": "Toxic",
                "actualOppMove": "Spikes",
                "pvMatchedReality": False,
                "hazardsAdded": [{"side": "mine", "name": "Spikes"}],
            },
        ],
    }
    (tmp_path / "coach.json").write_text(json.dumps(pm), encoding="utf-8")

    brief = _coach_brief("battle-coach", directory=tmp_path)

    assert brief["purpose"] == "battle_coach_brief"
    assert brief["battle"]["result"] == "loss"
    assert brief["turningPoints"][0]["turn"] == 1
    assert brief["turningPoints"][0]["title"] == "Planned action was stopped"
    assert brief["reviewQueue"][0]["category"] == "action_prevented"
    assert brief["reviewQueue"][0]["severity"] == "high"
    assert any(
        "Opponent prediction missed" in line
        for line in brief["turningPoints"][0]["evidence"]
    )
    assert any(
        item["title"] == "Switch timing"
        for item in brief["practiceFocus"]
    )
    assert "get_battle_context" in brief["modelHandoff"]["suggestedTools"]
    assert "reviewQueue" in brief["modelHandoff"]["suggestedTools"]


def test_review_queue_keeps_low_confidence_turns_out_of_high_confidence_bucket(tmp_path: Path) -> None:
    pm = {
        "battleId": "battle-review-queue",
        "format": "[Gen 9] National Dex",
        "myUsername": "Mariga",
        "opponentUsername": "Rival",
        "winner": "Rival",
        "endedAtMs": 1781891298095,
        "schemaVersion": 9,
        "replayUrl": "https://replay.pokemonshowdown.com/test",
        "turns": [
            {
                "turn": 4,
                "forceSwitch": False,
                "myPick": {
                    "kind": "move",
                    "name": "Swords Dance",
                    "confidence": 0.837,
                },
                "actualMyAction": {"kind": "move", "name": "Ivy Cudgel"},
                "enginePredictedOpp": "Toxic",
                "actualOppMove": "Stealth Rock",
                "pvMatchedReality": False,
                "hazardsAdded": [{"side": "mine", "name": "Stealth Rock"}],
            },
            {
                "turn": 7,
                "forceSwitch": False,
                "myPick": {
                    "kind": "move",
                    "name": "Flamethrower",
                    "confidence": 0.428,
                },
                "actualMyAction": {"kind": "switch", "name": "Garchomp"},
                "enginePredictedOpp": "Thunderbolt",
                "actualOppMove": "Thunderbolt",
                "pvMatchedReality": True,
                "faints": [{"side": "mine", "species": "Garchomp"}],
            },
        ],
    }
    (tmp_path / "review-queue.json").write_text(json.dumps(pm), encoding="utf-8")

    brief = _coach_brief("battle-review-queue", directory=tmp_path)
    cards = {card["turn"]: card for card in brief["reviewQueue"]}

    assert brief["reviewQueue"][0]["turn"] == 4
    assert cards[4]["category"] == "high_confidence_disagreement"
    assert cards[4]["confidenceTier"] == "high"
    assert "Opponent prediction also missed" in cards[4]["verdict"]
    assert cards[7]["category"] == "low_confidence_outcome_review"
    assert cards[7]["confidenceTier"] == "low"
    assert "high confidence" not in cards[7]["tags"]
    assert cards[7]["severity"] == "medium"


def test_coach_agent_fake_presets_produce_trace_and_tool_depth(tmp_path: Path) -> None:
    pm = {
        "battleId": "battle-agent",
        "format": "[Gen 9] National Dex",
        "myUsername": "Mariga",
        "opponentUsername": "Rival",
        "winner": "Rival",
        "endedAtMs": 1781891298095,
        "schemaVersion": 9,
        "replayUrl": "https://replay.pokemonshowdown.com/test",
        "teamPreview": {
            "mine": ["Garchomp", "Volcarona"],
            "opp": ["Meowscarada", "Corviknight"],
        },
        "turns": [
            {
                "turn": 1,
                "forceSwitch": False,
                "myPick": {
                    "kind": "move",
                    "name": "Stealth Rock",
                    "confidence": 0.71,
                },
                "actualMyAction": {
                    "kind": "prevented",
                    "name": None,
                    "reason": "fainted before action (Triple Axel)",
                },
                "enginePredictedOpp": "Corviknight",
                "actualOppMove": "Triple Axel",
                "pvMatchedReality": False,
                "faints": [{"side": "mine", "species": "Garchomp"}],
            },
            {
                "turn": 2,
                "forceSwitch": False,
                "myPick": {
                    "kind": "switch",
                    "name": "Volcarona",
                    "confidence": 0.8,
                },
                "actualMyAction": {"kind": "move", "name": "Dragon Claw"},
                "enginePredictedOpp": "Toxic",
                "actualOppMove": "Spikes",
                "pvMatchedReality": False,
                "hazardsAdded": [{"side": "mine", "name": "Spikes"}],
            },
        ],
    }
    (tmp_path / "agent.json").write_text(json.dumps(pm), encoding="utf-8")
    trace_dir = tmp_path / "traces"

    fast = _coach_agent_run(
        "battle-agent",
        "openai-gpt-54-mini-balanced",
        directory=tmp_path,
        trace_directory=trace_dir,
    )
    max_run = _coach_agent_run(
        "battle-agent",
        "anthropic-opus-48-xhigh",
        directory=tmp_path,
        trace_directory=trace_dir,
    )

    assert fast["mode"] == "fake"
    assert fast["comparisonMetrics"]["requiredToolsCalled"] is True
    assert [call["name"] for call in fast["toolCalls"]] == ["get_coach_brief"]
    assert [call["name"] for call in max_run["toolCalls"]] == [
        "get_coach_brief",
        "get_battle_context",
        "get_archive_context",
        "get_team_coach_brief",
    ]
    assert "Archive-context tool compared" in max_run["answer"]
    assert "Team-coach tool reviewed" in max_run["answer"]
    assert list(trace_dir.glob("*.jsonl"))


def test_team_coach_agent_fake_run_is_team_first(tmp_path: Path) -> None:
    pm = {
        "battleId": "battle-team-agent",
        "format": "[Gen 9] National Dex",
        "myUsername": "Mariga",
        "opponentUsername": "Rival",
        "winner": "Rival",
        "endedAtMs": 1781891298095,
        "schemaVersion": 11,
        "teamPreview": {
            "mine": ["Garchomp", "Volcarona"],
            "opp": ["Meowscarada", "Corviknight"],
        },
        "teamPerformance": {
            "mine": {
                "lead": "Garchomp",
                "pokemon": {
                    "Garchomp": {
                        "species": "Garchomp",
                        "led": True,
                        "survived": False,
                        "fainted": True,
                        "faintTurn": 1,
                        "fieldPressure": {"totalHpLost": 12},
                        "koCredit": {},
                    },
                    "Volcarona": {
                        "species": "Volcarona",
                        "led": False,
                        "survived": True,
                        "fainted": False,
                        "fieldPressure": {"totalHpLost": 0},
                        "koCredit": {"directKos": 1},
                    },
                },
            },
        },
        "turns": [
            {
                "turn": 1,
                "forceSwitch": False,
                "myPick": {
                    "kind": "move",
                    "name": "Stealth Rock",
                    "confidence": 0.71,
                    "pimcConsensus": {"tier": "split", "uncertain": True},
                },
                "actualMyAction": {
                    "kind": "prevented",
                    "name": None,
                    "reason": "fainted before action (Triple Axel)",
                },
                "enginePredictedOpp": "Corviknight",
                "actualOppMove": "Triple Axel",
                "pvMatchedReality": False,
                "faints": [{"side": "mine", "species": "Garchomp"}],
                "residualEvents": [
                    {
                        "side": "mine",
                        "source": "Stealth Rock",
                        "category": "hazard",
                        "hpPctLost": 12,
                        "targetSpecies": "Garchomp",
                    },
                ],
            },
            {
                "turn": 2,
                "forceSwitch": False,
                "myPick": {
                    "kind": "switch",
                    "name": "Volcarona",
                    "confidence": 0.8,
                    "pimcConsensus": {"tier": "unanimous", "uncertain": False},
                },
                "actualMyAction": {"kind": "move", "name": "Dragon Claw"},
                "enginePredictedOpp": "Toxic",
                "actualOppMove": "Spikes",
                "pvMatchedReality": False,
            },
        ],
    }
    (tmp_path / "team-agent.json").write_text(json.dumps(pm), encoding="utf-8")
    trace_dir = tmp_path / "traces"

    fast = _team_coach_agent_run(
        "battle-team-agent",
        "openai-gpt-54-mini-balanced",
        directory=tmp_path,
        trace_directory=trace_dir,
    )
    max_run = _team_coach_agent_run(
        "battle-team-agent",
        "openai-gpt-55-pro-xhigh",
        directory=tmp_path,
        trace_directory=trace_dir,
    )

    overview, overview_trace = _run_team_coach_tool(
        "get_team_overview",
        {"battleId": "battle-team-agent"},
        tmp_path,
    )
    bucket, bucket_trace = _run_team_coach_tool(
        "get_team_bucket_examples",
        {"battleId": "battle-team-agent", "bucket": "pimcSplits", "limit": 4},
        tmp_path,
    )
    window, window_trace = _run_team_coach_tool(
        "get_battle_window",
        {"battleId": "battle-team-agent", "turn": 1, "before": 0, "after": 1, "_anchorBattleId": "battle-team-agent"},
        tmp_path,
    )
    timeline, timeline_trace = _run_team_coach_tool(
        "get_pokemon_battle_timeline",
        {"battleId": "battle-team-agent", "targetBattleId": "battle-team-agent", "species": "Garchomp", "limit": 4},
        tmp_path,
    )
    state, state_trace = _run_team_coach_tool(
        "get_team_state_at_turn",
        {"battleId": "battle-team-agent", "targetBattleId": "battle-team-agent", "turn": 1},
        tmp_path,
    )

    assert overview["purpose"] == "team_overview"
    assert overview["team"]["trackedBattleCount"] == 1
    assert "battleIds" not in overview["team"]
    assert overview["pokemonProfiles"][0]["species"] == "Garchomp"
    assert "fieldPressure" in overview["pokemonProfiles"][0]
    assert overview["bucketCounts"]["pimcSplits"] == 1
    assert overview_trace["outputSummary"]
    assert bucket["bucket"] == "pimcSplits"
    assert len(bucket["examples"]) == 1
    assert bucket_trace["outputSummary"]
    assert window["purpose"] == "team_battle_window"
    assert "teamPerformance" not in window["battle"]
    assert len(window["turns"]) == 2
    assert window_trace["outputSummary"]
    assert timeline["purpose"] == "pokemon_battle_timeline"
    assert timeline["battlePerformance"]["faintTurn"] == 1
    assert timeline["turns"][0]["turn"] == 1
    assert timeline_trace["outputSummary"]
    assert state["purpose"] == "team_state_at_turn"
    assert state["rows"][0]["turn"] == 1
    assert state_trace["outputSummary"]

    assert [call["name"] for call in fast["toolCalls"]] == ["get_team_overview"]
    assert [call["name"] for call in max_run["toolCalls"]] == [
        "get_team_overview",
        "get_team_bucket_examples",
        "get_battle_window",
        "get_pokemon_profile",
        "get_pokemon_battle_timeline",
        "get_team_state_at_turn",
        "get_engine_eval_cases",
    ]
    assert max_run["comparisonMetrics"]["requiredToolsCalled"] is True
    assert max_run["comparisonMetrics"]["teamContextFirst"] is True
    assert max_run["comparisonMetrics"]["pokemonProfiles"] == 2
    assert "Team performance read" in max_run["answer"]
    assert "Engine uncertainty vs player-choice issues" in max_run["answer"]
    assert list(trace_dir.glob("*.jsonl"))


@pytest.mark.asyncio
async def test_openai_team_coach_real_mode_uses_compact_tool_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm = {
        "battleId": "battle-team-openai",
        "format": "[Gen 9] National Dex",
        "myUsername": "Mariga",
        "opponentUsername": "Rival",
        "winner": "Mariga",
        "endedAtMs": 1781891298095,
        "schemaVersion": 11,
        "teamPreview": {
            "mine": ["Garchomp", "Volcarona"],
            "opp": ["Meowscarada", "Corviknight"],
        },
        "teamPerformance": {
            "mine": {
                "lead": "Garchomp",
                "pokemon": {
                    "Garchomp": {
                        "species": "Garchomp",
                        "led": True,
                        "survived": True,
                        "fainted": False,
                        "fieldPressure": {"totalHpLost": 0},
                        "koCredit": {"directKos": 1},
                    },
                    "Volcarona": {
                        "species": "Volcarona",
                        "led": False,
                        "survived": True,
                        "fainted": False,
                        "fieldPressure": {"totalHpLost": 0},
                        "koCredit": {},
                    },
                },
            },
        },
        "turns": [
            {
                "turn": 1,
                "forceSwitch": False,
                "myPick": {
                    "kind": "move",
                    "name": "Stealth Rock",
                    "confidence": 0.71,
                    "pimcConsensus": {"tier": "split", "uncertain": True},
                },
                "actualMyAction": {"kind": "move", "name": "Earthquake"},
                "enginePredictedOpp": "Corviknight",
                "actualOppMove": "Triple Axel",
                "pvMatchedReality": False,
            },
            {
                "turn": 2,
                "forceSwitch": False,
                "myPick": {
                    "kind": "move",
                    "name": "Dragon Claw",
                    "confidence": 0.64,
                },
                "actualMyAction": {"kind": "move", "name": "Dragon Claw"},
                "enginePredictedOpp": "Recover",
                "actualOppMove": "Recover",
                "pvMatchedReality": True,
            },
        ],
    }
    (tmp_path / "team-openai.json").write_text(json.dumps(pm), encoding="utf-8")
    trace_dir = tmp_path / "traces"
    payloads: list[dict] = []

    async def fake_openai_create(payload: dict, timeout_seconds: int) -> dict:
        payloads.append(payload)
        if len(payloads) == 1:
            return {
                "id": "resp_1",
                "output": [{
                    "type": "function_call",
                    "call_id": "call_overview",
                    "name": "get_team_overview",
                    "arguments": json.dumps({"battleId": "battle-team-openai"}),
                }],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            }
        if len(payloads) == 2:
            tool_output = payload["input"][0]["output"]
            assert "team_overview" in tool_output
            return {
                "id": "resp_2",
                "output": [{
                    "type": "function_call",
                    "call_id": "call_bucket",
                    "name": "get_team_bucket_examples",
                    "arguments": json.dumps({
                        "battleId": "battle-team-openai",
                        "bucket": "pimcSplits",
                        "limit": 2,
                    }),
                }],
                "usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
            }
        return {
            "id": "resp_3",
            "output_text": "Team performance read\n\nEngine uncertainty vs player-choice issues\n\nPractice focus",
            "usage": {"input_tokens": 30, "output_tokens": 10, "total_tokens": 40},
        }

    monkeypatch.setattr(
        "showdown_copilot.dashboard_agent_service.openai_responses_create",
        fake_openai_create,
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    run = await _team_coach_agent_run_async(
        "battle-team-openai",
        "openai-gpt-54-mini-balanced",
        directory=tmp_path,
        trace_directory=trace_dir,
        run_mode="real",
    )

    assert [call["name"] for call in run["toolCalls"]] == [
        "get_team_overview",
        "get_team_bucket_examples",
    ]
    assert run["mode"] == "real"
    assert run["comparisonMetrics"]["teamContextFirst"] is True
    assert run["settings"]["toolChoice"] == "model-driven compact team tools"
    assert payloads[0]["tool_choice"] == {"type": "function", "name": "get_team_overview"}
    assert payloads[1]["tool_choice"] == "auto"
    assert run["usage"]["totalTokens"] == 80


@pytest.mark.asyncio
async def test_openai_team_coach_forces_final_answer_after_tool_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm = {
        "battleId": "battle-team-openai-budget",
        "format": "[Gen 9] National Dex",
        "myUsername": "Mariga",
        "opponentUsername": "Rival",
        "winner": "Mariga",
        "endedAtMs": 1781891298095,
        "schemaVersion": 11,
        "teamPreview": {
            "mine": ["Garchomp", "Volcarona"],
            "opp": ["Meowscarada", "Corviknight"],
        },
        "teamPerformance": {
            "mine": {
                "lead": "Garchomp",
                "pokemon": {
                    "Garchomp": {
                        "species": "Garchomp",
                        "led": True,
                        "survived": True,
                        "fainted": False,
                        "fieldPressure": {"totalHpLost": 0},
                        "koCredit": {"directKos": 1},
                    },
                    "Volcarona": {
                        "species": "Volcarona",
                        "led": False,
                        "survived": True,
                        "fainted": False,
                        "fieldPressure": {"totalHpLost": 0},
                        "koCredit": {},
                    },
                },
            },
        },
        "turns": [
            {
                "turn": 1,
                "forceSwitch": False,
                "myPick": {"kind": "move", "name": "Stealth Rock", "confidence": 0.71},
                "actualMyAction": {"kind": "move", "name": "Earthquake"},
                "enginePredictedOpp": "Corviknight",
                "actualOppMove": "Triple Axel",
                "pvMatchedReality": False,
            },
            {
                "turn": 2,
                "forceSwitch": False,
                "myPick": {"kind": "move", "name": "Dragon Claw", "confidence": 0.64},
                "actualMyAction": {"kind": "move", "name": "Dragon Claw"},
                "enginePredictedOpp": "Recover",
                "actualOppMove": "Recover",
                "pvMatchedReality": True,
            },
        ],
    }
    (tmp_path / "team-openai-budget.json").write_text(json.dumps(pm), encoding="utf-8")
    trace_dir = tmp_path / "traces"
    payloads: list[dict] = []

    async def fake_openai_create(payload: dict, timeout_seconds: int) -> dict:
        payloads.append(payload)
        if len(payloads) == 1:
            return {
                "id": "resp_1",
                "output": [{
                    "type": "function_call",
                    "call_id": "call_overview",
                    "name": "get_team_overview",
                    "arguments": json.dumps({"battleId": "battle-team-openai-budget"}),
                }],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            }
        if len(payloads) == 2:
            return {
                "id": "resp_2",
                "output": [{
                    "type": "function_call",
                    "call_id": "call_profile",
                    "name": "get_pokemon_profile",
                    "arguments": json.dumps({
                        "battleId": "battle-team-openai-budget",
                        "species": "Garchomp",
                    }),
                }],
                "usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
            }
        if len(payloads) == 3:
            return {
                "id": "resp_3",
                "output": [{
                    "type": "function_call",
                    "call_id": "call_bucket",
                    "name": "get_team_bucket_examples",
                    "arguments": json.dumps({
                        "battleId": "battle-team-openai-budget",
                        "bucket": "pvMisses",
                        "limit": 2,
                    }),
                }],
                "usage": {"input_tokens": 30, "output_tokens": 5, "total_tokens": 35},
            }
        assert "tools" not in payload
        assert "previous_response_id" not in payload
        assert "Local team-coach evidence JSON" in payload["input"]
        return {
            "id": "resp_4",
            "output_text": "Team performance read\n\nEngine uncertainty vs player-choice issues\n\nPractice focus",
            "usage": {"input_tokens": 40, "output_tokens": 10, "total_tokens": 50},
        }

    monkeypatch.setattr(
        "showdown_copilot.dashboard_agent_service.openai_responses_create",
        fake_openai_create,
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    run = await _team_coach_agent_run_async(
        "battle-team-openai-budget",
        "openai-gpt-54-mini-balanced",
        directory=tmp_path,
        trace_directory=trace_dir,
        run_mode="real",
    )

    assert run["settings"]["forcedFinalAfterToolBudget"] is True
    assert run["settings"]["budgetedSynthesisPass"] is True
    assert run["settings"]["standaloneSynthesisPass"] is False
    assert run["settings"]["toolLimitReached"] is False
    assert [call["name"] for call in run["toolCalls"]] == [
        "get_team_overview",
        "get_pokemon_profile",
        "get_team_bucket_examples",
    ]


@pytest.mark.asyncio
async def test_anthropic_team_coach_real_mode_uses_messages_tool_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm = {
        "battleId": "battle-team-anthropic",
        "format": "[Gen 9] National Dex",
        "myUsername": "Mariga",
        "opponentUsername": "Rival",
        "winner": "Mariga",
        "endedAtMs": 1781891298095,
        "schemaVersion": 11,
        "teamPreview": {
            "mine": ["Garchomp", "Volcarona"],
            "opp": ["Meowscarada", "Corviknight"],
        },
        "teamPerformance": {
            "mine": {
                "lead": "Garchomp",
                "pokemon": {
                    "Garchomp": {
                        "species": "Garchomp",
                        "led": True,
                        "survived": True,
                        "fainted": False,
                        "fieldPressure": {"totalHpLost": 0},
                        "koCredit": {"directKos": 1},
                    },
                    "Volcarona": {
                        "species": "Volcarona",
                        "led": False,
                        "survived": True,
                        "fainted": False,
                        "fieldPressure": {"totalHpLost": 0},
                        "koCredit": {},
                    },
                },
            },
        },
        "turns": [
            {
                "turn": 1,
                "forceSwitch": False,
                "myPick": {
                    "kind": "move",
                    "name": "Stealth Rock",
                    "confidence": 0.71,
                    "pimcConsensus": {"tier": "split", "uncertain": True},
                },
                "actualMyAction": {"kind": "move", "name": "Earthquake"},
                "enginePredictedOpp": "Corviknight",
                "actualOppMove": "Triple Axel",
                "pvMatchedReality": False,
            },
            {
                "turn": 2,
                "forceSwitch": False,
                "myPick": {
                    "kind": "move",
                    "name": "Dragon Claw",
                    "confidence": 0.64,
                },
                "actualMyAction": {"kind": "move", "name": "Dragon Claw"},
                "enginePredictedOpp": "Recover",
                "actualOppMove": "Recover",
                "pvMatchedReality": True,
            },
        ],
    }
    (tmp_path / "team-anthropic.json").write_text(json.dumps(pm), encoding="utf-8")
    trace_dir = tmp_path / "traces"
    payloads: list[dict] = []

    async def fake_anthropic_create(payload: dict, timeout_seconds: int) -> dict:
        payloads.append(payload)
        if len(payloads) == 1:
            return {
                "id": "msg_1",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "",
                        "signature": "sig_1",
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu_overview",
                        "name": "get_team_overview",
                        "input": {"battleId": "battle-team-anthropic"},
                    },
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 12, "output_tokens": 8},
            }
        tool_result = payload["messages"][-1]["content"][0]
        assert tool_result["type"] == "tool_result"
        assert "team_overview" in tool_result["content"]
        return {
            "id": "msg_2",
            "content": [{
                "type": "text",
                "text": "Team performance read\n\nEngine uncertainty vs player-choice issues\n\nPractice focus",
            }],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 25, "output_tokens": 12},
        }

    monkeypatch.setattr(
        "showdown_copilot.dashboard_agent_service.anthropic_messages_create",
        fake_anthropic_create,
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    run = await _team_coach_agent_run_async(
        "battle-team-anthropic",
        "anthropic-sonnet-46-high",
        directory=tmp_path,
        trace_directory=trace_dir,
        run_mode="real",
    )

    assert [call["name"] for call in run["toolCalls"]] == ["get_team_overview"]
    assert run["provider"] == "anthropic"
    assert run["mode"] == "real"
    assert run["settings"]["api"] == "messages"
    assert run["settings"]["toolChoice"] == "model-driven compact team tools"
    assert run["settings"]["thinkingMode"] == "adaptive"
    assert run["settings"]["thinkingEffort"] == "high"
    assert "tool_choice" not in payloads[0]
    assert "tool_choice" not in payloads[1]
    assert payloads[0]["thinking"] == {"type": "adaptive", "display": "omitted"}
    assert payloads[0]["output_config"] == {"effort": "high"}
    assert payloads[1]["thinking"] == {"type": "adaptive", "display": "omitted"}
    assert payloads[1]["output_config"] == {"effort": "high"}
    assert payloads[0]["system"].startswith("<role>")
    assert "Start with get_team_overview" in payloads[0]["messages"][0]["content"]
    assert payloads[0]["tools"][0]["description"].startswith("Use this first")
    assert run["settings"]["stopReasons"] == ["tool_use", "end_turn"]
    assert run["settings"]["responseSummaries"][0]["contentTypes"] == ["thinking", "tool_use"]
    assert run["settings"]["responseSummaries"][0]["toolNames"] == ["get_team_overview"]
    assert run["usage"]["inputTokens"] == 37
    assert run["usage"]["outputTokens"] == 20
    assert run["usage"]["totalTokens"] == 57


async def test_anthropic_recent_battle_auto_mode_uses_real_messages_tool_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm = {
        "battleId": "battle-recent-anthropic",
        "format": "[Gen 9] National Dex",
        "myUsername": "Mariga",
        "opponentUsername": "Rival",
        "winner": "Rival",
        "endedAtMs": 1781891298095,
        "schemaVersion": 11,
        "teamPreview": {
            "mine": ["Garchomp", "Volcarona"],
            "opp": ["Meowscarada", "Corviknight"],
        },
        "turns": [
            {
                "turn": 1,
                "forceSwitch": False,
                "myPick": {
                    "kind": "move",
                    "name": "Stealth Rock",
                    "confidence": 0.71,
                },
                "actualMyAction": {"kind": "move", "name": "Earthquake"},
                "enginePredictedOpp": "Corviknight",
                "actualOppMove": "Triple Axel",
                "pvMatchedReality": False,
            },
            {
                "turn": 2,
                "forceSwitch": False,
                "myPick": {
                    "kind": "move",
                    "name": "Dragon Claw",
                    "confidence": 0.64,
                },
                "actualMyAction": {"kind": "move", "name": "Dragon Claw"},
                "enginePredictedOpp": "Recover",
                "actualOppMove": "Recover",
                "pvMatchedReality": True,
            },
        ],
    }
    (tmp_path / "recent-anthropic.json").write_text(json.dumps(pm), encoding="utf-8")
    trace_dir = tmp_path / "traces"
    payloads: list[dict] = []

    async def fake_anthropic_create(payload: dict, timeout_seconds: int) -> dict:
        payloads.append(payload)
        if len(payloads) == 1:
            return {
                "id": "msg_recent_1",
                "content": [{
                    "type": "tool_use",
                    "id": "toolu_brief",
                    "name": "get_coach_brief",
                    "input": {"battleId": "battle-recent-anthropic"},
                }],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 10, "output_tokens": 6},
            }
        tool_result = payload["messages"][-1]["content"][0]
        assert tool_result["type"] == "tool_result"
        assert "coach_brief" in tool_result["content"]
        return {
            "id": "msg_recent_2",
            "content": [{
                "type": "text",
                "text": "Diagnosis\n\nTurning points\n\nPractice focus",
            }],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 22, "output_tokens": 9},
        }

    monkeypatch.setattr(
        "showdown_copilot.dashboard_agent_service.anthropic_messages_create",
        fake_anthropic_create,
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    run = await _coach_agent_run_async(
        "battle-recent-anthropic",
        "anthropic-sonnet-46-high",
        directory=tmp_path,
        trace_directory=trace_dir,
        run_mode="auto",
    )

    assert [call["name"] for call in run["toolCalls"]] == ["get_coach_brief"]
    assert run["provider"] == "anthropic"
    assert run["mode"] == "real"
    assert run["settings"]["api"] == "messages"
    assert run["settings"]["toolChoice"] == "auto"
    assert run["settings"]["thinkingMode"] == "adaptive"
    assert "tool_choice" not in payloads[0]
    assert payloads[0]["system"].startswith("You are Showdown Copilot's post-game coaching agent.")
    assert "First call get_coach_brief" in payloads[0]["messages"][0]["content"]
    assert payloads[0]["tools"][0]["name"] == "get_coach_brief"
    assert run["settings"]["stopReasons"] == ["tool_use", "end_turn"]
    assert run["usage"]["totalTokens"] == 47


def test_coach_model_presets_include_provider_reasoning_tiers() -> None:
    presets = _coach_model_presets()
    providers = {preset["provider"] for preset in presets}
    tiers = {preset["tier"] for preset in presets}

    assert {"openai", "anthropic", "google"}.issubset(providers)
    assert {"fast", "advanced", "max"}.issubset(tiers)
    assert all(preset["mode"] == "fake" for preset in presets)


def test_openai_presets_have_real_run_settings() -> None:
    presets = [
        preset for preset in _coach_model_presets()
        if preset["provider"] == "openai"
    ]
    by_id = {preset["id"]: preset for preset in presets}

    assert presets
    assert all(preset["apiModel"] for preset in presets)
    assert {preset["openaiReasoningEffort"] for preset in presets} == {"medium", "high"}
    assert by_id["openai-gpt-54-mini-balanced"]["maxOutputTokens"] >= 1200
    assert by_id["openai-gpt-55-high"]["maxOutputTokens"] >= 3500
    assert by_id["openai-gpt-55-pro-xhigh"]["maxOutputTokens"] >= 4500
    assert all(preset["maxToolRounds"] >= 3 for preset in presets)


def test_anthropic_presets_have_real_run_settings() -> None:
    presets = [
        preset for preset in _coach_model_presets()
        if preset["provider"] == "anthropic"
    ]
    by_id = {preset["id"]: preset for preset in presets}

    assert presets
    assert all(preset["apiModel"] for preset in presets)
    assert all(preset["realProvider"] == "anthropic" for preset in presets)
    assert by_id["anthropic-haiku-45-balanced"]["maxOutputTokens"] >= 2200
    assert by_id["anthropic-sonnet-46-high"]["maxOutputTokens"] >= 6000
    assert by_id["anthropic-opus-48-xhigh"]["maxOutputTokens"] >= 7500
    assert by_id["anthropic-haiku-45-balanced"].get("anthropicThinking") is None
    assert by_id["anthropic-sonnet-46-high"]["anthropicThinking"] == "adaptive"
    assert by_id["anthropic-sonnet-46-high"]["anthropicThinkingEffort"] == "high"
    assert by_id["anthropic-opus-48-xhigh"]["anthropicThinking"] == "adaptive"
    assert by_id["anthropic-opus-48-xhigh"]["anthropicThinkingEffort"] == "xhigh"
    assert all(preset["maxToolRounds"] >= 3 for preset in presets)


def test_real_openai_mode_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    preset = _coach_preset("openai-gpt-54-mini-balanced")

    with pytest.raises(Exception) as exc:
        _should_run_real_provider(preset, "real")

    assert getattr(exc.value, "status_code", None) == 503


def test_auto_openai_mode_falls_back_to_fake_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    preset = _coach_preset("openai-gpt-54-mini-balanced")

    assert _should_run_real_provider(preset, "auto") is False


def test_openai_response_helpers_parse_tool_calls_and_usage() -> None:
    calls = _response_function_calls({
        "output": [{
            "type": "function_call",
            "call_id": "call_1",
            "name": "get_coach_brief",
            "arguments": json.dumps({"battleId": "battle-test"}),
        }],
    })
    usage = _usage_from_responses([
        {
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "output_tokens_details": {"reasoning_tokens": 2},
            }
        },
        {
            "usage": {
                "input_tokens": 7,
                "output_tokens": 3,
                "total_tokens": 10,
            }
        },
    ])

    assert calls == [{
        "callId": "call_1",
        "name": "get_coach_brief",
        "args": {"battleId": "battle-test"},
    }]
    assert usage["inputTokens"] == 17
    assert usage["outputTokens"] == 8
    assert usage["totalTokens"] == 25
    assert usage["reasoningTokens"] == 2


def test_openai_response_text_and_final_answer_prompt() -> None:
    text = _response_text({
        "output": [{
            "type": "message",
            "content": [{"type": "output_text", "text": "Final coach answer"}],
        }],
    })
    prompt = _coach_final_answer_prompt(
        "battle-test",
        _coach_preset("openai-gpt-55-high"),
        [{"name": "get_coach_brief"}, {"name": "get_battle_context"}],
    )

    assert text == "Final coach answer"
    assert "Do not call any more tools" in prompt
    assert "get_coach_brief, get_battle_context" in prompt
    assert "battle-test" in prompt


def test_openai_synthesis_prompt_and_tool_arg_guard() -> None:
    preset = _coach_preset("openai-gpt-55-high")
    prompt = _coach_synthesis_prompt(
        "battle-safe",
        preset,
        [{"name": "get_coach_brief", "args": {"battleId": "battle-safe"}, "output": {"ok": True}}],
    )
    args = _normalize_coach_tool_args(
        "get_battle_context",
        {"battleId": "other-battle"},
        "battle-safe",
    )

    assert "Local tool evidence JSON" in prompt
    assert "battle-safe" in prompt
    assert "other-battle" not in json.dumps(args)
    assert args["battleId"] == "battle-safe"


def test_openai_incomplete_and_truncated_text_helpers() -> None:
    assert _response_incomplete({"status": "incomplete"}) is True
    assert _response_incomplete({"output": [{"type": "message", "status": "incomplete"}]}) is True
    assert _response_incomplete({"status": "completed"}) is False
    assert _looks_truncated_text("Turn 22 fainted before action to **") is True
    assert _looks_truncated_text("This answer is complete.") is False
