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


from showdown_copilot.mechanics_facts import get_hidden_formes


def test_get_hidden_formes_diancie_mega():
    formes = get_hidden_formes("Diancie")
    assert len(formes) == 1
    m = formes[0]
    assert m["name"] == "Diancie-Mega"
    assert m["formeKind"] == "Mega"
    assert m["basis"] == "mega-evolution"
    assert m["types"] == ["Rock", "Fairy"]
    assert m["abilities"] == ["Magic Bounce"]
    assert m["spe"] == 110
    assert m["triggerItem"] == "Diancite"


def test_get_hidden_formes_charizard_two_megas():
    names = {f["name"] for f in get_hidden_formes("Charizard")}
    assert names == {"Charizard-Mega-X", "Charizard-Mega-Y"}
    by_name = {f["name"]: f for f in get_hidden_formes("Charizard")}
    assert by_name["Charizard-Mega-X"]["types"] == ["Fire", "Dragon"]
    assert by_name["Charizard-Mega-Y"]["types"] == ["Fire", "Flying"]
    assert all(f["formeKind"] == "Mega" for f in get_hidden_formes("Charizard"))


def test_get_hidden_formes_urshifu_wildcard_battle_forme():
    formes = get_hidden_formes("Urshifu-*")
    names = {f["name"] for f in formes}
    assert "Urshifu-Rapid-Strike" in names
    rapid = next(f for f in formes if f["name"] == "Urshifu-Rapid-Strike")
    assert rapid["formeKind"] == "Battle"
    assert rapid["basis"] == "team-preview-forme"
    assert rapid["types"] == ["Fighting", "Water"]
    assert rapid["triggerItem"] is None


def test_get_hidden_formes_none_for_plain_species():
    # Kingambit is genuinely forme-less (no Mega, no battle forme). NOTE: do NOT
    # use Garchomp here — Mega Garchomp exists in the NatDex dex.
    assert get_hidden_formes("Kingambit") == []


def test_get_hidden_formes_no_battle_formes_without_wildcard():
    # Ogerpon-Wellspring is a concrete preview species (not a wildcard) and has
    # no Mega; its Tera/other formes must NOT be enumerated.
    assert get_hidden_formes("Ogerpon-Wellspring") == []


def test_get_hidden_formes_unknown_species():
    assert get_hidden_formes("Notarealmon") == []


def test_get_hidden_formes_wildcard_excludes_tera_formes():
    # Exercises the `"Tera" not in forme_name` gate: a wildcard whose base has
    # both battle formes and -Tera variants must yield the battle formes and
    # exclude every -Tera sibling.
    names = {f["name"] for f in get_hidden_formes("Ogerpon-*")}
    assert names == {"Ogerpon-Wellspring", "Ogerpon-Hearthflame", "Ogerpon-Cornerstone"}
    assert not any("Tera" in n for n in names)
