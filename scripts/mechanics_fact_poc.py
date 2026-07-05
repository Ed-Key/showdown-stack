#!/usr/bin/env python
"""Print a small mechanics-fact proof of concept for preview planning."""
from __future__ import annotations

import json

from showdown_copilot.mechanics_facts import (
    build_preview_fact_pack,
    check_ability_claim,
    check_type_matchup_claim,
    get_move_facts,
)


def main() -> None:
    my_team = [
        {
            "species": "Volcarona",
            "moves": ["Quiver Dance", "Fire Blast", "Bug Buzz", "Giga Drain"],
        },
        {
            "species": "Garchomp",
            "moves": ["Stealth Rock", "Earthquake", "Dragon Tail", "Stone Edge"],
        },
        {
            "species": "Gholdengo",
            "moves": ["Make It Rain", "Shadow Ball", "Recover", "Nasty Plot"],
        },
        {
            "species": "Iron Valiant",
            "moves": ["Close Combat", "Moonblast", "Knock Off", "Encore"],
        },
        {
            "species": "Ogerpon-Wellspring",
            "moves": ["Ivy Cudgel", "Horn Leech", "Swords Dance", "Encore"],
        },
        {
            "species": "Terapagos",
            "moves": ["Terapagos Terastal", "Tera Starstorm", "Rapid Spin", "Calm Mind"],
        },
    ]
    opponent_team = ["Sceptile", "Excadrill", "Azumarill", "Togekiss", "Torkoal", "Galvantula"]

    pack = build_preview_fact_pack(my_team, opponent_team)
    compact_pack = {
        "source": pack["source"],
        "opponentTeam": [
            {
                "name": mon.get("name"),
                "types": mon.get("types"),
                "abilities": mon.get("abilities"),
                "spe": (mon.get("baseStats") or {}).get("spe"),
            }
            for mon in pack["opponentTeam"]
        ],
        "myTeam": [
            {
                "name": mon.get("name"),
                "types": mon.get("types"),
                "abilities": mon.get("abilities"),
                "moves": [
                    {
                        "name": move.get("name") or move.get("query"),
                        "found": move.get("found"),
                        "type": move.get("type"),
                        "category": move.get("category"),
                        "dynamicType": move.get("dynamicType"),
                    }
                    for move in mon.get("knownMoves") or []
                ],
            }
            for mon in pack["myTeam"]
        ],
        "fieldFacts": pack["fieldFacts"],
    }
    checks = [
        check_ability_claim("Sceptile", "Chlorophyll"),
        check_ability_claim("Torkoal", "Drought"),
        check_type_matchup_claim("Grass", "Garchomp", 4.0),
        {
            "claim": "Tera Starstorm is Water-type",
            "verdict": "supported" if get_move_facts("Tera Starstorm").get("type") == "Water" else "false",
            "reason": f"Tera Starstorm's listed type is {get_move_facts('Tera Starstorm').get('type')}.",
            "facts": get_move_facts("Tera Starstorm"),
        },
    ]
    print(json.dumps({"factPack": compact_pack, "claimChecks": checks}, indent=2))


if __name__ == "__main__":
    main()
