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


def test_on_reveal_move_ignores_empty_string():
    """REGRESSION (Task 1 review): an empty move_id must NOT corrupt R1
    state. _normalize("") returns "", and without a guard the empty string
    would be added to revealed_moves and set as last_used_move — causing
    R1 to fire spuriously on the NEXT real move ("" != real_move)."""
    t = BeliefTracker()
    t.on_switch_in("Garchomp")
    t.on_reveal_move("Garchomp", "")
    b = t.get("Garchomp")
    assert b.revealed_moves == set()
    assert b.last_used_move is None
    assert b.moves_used_since_switch_in == []


def test_on_reveal_item_ignores_empty_string():
    """REGRESSION: empty item_id must not set revealed_item to ""."""
    t = BeliefTracker()
    t.on_reveal_item("Garchomp", "")
    assert t.get("Garchomp").revealed_item is None


def test_on_reveal_ability_ignores_empty_string():
    """REGRESSION: empty ability_id must not set revealed_ability to ""."""
    t = BeliefTracker()
    t.on_reveal_ability("Garchomp", "")
    assert t.get("Garchomp").revealed_ability is None


def test_clear_drops_all_beliefs_in_place():
    """REGRESSION (Task 3 review): clear() must reset state WITHOUT
    replacing the tracker instance. SpectatorAdapter.on_team_preview
    relies on this so external callers (Task 9 harness) holding a
    reference to the tracker stay connected across battle resets."""
    t = BeliefTracker()
    t.on_reveal_move("Garchomp", "Earthquake")
    t.on_reveal_item("Garchomp", "Choice Band")
    assert "earthquake" in t.get("Garchomp").revealed_moves

    t.clear()

    # All belief state is gone — fresh OpponentBelief is built lazily
    assert t.get("Garchomp").revealed_moves == set()
    assert t.get("Garchomp").revealed_item is None


# ---------- R5 (Task 4): auto-trigger abilities ruled out on switch-in ----------


def test_R5_eagerly_rules_out_intimidate_on_switch_in():
    """A vanilla switch-in with no special conditions: all auto-trigger
    abilities should be ruled out (the protocol *would* have announced)."""
    t = BeliefTracker()
    t.on_switch_in("Landorus-Therian")
    b = t.get("Landorus-Therian")
    assert "intimidate" in b.impossible_abilities
    assert "sandstream" in b.impossible_abilities
    assert "pressure" in b.impossible_abilities


def test_R5_carve_out_a_gen3_pressure_skipped():
    """Pressure is silent in gen 3 — don't rule it out."""
    t = BeliefTracker()
    t.on_switch_in("Mewtwo", generation=3)
    b = t.get("Mewtwo")
    assert "pressure" not in b.impossible_abilities
    # Other abilities still get ruled out
    assert "intimidate" in b.impossible_abilities


def test_R5_carve_out_b_sandstream_skipped_when_sand_already_up():
    """Sand is already up when Tyranitar switches in → Sand Stream
    can't be ruled out (the announcement would have been silent)."""
    t = BeliefTracker()
    t.on_switch_in("Tyranitar", current_weather="sandstorm")
    b = t.get("Tyranitar")
    assert "sandstream" not in b.impossible_abilities
    # Other weather-setters still get ruled out (different weathers)
    assert "drought" in b.impossible_abilities
    assert "drizzle" in b.impossible_abilities
    assert "snowwarning" in b.impossible_abilities
    # Non-weather abilities still get ruled out
    assert "intimidate" in b.impossible_abilities


def test_R5_carve_out_c_neutralizing_gas_skips_entire_pass():
    """Our active has Neutralizing Gas → all opp on-switch-in
    announcements are suppressed; nothing is ruled out."""
    t = BeliefTracker()
    t.on_switch_in("Landorus-Therian", our_active_ability="Neutralizing Gas")
    b = t.get("Landorus-Therian")
    assert b.impossible_abilities == set()


def test_R5_revealed_ability_overrides_false_impossibility():
    """If the protocol later reveals Intimidate (e.g. it DID fire and
    we see the -ability event), revealed_ability is set positively;
    R5's earlier impossible_abilities entry is no longer load-bearing
    because revealed_ability is the source of truth.

    This test documents the design contract; the priors filter
    consults revealed_ability first, then impossible_abilities."""
    t = BeliefTracker()
    t.on_switch_in("Landorus-Therian")  # ruled out
    t.on_reveal_ability("Landorus-Therian", "Intimidate")  # actually has it
    b = t.get("Landorus-Therian")
    assert b.revealed_ability == "intimidate"
    # impossible_abilities still contains the entry — this is fine; the
    # priors filter prefers revealed_ability when set.
    assert "intimidate" in b.impossible_abilities
