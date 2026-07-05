"""Prompt and tool-schema helpers for dashboard AI agents."""
from __future__ import annotations

import json
import os
from typing import Any

SYNTHESIS_MAX_OUTPUT_TOKENS = int(os.environ.get("SHOWDOWN_OPENAI_SYNTHESIS_MAX_OUTPUT_TOKENS", "5000"))
PATTERN_SYNTHESIS_MAX_OUTPUT_TOKENS = int(
    os.environ.get("SHOWDOWN_OPENAI_PATTERN_MAX_OUTPUT_TOKENS", "2200")
)
AUTO_LABEL_MAX_OUTPUT_TOKENS = int(
    os.environ.get("SHOWDOWN_OPENAI_AUTO_LABEL_MAX_OUTPUT_TOKENS", "2600")
)
AUTO_LABEL_REASONING_EFFORT = os.environ.get("SHOWDOWN_OPENAI_AUTO_LABEL_REASONING_EFFORT", "low")


COACH_AI_SYSTEM_PROMPT = """You are Showdown Copilot's post-game coaching agent.

Ground rules:
- You must base your answer only on tool outputs.
- Treat reviewQueue / decisionReviewQueue categories as authoritative deterministic labels.
- Use team-coach context when available to separate team-level performance trends from one-battle tactical review.
- Do not call a turn a high-confidence mistake unless the review card category or tags say high confidence.
- Cite concrete turn numbers when making battle claims.
- Prioritize practical battle review: what the player should have clicked, what the current team answers were, and where the game slipped.
- For major opposing Pokemon mentioned by the tools, name the likely answer on the player's team when evidence supports it.
- When recommending a different move or switch, say whether it is engine-backed, evidence-backed inference, or uncertain.
- Separate player-choice issues from opponent-model uncertainty.
- Do not invent Pokemon, moves, items, abilities, replay facts, or statistics.
- If the available tools do not expose legal move options, damage rolls, or meta sets, state that limitation instead of pretending certainty.
- Treat this as post-game coaching, not live ladder assistance.
- Keep the answer concise but actionable, aiming for 350-550 words unless the tool evidence requires more.

Final answer format:
1. Where the battle slipped
2. What I should have clicked
3. Opponent answer chart
4. Next-time game plan
5. Engine/model uncertainty
"""


PATTERN_AI_SYSTEM_PROMPT = """You are Showdown Copilot's cross-battle pattern analyst.

Ground rules:
- You must base your answer only on deterministic pattern tool output.
- Pattern evidence is review-card data, not proof that the player misplayed.
- If human review labels are present, treat them as user-created conclusions and distinguish them from model-generated analysis.
- Separate player habit, engine/model uncertainty, and field/context pressure.
- Cite concrete turn numbers and opponents from the evidence list.
- Keep team-building advice framed as suggestions unless the evidence includes meta data or simulator/damage-calculator results.
- Keep the answer concise and actionable.

Final answer format:
1. Pattern read
2. Player habit vs engine uncertainty
3. Evidence to review
4. Practice drill
5. Data to collect next
"""


TEAM_COACH_AI_SYSTEM_PROMPT = """You are Showdown Copilot's team performance coach.

Ground rules:
- You must base your answer only on tool outputs.
- Start from get_team_overview; this is the authoritative team-level context.
- Use narrow follow-up tools instead of asking for broad archive dumps.
- Investigate only the Pokemon, battle turns, and evidence buckets needed for the question.
- Separate team performance, player-choice calibration, engine uncertainty, and field-pressure constraints.
- Treat PIMC hidden-info splits, opponent prediction misses, and no-stable-line positions as engine/position uncertainty unless the evidence clearly says otherwise.
- Use Pokemon-specific stats from pokemonProfiles when available.
- Team-building advice must be labeled as suggestions unless meta, simulator, or damage-check evidence is present.
- Do not invent Pokemon, moves, items, abilities, replay facts, or statistics.
- Keep the answer concise but actionable.

Final answer format:
1. Team performance read
2. What is losing games
3. Engine uncertainty vs player-choice issues
4. Pokemon-specific notes
5. Practice focus
6. Team-building suggestions
"""


ANTHROPIC_TEAM_COACH_AI_SYSTEM_PROMPT = """<role>
You are Showdown Copilot's team performance coach.
</role>

<ground_rules>
- Base your answer only on tool outputs supplied in this run.
- Treat get_team_overview as the authoritative team-level table of contents.
- Use narrow follow-up tools instead of requesting broad archive dumps.
- Investigate only the Pokemon, battle turns, and evidence buckets needed for the question.
- Separate team performance, player-choice calibration, engine uncertainty, and field-pressure constraints.
- Treat PIMC hidden-info splits, opponent prediction misses, and no-stable-line positions as engine/position uncertainty unless the evidence clearly says otherwise.
- Use Pokemon-specific stats from pokemonProfiles when available.
- Label team-building advice as suggestions unless meta, simulator, or damage-check evidence is present.
- Do not invent Pokemon, moves, items, abilities, replay facts, or statistics.
</ground_rules>

<tool_policy>
- Start by calling get_team_overview unless the user prompt explicitly says local tool evidence is already supplied for synthesis.
- After get_team_overview, choose the smallest useful set of follow-up tools.
- For Pokemon-specific claims, call get_pokemon_profile before writing the claim.
- For a turn-specific claim, prefer get_team_state_at_turn; call get_battle_window only when nearby context matters.
- For engine-health claims, call get_engine_eval_cases with the relevant kind.
- Stop calling tools once you have enough evidence to answer concisely.
</tool_policy>

<final_answer_format>
1. Team performance read
2. What is losing games
3. Engine uncertainty vs player-choice issues
4. Pokemon-specific notes
5. Practice focus
6. Team-building suggestions
</final_answer_format>
"""


ANTHROPIC_TEAM_COACH_TOOL_DESCRIPTIONS: dict[str, str] = {
    "get_team_overview": (
        "Use this first for team-level orientation. It returns the roster, battle count, win/follow/PV stats, "
        "Pokemon mini profiles, evidence bucket counts, and top review priorities. Use it to decide which "
        "Pokemon, turns, or evidence buckets need deeper inspection."
    ),
    "get_team_bucket_examples": (
        "Use this when a team-wide pattern needs concrete examples before you make a claim. Choose one bucket: "
        "robustIgnoredAdvice for clean player-vs-engine disagreements, pimcSplits for hidden-info uncertainty, "
        "pvMisses for opponent prediction misses, noStableLines for engine positions without a reliable line, "
        "or fieldPressure for hazards/status/contact/residual pressure. Do not treat examples as automatic misplays."
    ),
    "get_pokemon_profile": (
        "Use this before making Pokemon-specific claims. It returns one team member's lead rate, survival, "
        "win-alive rate, faint timing, field pressure, KO credit, recommendation frequency, and disagreement stats. "
        "Use exact species names from get_team_overview.roster."
    ),
    "get_pokemon_battle_timeline": (
        "Use this after get_pokemon_profile when you need to inspect what one Pokemon actually did in a specific "
        "battle. It returns relevant turns, actions, switches, faint/KO context, field pressure, and engine disagreement signals."
    ),
    "get_team_state_at_turn": (
        "Use this for a precise turn-level check. It returns active Pokemon, field/hazard/status context, "
        "engine recommendation, actual player action, opponent prediction, and tactical signals for one turn."
    ),
    "get_battle_window": (
        "Use this when a collapse or turning point needs nearby context. It returns a compact window of turns around "
        "a key turn. Prefer get_team_state_at_turn first unless the before/after sequence matters."
    ),
    "get_engine_eval_cases": (
        "Use this for engine-quality analysis, not player coaching alone. Filter by pimc_splits, no_stable_lines, "
        "pv_misses, field_pressure, or all. It helps identify whether the engine's advice was unreliable because "
        "of hidden information, low stability, opponent prediction errors, or field-pressure blind spots."
    ),
}


REVIEW_AUTO_LABEL_SYSTEM_PROMPT = """You are Showdown Copilot's review-card classifier.

You must return JSON only. Do not write markdown, commentary, or extra keys.
Use only the supplied deterministic evidence and allowed label IDs.
Do not invent review keys, battle IDs, labels, or tool calls.
Classify each supplied evidence card into exactly one allowed label:
- player_issue
- field_pressure
- engine_uncertainty
- team_issue
- engine_issue
- unclear

Return this shape:
{"labels":[{"reviewKey":"...","label":"field_pressure","confidence":0.78,"reason":"short evidence-based reason"}]}
"""


REVIEW_AUTO_LABEL_RESPONSE_FORMAT: dict[str, Any] = {
    "format": {
        "type": "json_schema",
        "name": "showdown_review_auto_labels",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "labels": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "reviewKey": {
                                "type": "string",
                                "description": "The exact reviewKey from the supplied evidence list.",
                            },
                            "label": {
                                "type": "string",
                                "enum": [
                                    "player_issue",
                                    "field_pressure",
                                    "engine_uncertainty",
                                    "team_issue",
                                    "engine_issue",
                                    "unclear",
                                ],
                            },
                            "confidence": {
                                "type": "number",
                                "description": "Classifier confidence from 0 to 1.",
                            },
                            "reason": {
                                "type": "string",
                                "description": "Short evidence-based reason for the label.",
                            },
                        },
                        "required": ["reviewKey", "label", "confidence", "reason"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["labels"],
            "additionalProperties": False,
        },
    },
}


OPENAI_COACH_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "get_coach_brief",
        "description": "Return the deterministic coach brief for a battle: diagnosis, decision review queue, turning points, practice focus, and data coverage.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "battleId": {"type": "string"},
            },
            "required": ["battleId"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_battle_context",
        "description": "Return turn-level battle context with strategic signals, field state, recommendations, and actual actions.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "battleId": {"type": "string"},
            },
            "required": ["battleId"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_archive_context",
        "description": "Return archive-level context across local finished battles for team and recommendation patterns.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_team_coach_brief",
        "description": "Return team-level coaching context for the team used in the selected battle: team performance, Pokemon profiles, and separated evidence buckets for player calibration, engine uncertainty, no-stable lines, and field pressure.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "battleId": {"type": "string"},
            },
            "required": ["battleId"],
            "additionalProperties": False,
        },
    },
]


OPENAI_TEAM_COACH_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "get_team_overview",
        "description": "Return the compact team table of contents: roster, team summary, Pokemon mini stats, evidence bucket counts, and top review priorities.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "battleId": {"type": "string"},
            },
            "required": ["battleId"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_team_bucket_examples",
        "description": "Return compact examples from one team evidence bucket. Use this to inspect why a pattern exists before making claims.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "battleId": {"type": "string"},
                "bucket": {
                    "type": "string",
                    "enum": [
                        "robustIgnoredAdvice",
                        "pimcSplits",
                        "pvMisses",
                        "noStableLines",
                        "fieldPressure",
                    ],
                },
                "limit": {"type": "integer"},
            },
            "required": ["battleId", "bucket", "limit"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_pokemon_profile",
        "description": "Return one Pokemon's team-performance profile: lead rate, survival, win-alive rate, faint timing, field pressure, KO credit, and recommendation/disagreement stats.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "battleId": {"type": "string"},
                "species": {"type": "string"},
            },
            "required": ["battleId", "species"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_pokemon_battle_timeline",
        "description": "Return what one Pokemon did in one team battle: relevant turns, actions, engine disagreement, field pressure, KO/faint/switch context, and local signals.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "battleId": {"type": "string"},
                "species": {"type": "string"},
                "targetBattleId": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["battleId", "species", "targetBattleId", "limit"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_team_state_at_turn",
        "description": "Return compact board/team state for a specific turn: active Pokemon, hazards/status context, recommendation vs actual, opponent prediction, and nearby tactical signals.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "battleId": {"type": "string"},
                "targetBattleId": {"type": "string"},
                "turn": {"type": "integer"},
            },
            "required": ["battleId", "targetBattleId", "turn"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_battle_window",
        "description": "Return a compact turn window from a team battle so the coach can inspect context before and after a key turn.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "battleId": {"type": "string"},
                "turn": {"type": "integer"},
                "before": {"type": "integer"},
                "after": {"type": "integer"},
            },
            "required": ["battleId", "turn", "before", "after"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_engine_eval_cases",
        "description": "Return compact engine-eval cases for the selected team, filtered by uncertainty or failure type.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "battleId": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": [
                        "all",
                        "pimc_splits",
                        "no_stable_lines",
                        "pv_misses",
                        "field_pressure",
                    ],
                },
                "limit": {"type": "integer"},
            },
            "required": ["battleId", "kind", "limit"],
            "additionalProperties": False,
        },
    },
]


OPENAI_PATTERN_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "get_pattern_context",
        "description": "Return deterministic cross-battle context for one selected pattern panel, including evidence cards and breakdowns.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "patternId": {"type": "string"},
            },
            "required": ["patternId"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_archive_context",
        "description": "Return archive-level context across local finished battles for comparison with the selected pattern.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
]


def pattern_output_token_budget(max_output_tokens: int) -> int:
    return max(900, min(max_output_tokens, PATTERN_SYNTHESIS_MAX_OUTPUT_TOKENS))


def auto_label_output_token_budget(max_output_tokens: int) -> int:
    return max(900, min(max(max_output_tokens, 1800), AUTO_LABEL_MAX_OUTPUT_TOKENS))


def coach_prompt(battle_id: str, preset: dict[str, Any]) -> str:
    return (
        f"Analyze battle {battle_id} as a practical recent-battle review using the available tools. "
        "First call get_coach_brief for this battle and use its reviewQueue as the primary review order. "
        "If the selected preset has advanced or max reasoning depth, call get_battle_context too. "
        "If the selected preset has max depth, call get_archive_context and get_team_coach_brief too. "
        f"Selected preset: {preset.get('label')} / tool depth {preset.get('toolDepth')} / effort {preset.get('effort')}. "
        "After tool calls, explain where the game slipped, what the player should have clicked on the key turns, "
        "which team members answer the opponent's important Pokemon, and how to approach the matchup next time. "
        "Use turn-number evidence and do not reclassify low-confidence cards as high-confidence mistakes."
    )


def coach_final_answer_prompt(battle_id: str, preset: dict[str, Any], tool_calls: list[dict[str, Any]]) -> str:
    called_tools = ", ".join(
        str(call.get("name"))
        for call in tool_calls
        if call.get("name")
    ) or "none"
    return (
        f"Write the final coaching answer for battle {battle_id} now. "
        "Do not call any more tools. Use only the tool outputs already provided in this response chain. "
        f"Tools already called: {called_tools}. "
        f"Selected preset: {preset.get('label')} / effort {preset.get('effort')}. "
        "Follow the required final answer format exactly. Be practical: name the key turns, the better clicks or switches when evidence supports them, "
        "the player's team answers to the opponent's important Pokemon, and the next-time matchup plan. "
        "Cite concrete turn numbers, use reviewQueue labels as authoritative, separate player-choice issues from opponent-model uncertainty, "
        "use team-coach evidence when available, and keep it concise."
    )


def coach_synthesis_prompt(
    battle_id: str,
    preset: dict[str, Any],
    tool_context: list[dict[str, Any]],
) -> str:
    payload = {
        "battleId": battle_id,
        "selectedPreset": {
            "label": preset.get("label"),
            "tier": preset.get("tier"),
            "effort": preset.get("effort"),
            "toolDepth": preset.get("toolDepth"),
        },
        "toolOutputs": tool_context,
    }
    return (
        f"Synthesize the final Showdown Copilot coach answer for battle {battle_id}. "
        "Use only the local tool evidence below. Do not request tools. "
        "Follow the final answer format: Where the battle slipped, What I should have clicked, Opponent answer chart, Next-time game plan, Engine/model uncertainty. "
        "Cite concrete turn numbers and keep the answer concise. "
        "Be practical: name better clicks or switches only when tool evidence supports them. "
        "Use reviewQueue labels as authoritative and do not promote low-confidence cards to high-confidence mistakes. "
        "Use team-coach evidence when present to connect the battle to team-level performance. "
        "Write a complete 250-450 word answer. Do not trail off mid-bullet or mid-sentence. "
        f"Local tool evidence JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def team_coach_prompt(battle_id: str, preset: dict[str, Any]) -> str:
    tier = str(preset.get("tier") or "fast")
    if tier == "max":
        tool_call_budget = 8
    elif tier == "advanced":
        tool_call_budget = 7
    else:
        tool_call_budget = 4
    return (
        f"Analyze the team used in battle {battle_id}. "
        "First call get_team_overview. Then choose narrow tools for evidence: bucket examples, Pokemon profiles, Pokemon battle timelines, team state at a turn, battle windows, or engine eval cases. "
        f"Use at most {tool_call_budget} total tool calls including get_team_overview. "
        "Do not inspect every Pokemon unless the answer truly needs every profile. Prefer 2-3 key Pokemon, 1-2 evidence buckets, and at most one turn/window drilldown. "
        "For Pokemon-specific claims, inspect that Pokemon with get_pokemon_profile and, when needed, get_pokemon_battle_timeline. "
        "For turn-specific claims, inspect get_team_state_at_turn before using a wider get_battle_window. "
        "For follow-up-worthy strategic claims, prefer get_engine_eval_cases over more broad bucket examples. "
        "After enough evidence, stop calling tools and write the final answer. "
        "Do not request broad archive context. "
        f"Selected preset: {preset.get('label')} / tool depth {preset.get('toolDepth')} / effort {preset.get('effort')}. "
        "Write a team-first coaching answer, not a single-battle postmortem."
    )


def anthropic_team_coach_prompt(battle_id: str, preset: dict[str, Any]) -> str:
    max_rounds = int(preset.get("maxToolRounds") or 4)
    return (
        "<task>\n"
        f"Analyze the team used in battle {battle_id}. Write a team-first coaching answer, not a single-battle postmortem.\n"
        "</task>\n\n"
        "<tool_budget>\n"
        f"You may use up to {max_rounds} tool rounds. Start with get_team_overview. "
        "Then pick only the narrow drilldowns that materially improve the answer.\n"
        "</tool_budget>\n\n"
        "<drilldown_policy>\n"
        "- For Pokemon-specific claims, inspect get_pokemon_profile.\n"
        "- For a key turn, inspect get_team_state_at_turn before using get_battle_window.\n"
        "- For recurring uncertainty, inspect get_team_bucket_examples or get_engine_eval_cases.\n"
        "- Do not request broad archive context.\n"
        "</drilldown_policy>\n\n"
        "<selected_preset>\n"
        f"label: {preset.get('label')}\n"
        f"toolDepth: {preset.get('toolDepth')}\n"
        f"effort: {preset.get('effort')}\n"
        "</selected_preset>"
    )


def team_coach_final_answer_prompt(battle_id: str, preset: dict[str, Any], tool_calls: list[dict[str, Any]]) -> str:
    called_tools = ", ".join(
        str(call.get("name"))
        for call in tool_calls
        if call.get("name")
    ) or "none"
    return (
        f"Write the final team-coach answer for the team anchored by battle {battle_id}. "
        "Do not call any more tools. Use only the tool outputs already provided in this response chain. "
        f"Tools already called: {called_tools}. "
        f"Selected preset: {preset.get('label')} / effort {preset.get('effort')}. "
        "Follow the required final answer format exactly. Cite concrete turns or Pokemon stats where available. "
        "Keep player-choice issues separate from engine uncertainty and field pressure."
    )


def team_coach_synthesis_prompt(
    battle_id: str,
    preset: dict[str, Any],
    tool_context: list[dict[str, Any]],
) -> str:
    payload = {
        "anchorBattleId": battle_id,
        "selectedPreset": {
            "label": preset.get("label"),
            "tier": preset.get("tier"),
            "effort": preset.get("effort"),
            "toolDepth": preset.get("toolDepth"),
        },
        "toolOutputs": tool_context,
    }
    return (
        f"Synthesize the final Showdown Copilot team-coach answer for the team anchored by battle {battle_id}. "
        "Use only the local tool evidence below. Do not request tools. "
        "Write a complete 300-500 word answer with these sections: "
        "1. Team performance read, 2. What is losing games, 3. Engine uncertainty vs player-choice issues, "
        "4. Pokemon-specific notes, 5. Practice focus, 6. Team-building suggestions. "
        "Cite team-level counts and concrete turn examples when available. "
        "Do not call PIMC splits, PV misses, or no-stable lines player mistakes. "
        "Mark team-building changes as suggestions unless the evidence includes meta or simulator support.\n\n"
        f"Local team-coach evidence JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def anthropic_team_coach_synthesis_prompt(
    battle_id: str,
    preset: dict[str, Any],
    tool_context: list[dict[str, Any]],
) -> str:
    payload = {
        "anchorBattleId": battle_id,
        "selectedPreset": {
            "label": preset.get("label"),
            "tier": preset.get("tier"),
            "effort": preset.get("effort"),
            "toolDepth": preset.get("toolDepth"),
        },
        "toolOutputs": tool_context,
    }
    return (
        "<task>\n"
        f"Synthesize the final Showdown Copilot team-coach answer for the team anchored by battle {battle_id}.\n"
        "</task>\n\n"
        "<constraints>\n"
        "- Use only the local tool evidence supplied below.\n"
        "- Do not request tools.\n"
        "- Do not call PIMC splits, PV misses, or no-stable lines player mistakes.\n"
        "- Mark team-building changes as suggestions unless the evidence includes meta or simulator support.\n"
        "- Cite team-level counts and concrete turn examples when available.\n"
        "- Write a complete 300-500 word answer and end cleanly.\n"
        "</constraints>\n\n"
        "<local_tool_evidence_json>\n"
        f"{json.dumps(payload, ensure_ascii=False)}\n"
        "</local_tool_evidence_json>"
    )


def pattern_prompt(pattern_id: str, preset: dict[str, Any]) -> str:
    return (
        f"Analyze pattern {pattern_id} using the available tools. "
        "First call get_pattern_context for this pattern. "
        "If the selected preset has max depth, call get_archive_context too. "
        f"Selected preset: {preset.get('label')} / tool depth {preset.get('toolDepth')} / effort {preset.get('effort')}. "
        "Write a pattern-level coaching answer. Separate player habit, engine uncertainty, and field/context pressure. "
        "If human review labels are present, summarize what they imply without overwriting them. "
        "Cite concrete evidence turns from the pattern context."
    )


def pattern_synthesis_prompt(
    pattern_id: str,
    preset: dict[str, Any],
    tool_context: list[dict[str, Any]],
) -> str:
    payload = {
        "patternId": pattern_id,
        "selectedPreset": {
            "label": preset.get("label"),
            "tier": preset.get("tier"),
            "effort": preset.get("effort"),
            "toolDepth": preset.get("toolDepth"),
        },
        "toolOutputs": tool_context,
    }
    return (
        f"Synthesize the final Showdown Copilot pattern analysis for pattern {pattern_id}. "
        "Use only the local deterministic pattern evidence below. Do not request tools. "
        "Write a complete 250-450 word answer with these sections: "
        "1. Pattern read, 2. Player habit vs engine uncertainty, 3. Evidence to review, "
        "4. Practice drill, 5. Data to collect next. "
        "If human review labels are present, mention the label distribution and use it to refine the practice drill. "
        "Cite specific turns and opponents. Do not overclaim team-building advice without external meta or simulator data.\n\n"
        f"Local pattern evidence JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def review_auto_label_prompt(pattern_id: str, preset: dict[str, Any], context: dict[str, Any]) -> str:
    payload = {
        "patternId": pattern_id,
        "selectedPreset": {
            "label": preset.get("label"),
            "tier": preset.get("tier"),
            "effort": preset.get("effort"),
        },
        "context": context,
    }
    return (
        f"Classify the supplied unreviewed Showdown Copilot review cards for pattern {pattern_id}. "
        "Return JSON only. Every output label must use a reviewKey from context.evidence and a label ID from context.allowedLabels. "
        "Keep reasons short and evidence-based. If a card is ambiguous, use unclear.\n\n"
        f"Review-card evidence JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def review_auto_label_repair_prompt(
    pattern_id: str,
    preset: dict[str, Any],
    context: dict[str, Any],
    malformed_text: str,
) -> str:
    payload = {
        "patternId": pattern_id,
        "selectedPreset": {
            "label": preset.get("label"),
            "tier": preset.get("tier"),
            "effort": preset.get("effort"),
        },
        "context": context,
        "malformedModelText": malformed_text[:9000],
    }
    return (
        "Repair the malformed model output into valid JSON only. "
        "Do not add markdown. Do not add labels for review keys that are not in context.evidence. "
        "Use only label IDs from context.allowedLabels. If the malformed text has no usable label, infer the safest label from context evidence. "
        "Return exactly this shape: {\"labels\":[{\"reviewKey\":\"...\",\"label\":\"engine_uncertainty\",\"confidence\":0.7,\"reason\":\"short reason\"}]}.\n\n"
        f"Auto-label repair payload JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )
