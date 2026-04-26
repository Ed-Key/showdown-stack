"""Tests for belief.py — Phase 1 skeleton + state recording + helpers.
Rules R1-R5 will be tested in Tasks 4-8.
"""
from __future__ import annotations

from showdown_copilot.belief import (
    BeliefTracker,
    OpponentBelief,
    _normalize,
    is_passive_move_event,
    has_type,
)


def test_normalize_lowercases_and_strips_special():
    assert _normalize("Iron Hands") == "ironhands"
    assert _normalize("Urshifu-Rapid-Strike") == "urshifurapidstrike"
    assert _normalize("Mr. Mime") == "mrmime"


def test_get_creates_entry_lazily():
    t = BeliefTracker()
    b = t.get("Garchomp")
    assert isinstance(b, OpponentBelief)
    assert b.species == "garchomp"
    assert b.revealed_moves == set()
    assert b.revealed_item is None
    # Same call returns the same instance
    assert t.get("Garchomp") is b


def test_on_reveal_move_records_state():
    t = BeliefTracker()
    t.on_reveal_move("Garchomp", "Earthquake")
    b = t.get("Garchomp")
    assert b.revealed_moves == {"earthquake"}
    assert b.last_used_move == "earthquake"
    assert b.moves_used_since_switch_in == ["earthquake"]


def test_on_reveal_item_and_ability_record_state():
    t = BeliefTracker()
    t.on_reveal_item("Garchomp", "Rocky Helmet")
    t.on_reveal_ability("Garchomp", "Rough Skin")
    b = t.get("Garchomp")
    assert b.revealed_item == "rockyhelmet"
    assert b.revealed_ability == "roughskin"


def test_on_switch_in_resets_per_stretch_state():
    t = BeliefTracker()
    t.on_reveal_move("Garchomp", "Earthquake")
    # After a switch-in, the move history for the new stretch is empty
    t.on_switch_in("Garchomp", side_hazards={"stealthrock": 1})
    b = t.get("Garchomp")
    assert b.moves_used_since_switch_in == []
    assert b.last_used_move is None
    assert b.just_switched_in is True
    assert b.side_hazards_at_switch_in == {"stealthrock": 1}
    # But cumulative revealed_moves persists across switches
    assert b.revealed_moves == {"earthquake"}


def test_is_passive_move_event_sleep_talk():
    """Sleep Talk is the most damaging false-positive for R1: a
    Choice-Specs Lapras using Sleep Talk → HydroPump, then waking →
    IceBeam, naive R1 reads as 'two different moves' but it's not."""
    assert is_passive_move_event(["[from]Sleep Talk"]) is True
    assert is_passive_move_event(["[from] move: Sleep Talk"]) is True


def test_is_passive_move_event_lockedmove_is_NOT_passive():
    """Locked Move (Outrage) IS the mover's choice — just constrained to
    repeat. R1/R2/R3 should fire normally on lockedmove."""
    assert is_passive_move_event(["[from]lockedmove"]) is False


def test_is_passive_move_event_normal_move():
    """A vanilla move event with no [from] tokens is not passive."""
    assert is_passive_move_event(["|move|", "p2a: Garchomp", "Earthquake", "p1a: Skarmory"]) is False


def test_is_passive_move_event_dancer_copy():
    """Magic Bounce / Dancer copies are passive — opp didn't select."""
    assert is_passive_move_event(["[from]ability: Dancer"]) is True


def test_has_type_pre_tera():
    """Before terastallization, has_type checks base types."""
    b = OpponentBelief(species="garchomp")
    assert has_type(b, "Dragon", ("Dragon", "Ground")) is True
    assert has_type(b, "Flying", ("Dragon", "Ground")) is False


def test_has_type_post_tera_replaces_base():
    """After Tera, Tera type REPLACES base types for type-effectiveness
    (gen 9). R4 hazard carve-outs depend on this — Tera Flying ignores
    Spikes."""
    b = OpponentBelief(species="garchomp", terastallized=True, tera_type="flying")
    assert has_type(b, "Flying", ("Dragon", "Ground")) is True
    assert has_type(b, "Dragon", ("Dragon", "Ground")) is False  # Tera replaced it
    assert has_type(b, "Ground", ("Dragon", "Ground")) is False


def test_on_item_swapped_resets_move_history():
    """Trick / Switcheroo flips opp's item; R1's move-history must reset
    so subsequent moves don't trigger a spurious 'two different moves'."""
    t = BeliefTracker()
    t.on_switch_in("Mew")
    t.on_reveal_move("Mew", "Earthquake")
    assert t.get("Mew").last_used_move == "earthquake"
    assert t.get("Mew").moves_used_since_switch_in == ["earthquake"]
    # Trick happens — Mew gets a new item
    t.on_item_swapped("Mew", new_item="Choice Band", old_item="Lagging Tail")
    b = t.get("Mew")
    assert b.removed_item == "Lagging Tail"
    assert b.revealed_item == "choiceband"
    assert b.last_used_move is None  # reset so R1 doesn't fire on next move
    assert b.moves_used_since_switch_in == []


def test_on_terastallize_sets_tera_state():
    t = BeliefTracker()
    t.on_terastallize("Hawlucha", "Flying")
    b = t.get("Hawlucha")
    assert b.terastallized is True
    assert b.tera_type == "flying"


def test_on_turn_boundary_clears_just_switched_in():
    """Skeleton-only behavior; Task 8 (R4) adds the actual HDB conclusion."""
    t = BeliefTracker()
    t.on_switch_in("Garchomp", side_hazards={"stealthrock": 1})
    assert t.get("Garchomp").just_switched_in is True
    t.on_turn_boundary()
    assert t.get("Garchomp").just_switched_in is False


def test_on_hazard_damage_sets_flag():
    """on_hazard_damage records that this Pokemon took damage from
    entry hazards on its switch-in. R4 (Task 8) reads this flag at
    turn boundary to decide whether to fire HDB."""
    t = BeliefTracker()
    t.on_switch_in("Skarmory", side_hazards={"spikes": 1})
    assert t.get("Skarmory").took_hazard_damage_this_stretch is False
    t.on_hazard_damage("Skarmory")
    assert t.get("Skarmory").took_hazard_damage_this_stretch is True
    # Cleared at turn boundary
    t.on_turn_boundary()
    assert t.get("Skarmory").took_hazard_damage_this_stretch is False
