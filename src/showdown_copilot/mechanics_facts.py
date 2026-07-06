"""Source-backed Pokemon mechanics facts for coach/planner grounding.

This module is deliberately boring: it exposes positive facts from local
Pokemon data and small generic checks over those facts. It should not encode
one-off anti-hallucination notes such as "Sceptile does not have Chlorophyll";
callers can infer that by checking Sceptile's actual ability pool.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from poke_env.data import GenData


def normalize_key(value: Any) -> str:
    return "".join(char for char in str(value or "").lower() if char.isalnum())


@lru_cache(maxsize=1)
def _gen9() -> GenData:
    return GenData.from_gen(9)


@lru_cache(maxsize=1)
def _species_index() -> dict[str, str]:
    pokedex = _gen9().pokedex
    index: dict[str, str] = {}
    for species_id, entry in pokedex.items():
        index[normalize_key(species_id)] = species_id
        name = entry.get("name")
        if name:
            index[normalize_key(name)] = species_id
    return index


@lru_cache(maxsize=1)
def _move_index() -> dict[str, str]:
    moves = _gen9().moves
    index: dict[str, str] = {}
    for move_id, entry in moves.items():
        index[normalize_key(move_id)] = move_id
        name = entry.get("name")
        if name:
            index[normalize_key(name)] = move_id
    return index


def resolve_species_id(species: str) -> str | None:
    return _species_index().get(normalize_key(species))


def resolve_move_id(move: str) -> str | None:
    return _move_index().get(normalize_key(move))


def get_pokemon_facts(species: str) -> dict[str, Any]:
    species_id = resolve_species_id(species)
    if not species_id:
        return {"query": species, "found": False}

    entry = _gen9().pokedex[species_id]
    abilities = entry.get("abilities") if isinstance(entry.get("abilities"), dict) else {}
    base_stats = entry.get("baseStats") if isinstance(entry.get("baseStats"), dict) else {}
    types = entry.get("types") if isinstance(entry.get("types"), list) else []

    return {
        "query": species,
        "found": True,
        "id": species_id,
        "name": entry.get("name") or species,
        "types": [str(item) for item in types],
        "abilities": [str(item) for item in abilities.values()],
        "baseStats": dict(base_stats),
    }


def get_hidden_formes(species: str) -> list[dict[str, Any]]:
    """Preview-relevant alternate formes of a species: Mega evolutions (always)
    and hidden battle formes reachable behind a team-preview wildcard (e.g.
    "Urshifu-*" -> Urshifu-Rapid-Strike). Pure gen-9 dex lookup; returns [] for
    a forme-less or unknown species.
    """
    raw = str(species or "").strip()
    is_wildcard = raw.endswith("*")
    base_id = resolve_species_id(raw)
    if not base_id:
        return []
    base_entry = _gen9().pokedex.get(base_id) or {}
    formes: list[dict[str, Any]] = []
    for forme_name in base_entry.get("otherFormes") or []:
        forme_id = resolve_species_id(str(forme_name))
        if not forme_id:
            continue
        entry = _gen9().pokedex.get(forme_id) or {}
        forme_tag = str(entry.get("forme") or "")
        is_mega = forme_tag.startswith("Mega")
        if is_mega:
            forme_kind, basis = "Mega", "mega-evolution"
        elif is_wildcard and "Tera" not in str(forme_name):
            forme_kind, basis = "Battle", "team-preview-forme"
        else:
            continue
        stats = entry.get("baseStats") if isinstance(entry.get("baseStats"), dict) else {}
        abilities = entry.get("abilities") if isinstance(entry.get("abilities"), dict) else {}
        formes.append({
            "name": entry.get("name") or str(forme_name),
            "formeKind": forme_kind,
            "basis": basis,
            "types": [str(t) for t in (entry.get("types") or [])],
            "abilities": [str(a) for a in abilities.values()],
            "spe": int(stats.get("spe") or 0),
            "atk": int(stats.get("atk") or 0),
            "spa": int(stats.get("spa") or 0),
            "triggerItem": entry.get("requiredItem"),
        })
    return formes


def get_move_facts(move: str) -> dict[str, Any]:
    move_id = resolve_move_id(move)
    if not move_id:
        return {"query": move, "found": False}

    entry = _gen9().moves[move_id]
    return {
        "query": move,
        "found": True,
        "id": move_id,
        "name": entry.get("name") or move,
        "type": entry.get("type"),
        "category": entry.get("category"),
        "dynamicType": bool(entry.get("onModifyType")),
        "basePower": entry.get("basePower"),
        "priority": entry.get("priority"),
        "target": entry.get("target"),
    }


def type_multiplier(attacking_type: str, defending_types: list[str]) -> float | None:
    """Return damage multiplier for one attacking type into defender typing.

    poke-env's chart is keyed by defending type, then attacking type, e.g.
    chart["DRAGON"]["ICE"] == 2.
    """
    attack = str(attacking_type or "").upper()
    if not attack:
        return None

    chart = _gen9().type_chart
    multiplier = 1.0
    seen = False
    for raw_type in defending_types:
        defend = str(raw_type or "").upper()
        type_row = chart.get(defend)
        if not isinstance(type_row, dict):
            continue
        value = type_row.get(attack)
        if value is None:
            continue
        multiplier *= float(value)
        seen = True
    return multiplier if seen else None


def check_ability_claim(species: str, ability: str) -> dict[str, Any]:
    facts = get_pokemon_facts(species)
    if not facts.get("found"):
        return {
            "claim": f"{species} can have {ability}",
            "verdict": "unknown",
            "reason": f"{species} was not found in the local gen-9 Pokedex.",
            "facts": facts,
        }

    wanted = normalize_key(ability)
    abilities = [str(item) for item in facts.get("abilities") or []]
    has_ability = any(normalize_key(item) == wanted for item in abilities)
    return {
        "claim": f"{facts.get('name') or species} can have {ability}",
        "verdict": "supported" if has_ability else "false",
        "reason": (
            f"{facts.get('name') or species}'s listed abilities include {ability}."
            if has_ability
            else f"{facts.get('name') or species}'s listed abilities are: {', '.join(abilities) or 'none'}."
        ),
        "facts": {
            "species": facts.get("name"),
            "abilities": abilities,
        },
    }


def check_type_matchup_claim(
    attacking_type: str,
    defender_species: str,
    claimed_multiplier: float,
) -> dict[str, Any]:
    facts = get_pokemon_facts(defender_species)
    if not facts.get("found"):
        return {
            "claim": f"{attacking_type} is {claimed_multiplier:g}x into {defender_species}",
            "verdict": "unknown",
            "reason": f"{defender_species} was not found in the local gen-9 Pokedex.",
            "facts": facts,
        }

    actual = type_multiplier(attacking_type, [str(item) for item in facts.get("types") or []])
    if actual is None:
        return {
            "claim": f"{attacking_type} is {claimed_multiplier:g}x into {facts.get('name')}",
            "verdict": "unknown",
            "reason": f"Could not calculate {attacking_type} into {facts.get('types')}.",
            "facts": {"species": facts.get("name"), "types": facts.get("types")},
        }
    supported = abs(actual - float(claimed_multiplier)) < 0.001
    return {
        "claim": f"{attacking_type} is {claimed_multiplier:g}x into {facts.get('name')}",
        "verdict": "supported" if supported else "false",
        "reason": (
            f"{attacking_type} is {actual:g}x into {facts.get('name')} ({'/'.join(facts.get('types') or [])})."
        ),
        "facts": {
            "species": facts.get("name"),
            "types": facts.get("types"),
            "actualMultiplier": actual,
        },
    }


def build_preview_fact_pack(
    my_team: list[dict[str, Any]],
    opponent_team: list[str],
) -> dict[str, Any]:
    """Build compact facts suitable for planner prompts or tool responses."""
    my_facts = []
    for mon in my_team:
        species = str(mon.get("species") or "")
        facts = get_pokemon_facts(species)
        facts["knownMoves"] = [get_move_facts(str(move)) for move in mon.get("moves") or []]
        my_facts.append(facts)

    return {
        "source": "poke-env GenData gen9",
        "myTeam": my_facts,
        "opponentTeam": [get_pokemon_facts(species) for species in opponent_team],
        "fieldFacts": {
            "sun": {
                "boostsMoveTypes": ["Fire"],
                "weakensMoveTypes": ["Water"],
            },
        },
    }


def build_preview_planner_fact_pack(
    my_team: list[dict[str, Any]],
    opponent_team: list[str],
) -> dict[str, Any]:
    """Build a compact fact pack for LLM preview planning prompts."""
    full = build_preview_fact_pack(my_team, opponent_team)

    def compact_mon(mon: dict[str, Any], include_moves: bool = False) -> dict[str, Any]:
        base_stats = mon.get("baseStats") if isinstance(mon.get("baseStats"), dict) else {}
        out: dict[str, Any] = {
            "name": mon.get("name") or mon.get("query"),
            "found": mon.get("found"),
            "types": mon.get("types") or [],
            "abilities": mon.get("abilities") or [],
            "spe": base_stats.get("spe"),
        }
        if include_moves:
            out["knownMoves"] = [
                {
                    "name": move.get("name") or move.get("query"),
                    "found": move.get("found"),
                    "type": move.get("type"),
                    "category": move.get("category"),
                    "dynamicType": move.get("dynamicType"),
                }
                for move in mon.get("knownMoves") or []
            ]
        return out

    return {
        "source": full["source"],
        "opponentTeam": [compact_mon(mon) for mon in full["opponentTeam"]],
        "myTeam": [compact_mon(mon, include_moves=True) for mon in full["myTeam"]],
        "fieldFacts": full["fieldFacts"],
    }
