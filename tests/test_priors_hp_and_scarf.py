"""Tests for Plan H bug-catalog #4 (Hidden Power belief) and #6 (Choice
Scarf hard-lock when bracket math forces it).

Both fixes live in `priors._modal_set_from_consistent_candidates` /
`priors._select_modal_moves_with_revealed`. We exercise each bug at
the unit level — no chaos files, no battle replay; just synthetic
chaos entries crafted to surface each issue.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from showdown_copilot.belief import OpponentBelief
from showdown_copilot.priors import PriorsSource


@pytest.fixture
def src(tmp_path: Path) -> PriorsSource:
    """A PriorsSource with no chaos files. We only call helper methods that
    don't require loaded data."""
    return PriorsSource(cache_dir=tmp_path)


# =============================================================================
# Bug #4: Hidden Power belief
# =============================================================================


def test_hp_revealed_without_type_matches_chaos_typed_variant(src: PriorsSource) -> None:
    """Showdown's `|move|` event reports 'Hidden Power' (no type), so
    revealed_moves contains `hiddenpower`. Chaos data only has typed
    variants like `hiddenpowerice`. The subset check must NOT reject
    the candidate set — opp clearly has *some* HP move."""
    moves_dist = {
        "psychic": 0.85,
        "psyshock": 0.40,
        "hiddenpowerice": 0.32,
        "thunderbolt": 0.20,
        "icebeam": 0.15,
    }
    revealed = {"hiddenpower"}

    result = src._select_modal_moves_with_revealed(moves_dist, revealed)

    assert result is not None, "fuzzy HP match must not return None"
    assert any(m.startswith("hiddenpower") for m in result), \
        "modal moves must include some hiddenpower variant when revealed has hiddenpower"


def test_hp_revealed_with_type_overrides_chaos_variant(src: PriorsSource) -> None:
    """When revealed_moves contains the actual HP type (e.g.
    `hiddenpowerground` from Showdown's type-effectiveness inference),
    the modal set must surface THAT type, not the chaos top variant.
    Otherwise the engine computes damage with the wrong type."""
    moves_dist = {
        "psychic": 0.85,
        "psyshock": 0.40,
        "hiddenpowerice": 0.32,  # chaos top variant
        "thunderbolt": 0.20,
    }
    revealed = {"hiddenpowerground"}  # actual type the opp is running

    result = src._select_modal_moves_with_revealed(moves_dist, revealed)

    assert result is not None
    assert "hiddenpowerground" in result, \
        "revealed HP type must be preserved, not replaced with chaos's top HP variant"
    assert "hiddenpowerice" not in result, \
        "must not double-count: only one HP variant in the kept list"


def test_hp_in_chaos_but_not_revealed_still_works(src: PriorsSource) -> None:
    """Regression guard: when revealed_moves doesn't contain HP at all,
    the existing top-N pick must still surface chaos's top HP variant
    if it's in the top 4."""
    moves_dist = {
        "psychic": 0.85,
        "hiddenpowerice": 0.50,
        "psyshock": 0.40,
        "thunderbolt": 0.20,
    }
    revealed: set[str] = set()

    result = src._select_modal_moves_with_revealed(moves_dist, revealed)
    assert result is not None
    assert result[0] == "psychic"  # top
    assert "hiddenpowerice" in result


def test_hp_revealed_but_chaos_has_none(src: PriorsSource) -> None:
    """If chaos has no HP variants at all and we revealed `hiddenpower`,
    the subset check should fail (no candidate set is consistent)."""
    moves_dist = {
        "earthquake": 0.85,
        "stoneedge": 0.40,
        "swordsdance": 0.20,
    }
    revealed = {"hiddenpower"}

    result = src._select_modal_moves_with_revealed(moves_dist, revealed)
    assert result is None, "no HP in chaos AND revealed has HP → fall through to unfiltered"


# =============================================================================
# Bug #6: Choice Scarf hard-lock
# =============================================================================


def _build_chaos_entry_with_lifeorb_and_scarf() -> dict:
    """Lando-T-shaped synthetic entry where Life Orb is more popular than
    Scarf in the raw chaos. Without the hard-lock fix, modal item picks LO."""
    return {
        "Moves": {
            "earthquake": 0.95,
            "uturn": 0.80,
            "stoneedge": 0.55,
            "swordsdance": 0.35,
            "stealthrock": 0.20,
        },
        "Items": {
            "lifeorb": 0.50,        # chaos modal in the no-belief case
            "choicescarf": 0.30,
            "rockyhelmet": 0.15,
            "leftovers": 0.05,
        },
        "Abilities": {"intimidate": 1.0},
        "Spreads": {
            "Jolly:0/252/0/0/4/252": 0.60,
            "Adamant:0/252/0/0/4/252": 0.30,
        },
        "Tera Types": {"Steel": 0.5, "Flying": 0.5},
    }


def test_inferred_scarf_locks_item_to_choicescarf(src: PriorsSource) -> None:
    """When `item_inferred_choicescarf=True` (forced by bracket math), the
    modal item MUST be choicescarf. Bug #6 evidence: Mega Diancie outsped
    by Lando-T meant Scarf was bracket-forced; engine kept treating
    Lando's set as Life Orb (chaos modal)."""
    entry = _build_chaos_entry_with_lifeorb_and_scarf()
    belief = OpponentBelief(species="landorustherian")
    belief.item_inferred_choicescarf = True
    # infer_choicescarf side-effects this normally; mirror it manually since
    # we're constructing the belief by hand.
    belief.impossible_items.add("choiceband")
    belief.impossible_items.add("choicespecs")

    modal = src._modal_set_from_consistent_candidates(
        species="landorustherian", entry=entry, belief=belief,
    )

    assert modal is not None, "scarf is in chaos → consistent candidate must exist"
    assert modal.item == "choicescarf", \
        f"item_inferred_choicescarf=True must lock item to choicescarf, got {modal.item}"


def test_inferred_scarf_with_no_scarf_in_chaos_returns_none(src: PriorsSource) -> None:
    """If chaos has no choicescarf for this species, the lock has nothing
    to pick — return None and let the caller fall through to the
    unfiltered modal (we still infer scarf via spread-filter)."""
    entry = _build_chaos_entry_with_lifeorb_and_scarf()
    entry["Items"] = {"lifeorb": 0.5, "rockyhelmet": 0.5}  # no scarf
    belief = OpponentBelief(species="landorustherian")
    belief.item_inferred_choicescarf = True

    modal = src._modal_set_from_consistent_candidates(
        species="landorustherian", entry=entry, belief=belief,
    )
    assert modal is None, "no scarf in chaos → no consistent item under hard-lock"


def test_no_inferred_scarf_keeps_default_modal_item(src: PriorsSource) -> None:
    """Regression guard: when scarf is NOT inferred, the modal item is
    chaos's top item (Life Orb here), preserving prior behavior."""
    entry = _build_chaos_entry_with_lifeorb_and_scarf()
    belief = OpponentBelief(species="landorustherian")
    # item_inferred_choicescarf defaults False

    modal = src._modal_set_from_consistent_candidates(
        species="landorustherian", entry=entry, belief=belief,
    )
    assert modal is not None
    assert modal.item == "lifeorb", \
        "without scarf inference, chaos modal item (Life Orb) wins"
