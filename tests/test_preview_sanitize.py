from showdown_copilot.preview_verifier import PreviewPlanIssue, sanitize_preview_plan


def make_plan() -> dict:
    return {
        "archetype": "rain offense",
        "confidence": "medium",
        "summary": "Rain team.",
        "winPath": "Preserve the Water answer.",
        "recommendedLead": {"pokemon": "Skarmory", "rating": "safe", "reason": "Info lead."},
        "backupLeads": [],
        "avoidLeads": [],
        "leadRules": [],
        "preserveTargets": [],
        "mainThreats": [
            {"pokemon": "Kingdra", "reason": "Bad claim about immunity.", "priority": "high"},
            {"pokemon": "Pelipper", "reason": "Fine claim.", "priority": "medium"},
        ],
        "dangerRules": [
            {"id": "a", "rule": "Bad rule.", "trigger": {}, "severity": "high"},
            {"id": "b", "rule": "Good rule.", "trigger": {}, "severity": "medium"},
        ],
        "earlyPriorities": [],
        "uncertainties": ["Sets unknown."],
    }


def issue(path: str, reason: str = "wrong") -> PreviewPlanIssue:
    return PreviewPlanIssue(
        id="type_relation_mismatch", path=path, severity="high",
        badClaim="x", reason=reason, repairInstruction="fix",
    )


def test_drops_flagged_list_items_and_appends_uncertainty():
    plan, removed, core = sanitize_preview_plan(
        make_plan(),
        [issue("plan.mainThreats[0].reason"), issue("plan.dangerRules[0].rule")],
    )
    assert [t["pokemon"] for t in plan["mainThreats"]] == ["Pelipper"]
    assert [r["id"] for r in plan["dangerRules"]] == ["b"]
    assert len(removed) == 2
    assert core == []
    assert plan["uncertainties"][-1] == "2 generated claim(s) removed by the mechanics checker."


def test_core_field_issues_pass_through():
    plan, removed, core = sanitize_preview_plan(
        make_plan(),
        [issue("plan.winPath"), issue("plan.mainThreats[1].reason")],
    )
    assert len(core) == 1 and core[0].path == "plan.winPath"
    assert [t["pokemon"] for t in plan["mainThreats"]] == ["Kingdra"]
    assert plan["winPath"] == "Preserve the Water answer."  # untouched; caller repairs


def test_duplicate_indexes_removed_once():
    plan, removed, core = sanitize_preview_plan(
        make_plan(),
        [issue("plan.dangerRules[0].rule"), issue("plan.dangerRules[0].id")],
    )
    assert [r["id"] for r in plan["dangerRules"]] == ["b"]
    assert plan["uncertainties"][-1].startswith("2 generated claim(s)")
