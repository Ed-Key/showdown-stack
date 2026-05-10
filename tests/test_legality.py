"""Tests for showdown_copilot.legality.

Coverage matrix:
- One positive + one negative test per intrinsic rule.
- Belief reconciliation: revealed_item, impossible_items,
  item_inferred_choicescarf, revealed_ability, impossible_abilities,
  speed_range (scarfed and unscarfed brackets).
- Integration: feed a synthetic Garchomp chaos entry through
  PriorsSource._modal_set_from_consistent_candidates and assert the
  resulting modal isn't a nonsense combination (Choice + setup move).
"""
from __future__ import annotations

import pytest

from showdown_copilot.belief import OpponentBelief
from showdown_copilot.legality import (
    set_makes_sense,
    smogon_set_makes_sense,
)
from showdown_copilot.priors import PriorsSource


def _evs(spe: int = 0, atk: int = 0, spa: int = 0) -> dict:
    """Helper to build a flat EV dict; defaults to zeros."""
    return {"hp": 0, "atk": atk, "def": 0, "spa": spa, "spd": 0, "spe": spe}


# ---------------------------------------------------------------------------
# Intrinsic rules
# ---------------------------------------------------------------------------


# --- Toxic Orb -------------------------------------------------------------

def test_toxic_orb_with_poisonheal_ok():
    assert smogon_set_makes_sense(
        "toxicorb", "poisonheal",
        ["protect", "spore", "leechseed", "substitute"],
        "Careful", _evs(),
    )


def test_toxic_orb_with_random_ability_rejected():
    assert not smogon_set_makes_sense(
        "toxicorb", "intimidate",
        ["earthquake", "stoneedge", "icefang", "fireFang"],
        "Jolly", _evs(spe=252, atk=252),
    )


# --- Flame Orb -------------------------------------------------------------

def test_flame_orb_with_guts_ok():
    assert smogon_set_makes_sense(
        "flameorb", "guts",
        ["facade", "drainpunch", "knockoff", "machpunch"],
        "Adamant", _evs(atk=252),
    )


def test_flame_orb_with_poisonheal_rejected():
    # Poison Heal heals POISON, not BURN — Flame Orb on a Poison Heal
    # mon is dead weight.
    assert not smogon_set_makes_sense(
        "flameorb", "poisonheal", ["protect"], "Careful", _evs(),
    )


# --- Choice items ----------------------------------------------------------

def test_choice_band_with_attacks_only_ok():
    assert smogon_set_makes_sense(
        "choiceband", "moldbreaker",
        ["earthquake", "stoneedge", "ironhead", "firefang"],
        "Adamant", _evs(atk=252, spe=252),
    )


def test_choice_band_with_protect_rejected():
    assert not smogon_set_makes_sense(
        "choiceband", "moldbreaker",
        ["earthquake", "stoneedge", "ironhead", "protect"],
        "Adamant", _evs(atk=252, spe=252),
    )


def test_choice_specs_with_two_status_moves_rejected():
    # Choice tolerates AT MOST 1 illogical move (e.g. one Trick or one
    # surprise status). Two status moves on Specs = nonsense.
    assert not smogon_set_makes_sense(
        "choicespecs", "competitive",
        ["dazzlinggleam", "moonblast", "calmmind", "wish"],
        "Modest", _evs(spa=252, spe=252),
    )


# --- Assault Vest ----------------------------------------------------------

def test_assault_vest_with_all_attacks_ok():
    assert smogon_set_makes_sense(
        "assaultvest", "regenerator",
        ["scald", "futuresight", "psyshock", "flamethrower"],
        "Calm", _evs(),
    )


def test_assault_vest_with_recover_rejected():
    assert not smogon_set_makes_sense(
        "assaultvest", "regenerator",
        ["scald", "recover", "futuresight", "teleport"],
        "Calm", _evs(),
    )


def test_assault_vest_with_klutz_carve_out():
    # Klutz disables the held item — AV constraint doesn't apply.
    assert smogon_set_makes_sense(
        "assaultvest", "klutz",
        ["swordsdance", "knockoff", "uturn", "trick"],
        "Jolly", _evs(atk=252, spe=252),
    )


# --- Poison Heal ability ---------------------------------------------------

def test_poisonheal_with_toxicorb_ok():
    assert smogon_set_makes_sense(
        "toxicorb", "poisonheal",
        ["protect", "substitute", "earthquake", "icepunch"],
        "Careful", _evs(),
    )


def test_poisonheal_without_toxicorb_rejected():
    assert not smogon_set_makes_sense(
        "leftovers", "poisonheal", ["protect"], "Careful", _evs(),
    )


# --- Setup moves -----------------------------------------------------------

def test_swordsdance_with_choice_rejected():
    assert not smogon_set_makes_sense(
        "choiceband", "intimidate",
        ["swordsdance", "earthquake", "stoneedge", "ironhead"],
        "Jolly", _evs(atk=252, spe=252),
    )


def test_swordsdance_predominantly_physical_ok():
    # Up to 1 non-physical move (here: Roost) is allowed alongside SD.
    assert smogon_set_makes_sense(
        "leftovers", "intimidate",
        ["swordsdance", "earthquake", "stoneedge", "roost"],
        "Jolly", _evs(atk=252, spe=252),
    )


def test_nastyplot_with_three_physical_rejected():
    # Nasty Plot + 3 physical = nonsense; SpA boost wasted.
    assert not smogon_set_makes_sense(
        "leftovers", "competitive",
        ["nastyplot", "earthquake", "stoneedge", "knockoff"],
        "Modest", _evs(spa=252, spe=252),
    )


# --- Bulk Up / Curse -------------------------------------------------------

def test_bulkup_with_attacks_ok():
    assert smogon_set_makes_sense(
        "leftovers", "guts",
        ["bulkup", "drainpunch", "knockoff", "machpunch"],
        "Adamant", _evs(atk=252),
    )


def test_bulkup_with_choice_rejected():
    assert not smogon_set_makes_sense(
        "choiceband", "guts",
        ["bulkup", "drainpunch", "knockoff", "machpunch"],
        "Adamant", _evs(atk=252),
    )


def test_bulkup_with_spa_evs_rejected():
    assert not smogon_set_makes_sense(
        "leftovers", "intimidate",
        ["bulkup", "earthquake", "drainpunch", "ironhead"],
        "Adamant", _evs(spa=252),
    )


def test_bulkup_with_modest_nature_rejected():
    assert not smogon_set_makes_sense(
        "leftovers", "intimidate",
        ["bulkup", "earthquake", "drainpunch", "ironhead"],
        "Modest", _evs(atk=252),
    )


# --- Calm Mind -------------------------------------------------------------

def test_calmmind_with_special_attacks_ok():
    assert smogon_set_makes_sense(
        "leftovers", "magicbounce",
        ["calmmind", "psychic", "shadowball", "moonblast"],
        "Timid", _evs(spa=252, spe=252),
    )


def test_calmmind_with_atk_evs_rejected():
    assert not smogon_set_makes_sense(
        "leftovers", "magicbounce",
        ["calmmind", "psychic", "shadowball", "moonblast"],
        "Timid", _evs(atk=252, spe=252),
    )


def test_calmmind_with_adamant_nature_rejected():
    assert not smogon_set_makes_sense(
        "leftovers", "magicbounce",
        ["calmmind", "psychic", "shadowball", "moonblast"],
        "Adamant", _evs(spa=252, spe=252),
    )


def test_calmmind_with_choice_rejected():
    assert not smogon_set_makes_sense(
        "choicespecs", "magicbounce",
        ["calmmind", "psychic", "shadowball", "moonblast"],
        "Timid", _evs(spa=252, spe=252),
    )


# --- Trick / Switcheroo ----------------------------------------------------

def test_trick_with_choice_scarf_ok():
    assert smogon_set_makes_sense(
        "choicescarf", "infiltrator",
        ["trick", "shadowball", "uturn", "dracometeor"],
        "Timid", _evs(spa=252, spe=252),
    )


def test_trick_with_leftovers_rejected():
    # Tricking Leftovers onto the opponent helps THEM, not us — nonsense.
    assert not smogon_set_makes_sense(
        "leftovers", "noability",
        ["trick", "psychic", "focusblast", "shadowball"],
        "Timid", _evs(spa=252, spe=252),
    )


def test_switcheroo_with_choicescarf_ok():
    # Choice Scarf is in TRICKABLE_ITEMS — Switcheroo to dump the lock onto
    # a setup sweeper is a common gimmick.
    assert smogon_set_makes_sense(
        "choicescarf", "prankster",
        ["switcheroo", "thunderwave", "uturn", "knockoff"],
        "Jolly", _evs(spe=252),
    )


def test_flameorb_with_guts_switcheroo_ok():
    # Flame Orb + Guts (orb makes mechanical sense) + Switcheroo
    # (item is trickable) — a real ladder set, must pass.
    assert smogon_set_makes_sense(
        "flameorb", "guts",
        ["switcheroo", "facade", "drainpunch", "knockoff"],
        "Jolly", _evs(atk=252, spe=252),
    )


# ---------------------------------------------------------------------------
# Belief reconciliation
# ---------------------------------------------------------------------------


def _belief(species="garchomp", **kwargs) -> OpponentBelief:
    b = OpponentBelief(species=species)
    for k, v in kwargs.items():
        setattr(b, k, v)
    return b


def test_belief_revealed_item_must_match():
    b = _belief(revealed_item="leftovers")
    # Choice Band candidate vs revealed Leftovers → reject.
    assert not set_makes_sense(
        "choiceband", "moldbreaker",
        ["earthquake", "stoneedge", "ironhead", "firefang"],
        "Adamant", _evs(atk=252, spe=252),
        belief=b,
    )


def test_belief_revealed_item_consistent_passes():
    b = _belief(revealed_item="choiceband")
    assert set_makes_sense(
        "choiceband", "moldbreaker",
        ["earthquake", "stoneedge", "ironhead", "firefang"],
        "Adamant", _evs(atk=252, spe=252),
        belief=b,
    )


def test_belief_impossible_items_blocks():
    b = _belief(impossible_items={"choiceband", "choicescarf", "choicespecs"})
    assert not set_makes_sense(
        "choiceband", "moldbreaker",
        ["earthquake", "stoneedge", "ironhead", "firefang"],
        "Adamant", _evs(atk=252, spe=252),
        belief=b,
    )


def test_belief_impossible_abilities_blocks():
    b = _belief(impossible_abilities={"intimidate"})
    assert not set_makes_sense(
        "leftovers", "intimidate",
        ["earthquake", "stoneedge", "ironhead", "firefang"],
        "Jolly", _evs(atk=252, spe=252),
        belief=b,
    )


def test_belief_revealed_ability_must_match():
    b = _belief(revealed_ability="roughskin")
    assert not set_makes_sense(
        "leftovers", "moldbreaker", ["earthquake"], "Jolly", _evs(),
        belief=b,
    )


def test_belief_forced_choicescarf_rejects_other_items():
    b = _belief(item_inferred_choicescarf=True)
    # Life Orb candidate — bracket math has already proven scarf, reject.
    assert not set_makes_sense(
        "lifeorb", "roughskin",
        ["earthquake", "outrage", "stoneedge", "firefang"],
        "Jolly", _evs(atk=252, spe=252),
        belief=b,
    )
    # Choice Scarf passes the inferred-scarf gate (still subject to other rules).
    assert set_makes_sense(
        "choicescarf", "roughskin",
        ["earthquake", "outrage", "stoneedge", "firefang"],
        "Jolly", _evs(atk=252, spe=252),
        belief=b,
    )


def test_belief_speed_range_unscarfed_branch():
    # Garchomp base 102. 252+ Spe Jolly Garchomp = 328. Bracket [320, 340]
    # accepts the unscarfed bracket.
    b = _belief(speed_range=(320, 340))
    assert set_makes_sense(
        "lifeorb", "roughskin",
        ["earthquake", "outrage", "stoneedge", "firefang"],
        "Jolly", _evs(atk=252, spe=252),
        belief=b,
        base_speed=102,
    )


def test_belief_speed_range_rejects_too_slow():
    # Same Garchomp (328 Spe), but observed bracket is [400, 500] —
    # neither raw nor scarfed bracket matches, reject.
    b = _belief(
        speed_range=(400, 500),
        impossible_items={"choicescarf"},  # rule out scarfed branch entirely
    )
    assert not set_makes_sense(
        "lifeorb", "roughskin",
        ["earthquake", "outrage", "stoneedge", "firefang"],
        "Jolly", _evs(atk=252, spe=252),
        belief=b,
        base_speed=102,
    )


def test_belief_speed_range_skipped_without_base_speed():
    # No base_speed → can't compute, must NOT reject (under-fire safer).
    b = _belief(speed_range=(1, 2))
    assert set_makes_sense(
        "lifeorb", "roughskin",
        ["earthquake"], "Jolly", _evs(atk=252, spe=252),
        belief=b,
        base_speed=None,
    )


# ---------------------------------------------------------------------------
# Integration — _modal_set_from_consistent_candidates must reject nonsense
# ---------------------------------------------------------------------------


@pytest.fixture
def garchomp_choice_setup_chaos(tmp_path):
    """Synthetic chaos entry where the top-of-distribution combination is
    Choice Band + Swords Dance — nonsense (Choice locks moves so SD can
    never be selected). Legality filter must promote a non-Choice item or
    a non-setup moveset, OR fall through cleanly to top-of-distribution.

    Shape mirrors the real chaos files (Items / Abilities / Moves / Spreads
    / Tera Types under data[species]).
    """
    import json

    src = {
        "info": {"number of battles": 1000, "cutoff": 1630},
        "data": {
            "Garchomp": {
                # Choice Band is most-popular but creates a nonsense combo
                # with SD. Heavy-Duty Boots is the next-best legal item.
                "Items": {
                    "choiceband": 0.40,
                    "heavydutyboots": 0.30,
                    "lifeorb": 0.20,
                    "leftovers": 0.10,
                },
                "Abilities": {
                    "roughskin": 0.95,
                    "sandveil": 0.05,
                },
                # Swords Dance is in the top-4 — if we picked CB + this
                # moveset we'd get a legality fail.
                "Moves": {
                    "earthquake": 0.95,
                    "swordsdance": 0.65,
                    "stoneedge": 0.55,
                    "firefang": 0.40,
                    "outrage": 0.20,
                },
                "Spreads": {
                    "Jolly:0/252/0/0/4/252": 0.80,
                    "Adamant:0/252/0/0/4/252": 0.20,
                },
                "Tera Types": {"Steel": 0.40, "Fire": 0.30, "Ground": 0.30},
                "Raw count": 800,
                "usage": 0.30,
            }
        },
    }
    (tmp_path / "gen9ou-1630.json").write_text(json.dumps(src))
    return tmp_path


def test_integration_modal_avoids_choice_plus_swordsdance(
    garchomp_choice_setup_chaos,
):
    """The modal pipeline must NOT emit (CB, SD) as a sensible Garchomp
    set. With Heavy-Duty Boots available as the next-popular legal item,
    the filter should promote it.
    """
    src = PriorsSource(cache_dir=garchomp_choice_setup_chaos)
    belief = OpponentBelief(species="garchomp")
    ms = src.get_set("Garchomp", format="gen9ou", belief=belief)

    if "swordsdance" in ms.moves:
        # SD made the modal moveset → item must NOT be a Choice item.
        assert ms.item not in {"choiceband", "choicespecs", "choicescarf"}, (
            f"Nonsense combo: Choice item {ms.item!r} + Swords Dance"
        )
    else:
        # Filter pruned SD instead — that's also acceptable.
        assert "swordsdance" not in ms.moves


def test_integration_modal_falls_through_when_no_legal_combo_exists(tmp_path):
    """Pathological case: every chaos item × ability combo creates a
    legality fail. The pipeline must NOT crash or return None — it falls
    back to the unfiltered top-of-distribution pick (current behavior,
    safer than empty)."""
    import json

    src = {
        "info": {"number of battles": 1000, "cutoff": 1630},
        "data": {
            "Mewtwo": {
                # Toxic Orb is the ONLY item; Intimidate is the ONLY ability.
                # No abilities in the chaos for Mewtwo trigger Toxic Orb
                # logic → all combos legality-fail. Must fall through.
                "Items": {"toxicorb": 1.0},
                "Abilities": {"pressure": 1.0},
                "Moves": {
                    "psystrike": 0.9, "icebeam": 0.7,
                    "aurasphere": 0.6, "recover": 0.5,
                },
                "Spreads": {"Timid:0/0/4/252/0/252": 1.0},
                "Tera Types": {"Fighting": 1.0},
                "Raw count": 100,
                "usage": 0.05,
            }
        },
    }
    (tmp_path / "gen9ou-1630.json").write_text(json.dumps(src))

    src_obj = PriorsSource(cache_dir=tmp_path)
    belief = OpponentBelief(species="mewtwo")
    ms = src_obj.get_set("Mewtwo", format="gen9ou", belief=belief)

    # We get SOMETHING back (the unfiltered top pick) — never None.
    assert ms is not None
    assert ms.species == "mewtwo"
    assert ms.item == "toxicorb"  # top-of-distribution fallback
    assert ms.ability == "pressure"
