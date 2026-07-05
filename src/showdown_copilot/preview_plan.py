"""Live matchup-preview planning for Showdown Copilot.

This module owns the strategic team-preview plan used by the extension.
It intentionally keeps planning logic out of proxy.py and content.ts:
the proxy only exposes the endpoint, and the extension only renders/uses
the returned structured plan.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from .dashboard_config import coach_preset
from .llm_response import parse_jsonish_model_output, response_text, usage_from_responses
from .mechanics_facts import build_preview_planner_fact_pack
from .preview_repair import merge_plan_and_repair_usage, repair_preview_plan_json
from .preview_verifier import issue_messages, verify_preview_plan

try:
    from fastapi import HTTPException
except ModuleNotFoundError:  # Allows offline evaluator/tests without proxy extras.
    class HTTPException(Exception):  # type: ignore[no-redef]
        def __init__(self, status_code: int, detail: str):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

Confidence = Literal["low", "medium", "high"]
PlanRating = Literal["good", "risky", "violates_plan", "uncertain"]


class PreviewPokemon(BaseModel):
    species: str
    item: str | None = None
    ability: str | None = None
    moves: list[str] = Field(default_factory=list)
    teraType: str | None = None


class LeadRule(BaseModel):
    ifOpponentLead: str
    prefer: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    reason: str


class LeadOption(BaseModel):
    pokemon: str
    rating: Literal["safe", "situational", "risky", "avoid"] = "situational"
    reason: str


class PreserveTarget(BaseModel):
    pokemon: str
    reason: str
    priority: Confidence = "medium"


class ThreatItem(BaseModel):
    pokemon: str
    reason: str
    priority: Confidence = "medium"


class DangerRule(BaseModel):
    id: str
    rule: str
    trigger: dict[str, Any] = Field(default_factory=dict)
    severity: Confidence = "medium"


class MatchupPlan(BaseModel):
    archetype: str
    confidence: Confidence = "medium"
    summary: str
    winPath: str
    recommendedLead: LeadOption
    backupLeads: list[LeadOption] = Field(default_factory=list)
    avoidLeads: list[LeadOption] = Field(default_factory=list)
    leadRules: list[LeadRule] = Field(default_factory=list)
    preserveTargets: list[PreserveTarget] = Field(default_factory=list)
    mainThreats: list[ThreatItem] = Field(default_factory=list)
    dangerRules: list[DangerRule] = Field(default_factory=list)
    earlyPriorities: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


class PreviewPlanRequest(BaseModel):
    battleId: str = "preview"
    format: str = "gen9nationaldex"
    myTeam: list[PreviewPokemon]
    opponentTeam: list[str]
    teamStats: dict[str, Any] = Field(default_factory=dict)
    presetId: str = "anthropic-sonnet-46-high"
    runMode: Literal["fake", "auto", "real"] = "auto"


class PreviewPlanResponse(BaseModel):
    battleId: str
    format: str
    provider: str
    mode: Literal["fake", "auto", "real"]
    source: Literal["model", "fallback"]
    model: str | None = None
    latencyMs: int
    usage: dict[str, Any] = Field(default_factory=dict)
    plan: MatchupPlan
    rawText: str | None = None
    fallbackReason: str | None = None


RAIN_SETTERS = {"pelipper", "politoed"}
RAIN_ABUSERS = {"kingdra", "basculegion", "swampert", "barraskewda", "dracovish", "floatzel"}
SUN_SETTERS = {"torkoal", "ninetales", "charizard-y", "charizardmega-y"}
SUN_ABUSERS = {"venusaur", "charizard", "walkingwake", "roaringmoon", "slitherwing"}
SAND_SETTERS = {"tyranitar", "hippowdon"}
HAZARD_OR_REMOVAL = {
    "gliscor", "corviknight", "excadrill", "greattusk", "skarmory", "claydol",
    "tinglu", "ferrothorn", "clodsire", "landorustherian", "samurotthisui",
}
STATUS_SUPPORT = {"alomomola", "gliscor", "toxapex", "blissey", "clefable", "sableye", "salazzle"}
STALL_CORE = {"alomomola", "gliscor", "toxapex", "blissey", "slowbro", "claydol", "skarmory", "corviknight", "clefable"}
SETUP_THREATS = {
    "volcarona", "gyarados", "kommoo", "dragonite", "ceruledge", "scizor",
    "ogerponwellspring", "terapagos", "zamazenta", "moltrestrategalar", "venusaur",
}


def _norm(value: Any) -> str:
    return "".join(char for char in str(value or "").lower() if char.isalnum())


def _display_map(values: list[str]) -> dict[str, str]:
    return {_norm(value): value for value in values if value}


def _has_move(mon: PreviewPokemon, move_name: str) -> bool:
    wanted = _norm(move_name)
    return any(_norm(move) == wanted for move in mon.moves)


def _moves_matching(mon: PreviewPokemon, names: list[str]) -> list[str]:
    wanted = {_norm(name) for name in names}
    return [move for move in mon.moves if _norm(move) in wanted]


def _first_my_species(req: PreviewPlanRequest, species: str) -> PreviewPokemon | None:
    wanted = _norm(species)
    for mon in req.myTeam:
        if _norm(mon.species) == wanted:
            return mon
    return None


def _fallback_plan(req: PreviewPlanRequest, reason: str | None = None) -> PreviewPlanResponse:
    start = time.perf_counter()
    opp_names = _display_map(req.opponentTeam)
    opp_norms = set(opp_names)

    rain = bool(opp_norms & RAIN_SETTERS) and bool(opp_norms & RAIN_ABUSERS)
    sun = bool(opp_norms & {_norm(item) for item in SUN_SETTERS}) or bool(
        opp_norms & {"torkoal"} and opp_norms & SUN_ABUSERS
    )
    sand = bool(opp_norms & SAND_SETTERS)
    stall_count = len(opp_norms & STALL_CORE)
    status_count = len(opp_norms & STATUS_SUPPORT)

    if rain:
        archetype = "rain offense"
        confidence: Confidence = "high"
        summary = "Opponent preview shows rain setter plus Water sweepers/support."
        win_path = "Preserve the main Water/rain answer while denying free rain-sweeper cleanup."
    elif sun:
        archetype = "sun offense"
        confidence = "high" if "torkoal" in opp_norms else "medium"
        summary = "Opponent preview shows sun pressure and fast offensive threats."
        win_path = "Limit sun turns, avoid free setup, and preserve checks to the boosted attackers."
    elif stall_count >= 3:
        archetype = "bulky stall/control"
        confidence = "high"
        summary = "Opponent preview shows multiple recovery/status/trapping/control pieces."
        win_path = "Create progress before recovery loops stabilize; protect hazard and breaker value."
    elif sand:
        archetype = "sand balance"
        confidence = "medium"
        summary = "Opponent preview shows sand with hazard/removal or bulky support."
        win_path = "Do not overcommit into sand chip; preserve the pieces that punish hazard/removal loops."
    else:
        archetype = "balanced offense"
        confidence = "medium"
        summary = "Opponent preview has mixed offensive and defensive signals."
        win_path = "Scout the lead, avoid losing a key answer early, and convert engine edges into progress."

    def _pick_lead(mon_summaries: list[dict[str, Any]] | None = None) -> PreviewPokemon:
        if mon_summaries:
            ranked = sorted(
                mon_summaries,
                key=lambda row: int(row.get("survives") or 0) + int(row.get("threatens") or 0),
                reverse=True,
            )
            best = _first_my_species(req, str(ranked[0].get("species") or ""))
            if best:
                return best
        return req.myTeam[0] if req.myTeam else PreviewPokemon(species="Unknown")

    mon_summaries = None  # Task 11 threads req.grounding.monSummaries through here.
    recommended = _pick_lead(mon_summaries)
    recommended_lead = LeadOption(
        pokemon=recommended.species,
        rating="situational",
        reason="Heuristic pick: no model plan available; chosen from preview matchup counts."
        if mon_summaries
        else "Heuristic pick: no model plan available; first team slot by default.",
    )

    backup_leads: list[LeadOption] = []
    avoid_leads: list[LeadOption] = []
    lead_rules: list[LeadRule] = []

    preserve_targets: list[PreserveTarget] = []
    for row in (mon_summaries or [])[:6]:
        if int(row.get("threatens") or 0) >= 2 and len(preserve_targets) < 2:
            mon = _first_my_species(req, str(row.get("species") or ""))
            if mon and _norm(mon.species) != _norm(recommended.species):
                preserve_targets.append(PreserveTarget(
                    pokemon=mon.species,
                    priority="medium",
                    reason="Threatens multiple preview opponents; avoid trading it away early.",
                ))

    main_threats: list[ThreatItem] = []
    for norm_name in sorted(opp_norms & (RAIN_ABUSERS | SUN_ABUSERS | SETUP_THREATS)):
        main_threats.append(ThreatItem(
            pokemon=opp_names.get(norm_name, norm_name),
            priority="high" if rain and norm_name in RAIN_ABUSERS else "medium",
            reason="Preview suggests this Pokemon can become a major tempo or cleanup threat.",
        ))
    for norm_name in sorted(opp_norms & STATUS_SUPPORT):
        main_threats.append(ThreatItem(
            pokemon=opp_names.get(norm_name, norm_name),
            priority="medium",
            reason="Can create status, recovery, or disruption loops that change the value of setup turns.",
        ))

    danger_rules: list[DangerRule] = []
    if rain:
        danger_rules.append(DangerRule(
            id="rain_preserve_water_answer",
            severity="high",
            rule="Do not trade away your best answer to the rain attackers for early chip damage.",
            trigger={"oppArchetype": "rain"},
        ))
    if stall_count >= 3:
        danger_rules.append(DangerRule(
            id="stall_no_passive_turns",
            severity="medium",
            rule="Avoid giving free turns to recovery/status loops; make progress before they stabilize.",
            trigger={"oppArchetype": "stall"},
        ))

    early_priorities = [
        "Identify the opponent lead plan before committing a passive setup or hazard turn.",
        "Preserve high-priority matchup answers until the main sweep threats are controlled.",
    ]
    if rain:
        early_priorities.insert(0, "Track rain turns and avoid trading away the main Water answer.")
    if status_count:
        early_priorities.append("Scout status users before spending slow setup turns.")

    uncertainties = [
        "Exact opponent sets, items, EVs, and Tera types are unknown at preview.",
        "Treat the plan as a strategic frame, then update it as moves are revealed.",
    ]

    plan = MatchupPlan(
        archetype=archetype,
        confidence=confidence,
        summary=summary,
        winPath=win_path,
        recommendedLead=recommended_lead,
        backupLeads=backup_leads,
        avoidLeads=avoid_leads,
        leadRules=lead_rules,
        preserveTargets=preserve_targets,
        mainThreats=main_threats[:6],
        dangerRules=danger_rules,
        earlyPriorities=early_priorities[:5],
        uncertainties=uncertainties,
    )
    return PreviewPlanResponse(
        battleId=req.battleId,
        format=req.format,
        provider="local",
        mode=req.runMode,
        source="fallback",
        model=None,
        latencyMs=int((time.perf_counter() - start) * 1000),
        usage={},
        plan=plan,
        fallbackReason=reason,
    )


PREVIEW_SYSTEM_PROMPT = """You are Showdown Copilot's live Pokemon team-preview planner.

Create a concise, structured matchup plan from team preview. You know hidden sets are uncertain.
Use the player's known moves/items/abilities when given. Do not invent exact opponent sets.
Return JSON only. No markdown.

The plan is used live by an extension, so every warning must be actionable and structured.
If you warn about a lead matchup, name the exact moves to prefer/avoid from the player's known set.
Use verifiedMechanics for exact mechanics claims. If a Pokemon ability, typing, move type,
or weather interaction is not in verifiedMechanics, phrase it as uncertain or omit it.
If a known move has dynamicType=true, do not state its exact effective type unless the
needed form/item/tera context is explicitly present.

Mechanics discipline:
- Do not claim a KO/OHKO without a damage calculation. Use "threatens", "pressures", or "likely" when preview-only.
- Do not state type, ability, weather, hazard, or immunity interactions as facts unless the supplied evidence supports them.
- If a mechanic, set, item, or ability is uncertain, put it in uncertainties instead of stating it as fact.
"""


def _preview_user_prompt(req: PreviewPlanRequest) -> str:
    payload = {
        "format": req.format,
        "myTeam": [mon.model_dump(exclude_none=True) for mon in req.myTeam],
        "opponentTeam": req.opponentTeam,
        "teamStats": req.teamStats,
        "verifiedMechanics": build_preview_planner_fact_pack(
            [mon.model_dump(exclude_none=True) for mon in req.myTeam],
            req.opponentTeam,
        ),
        "requiredShape": {
            "archetype": "string",
            "confidence": "low|medium|high",
            "summary": "one sentence",
            "winPath": "one sentence",
            "recommendedLead": {"pokemon": "string", "rating": "safe|situational|risky|avoid", "reason": "string"},
            "backupLeads": [{"pokemon": "string", "rating": "safe|situational|risky|avoid", "reason": "string"}],
            "avoidLeads": [{"pokemon": "string", "rating": "avoid", "reason": "string"}],
            "leadRules": [{"ifOpponentLead": "string", "prefer": ["move"], "avoid": ["move"], "reason": "string"}],
            "preserveTargets": [{"pokemon": "string", "reason": "string", "priority": "low|medium|high"}],
            "mainThreats": [{"pokemon": "string", "reason": "string", "priority": "low|medium|high"}],
            "dangerRules": [{"id": "snake_case", "rule": "string", "trigger": {}, "severity": "low|medium|high"}],
            "earlyPriorities": ["string"],
            "uncertainties": ["string"],
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _coerce_plan(data: Any) -> MatchupPlan:
    if isinstance(data, dict) and isinstance(data.get("plan"), dict):
        data = data["plan"]
    if not isinstance(data, dict):
        raise ValueError("preview model did not return an object")
    return MatchupPlan.model_validate(data)


def _matchup_plan_json_schema() -> dict[str, Any]:
    confidence_enum = ["low", "medium", "high"]
    lead_rating_enum = ["safe", "situational", "risky", "avoid"]
    lead_option = {
        "type": "object",
        "properties": {
            "pokemon": {"type": "string"},
            "rating": {"type": "string", "enum": lead_rating_enum},
            "reason": {"type": "string"},
        },
        "required": ["pokemon", "rating", "reason"],
        "additionalProperties": False,
    }
    lead_rule = {
        "type": "object",
        "properties": {
            "ifOpponentLead": {"type": "string"},
            "prefer": {"type": "array", "items": {"type": "string"}},
            "avoid": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"},
        },
        "required": ["ifOpponentLead", "prefer", "avoid", "reason"],
        "additionalProperties": False,
    }
    preserve_or_threat = {
        "type": "object",
        "properties": {
            "pokemon": {"type": "string"},
            "reason": {"type": "string"},
            "priority": {"type": "string", "enum": confidence_enum},
        },
        "required": ["pokemon", "reason", "priority"],
        "additionalProperties": False,
    }
    danger_rule = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "rule": {"type": "string"},
            "trigger": {
                "type": "object",
                "description": "Small JSON object describing the live condition that activates this rule.",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            "severity": {"type": "string", "enum": confidence_enum},
        },
        "required": ["id", "rule", "trigger", "severity"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "archetype": {"type": "string"},
            "confidence": {"type": "string", "enum": confidence_enum},
            "summary": {"type": "string"},
            "winPath": {"type": "string"},
            "recommendedLead": lead_option,
            "backupLeads": {"type": "array", "items": lead_option},
            "avoidLeads": {"type": "array", "items": lead_option},
            "leadRules": {"type": "array", "items": lead_rule},
            "preserveTargets": {"type": "array", "items": preserve_or_threat},
            "mainThreats": {"type": "array", "items": preserve_or_threat},
            "dangerRules": {"type": "array", "items": danger_rule},
            "earlyPriorities": {"type": "array", "items": {"type": "string"}},
            "uncertainties": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "archetype",
            "confidence",
            "summary",
            "winPath",
            "recommendedLead",
            "backupLeads",
            "avoidLeads",
            "leadRules",
            "preserveTargets",
            "mainThreats",
            "dangerRules",
            "earlyPriorities",
            "uncertainties",
        ],
        "additionalProperties": False,
    }


def _iter_plan_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for child in value.values():
            out.extend(_iter_plan_strings(child))
        return out
    if isinstance(value, list):
        out: list[str] = []
        for child in value:
            out.extend(_iter_plan_strings(child))
        return out
    return []


def model_plan_mechanics_violations(plan: MatchupPlan, opponent_team: list[str]) -> list[str]:
    """Compatibility wrapper for existing tests and evaluator scripts."""
    return issue_messages(verify_preview_plan(plan, opponent_team))


async def _openai_preview_plan(req: PreviewPlanRequest, preset: dict[str, Any]) -> tuple[MatchupPlan, dict[str, Any], str]:
    from .dashboard_agent_service import openai_responses_create

    model = str(preset.get("apiModel") or os.environ.get("SHOWDOWN_OPENAI_FAST_MODEL") or "gpt-5.4-mini")
    response = await openai_responses_create(
        {
            "model": model,
            "instructions": PREVIEW_SYSTEM_PROMPT,
            "input": _preview_user_prompt(req),
            "reasoning": {"effort": preset.get("openaiReasoningEffort") or "medium"},
            "max_output_tokens": int(preset.get("maxOutputTokens") or 1800),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "matchup_plan",
                    "schema": _matchup_plan_json_schema(),
                    "strict": True,
                },
            },
        },
        int(preset.get("timeoutSeconds") or 60),
    )
    text = response_text(response)
    plan = _coerce_plan(parse_jsonish_model_output(text))
    return plan, usage_from_responses([response]), text


def _anthropic_response_text(response: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in response.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts).strip()


async def _anthropic_preview_plan(req: PreviewPlanRequest, preset: dict[str, Any]) -> tuple[MatchupPlan, dict[str, Any], str]:
    from .dashboard_agent_service import anthropic_messages_create, _anthropic_thinking_payload

    model = str(preset.get("apiModel") or os.environ.get("SHOWDOWN_ANTHROPIC_FAST_MODEL") or "claude-haiku-4-5")
    payload = {
        "model": model,
        "system": PREVIEW_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": _preview_user_prompt(req)}],
        "max_tokens": int(preset.get("maxOutputTokens") or 2200),
    }
    if os.environ.get("SHOWDOWN_PREVIEW_USE_THINKING") == "1":
        payload.update(_anthropic_thinking_payload(preset))
    output_config = payload.get("output_config") if isinstance(payload.get("output_config"), dict) else {}
    payload["output_config"] = {
        **output_config,
        "format": {
            "type": "json_schema",
            "schema": _matchup_plan_json_schema(),
        },
    }
    response = await anthropic_messages_create(payload, int(preset.get("timeoutSeconds") or 90))
    text = _anthropic_response_text(response)
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    plan = _coerce_plan(parse_jsonish_model_output(text))
    return plan, {
        "inputTokens": usage.get("input_tokens"),
        "outputTokens": usage.get("output_tokens"),
        "totalTokens": (
            int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
            if usage
            else None
        ),
        "costUsd": None,
    }, text


def _real_provider_available(provider: str) -> bool:
    if provider == "openai":
        return bool(os.environ.get("OPENAI_API_KEY"))
    if provider == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    return False


async def build_preview_plan(req: PreviewPlanRequest) -> PreviewPlanResponse:
    started = time.perf_counter()
    try:
        preset = coach_preset(req.presetId)
    except ValueError:
        preset = coach_preset("anthropic-sonnet-46-high")
    provider = str(preset.get("realProvider") or preset.get("provider") or "openai")
    model = str(preset.get("apiModel") or preset.get("modelLabel") or "")

    should_call_model = req.runMode == "real" or (req.runMode == "auto" and _real_provider_available(provider))
    if req.runMode == "real" and not _real_provider_available(provider):
        raise HTTPException(status_code=503, detail=f"{provider} API key is not configured")
    if not should_call_model:
        return _fallback_plan(req, reason="model provider not configured or fake mode selected")

    try:
        if provider == "openai":
            plan, usage, raw_text = await _openai_preview_plan(req, preset)
        elif provider == "anthropic":
            plan, usage, raw_text = await _anthropic_preview_plan(req, preset)
        else:
            return _fallback_plan(req, reason=f"provider {provider} is not wired for preview planning")
    except HTTPException as exc:
        if req.runMode == "auto":
            detail = getattr(exc, "detail", str(exc))
            return _fallback_plan(req, reason=f"model preview failed: {detail}")
        raise
    except Exception as exc:  # noqa: BLE001 - live preview must degrade cleanly.
        return _fallback_plan(req, reason=f"model preview failed: {exc}")

    my_species = [mon.species for mon in req.myTeam]
    issues = verify_preview_plan(plan, req.opponentTeam, my_species)
    repair_attempts = max(0, int(os.environ.get("SHOWDOWN_PREVIEW_REPAIR_ATTEMPTS", "2")))
    for attempt_index in range(repair_attempts):
        if not issues:
            break
        try:
            repaired_json, repair_usage, repair_raw_text = await repair_preview_plan_json(
                provider=provider,
                preset=preset,
                plan=plan.model_dump(),
                issues=[issue.model_dump() for issue in issues],
                schema=_matchup_plan_json_schema(),
            )
            repaired_plan = _coerce_plan(repaired_json)
            repaired_issues = verify_preview_plan(repaired_plan, req.opponentTeam, my_species)
            if not repaired_issues:
                plan = repaired_plan
                issues = []
                usage = merge_plan_and_repair_usage(usage, repair_usage)
                raw_text = f"{raw_text}\n\n[repair {attempt_index + 1}]\n{repair_raw_text or ''}".strip()
                break
            plan = repaired_plan
            issues = repaired_issues
            usage = merge_plan_and_repair_usage(usage, repair_usage)
            raw_text = f"{raw_text}\n\n[repair {attempt_index + 1} incomplete]\n{repair_raw_text or ''}".strip()
        except Exception as exc:  # noqa: BLE001 - keep live preview degradable.
            return _fallback_plan(req, reason=f"model preview repair failed: {exc}")

    if issues:
        return _fallback_plan(req, reason=f"model mechanics validation failed: {'; '.join(issue_messages(issues))}")

    return PreviewPlanResponse(
        battleId=req.battleId,
        format=req.format,
        provider=provider,
        mode=req.runMode,
        source="model",
        model=model,
        latencyMs=int((time.perf_counter() - started) * 1000),
        usage=usage,
        plan=plan,
        rawText=raw_text,
    )


def preview_plan_quality_checks(plan: MatchupPlan, opponent_team: list[str]) -> list[dict[str, Any]]:
    """Small offline rubric for preview-plan evaluator output."""
    opp_norms = {_norm(item) for item in opponent_team}
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, expected: str) -> None:
        checks.append({"name": name, "passed": passed, "expected": expected})

    plan_text = json.dumps(plan.model_dump(), ensure_ascii=False).lower()
    if "pelipper" in opp_norms and opp_norms & RAIN_ABUSERS:
        add("rain_frame", "rain" in plan.archetype.lower() or "rain" in plan_text, "identify rain pressure")
        add(
            "rain_preserve",
            "preserve" in plan_text or bool(plan.preserveTargets) or any(
                rule.id == "rain_preserve_water_answer" for rule in plan.dangerRules
            ),
            "flag preservation pressure against rain",
        )
    if "torkoal" in opp_norms:
        add("sun_frame", "sun" in plan.archetype.lower() or "sun" in plan_text, "identify sun pressure")
    if len(opp_norms & STALL_CORE) >= 3:
        add("bulky_control_frame", any(word in plan_text for word in ["stall", "bulky", "control", "recovery"]), "identify bulky control/stall")
    if "gliscor" in opp_norms:
        add("gliscor_disruption", any(word in plan_text for word in ["gliscor", "taunt", "toxic"]), "respect Gliscor disruption risk")
    if "alomomola" in opp_norms:
        add("alomomola_status", any(word in plan_text for word in ["alomomola", "toxic", "wish", "status"]), "respect Alomomola status/support")
    return checks
