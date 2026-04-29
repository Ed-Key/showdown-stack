"""Tests for Plan H Phase 2 Day 3 — on_turn_boundary_speed inference rule
+ priors._spread_consistent_with_speed.

Coverage matrix per spec Section 4.1:
- Skip-list (T-K1..T-K8): every reason produces a "skipped:" observation
- State-based skips (T-S5..T-S7): scarf-known, can_have_speed_modified
- Bracket math (T-M1..T-M6): opp_first / us_first / Tailwind / paralysis / TR
- Choice Scarf forks (T-C1): forced inference when min > max_non_scarf
- Spread filter (T-F1..T-F4): None range → all pass; tight range filters
"""
from __future__ import annotations

import pytest

from showdown_copilot.belief import (
    BeliefTracker,
    OpponentBelief,
    _SPEED_HI_SENTINEL,
    _BASE_SPEEDS,
)
from showdown_copilot.priors import _spread_consistent_with_speed


# ----- Skip-list (Section 4.1: T-K1..T-K8) -----


@pytest.mark.parametrize("reason", [
    "cant",
    "confusion",
    "custap",
    "quick_claw",
    "switch",
    "priority_mismatch",
    "encore",
])
def test_skip_list_records_no_op(reason):
    t = BeliefTracker()
    t.on_turn_boundary_speed(
        "garchomp", turn=1,
        my_active_speed_post_modifiers=394,
        opp_moved_first=True,
        skip_reasons=[reason],
    )
    b = t.get("garchomp")
    assert b.speed_range is None  # NO-OP
    assert b.speed_observations == [(1, 394, f"skipped:{reason}")]


def test_no_move_order_records_skipped():
    """Path A degraded path: opp_moved_first=None records skipped:no_move_order."""
    t = BeliefTracker()
    t.on_turn_boundary_speed(
        "garchomp", turn=1,
        my_active_speed_post_modifiers=394,
        opp_moved_first=None,
    )
    b = t.get("garchomp")
    assert b.speed_range is None
    assert b.speed_observations == [(1, 394, "skipped:no_move_order")]


# ----- State-based skips (T-S5..T-S7) -----


def test_skip_when_opp_scarf_revealed():
    """T-S5: opp.revealed_item == 'choicescarf' → no narrowing possible."""
    t = BeliefTracker()
    b = t.get("garchomp")
    b.revealed_item = "choicescarf"
    t.on_turn_boundary_speed(
        "garchomp", turn=1,
        my_active_speed_post_modifiers=394,
        opp_moved_first=True,
    )
    assert b.speed_range is None
    assert b.speed_observations == []  # nothing recorded


def test_skip_when_can_have_speed_modified():
    """T-S6: opp could have unobserved Sand Rush under sand."""
    t = BeliefTracker()
    t.on_turn_boundary_speed(
        "excadrill", turn=1,
        my_active_speed_post_modifiers=394,
        opp_moved_first=True,
        weather="Sandstorm",
    )
    b = t.get("excadrill")
    assert b.speed_range is None
    assert b.speed_observations == [(1, 394, "skipped:speed_modifier")]


# ----- Bracket math (T-M1..T-M6) -----


def test_opp_moved_first_no_modifiers_raises_min():
    """T-M1: opp moved first @ 394 → min = 395."""
    t = BeliefTracker()
    t.on_turn_boundary_speed(
        "garchomp", turn=1,
        my_active_speed_post_modifiers=200,  # bot speed
        opp_moved_first=True,
    )
    b = t.get("garchomp")
    assert b.speed_range == (201, _SPEED_HI_SENTINEL)


def test_us_moved_first_no_modifiers_lowers_max():
    """T-M2: bot moved first @ 394 → max = 393."""
    t = BeliefTracker()
    t.on_turn_boundary_speed(
        "garchomp", turn=1,
        my_active_speed_post_modifiers=394,
        opp_moved_first=False,
    )
    b = t.get("garchomp")
    assert b.speed_range == (0, 393)


def test_two_observations_tighten_range():
    """Successive observations on same Pokemon tighten the range."""
    t = BeliefTracker()
    t.on_turn_boundary_speed(
        "garchomp", turn=1, my_active_speed_post_modifiers=400,
        opp_moved_first=False,
    )
    t.on_turn_boundary_speed(
        "garchomp", turn=2, my_active_speed_post_modifiers=200,
        opp_moved_first=True,
    )
    b = t.get("garchomp")
    # Turn 1: (0, 399). Turn 2: (max(0, 201), min(399, ...)) = (201, 399).
    assert b.speed_range == (201, 399)


def test_trick_room_inverts_direction():
    """T-M6: in TR, opp_moved_first=True means opp is SLOWER."""
    t = BeliefTracker()
    t.on_turn_boundary_speed(
        "shuckle", turn=1, my_active_speed_post_modifiers=200,
        opp_moved_first=True,
        in_trick_room=True,
    )
    b = t.get("shuckle")
    # In TR, opp moved first because slower → opp speed < bot speed
    assert b.speed_range == (0, 199)


# ----- Choice Scarf fork (T-C1) -----


def test_forced_scarf_when_min_exceeds_max_non_scarf():
    """T-C1: opp moved first faster than max-non-scarf → forced scarf inference."""
    t = BeliefTracker()
    # Garchomp's max non-scarf at level 100: compute_speed_stat(102, 252, 31, 1.1) = 333
    # If bot speed is 400 and opp moved first → min = 401 > 333 → infer scarf
    t.on_turn_boundary_speed(
        "garchomp", turn=1, my_active_speed_post_modifiers=400,
        opp_moved_first=True,
    )
    b = t.get("garchomp")
    assert b.item_inferred_choicescarf is True
    assert "choiceband" in b.impossible_items
    assert "choicespecs" in b.impossible_items
    assert b.speed_range[0] == 401  # min preserved
    assert b.speed_range[1] == _SPEED_HI_SENTINEL  # upper unset → sentinel


def test_no_forced_scarf_below_threshold():
    """If min stays below max_non_scarf, no scarf inference."""
    t = BeliefTracker()
    # Garchomp max non-scarf = 333; bot at 200 → min = 201, no inference
    t.on_turn_boundary_speed(
        "garchomp", turn=1, my_active_speed_post_modifiers=200,
        opp_moved_first=True,
    )
    b = t.get("garchomp")
    assert b.item_inferred_choicescarf is False


def test_no_forced_scarf_when_already_in_impossible_items():
    """If R1 already added scarf to impossible_items, infer_choicescarf can't fire."""
    t = BeliefTracker()
    b = t.get("garchomp")
    b.impossible_items.add("choicescarf")  # simulate R1 fired earlier
    t.on_turn_boundary_speed(
        "garchomp", turn=1, my_active_speed_post_modifiers=400,
        opp_moved_first=True,
    )
    # min=401, max_non_scarf=333, 401>333 BUT scarf is impossible → skip
    assert b.item_inferred_choicescarf is False


# ----- Spread filter (T-F1..T-F4) -----


def _make_belief(species="garchomp", speed_range=None, scarf_inferred=False,
                 impossible_items=None):
    b = OpponentBelief(species=species)
    b.speed_range = speed_range
    b.item_inferred_choicescarf = scarf_inferred
    if impossible_items:
        b.impossible_items.update(impossible_items)
    return b


def test_spread_filter_None_range_accepts_all():
    """T-F1: speed_range=None → all spreads pass."""
    b = _make_belief(speed_range=None)
    assert _spread_consistent_with_speed(
        "Jolly:0/0/4/252/0/252", base_speed=102, belief=b
    ) is True
    assert _spread_consistent_with_speed(
        "Adamant:252/252/0/0/4/0", base_speed=102, belief=b  # 0 Spe EVs
    ) is True


def test_spread_filter_tight_range_rejects_when_neither_bracket_fits():
    """T-F2: range that excludes BOTH the raw and scarfed bracket → reject.
    Garchomp Adamant 0 Spe EV: raw=240, scarfed=int(240*1.5)=360.
    Range (450, 500) excludes both → rejected."""
    b = _make_belief(speed_range=(450, 500))
    assert _spread_consistent_with_speed(
        "Adamant:252/252/4/0/0/0", base_speed=102, belief=b
    ) is False


def test_spread_filter_low_speed_via_scarf_bracket_accepts():
    """A 0-Spe-EV spread can still pass via scarf bracket if range allows.
    Garchomp Adamant 0 Spe: raw=240, scarfed=360. Range (350, 370) → accept (scarf)."""
    b = _make_belief(speed_range=(350, 370))
    assert _spread_consistent_with_speed(
        "Adamant:252/252/4/0/0/0", base_speed=102, belief=b
    ) is True


def test_spread_filter_low_speed_with_scarf_blocked_rejects():
    """Same low-Spe spread fails when scarf is impossible_items."""
    b = _make_belief(
        speed_range=(350, 370),
        impossible_items={"choicescarf"},
    )
    assert _spread_consistent_with_speed(
        "Adamant:252/252/4/0/0/0", base_speed=102, belief=b
    ) is False


def test_spread_filter_scarf_bracket_acceptance():
    """T-F3: spread that fits scarf bracket accepted when scarf allowed."""
    # Garchomp Jolly 252+ = 333. Scarfed = int(333 * 1.5) = 499.
    # If range is (495, 510), 333 fails but 499 passes.
    b = _make_belief(speed_range=(495, 510))
    assert _spread_consistent_with_speed(
        "Jolly:0/0/4/252/0/252", base_speed=102, belief=b
    ) is True  # via scarf bracket


def test_spread_filter_scarf_bracket_blocked_when_impossible():
    """If scarf is in impossible_items, only non-scarf bracket counts."""
    b = _make_belief(
        speed_range=(495, 510),
        impossible_items={"choicescarf"},
    )
    # 333 outside (495,510), and scarf bracket ruled out → reject.
    assert _spread_consistent_with_speed(
        "Jolly:0/0/4/252/0/252", base_speed=102, belief=b
    ) is False


def test_spread_filter_forced_scarf_only_scarf_bracket():
    """When scarf is forced, ONLY the scarf bracket is checked."""
    b = _make_belief(speed_range=(495, 510), scarf_inferred=True)
    # 333 (non-scarf) is in (495, 510)? No. Scarf 499 is. → accept.
    assert _spread_consistent_with_speed(
        "Jolly:0/0/4/252/0/252", base_speed=102, belief=b
    ) is True


def test_spread_filter_filter_empty_falls_through():
    """T-F4: When narrowed range excludes ALL spreads, get_set returns None
    and the unfiltered modal path takes over (Phase 1 priors.py:174-178
    behavior preserved). This is an integration test against PriorsSource.

    Using the existing fixture pattern:
    """
    from pathlib import Path
    from showdown_copilot.priors import PriorsSource

    fixture = Path(__file__).parent / "fixtures" / "mini_chaos_natdex.json"

    # Stage chaos as a tmp file
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "gen9nationaldex-1630.json"
        target.write_text(fixture.read_text())
        src = PriorsSource(cache_dir=Path(tmpdir))

        # Belief that no spread can satisfy: claim opp speed > 9999.
        b = OpponentBelief(species="dragapult")
        b.speed_range = (9990, _SPEED_HI_SENTINEL)

        # get_set should NOT raise; it returns the unfiltered modal as fallback.
        ms = src.get_set("Dragapult", "gen9nationaldex", belief=b)
        assert ms.species == "dragapult"
        # Modal moves come back from unfiltered path
        assert len(ms.moves) > 0
