"""Verification helpers for live team-preview plans.

The preview planner is allowed to make strategic judgments, but exact Pokemon
mechanics need a separate pass. This module inspects generated plan text,
flags mechanics-sensitive claims that conflict with source-backed facts, and
returns repair instructions that a second model call can apply.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from .mechanics_facts import get_hidden_formes, get_pokemon_facts, normalize_key, type_multiplier

IssueSeverity = Literal["low", "medium", "high"]

POKEMON_TYPES = [
    "Normal",
    "Fire",
    "Water",
    "Electric",
    "Grass",
    "Ice",
    "Fighting",
    "Poison",
    "Ground",
    "Flying",
    "Psychic",
    "Bug",
    "Rock",
    "Ghost",
    "Dragon",
    "Dark",
    "Steel",
    "Fairy",
]


class PreviewPlanIssue(BaseModel):
    id: str
    path: str
    severity: IssueSeverity = "high"
    badClaim: str
    reason: str
    repairInstruction: str
    referenceFacts: list[str] = Field(default_factory=list)


def _plan_to_dict(plan: Any) -> dict[str, Any]:
    if hasattr(plan, "model_dump"):
        data = plan.model_dump()
    else:
        data = plan
    return data if isinstance(data, dict) else {}


def iter_plan_strings(value: Any, path: str = "plan") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        out: list[tuple[str, str]] = []
        for key, child in value.items():
            out.extend(iter_plan_strings(child, f"{path}.{key}"))
        return out
    if isinstance(value, list):
        out = []
        for index, child in enumerate(value):
            out.extend(iter_plan_strings(child, f"{path}[{index}]"))
        return out
    return []


def _has_negation_or_uncertainty(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "does not",
            "do not",
            "don't",
            "not assume",
            "not active",
            "without",
            "no ",
            "unknown",
            "uncertain",
            "possible",
            "may",
            "could",
        )
    )


def _has_sand_evidence(opponent_team: list[str]) -> bool:
    sand_setters = {"tyranitar", "hippowdon", "gigalith"}
    return bool({normalize_key(item) for item in opponent_team} & sand_setters)


def _hidden_forme_names(known_species: list[str]) -> list[str]:
    names: list[str] = []
    for species in known_species:
        try:
            for forme in get_hidden_formes(species):
                name = str(forme.get("name") or "")
                if name:
                    names.append(name)
        except Exception:  # noqa: BLE001 - forme lookup must never break verification
            continue
    return names


def _species_applies(species: str, text: str, all_names: list[str]) -> bool:
    """True if `species` matches `text` and is the most specific match — i.e. no
    longer registered name that contains it also matches this text. Prevents a
    base name ('Charizard') from matching inside a forme mention ('Charizard-Mega-X')."""
    sn = normalize_key(species)
    nt = normalize_key(text)
    if not sn or sn not in nt:
        return False
    for other in all_names:
        on = normalize_key(other)
        if len(on) > len(sn) and sn in on and on in nt:
            return False
    return True


def _known_species_names(plan_dict: dict[str, Any], known_species: list[str]) -> list[str]:
    names: dict[str, str] = {}
    for species in known_species:
        facts = get_pokemon_facts(species)
        if facts.get("found"):
            name = str(facts.get("name") or species)
            names[normalize_key(name)] = name
        elif species:
            names[normalize_key(species)] = species

    # Include obvious Pokemon names that are present in structured Pokemon fields.
    for _path, text in iter_plan_strings(plan_dict):
        if not text:
            continue
        # This is intentionally conservative. Full entity extraction can come
        # later; the caller should pass preview teams for live use.
        for species in known_species:
            if normalize_key(species) and normalize_key(species) in normalize_key(text):
                names[normalize_key(species)] = species

    for forme_name in _hidden_forme_names(known_species):
        for _path, text in iter_plan_strings(plan_dict):
            if normalize_key(forme_name) and normalize_key(forme_name) in normalize_key(text):
                names[normalize_key(forme_name)] = forme_name
                break
    return sorted(names.values(), key=len, reverse=True)


def _parse_multiplier(raw: str) -> float | None:
    token = raw.strip().lower()
    if token in {"1/4", ".25", "0.25"}:
        return 0.25
    if token in {"1/2", ".5", "0.5"}:
        return 0.5
    try:
        return float(token)
    except ValueError:
        return None


def _clauses_for_species(text: str, species: str) -> list[str]:
    clauses = re.split(r"(?i)[.;]|—|\band\b|\bbut\b", text)
    matches = [clause for clause in clauses if normalize_key(species) in normalize_key(clause)]
    return matches or [text]


def _type_relation_claim(text: str, attack_type: str) -> tuple[str, str] | None:
    type_name = re.escape(attack_type.lower())
    patterns: list[tuple[str, str, str]] = [
        (
            "immune",
            "0x",
            rf"\b(?:immune|immunity)\b\s+(?:to|from)?\s*(?:[\w-]+\s+){{0,3}}{type_name}\b|"
            rf"\b{type_name}\b(?:\s+[\w-]+){{0,1}}\s+\b(?:immune|immunity)\b",
        ),
        (
            "resists",
            "less than 1x",
            rf"\b(?:resist|resists|resisted|resistance)\b\s+(?:to|from)?\s*(?:[\w-]+\s+){{0,3}}{type_name}\b|"
            rf"\b{type_name}\b(?:\s+[\w-]+){{0,1}}\s+\b(?:resist|resists|resisted|resistance)\b",
        ),
        (
            "weak",
            "greater than 1x",
            rf"\b(?:weak|weakness|vulnerable)\b\s+(?:to|from)?\s*(?:[\w-]+\s+){{0,3}}{type_name}\b|"
            rf"\b{type_name}\b(?:\s+[\w-]+){{0,1}}\s+\b(?:weak|weakness|vulnerable)\b|"
            rf"\b(?:super\s+effective)\b\s+(?:against|into|on)?\s*(?:[\w-]+\s+){{0,3}}{type_name}\b|"
            rf"\b{type_name}\b(?:\s+[\w-]+){{0,1}}\s+super\s+effective",
        ),
    ]
    lowered = text.lower()
    for relation, expected, pattern in patterns:
        if re.search(pattern, lowered):
            return relation, expected
    return None


def _type_multiplier_issues(
    plan_dict: dict[str, Any],
    known_species: list[str],
) -> list[PreviewPlanIssue]:
    issues: list[PreviewPlanIssue] = []
    species_names = _known_species_names(plan_dict, known_species)
    multiplier_pattern = re.compile(r"(?P<mult>(?:0?\.\d+)|(?:\d+(?:\.\d+)?)|(?:1/2)|(?:1/4))\s*x", re.I)

    for path, text in iter_plan_strings(plan_dict):
        lowered = text.lower()
        found_multipliers = list(multiplier_pattern.finditer(text)) if "x" in lowered else []
        if found_multipliers:
            for species in species_names:
                facts = get_pokemon_facts(species)
                if not facts.get("found") or not _species_applies(species, text, species_names):
                    continue
                defender_types = [str(item) for item in facts.get("types") or []]
                for attack_type in POKEMON_TYPES:
                    if attack_type.lower() not in lowered:
                        continue
                    actual = type_multiplier(attack_type, defender_types)
                    if actual is None:
                        continue
                    for match in found_multipliers:
                        claimed = _parse_multiplier(match.group("mult"))
                        if claimed is None or abs(claimed - actual) < 0.001:
                            continue
                        issues.append(PreviewPlanIssue(
                            id="type_multiplier_mismatch",
                            path=path,
                            severity="high",
                            badClaim=text,
                            reason=(
                                f"{facts.get('name') or species} is {'/'.join(defender_types)}; "
                                f"{attack_type} is {actual:g}x, not {claimed:g}x."
                            ),
                            repairInstruction=(
                                f"Rewrite the claim using {attack_type} as {actual:g}x into "
                                f"{facts.get('name') or species}, or remove the multiplier."
                            ),
                            referenceFacts=[
                                f"{facts.get('name') or species} types: {', '.join(defender_types)}.",
                                f"{attack_type} multiplier into {'/'.join(defender_types)}: {actual:g}x.",
                            ],
                        ))
                        break

        for species in species_names:
            facts = get_pokemon_facts(species)
            if not facts.get("found") or not _species_applies(species, text, species_names):
                continue
            defender_types = [str(item) for item in facts.get("types") or []]
            for clause in _clauses_for_species(text, species):
                for attack_type in POKEMON_TYPES:
                    relation_claim = _type_relation_claim(clause, attack_type)
                    if relation_claim is None:
                        continue
                    actual = type_multiplier(attack_type, defender_types)
                    if actual is None:
                        continue
                    relation, expected = relation_claim
                    if relation == "immune":
                        bad = actual != 0
                    elif relation == "resists":
                        bad = actual >= 1
                    else:
                        bad = actual <= 1
                    if not bad:
                        continue
                    issues.append(PreviewPlanIssue(
                        id="type_relation_mismatch",
                        path=path,
                        severity="high",
                        badClaim=text,
                        reason=(
                            f"{facts.get('name') or species} is {'/'.join(defender_types)}; "
                            f"{attack_type} is {actual:g}x, so the '{relation}' claim is unsupported."
                        ),
                        repairInstruction=(
                            f"Rewrite the claim using {attack_type} as {actual:g}x into "
                            f"{facts.get('name') or species}, or remove the type relationship."
                        ),
                        referenceFacts=[
                            f"{facts.get('name') or species} types: {', '.join(defender_types)}.",
                            f"{attack_type} multiplier into {'/'.join(defender_types)}: {actual:g}x.",
                            f"A '{relation}' claim would require {expected}.",
                        ],
                    ))
    return issues


def verify_preview_plan(
    plan: Any,
    opponent_team: list[str],
    my_team: list[str] | None = None,
) -> list[PreviewPlanIssue]:
    plan_dict = _plan_to_dict(plan)
    known_species = list(dict.fromkeys([*(my_team or []), *opponent_team]))
    issues: list[PreviewPlanIssue] = []

    issues.extend(_type_multiplier_issues(plan_dict, known_species))

    has_sand = _has_sand_evidence(opponent_team)
    for path, text in iter_plan_strings(plan_dict):
        lowered = text.lower()

        if "permanent sun" in lowered:
            issues.append(PreviewPlanIssue(
                id="permanent_sun_claim",
                path=path,
                severity="medium",
                badClaim=text,
                reason="Drought creates turn-limited harsh sunlight; do not call it permanent without explicit evidence.",
                repairInstruction="Rewrite as turn-limited sun pressure from Drought.",
                referenceFacts=[
                    "Drought creates harsh sunlight when the Pokemon enters battle.",
                    "Weather from Drought is turn-limited by battle mechanics unless extended by item/context.",
                ],
            ))

        if (
            "good as gold" in lowered
            and "sticky web" in lowered
            and any(word in lowered for word in ("block", "prevent", "deny", "stop"))
            and not _has_negation_or_uncertainty(lowered)
        ):
            issues.append(PreviewPlanIssue(
                id="good_as_gold_sticky_web",
                path=path,
                severity="high",
                badClaim=text,
                reason="Good as Gold does not prevent Sticky Web from being set because Sticky Web is an entry hazard placed on a side.",
                repairInstruction="Remove the claim that Good as Gold blocks Sticky Web setup. If useful, say Gholdengo may matter for other hazard-control interactions.",
                referenceFacts=[
                    "Good as Gold protects the Pokemon from status moves that affect it.",
                    "Sticky Web is an entry hazard placed on the opposing side of the field.",
                    "Good as Gold does not block Sticky Web setup.",
                ],
            ))

        if "excadrill" in lowered and "sun abuser" in lowered:
            issues.append(PreviewPlanIssue(
                id="excadrill_sun_abuser",
                path=path,
                severity="high",
                badClaim=text,
                reason="Excadrill is not a sun abuser by default; sun does not activate Sand Rush.",
                repairInstruction="Describe Excadrill by known typing/speed/possible abilities, not as a sun abuser.",
                referenceFacts=[
                    "Sand Rush doubles Speed only during sandstorm.",
                    "Drought creates sun, not sandstorm.",
                ],
            ))

        if "sand rush" in lowered and not has_sand and not _has_negation_or_uncertainty(lowered):
            issues.append(PreviewPlanIssue(
                id="sand_rush_without_sand",
                path=path,
                severity="medium",
                badClaim=text,
                reason="Sand Rush should not be treated as active without sand evidence.",
                repairInstruction="Move Sand Rush to uncertainties or state that sand speed should not be assumed without sand.",
                referenceFacts=[
                    "Sand Rush doubles Speed only during sandstorm.",
                    "No sand setter is present in the supplied opponent preview.",
                ],
            ))

        if (
            "ogerpon" in lowered
            and "azumarill" in lowered
            and "water" in lowered
            and any(word in lowered for word in ("threat", "risky", "avoid", "pressure", "ko"))
            and not any(word in lowered for word in ("water absorb", "heal", "non-water", "fairy", "play rough", "sap sipper"))
        ):
            issues.append(PreviewPlanIssue(
                id="azumarill_water_ogerpon",
                path=path,
                severity="high",
                badClaim=text,
                reason="Ogerpon-Wellspring has Water Absorb, so Azumarill Water-type moves are not the direct Ogerpon issue.",
                repairInstruction="Frame Azumarill risk around non-Water coverage, setup, Sap Sipper uncertainty, item disruption, or chip.",
                referenceFacts=[
                    "Ogerpon-Wellspring has Water Absorb.",
                    "Water Absorb heals from Water-type moves instead of taking damage.",
                    "Azumarill can still threaten through non-Water options depending on its set.",
                ],
            ))

        if "sticky web" in lowered and any(word in lowered for word in ("halve", "halves", "halved", "half speed")):
            issues.append(PreviewPlanIssue(
                id="sticky_web_halves_speed",
                path=path,
                severity="medium",
                badClaim=text,
                reason="Sticky Web lowers Speed by one stage on switch-in; do not describe it as halving Speed.",
                repairInstruction="Rewrite as Sticky Web lowers the Speed stage of grounded switch-ins.",
                referenceFacts=[
                    "Sticky Web is an entry hazard.",
                    "When a grounded Pokemon switches in, Sticky Web lowers its Speed by one stage.",
                ],
            ))

    # De-duplicate repeated identical findings from nested plan strings.
    unique: dict[tuple[str, str], PreviewPlanIssue] = {}
    for issue in issues:
        unique[(issue.id, issue.path)] = issue
    return list(unique.values())


_SANITIZE_LIST_FIELDS = {
    "dangerRules", "mainThreats", "preserveTargets", "leadRules",
    "backupLeads", "avoidLeads", "earlyPriorities", "uncertainties",
}
_PATH_ITEM_RE = re.compile(r"^plan\.(?P<field>[A-Za-z]+)\[(?P<index>\d+)\]")


def sanitize_preview_plan(
    plan: Any,
    issues: list[PreviewPlanIssue],
) -> tuple[dict[str, Any], list[str], list[PreviewPlanIssue]]:
    """Drop flagged list items instead of rejecting the whole plan.

    Issues whose path points into a list field are resolved by removing that
    item; everything else (summary, winPath, recommendedLead, archetype) is
    returned as a core issue for the caller's single repair pass. Returns a
    plain dict so preview_plan.py can re-validate without a circular import.
    """
    data = dict(_plan_to_dict(plan))  # shallow copy; list fields replaced below
    removals: dict[str, set[int]] = {}
    removed_messages: list[str] = []
    core_issues: list[PreviewPlanIssue] = []

    for issue in issues:
        match = _PATH_ITEM_RE.match(issue.path or "")
        field = match.group("field") if match else None
        if match and field in _SANITIZE_LIST_FIELDS:
            removals.setdefault(field, set()).add(int(match.group("index")))
            removed_messages.append(issue.reason)
        else:
            core_issues.append(issue)

    for field, indexes in removals.items():
        items = list(data.get(field) or [])
        data[field] = [item for position, item in enumerate(items) if position not in indexes]

    if removed_messages:
        data["uncertainties"] = [
            *(data.get("uncertainties") or []),
            f"{len(removed_messages)} generated claim(s) removed by the mechanics checker.",
        ]
    return data, removed_messages, core_issues


def issue_messages(issues: list[PreviewPlanIssue]) -> list[str]:
    return [issue.reason for issue in issues]
