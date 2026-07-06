from showdown_copilot.preview_plan import (
    LeadOption,
    MatchupPlan,
    ThreatItem,
)
from showdown_copilot.preview_verifier import verify_preview_plan


def _plan_with_threat_reason(reason: str, threat_species: str = "Charizard") -> MatchupPlan:
    return MatchupPlan(
        archetype="offense", confidence="medium", summary="s.", winPath="w.",
        recommendedLead=LeadOption(pokemon="Garchomp", rating="safe", reason="lead."),
        backupLeads=[], avoidLeads=[], leadRules=[], preserveTargets=[],
        mainThreats=[ThreatItem(pokemon=threat_species, reason=reason, priority="high")],
        dangerRules=[], earlyPriorities=[], uncertainties=[],
    )


def test_mega_x_typing_claim_not_flagged():
    # Charizard-Mega-X is Fire/Dragon; Rock is 2x. Correct — must NOT be flagged
    # even though base Charizard (Fire/Flying) would make Rock 4x.
    plan = _plan_with_threat_reason("Stealth Rock is 2x into Charizard-Mega-X.")
    issues = verify_preview_plan(plan, ["Charizard"])
    assert issues == []


def test_wrong_mega_typing_claim_still_flagged():
    # Charizard-Mega-X is Fire/Dragon; Rock is 2x, not 4x. Wrong — must be flagged.
    plan = _plan_with_threat_reason("Stealth Rock is 4x into Charizard-Mega-X.")
    issues = verify_preview_plan(plan, ["Charizard"])
    assert any("multiplier" in i.id or "type" in i.id for i in issues)


def test_base_forme_claim_unchanged():
    # Base Charizard is Fire/Flying; Rock is 4x. Correct base claim, no flag.
    plan = _plan_with_threat_reason("Stealth Rock is 4x into Charizard.")
    issues = verify_preview_plan(plan, ["Charizard"])
    assert issues == []
    # And a wrong base claim is still flagged.
    bad = _plan_with_threat_reason("Stealth Rock is 2x into Charizard.")
    assert verify_preview_plan(bad, ["Charizard"]) != []
