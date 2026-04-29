"""Tests for Plan H Phase 2 belief data + rollback wiring.

Covers:
- New OpponentBelief fields (speed_range, item_inferred_choicescarf,
  speed_observations) and their defaults
- BeliefTracker.infer_choicescarf
- BeliefTracker._recompute_speed_range_no_scarf (incl. degenerate guard)
- Rollback wiring in on_reveal_item, on_item_swapped, on_move (R1 branches)
- can_have_speed_modified helper
"""
from __future__ import annotations

import pytest

from showdown_copilot.belief import (
    BeliefTracker,
    OpponentBelief,
    _NON_SCARF_CHOICE,
    _SPEED_HI_SENTINEL,
    can_have_speed_modified,
)


# ----- Task 3: New field defaults -----


def test_belief_speed_fields_default_None_or_empty():
    b = OpponentBelief(species="garchomp")
    assert b.speed_range is None
    assert b.item_inferred_choicescarf is False
    assert b.speed_observations == []


# ----- Task 4: infer_choicescarf -----


def test_infer_choicescarf_sets_flag_and_impossible_items():
    t = BeliefTracker()
    t.infer_choicescarf("garchomp")
    b = t.get("garchomp")
    assert b.item_inferred_choicescarf is True
    assert "choiceband" in b.impossible_items
    assert "choicespecs" in b.impossible_items


def test_infer_choicescarf_idempotent():
    """Calling twice doesn't re-add or duplicate."""
    t = BeliefTracker()
    t.infer_choicescarf("garchomp")
    t.infer_choicescarf("garchomp")
    b = t.get("garchomp")
    assert b.item_inferred_choicescarf is True
    assert b.impossible_items == {"choiceband", "choicespecs"}


# ----- Task 4: _recompute_speed_range_no_scarf -----


def test_recompute_no_scarf_no_observations_clears_state():
    t = BeliefTracker()
    b = t.get("garchomp")
    b.item_inferred_choicescarf = True
    b.speed_range = (395, 393)  # degenerate from prior scarf inference
    t._recompute_speed_range_no_scarf("garchomp")
    assert b.item_inferred_choicescarf is False
    assert b.speed_range is None  # no observations → no range


def test_recompute_no_scarf_only_us_first():
    """Only us_first observations → recompute gives (0, max-1)."""
    t = BeliefTracker()
    b = t.get("garchomp")
    b.speed_observations = [(1, 394, "us_first"), (3, 400, "us_first")]
    b.item_inferred_choicescarf = True
    t._recompute_speed_range_no_scarf("garchomp")
    # min(393, 399) = 393 — tightest us_first wins
    assert b.speed_range == (0, 393)
    assert b.item_inferred_choicescarf is False


def test_recompute_no_scarf_only_opp_first():
    """Only opp_first observations → recompute gives (max+1, sentinel)."""
    t = BeliefTracker()
    b = t.get("garchomp")
    b.speed_observations = [(1, 200, "opp_first"), (3, 250, "opp_first")]
    t._recompute_speed_range_no_scarf("garchomp")
    # max(201, 251) = 251 — tightest opp_first wins
    assert b.speed_range == (251, _SPEED_HI_SENTINEL)


def test_recompute_no_scarf_degenerate_drops_to_None():
    """T-C2 critical: opp_first @ 394 and us_first @ 394 produces
    (395, 393) under non-scarf assumption. Guard drops to None.
    """
    t = BeliefTracker()
    b = t.get("garchomp")
    b.speed_observations = [
        (1, 394, "us_first"),  # gives (0, 393)
        (2, 394, "opp_first"),  # would force (395, 393) — degenerate
    ]
    t._recompute_speed_range_no_scarf("garchomp")
    assert b.speed_range is None  # guard fires
    assert b.item_inferred_choicescarf is False


def test_recompute_no_scarf_skipped_observations_ignored():
    t = BeliefTracker()
    b = t.get("garchomp")
    b.speed_observations = [
        (1, 394, "skipped:cant"),
        (2, 394, "us_first"),
        (3, 394, "skipped:custap"),
    ]
    t._recompute_speed_range_no_scarf("garchomp")
    assert b.speed_range == (0, 393)


# ----- Task 6: rollback wiring -----


def test_rollback_via_on_reveal_item_choiceband():
    """T-R1: positive item reveal of non-scarf Choice item triggers rollback."""
    t = BeliefTracker()
    b = t.get("garchomp")
    b.speed_observations = [(1, 394, "us_first")]
    t.infer_choicescarf("garchomp")
    assert b.item_inferred_choicescarf is True

    t.on_reveal_item("garchomp", "choiceband")

    assert b.item_inferred_choicescarf is False
    assert b.revealed_item == "choiceband"
    # Speed range was recomputed under non-scarf
    assert b.speed_range == (0, 393)


def test_rollback_via_on_reveal_item_choicescarf_no_rollback():
    """Revealing scarf when scarf was inferred is consistent — no rollback."""
    t = BeliefTracker()
    b = t.get("garchomp")
    b.speed_observations = [(1, 394, "us_first")]
    t.infer_choicescarf("garchomp")

    t.on_reveal_item("garchomp", "choicescarf")

    # choicescarf is not in _NON_SCARF_CHOICE → flag stays True
    assert b.item_inferred_choicescarf is True
    assert b.revealed_item == "choicescarf"


def test_rollback_via_on_item_swapped():
    """T-R2: Trick / Switcheroo invalidates everything speed-related."""
    t = BeliefTracker()
    b = t.get("garchomp")
    b.speed_observations = [(1, 394, "us_first")]
    b.speed_range = (0, 393)
    t.infer_choicescarf("garchomp")

    t.on_item_swapped("garchomp", new_item="lifeorb", old_item="choicescarf")

    assert b.speed_range is None
    assert b.item_inferred_choicescarf is False
    assert b.speed_observations == []


def test_rollback_via_on_move_two_different_moves():
    """T-C2: R1 fires inside on_move; rollback must fire too."""
    t = BeliefTracker()
    b = t.get("garchomp")
    # Set up state as if turn 1 us_first happened, scarf inferred.
    b.speed_observations = [(1, 394, "us_first")]
    t.infer_choicescarf("garchomp")
    # Set last_used_move so the next move triggers R1 two-different.
    b.last_used_move = "earthquake"

    # opp uses a different move → R1 + rollback
    t.on_move("garchomp", "stoneedge", split_msg=[])

    assert b.item_inferred_choicescarf is False
    assert "choicescarf" in b.impossible_items
    # Recomputed under non-scarf → us_first observation alone gives (0, 393)
    assert b.speed_range == (0, 393)


def test_rollback_via_on_move_early_disprove():
    """T-C2b: R1 early-disprove path also triggers rollback."""
    t = BeliefTracker()
    b = t.get("garchomp")
    b.speed_observations = [(1, 394, "us_first")]
    t.infer_choicescarf("garchomp")

    # Substitute is in _CHOICE_INCOMPATIBLE_MOVES → early-disprove fires.
    t.on_move("garchomp", "substitute", split_msg=[])

    assert b.item_inferred_choicescarf is False
    assert "choicescarf" in b.impossible_items


# ----- Task 5: can_have_speed_modified -----


def test_can_have_speed_modified_returns_False_when_ability_known():
    """Once ability is known there's no hidden boost possibility."""
    b = OpponentBelief(species="kingdra")
    b.revealed_ability = "swiftswim"
    assert can_have_speed_modified(b, weather="RainDance", terrain=None) is False


def test_can_have_speed_modified_swiftswim_in_rain():
    b = OpponentBelief(species="kingdra")
    assert can_have_speed_modified(b, weather="RainDance", terrain=None) is True


def test_can_have_speed_modified_swiftswim_no_rain_returns_False():
    b = OpponentBelief(species="kingdra")
    # Swift Swim only matters in rain; outside rain the boost doesn't apply.
    # Note: kingdra is also a (no Quick Feet) so no other gates fire.
    assert can_have_speed_modified(b, weather=None, terrain=None) is False


def test_can_have_speed_modified_chlorophyll_in_sun():
    b = OpponentBelief(species="venusaur")
    assert can_have_speed_modified(b, weather="SunnyDay", terrain=None) is True


def test_can_have_speed_modified_sandrush_in_sand():
    b = OpponentBelief(species="excadrill")
    assert can_have_speed_modified(b, weather="Sandstorm", terrain=None) is True


def test_can_have_speed_modified_unburden_after_item_loss():
    """T-K6 partial: Unburden activates when item is lost."""
    b = OpponentBelief(species="hawlucha")
    b.removed_item = "fightingiumz"  # consumed Z-crystal
    assert can_have_speed_modified(b, weather=None, terrain=None) is True


def test_can_have_speed_modified_protosynthesis_in_sun():
    """Phase 2 BUG FIX: foul-play didn't gate on this; we do."""
    b = OpponentBelief(species="roaringmoon")
    assert can_have_speed_modified(b, weather="SunnyDay", terrain=None) is True


def test_can_have_speed_modified_quarkdrive_in_eterrain():
    b = OpponentBelief(species="ironvaliant")
    assert can_have_speed_modified(
        b, weather=None, terrain="ELECTRIC_TERRAIN"
    ) is True


def test_can_have_speed_modified_quarkdrive_with_booster():
    """Booster Energy can activate without sun/eterrain."""
    b = OpponentBelief(species="ironvaliant")
    b.removed_item = "boosterenergy"
    assert can_have_speed_modified(b, weather=None, terrain=None) is True
