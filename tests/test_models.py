from showdown_copilot.models import ModalSet


def test_modal_set_to_pokemon_spec_basic():
    ms = ModalSet(
        species="kingambit",
        level=100,
        types=["Dark", "Steel"],
        moves=["kowtowcleave", "suckerpunch", "ironhead", "swordsdance"],
        item="blackglasses",
        ability="supremeoverlord",
        nature="Adamant",
        evs={"hp": 0, "atk": 252, "def": 4, "spa": 0, "spd": 0, "spe": 252},
        ivs={"hp": 31, "atk": 31, "def": 31, "spa": 31, "spd": 31, "spe": 31},
        stats={"hp": 291, "atk": 399, "def": 226, "spa": 200, "spd": 226, "spe": 201},
        tera_type="Fairy",
        weight_kg=120.0,
    )
    spec = ms.to_pokemon_spec()
    assert spec.species == "kingambit"
    assert spec.level == 100
    assert spec.moves == ["kowtowcleave", "suckerpunch", "ironhead", "swordsdance"]
    assert spec.item == "blackglasses"
    assert spec.ability == "supremeoverlord"
    assert spec.tera_type == "Fairy"
    assert spec.stats["hp"] == 291
