"""Tests for the Transform/Imposter overlay in proxy._apply_transform_if_present.

Bug context: opp Ditto with Imposter transforms into our Volcarona, then opp
Teras the Ditto to Ghost. Our proxy was forwarding stale Ditto state (types
still Normal, moves missing) to the engine, which then confidently recommended
Extreme Speed (Normal) — a move that does 0 damage to Ghost. The fix mirrors
poke-env's Pokemon.transform() — copies types, moves, stats, ability, boosts
from the target while keeping HP/status/item/tera native to the transforming
Pokemon.

Reference: hsahovic/poke-env src/poke_env/battle/pokemon.py:615
"""

from showdown_copilot.proxy import _apply_transform_if_present


def _ditto_state(transformed_into=None):
    """Build a minimal Ditto pkmn dict as it appears in engine_request."""
    pkmn = {
        "species": "ditto",
        "types": ["Normal"],
        "hp": 78,
        "maxhp": 237,
        "ability": "imposter",
        "item": "choicescarf",
        "attack": 91,
        "defense": 91,
        "specialAttack": 91,
        "specialDefense": 91,
        "speed": 91,
        "moves": [
            {"id": "transform", "pp": 8, "disabled": False},
            {"id": "none", "pp": 0, "disabled": False},
            {"id": "none", "pp": 0, "disabled": False},
            {"id": "none", "pp": 0, "disabled": False},
        ],
        "status": "None",
        "terastallized": False,
        "teraType": "nothing",
    }
    if transformed_into is not None:
        pkmn["transformedInto"] = transformed_into
    return pkmn


def _volc_transform_payload(tera_ghost: bool = False):
    """The payload the (fixed) extension would forward when opp Ditto
    transforms into our Volcarona. Volc had Quiver Dance up so boosts
    show +1 SpA/SpD/Spe; the user-side caller is responsible for
    propagating those onto sideTwo.boosts."""
    return {
        "species": "volcarona",
        "types": ["Ghost"] if tera_ghost else ["Bug", "Fire"],
        "ability": "flamebody",
        "attack": 112,
        "defense": 167,
        "specialAttack": 369,
        "specialDefense": 246,
        "speed": 328,
        "moves": ["quiverdance", "flamethrower", "bugbuzz", "gigadrain"],
    }


def test_returns_false_when_no_transform_field():
    """Without transformedInto, the helper is a no-op and returns False."""
    pkmn = _ditto_state(transformed_into=None)
    original = dict(pkmn)
    assert _apply_transform_if_present(pkmn) is False
    assert pkmn == original  # untouched


def test_copies_types_from_target():
    """Types overwrite — should match poke-env: target's base dex types."""
    pkmn = _ditto_state(_volc_transform_payload(tera_ghost=False))
    assert _apply_transform_if_present(pkmn) is True
    assert pkmn["types"] == ["Bug", "Fire"]


def test_copies_types_when_tera_ghost():
    """When the extension forwards Tera-aware types (Ghost), they win.
    This is the smoking-gun case: Ditto Tera'd to Ghost via its OWN preview
    Tera, the extension should compute and forward ['Ghost']."""
    pkmn = _ditto_state(_volc_transform_payload(tera_ghost=True))
    assert _apply_transform_if_present(pkmn) is True
    assert pkmn["types"] == ["Ghost"]


def test_copies_moves_with_pp_5():
    """Gen5+ Transform: all copied moves get PP=5 (sim/pokemon.ts:1316)."""
    pkmn = _ditto_state(_volc_transform_payload())
    _apply_transform_if_present(pkmn)
    move_ids = [m["id"] for m in pkmn["moves"]]
    assert move_ids == ["quiverdance", "flamethrower", "bugbuzz", "gigadrain"]
    for m in pkmn["moves"]:
        assert m["pp"] == 5


def test_pads_short_movesets_to_four_slots():
    """If target has fewer than 4 revealed moves, pad with 'none' sentinels."""
    payload = _volc_transform_payload()
    payload["moves"] = ["quiverdance", "flamethrower"]  # only 2 revealed
    pkmn = _ditto_state(payload)
    _apply_transform_if_present(pkmn)
    assert len(pkmn["moves"]) == 4
    assert pkmn["moves"][0]["id"] == "quiverdance"
    assert pkmn["moves"][1]["id"] == "flamethrower"
    assert pkmn["moves"][2]["id"] == "none"
    assert pkmn["moves"][3]["id"] == "none"


def test_copies_stats_except_hp():
    """Transform copies target's atk/def/spa/spd/spe but NOT hp/maxhp.
    This is the critical Ditto-keeps-own-HP rule."""
    pkmn = _ditto_state(_volc_transform_payload())
    hp_before = pkmn["hp"]
    maxhp_before = pkmn["maxhp"]
    _apply_transform_if_present(pkmn)
    assert pkmn["attack"] == 112
    assert pkmn["specialAttack"] == 369
    assert pkmn["speed"] == 328
    assert pkmn["hp"] == hp_before
    assert pkmn["maxhp"] == maxhp_before


def test_copies_ability():
    pkmn = _ditto_state(_volc_transform_payload())
    _apply_transform_if_present(pkmn)
    assert pkmn["ability"] == "flamebody"


def test_preserves_item_and_status():
    """Transform doesn't touch item or status — Ditto keeps its Choice Scarf."""
    pkmn = _ditto_state(_volc_transform_payload())
    pkmn["status"] = "Paralyze"
    _apply_transform_if_present(pkmn)
    assert pkmn["item"] == "choicescarf"
    assert pkmn["status"] == "Paralyze"


def test_preserves_tera_state():
    """Ditto's own Tera state is independent of the target's. The Tera Ghost
    case is handled by the extension passing types=['Ghost'] in the payload,
    NOT by the helper inferring tera from the target."""
    pkmn = _ditto_state(_volc_transform_payload())
    pkmn["terastallized"] = True
    pkmn["teraType"] = "Ghost"
    _apply_transform_if_present(pkmn)
    assert pkmn["terastallized"] is True
    assert pkmn["teraType"] == "Ghost"


def test_empty_payload_is_skipped():
    """Defensive: an empty transformedInto dict (falsy) is treated as 'no
    transform' and leaves the Pokemon untouched. This matches the convention
    used elsewhere in the proxy — empty/missing fields mean 'no data', not
    'data is empty'. Extension should only emit transformedInto when a real
    Transform event has fired."""
    pkmn = _ditto_state({})
    original = dict(pkmn)
    original_moves = list(pkmn["moves"])
    result = _apply_transform_if_present(pkmn)
    assert result is False
    assert pkmn["moves"] == original_moves  # untouched
    assert pkmn["types"] == original["types"]
