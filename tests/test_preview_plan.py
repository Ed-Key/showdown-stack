import pytest
import json

import showdown_copilot.preview_plan as preview_plan_module
from showdown_copilot.preview_plan import (
    HTTPException,
    LeadOption,
    MatchupPlan,
    PreviewPlanRequest,
    PreviewPokemon,
    _preview_user_prompt,
    build_preview_plan,
    model_plan_mechanics_violations,
)
from showdown_copilot.preview_verifier import verify_preview_plan


def default_team() -> list[PreviewPokemon]:
    return [
        PreviewPokemon(
            species="Garchomp",
            item="Rocky Helmet",
            ability="Rough Skin",
            moves=["Stealth Rock", "Earthquake", "Dragon Tail", "Stone Edge"],
        ),
        PreviewPokemon(
            species="Ogerpon-Wellspring",
            item="Wellspring Mask",
            moves=["Ivy Cudgel", "Horn Leech", "Swords Dance", "Encore"],
        ),
        PreviewPokemon(species="Gholdengo", moves=["Make It Rain", "Shadow Ball", "Recover"]),
        PreviewPokemon(species="Volcarona", moves=["Quiver Dance", "Fire Blast", "Bug Buzz"]),
    ]


@pytest.mark.asyncio
async def test_fake_preview_plan_identifies_rain(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    req = PreviewPlanRequest(
        battleId="battle-test-rain",
        format="gen9nationaldex",
        myTeam=default_team(),
        opponentTeam=["Pelipper", "Basculegion", "Kingdra", "Ferrothorn", "Zapdos", "Barraskewda"],
        runMode="fake",
    )

    result = await build_preview_plan(req)

    assert result.source == "fallback"
    assert result.plan.archetype == "rain offense"
    assert result.plan.recommendedLead.pokemon == "Garchomp"  # first team slot, not hardcoded


@pytest.mark.asyncio
async def test_fake_preview_plan_detects_stall_without_team_specific_rules(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    req = PreviewPlanRequest(
        battleId="battle-test-stall",
        format="gen9nationaldex",
        myTeam=default_team(),
        opponentTeam=["Gliscor", "Alomomola", "Claydol", "Heatran", "Garganacl", "Toxapex"],
        runMode="fake",
    )

    result = await build_preview_plan(req)

    assert result.plan.archetype == "bulky stall/control"
    # No fabricated per-species rules: everything mentioned must exist in the request.
    plan_text = json.dumps(result.plan.model_dump()).lower()
    for absent in ("ogerpon", "volcarona", "gholdengo"):
        assert absent not in plan_text


@pytest.mark.asyncio
async def test_fallback_plan_only_references_request_species(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    req = PreviewPlanRequest(
        battleId="battle-test-generic",
        format="gen9nationaldex",
        myTeam=[
            PreviewPokemon(species="Skarmory", moves=["Spikes", "Roost"]),
            PreviewPokemon(species="Blissey", moves=["Seismic Toss", "Soft-Boiled"]),
        ],
        opponentTeam=["Pelipper", "Kingdra", "Ferrothorn"],
        runMode="fake",
    )

    result = await build_preview_plan(req)

    plan_text = json.dumps(result.plan.model_dump()).lower()
    for absent in ("garchomp", "ogerpon", "volcarona", "gholdengo", "iron valiant", "terapagos"):
        assert absent not in plan_text
    assert result.plan.recommendedLead.pokemon == "Skarmory"


@pytest.mark.asyncio
async def test_auto_preview_plan_falls_back_when_provider_quota_fails(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    async def quota_failure(*_args, **_kwargs):
        raise HTTPException(status_code=502, detail="OpenAI error (insufficient_quota)")

    monkeypatch.setattr("showdown_copilot.preview_plan._openai_preview_plan", quota_failure)
    req = PreviewPlanRequest(
        battleId="battle-test-quota",
        format="gen9nationaldex",
        myTeam=default_team(),
        opponentTeam=["Sceptile", "Excadrill", "Azumarill", "Togekiss", "Torkoal", "Galvantula"],
        presetId="openai-gpt-54-mini-balanced",
        runMode="auto",
    )

    result = await build_preview_plan(req)

    assert result.source == "fallback"
    assert "insufficient_quota" in (result.fallbackReason or "")


def test_model_plan_mechanics_validator_catches_harmful_preview_claims():
    plan = MatchupPlan(
        archetype="Sun Offense",
        confidence="medium",
        summary="Opponent uses Excadrill as a sun abuser.",
        winPath="Prevent Sticky Web with Gholdengo's Good as Gold blocking Sticky Web.",
        recommendedLead=LeadOption(
            pokemon="Garchomp",
            rating="safe",
            reason="Set rocks.",
        ),
        backupLeads=[],
        avoidLeads=[],
        leadRules=[],
        preserveTargets=[],
        mainThreats=[],
        dangerRules=[],
        earlyPriorities=[
            "Avoid Ogerpon-Wellspring because Azumarill Water-type moves threaten it."
        ],
        uncertainties=[],
    )

    violations = model_plan_mechanics_violations(
        plan,
        ["Sceptile", "Excadrill", "Azumarill", "Togekiss", "Torkoal", "Galvantula"],
    )

    assert any("Good as Gold does not prevent Sticky Web" in violation for violation in violations)
    assert any("Excadrill is not a sun abuser" in violation for violation in violations)
    assert any("Ogerpon-Wellspring has Water Absorb" in violation for violation in violations)


def test_preview_verifier_catches_type_and_weather_claims_without_false_sand_warning():
    plan = MatchupPlan(
        archetype="Sun Offense",
        confidence="medium",
        summary="Torkoal creates permanent sun.",
        winPath="Clean with Volcarona after rocks are clear.",
        recommendedLead=LeadOption(
            pokemon="Garchomp",
            rating="safe",
            reason="Set rocks.",
        ),
        backupLeads=[],
        avoidLeads=[],
        leadRules=[],
        preserveTargets=[],
        mainThreats=[
            {
                "pokemon": "Galvantula",
                "reason": "4x Electric damage to Volcarona makes Thunder decisive.",
                "priority": "high",
            },
            {
                "pokemon": "Excadrill",
                "reason": "Excadrill's ability is unknown, so do not assume Sand Rush without sand.",
                "priority": "medium",
            },
        ],
        dangerRules=[
            {
                "id": "sun_fire",
                "rule": "Volcarona has a 4x Fire weakness in sun.",
                "trigger": {},
                "severity": "high",
            }
        ],
        earlyPriorities=[],
        uncertainties=[],
    )

    issues = verify_preview_plan(
        plan,
        ["Sceptile", "Excadrill", "Azumarill", "Togekiss", "Torkoal", "Galvantula"],
        ["Volcarona", "Garchomp", "Gholdengo", "Iron Valiant", "Ogerpon-Wellspring", "Terapagos"],
    )

    issue_ids = [issue.id for issue in issues]
    assert issue_ids.count("type_multiplier_mismatch") == 2
    assert "permanent_sun_claim" in issue_ids
    assert "sand_rush_without_sand" not in issue_ids


def test_preview_verifier_catches_type_relation_claims_without_explicit_multiplier():
    plan = MatchupPlan(
        archetype="Sun Offense",
        confidence="medium",
        summary="Opponent pressures Volcarona.",
        winPath="Use Volcarona carefully.",
        recommendedLead=LeadOption(
            pokemon="Garchomp",
            rating="safe",
            reason="Set rocks.",
        ),
        backupLeads=[],
        avoidLeads=[],
        leadRules=[],
        preserveTargets=[],
        mainThreats=[],
        dangerRules=[
            {
                "id": "bad_fire_relation",
                "rule": "Volcarona loses its Fire resistance advantage in sun.",
                "trigger": {},
                "severity": "medium",
            }
        ],
        earlyPriorities=[],
        uncertainties=[],
    )

    issues = verify_preview_plan(plan, ["Torkoal"], ["Volcarona"])

    assert any(issue.id == "type_relation_mismatch" for issue in issues)
    assert any("Fire is 1x" in issue.reason for issue in issues)


def test_preview_verifier_does_not_confuse_weather_weakens_or_mixed_type_sentence():
    plan = MatchupPlan(
        archetype="Sun Offense",
        confidence="medium",
        summary="Drought boosts Fire moves and weakens Water moves while Togekiss is immune to Ground.",
        winPath="Use Stone Edge into Togekiss if needed.",
        recommendedLead=LeadOption(
            pokemon="Garchomp",
            rating="safe",
            reason="Set rocks.",
        ),
        backupLeads=[],
        avoidLeads=[],
        leadRules=[],
        preserveTargets=[],
        mainThreats=[
            {
                "pokemon": "Togekiss",
                "reason": "Fairy/Flying typing is immune to Ground and threatens Garchomp.",
                "priority": "medium",
            }
        ],
        dangerRules=[],
        earlyPriorities=[],
        uncertainties=[],
    )

    issues = verify_preview_plan(plan, ["Torkoal", "Togekiss"], ["Garchomp"])

    assert issues == []


def test_preview_verifier_catches_sticky_web_halves_speed_claim():
    plan = MatchupPlan(
        archetype="Web Offense",
        confidence="medium",
        summary="Galvantula can set Sticky Web.",
        winPath="Clear Web before sweeping.",
        recommendedLead=LeadOption(
            pokemon="Garchomp",
            rating="safe",
            reason="Set rocks.",
        ),
        backupLeads=[],
        avoidLeads=[],
        leadRules=[],
        preserveTargets=[],
        mainThreats=[
            {
                "pokemon": "Galvantula",
                "reason": "Sticky Web halves Speed for grounded switch-ins.",
                "priority": "high",
            }
        ],
        dangerRules=[],
        earlyPriorities=[],
        uncertainties=[],
    )

    issues = verify_preview_plan(plan, ["Galvantula"], ["Garchomp"])

    assert any(issue.id == "sticky_web_halves_speed" for issue in issues)


@pytest.mark.asyncio
async def test_real_preview_repairs_mechanics_issues_before_fallback(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    bad_plan = MatchupPlan(
        archetype="Sun Offense",
        confidence="medium",
        summary="Torkoal creates permanent sun.",
        winPath="Clean with Volcarona.",
        recommendedLead=LeadOption(
            pokemon="Garchomp",
            rating="safe",
            reason="Set rocks.",
        ),
        backupLeads=[],
        avoidLeads=[],
        leadRules=[],
        preserveTargets=[],
        mainThreats=[
            {
                "pokemon": "Galvantula",
                "reason": "4x Electric damage to Volcarona makes Thunder decisive.",
                "priority": "high",
            }
        ],
        dangerRules=[],
        earlyPriorities=[],
        uncertainties=[],
    )
    repaired_plan = MatchupPlan(
        archetype=bad_plan.archetype,
        confidence=bad_plan.confidence,
        summary="Torkoal creates turn-limited sun.",
        winPath=bad_plan.winPath,
        recommendedLead=bad_plan.recommendedLead,
        backupLeads=[],
        avoidLeads=[],
        leadRules=[],
        preserveTargets=[],
        mainThreats=[
            {
                "pokemon": "Galvantula",
                "reason": "Fast neutral Electric pressure can still matter.",
                "priority": "high",
            }
        ],
        dangerRules=[],
        earlyPriorities=[],
        uncertainties=[],
    )

    async def fake_preview(*_args, **_kwargs):
        return bad_plan, {"inputTokens": 10, "outputTokens": 20, "totalTokens": 30}, "{}"

    async def fake_repair_preview_plan_json(**_kwargs):
        return repaired_plan.model_dump(), {"inputTokens": 3, "outputTokens": 4, "totalTokens": 7}, "{}"

    monkeypatch.setattr(preview_plan_module, "_anthropic_preview_plan", fake_preview)
    monkeypatch.setattr(preview_plan_module, "repair_preview_plan_json", fake_repair_preview_plan_json)

    req = PreviewPlanRequest(
        battleId="battle-test-repair",
        format="gen9nationaldex",
        myTeam=[
            PreviewPokemon(species="Volcarona", moves=["Quiver Dance", "Fire Blast"]),
            PreviewPokemon(species="Garchomp", moves=["Stealth Rock", "Earthquake"]),
        ],
        opponentTeam=["Torkoal", "Galvantula", "Excadrill", "Azumarill", "Sceptile", "Togekiss"],
        presetId="anthropic-sonnet-46-high",
        runMode="real",
    )

    result = await build_preview_plan(req)

    assert result.source == "model"
    assert result.plan.summary == "Torkoal creates turn-limited sun."
    assert result.usage["repair"]["totalTokens"] == 7


def test_preview_prompt_includes_verified_mechanics_fact_pack():
    req = PreviewPlanRequest(
        battleId="battle-test-facts",
        format="gen9nationaldex",
        myTeam=[
            PreviewPokemon(
                species="Garchomp",
                moves=["Stealth Rock", "Earthquake", "Dragon Tail", "Stone Edge"],
            ),
            PreviewPokemon(
                species="Terapagos",
                moves=["Tera Starstorm", "Rapid Spin"],
            ),
        ],
        opponentTeam=["Sceptile", "Torkoal"],
        runMode="fake",
    )

    payload = json.loads(_preview_user_prompt(req))
    mechanics = payload["verifiedMechanics"]
    opponent_by_name = {mon["name"]: mon for mon in mechanics["opponentTeam"]}
    mine_by_name = {mon["name"]: mon for mon in mechanics["myTeam"]}

    assert opponent_by_name["Sceptile"]["abilities"] == ["Overgrow", "Unburden"]
    assert opponent_by_name["Torkoal"]["abilities"] == ["White Smoke", "Drought", "Shell Armor"]
    assert mine_by_name["Garchomp"]["types"] == ["Dragon", "Ground"]
    tera_starstorm = next(
        move for move in mine_by_name["Terapagos"]["knownMoves"]
        if move["name"] == "Tera Starstorm"
    )
    assert tera_starstorm["dynamicType"] is True
    assert "Chlorophyll" not in json.dumps(mechanics)
