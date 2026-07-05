from showdown_copilot.mechanics_facts import (
    build_preview_fact_pack,
    build_preview_planner_fact_pack,
    check_ability_claim,
    check_type_matchup_claim,
    get_move_facts,
    get_pokemon_facts,
    type_multiplier,
)


def test_pokemon_facts_are_positive_source_backed_facts():
    facts = get_pokemon_facts("Sceptile")

    assert facts["found"] is True
    assert facts["name"] == "Sceptile"
    assert facts["types"] == ["Grass"]
    assert facts["abilities"] == ["Overgrow", "Unburden"]


def test_generic_ability_claim_check_catches_false_chlorophyll_claim():
    false_claim = check_ability_claim("Sceptile", "Chlorophyll")
    true_claim = check_ability_claim("Torkoal", "Drought")

    assert false_claim["verdict"] == "false"
    assert "Overgrow" in false_claim["reason"]
    assert true_claim["verdict"] == "supported"


def test_type_matchup_check_catches_false_garchomp_grass_claim():
    assert type_multiplier("Grass", ["Dragon", "Ground"]) == 1.0

    claim = check_type_matchup_claim("Grass", "Garchomp", 4.0)

    assert claim["verdict"] == "false"
    assert claim["facts"]["actualMultiplier"] == 1.0


def test_move_facts_are_source_backed():
    tera_starstorm = get_move_facts("Tera Starstorm")
    ivy_cudgel = get_move_facts("Ivy Cudgel")

    assert tera_starstorm["found"] is True
    assert tera_starstorm["type"] == "Normal"
    assert tera_starstorm["category"] == "Special"
    assert tera_starstorm["dynamicType"] is True
    assert ivy_cudgel["type"] == "Grass"
    assert ivy_cudgel["category"] == "Physical"
    assert ivy_cudgel["dynamicType"] is True


def test_preview_fact_pack_includes_known_moves_and_opponent_abilities():
    pack = build_preview_fact_pack(
        my_team=[
            {
                "species": "Garchomp",
                "moves": ["Stealth Rock", "Earthquake", "Dragon Tail", "Stone Edge"],
            }
        ],
        opponent_team=["Sceptile", "Torkoal"],
    )

    assert pack["source"] == "poke-env GenData gen9"
    assert pack["myTeam"][0]["name"] == "Garchomp"
    assert [move["name"] for move in pack["myTeam"][0]["knownMoves"]] == [
        "Stealth Rock",
        "Earthquake",
        "Dragon Tail",
        "Stone Edge",
    ]
    by_name = {mon["name"]: mon for mon in pack["opponentTeam"]}
    assert by_name["Sceptile"]["abilities"] == ["Overgrow", "Unburden"]
    assert "Drought" in by_name["Torkoal"]["abilities"]


def test_preview_planner_fact_pack_is_compact_and_marks_dynamic_moves():
    pack = build_preview_planner_fact_pack(
        my_team=[
            {
                "species": "Terapagos",
                "moves": ["Tera Starstorm", "Rapid Spin"],
            }
        ],
        opponent_team=["Sceptile", "Torkoal"],
    )

    sceptile = next(mon for mon in pack["opponentTeam"] if mon["name"] == "Sceptile")
    assert sceptile == {
        "name": "Sceptile",
        "found": True,
        "types": ["Grass"],
        "abilities": ["Overgrow", "Unburden"],
        "spe": 120,
    }

    move_by_name = {move["name"]: move for move in pack["myTeam"][0]["knownMoves"]}
    assert move_by_name["Tera Starstorm"]["type"] == "Normal"
    assert move_by_name["Tera Starstorm"]["dynamicType"] is True
    assert move_by_name["Rapid Spin"]["dynamicType"] is False
