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
    we see the -ability event), revealed_ability is set positively
    AND the false-impossibility entry is discarded from
    impossible_abilities. This is required for the priors filter at
    priors.py:get_set, which uses a CONJUNCTION of impossible-check AND
    revealed-equality; without the discard, the revealed ability would
    fail the impossible-check and produce an empty filter (silent
    fallback to unfiltered modal).

    REGRESSION (Plan H Task 8 review): originally documented as
    'impossible_abilities still contains the entry; filter prefers
    revealed_ability' — but the filter is a conjunction, not an
    override, so the original contract was broken. on_reveal_ability
    now discards from impossible_abilities to keep state consistent."""
    t = BeliefTracker()
    t.on_switch_in("Landorus-Therian")  # ruled out
    assert "intimidate" in t.get("Landorus-Therian").impossible_abilities
    t.on_reveal_ability("Landorus-Therian", "Intimidate")  # actually has it
    b = t.get("Landorus-Therian")
    assert b.revealed_ability == "intimidate"
    # The discarded entry: positive reveal supersedes the eager rule-out.
    assert "intimidate" not in b.impossible_abilities


# ---------- R2 (Task 5): status-move usage rules out Assault Vest ----------


def test_R2_status_move_adds_assault_vest():
    """Opp uses a status-category move (Recover) → AV is ruled out
    (AV blocks the holder from using non-damaging moves)."""
    t = BeliefTracker()
    t.on_switch_in("Toxapex")
    t.on_move(
        "Toxapex", "Recover",
        split_msg=["|move|", "p2a: Toxapex", "Recover"],
    )
    b = t.get("Toxapex")
    assert "assaultvest" in b.impossible_items
    assert b.used_status_move is True


def test_R2_no_av_on_damaging_only():
    """Opp uses only damaging moves (Earthquake) → AV is NOT ruled out;
    AV holders CAN use damaging moves, so no inference fires."""
    t = BeliefTracker()
    t.on_switch_in("Garchomp")
    t.on_move(
        "Garchomp", "Earthquake",
        split_msg=["|move|", "p2a: Garchomp", "Earthquake"],
    )
    b = t.get("Garchomp")
    assert "assaultvest" not in b.impossible_items
    assert b.used_status_move is False


def test_R2_sleep_talk_does_not_fire():
    """REGRESSION: the Sleep-Talk poisoning case. A Choice-Specs Lapras
    Sleep-Talks Soak (a status move). Naive R2 would rule out AV based
    on Soak being status — but the [from]-guard suppresses the firing
    because the move was Sleep-Talk-driven, not the mover's free choice.
    Also: revealed_moves should NOT include Soak (passive moves don't
    reveal that the move was *intentionally* in the moveset)."""
    t = BeliefTracker()
    t.on_switch_in("Lapras")
    t.on_move(
        "Lapras", "Soak",
        split_msg=["|move|", "p2a: Lapras", "Soak", "p1a: Garchomp", "[from]Sleep Talk"],
    )
    b = t.get("Lapras")
    assert "assaultvest" not in b.impossible_items
    assert b.used_status_move is False
    # Passive move → revealed_moves not updated
    assert "soak" not in b.revealed_moves


def test_R2_teleport_fires():
    """REGRESSION (Task 5 review): Teleport is status-category in gen 8+
    (it pivots like U-turn but is non-damaging). AV holders CANNOT use
    Teleport — so opp using Teleport IS evidence AV is impossible.
    Common archetype: Slowbro / Blissey / Slowking-G defensive pivots."""
    t = BeliefTracker()
    t.on_switch_in("Slowbro")
    t.on_move(
        "Slowbro", "Teleport",
        split_msg=["|move|", "p2a: Slowbro", "Teleport"],
    )
    b = t.get("Slowbro")
    assert "assaultvest" in b.impossible_items
    assert b.used_status_move is True


def test_R2_parting_shot_fires():
    """REGRESSION (Task 5 review): Parting Shot is status-category in gen 6+
    (debuffs target Atk/SpA and pivots). AV holders CANNOT use Parting Shot.
    Common archetype: Pangoro / Whimsicott defensive pivots."""
    t = BeliefTracker()
    t.on_switch_in("Pangoro")
    t.on_move(
        "Pangoro", "Parting Shot",
        split_msg=["|move|", "p2a: Pangoro", "Parting Shot"],
    )
    b = t.get("Pangoro")
    assert "assaultvest" in b.impossible_items
    assert b.used_status_move is True


def test_R2_handles_None_split_msg_defensively():
    """REGRESSION (Task 5 review): the live message hook in Task 9 may pass
    None for split_msg if a malformed protocol line couldn't be parsed.
    on_move must not crash — None is treated as 'no [from] tokens' (active
    move), so R2 fires normally."""
    t = BeliefTracker()
    t.on_switch_in("Toxapex")
    # No exception
    t.on_move("Toxapex", "Recover", split_msg=None)
    b = t.get("Toxapex")
    # R2 fires normally — None treated as active move
    assert "assaultvest" in b.impossible_items


# ---------- R3 (Task 6): damaging move rules out Life Orb (except SF/MG) ----


def test_R3_damaging_move_eliminates_lifeorb_normal_case():
    """Garchomp uses Earthquake — fires inline. Garchomp's ability pool
    is Rough Skin / Sand Veil; no SF or MG. LO ruled out."""
    t = BeliefTracker()
    t.on_switch_in("Garchomp")
    t.on_move(
        "Garchomp", "Earthquake",
        split_msg=["|move|", "p2a: Garchomp", "Earthquake"],
    )
    b = t.get("Garchomp")
    assert "lifeorb" in b.impossible_items


def test_R3_lifeorb_keepable_for_sheerforce_candidate():
    """Nidoking has Sheer Force as HA — LO can be on Nidoking without us
    seeing recoil (SF suppresses LO recoil on secondary-effect moves).
    R3 must NOT fire.

    Note: this test was originally fixtured with Tauros-Paldea-Aqua,
    but Paldean Tauros forms have Intimidate / Anger Point / Cud Chew —
    NOT Sheer Force. Only base Tauros has SF. Audit 2026-04-26 caught
    this self-reinforcing wrong test (Plan H Task 6 review)."""
    t = BeliefTracker()
    t.on_switch_in("Nidoking")
    t.on_move(
        "Nidoking", "Earthquake",
        split_msg=["|move|", "p2a: Nidoking", "Earthquake"],
    )
    b = t.get("Nidoking")
    assert "lifeorb" not in b.impossible_items


def test_R3_lifeorb_keepable_for_magicguard_candidate():
    """Sigilyph has Magic Guard — immune to LO recoil. R3 doesn't fire."""
    t = BeliefTracker()
    t.on_switch_in("Sigilyph")
    t.on_move(
        "Sigilyph", "Air Slash",
        split_msg=["|move|", "p2a: Sigilyph", "Air Slash"],
    )
    b = t.get("Sigilyph")
    assert "lifeorb" not in b.impossible_items


def test_R3_does_not_fire_on_status_move():
    """R3 only fires on damaging moves; a status move triggers R2 but
    must NOT also trigger R3 (LO doesn't recoil on status moves anyway)."""
    t = BeliefTracker()
    t.on_switch_in("Toxapex")
    t.on_move(
        "Toxapex", "Recover",
        split_msg=["|move|", "p2a: Toxapex", "Recover"],
    )
    b = t.get("Toxapex")
    # R2 fires
    assert "assaultvest" in b.impossible_items
    # R3 doesn't fire on status moves
    assert "lifeorb" not in b.impossible_items


def test_SHEERFORCE_OR_MAGICGUARD_SPECIES_pool_membership():
    """REGRESSION (Plan H Task 6 review): every species in the R3
    exemption set must ACTUALLY have Sheer Force or Magic Guard in its
    gen 9 ability pool. Verified against poke-env's authoritative
    pokedex data so future drift is caught at commit time.

    Without this test, the original Task 6 implementation included 11
    species that did NOT actually have SF/MG (taurospaldea forms,
    darmanitan-galar, krookodile, mienshao, bouffalant, irontreads,
    ursaring, spinda, alakazammega) — silent R3 false-negatives.

    This test is also the natural Phase-2 transition path: once the
    set is derived programmatically from chaos data, this test stays
    relevant as the regression guard.
    """
    from poke_env.data import GenData

    from showdown_copilot.belief import _SHEERFORCE_OR_MAGICGUARD_SPECIES

    pokedex = GenData.from_gen(9).pokedex
    relevant_abilities = {"sheerforce", "magicguard"}

    failures = []
    for species_id in _SHEERFORCE_OR_MAGICGUARD_SPECIES:
        if species_id not in pokedex:
            failures.append(f"{species_id!r}: not in poke-env gen9 pokedex")
            continue
        # poke-env pokedex entries: {"abilities": {"0": "...", "1": "...", "H": "..."}, ...}
        abilities = pokedex[species_id].get("abilities", {})
        normalized = {_normalize(name) for name in abilities.values()}
        if not (relevant_abilities & normalized):
            failures.append(
                f"{species_id!r}: ability pool {sorted(normalized)} contains neither SF nor MG"
            )

    assert not failures, (
        "Some species in _SHEERFORCE_OR_MAGICGUARD_SPECIES don't actually "
        "have SF or MG — these would cause R3 to silently miss inferences:\n  "
        + "\n  ".join(failures)
    )


# ---------- R1 (Task 7): two-different-moves disproves Choice + early-disprove ----


def test_R1_two_different_moves_disproves_choice():
    t = BeliefTracker()
    t.on_switch_in("Garchomp")
    t.on_move("Garchomp", "Earthquake",
              split_msg=["|move|", "p2a: Garchomp", "Earthquake"])
    t.on_move("Garchomp", "Stone Edge",
              split_msg=["|move|", "p2a: Garchomp", "Stone Edge"])
    b = t.get("Garchomp")
    assert "choiceband" in b.impossible_items
    assert "choicescarf" in b.impossible_items
    assert "choicespecs" in b.impossible_items


def test_R1_no_disproof_on_same_move_twice():
    t = BeliefTracker()
    t.on_switch_in("Garchomp")
    t.on_move("Garchomp", "Earthquake",
              split_msg=["|move|", "p2a: Garchomp", "Earthquake"])
    t.on_move("Garchomp", "Earthquake",
              split_msg=["|move|", "p2a: Garchomp", "Earthquake"])
    b = t.get("Garchomp")
    # No Choice items in impossible_items from R1 (R3 may have added LO,
    # which is a different rule)
    assert "choiceband" not in b.impossible_items


def test_R1_no_disproof_after_switch():
    """Move history resets on switch-in; second move treated as 'first
    after switch' so R1 doesn't fire."""
    t = BeliefTracker()
    t.on_switch_in("Garchomp")
    t.on_move("Garchomp", "Earthquake",
              split_msg=["|move|", "p2a: Garchomp", "Earthquake"])
    t.on_switch_out("Garchomp")
    t.on_switch_in("Garchomp")  # comes back
    t.on_move("Garchomp", "Stone Edge",
              split_msg=["|move|", "p2a: Garchomp", "Stone Edge"])
    b = t.get("Garchomp")
    assert "choiceband" not in b.impossible_items


def test_R1_trick_resets_move_history():
    """REGRESSION: opp gets Tricked into Choice Band, then uses 2 different
    moves. Naive R1 would say 'no Choice item' — exactly wrong. The
    on_item_swapped hook resets last_used_move so the post-Trick moves
    don't fire R1."""
    t = BeliefTracker()
    t.on_switch_in("Mew")
    t.on_move("Mew", "Earthquake",
              split_msg=["|move|", "p2a: Mew", "Earthquake"])
    # Trick: Mew gets Choice Band, was holding Lagging Tail
    t.on_item_swapped("Mew", new_item="Choice Band", old_item="Lagging Tail")
    # Post-Trick moves — R1 must NOT fire (Mew genuinely now has CB)
    t.on_move("Mew", "Stone Edge",
              split_msg=["|move|", "p2a: Mew", "Stone Edge"])
    b = t.get("Mew")
    assert b.revealed_item == "choiceband"
    # No Choice items in impossible_items from the Trick path. Note:
    # R1's pre-Trick move (Earthquake) also can't trigger R1 because
    # there was no prior move; and on_item_swapped reset last_used_move.
    assert "choiceband" not in b.impossible_items


def test_R1_sleep_talk_does_not_trigger_disproof():
    """REGRESSION (Sleep Talk poison): Choice-Specs Lapras Sleep-Talks
    HydroPump on turn 1, wakes and uses IceBeam on turn 2. Naive R1
    reads as 'two different moves' but the first was Sleep-Talk-driven."""
    t = BeliefTracker()
    t.on_switch_in("Lapras")
    t.on_move("Lapras", "Hydro Pump",
              split_msg=["|move|", "p2a: Lapras", "Hydro Pump", "[from]Sleep Talk"])
    t.on_move("Lapras", "Ice Beam",
              split_msg=["|move|", "p2a: Lapras", "Ice Beam"])
    b = t.get("Lapras")
    # Hydro Pump was passive — wasn't recorded as last_used_move.
    # Ice Beam is now the FIRST recorded move; R1 doesn't fire.
    assert "choiceband" not in b.impossible_items
    assert b.last_used_move == "icebeam"


def test_R1_early_disprove_swordsdance():
    """Early-disprove free win: Swords Dance is impossible under Choice
    lock (you can't use a status move while locked to a damaging move).
    First-and-only observation rules out Choice."""
    t = BeliefTracker()
    t.on_switch_in("Garchomp")
    t.on_move("Garchomp", "Swords Dance",
              split_msg=["|move|", "p2a: Garchomp", "Swords Dance"])
    b = t.get("Garchomp")
    assert "choiceband" in b.impossible_items
    assert "choicescarf" in b.impossible_items
    assert "choicespecs" in b.impossible_items


def test_R1_early_disprove_substitute():
    """Substitute is also Choice-impossible."""
    t = BeliefTracker()
    t.on_switch_in("Kingambit")
    t.on_move("Kingambit", "Substitute",
              split_msg=["|move|", "p2a: Kingambit", "Substitute"])
    b = t.get("Kingambit")
    assert "choiceband" in b.impossible_items


def test_R1_early_disprove_roost():
    """Recovery moves are categorically Choice-impossible (you can't use
    a status move under Choice lock). Roost on first observation rules
    out all 3 Choice items."""
    t = BeliefTracker()
    t.on_switch_in("Corviknight")
    t.on_move("Corviknight", "Roost",
              split_msg=["|move|", "p2a: Corviknight", "Roost"])
    b = t.get("Corviknight")
    assert "choiceband" in b.impossible_items
    assert "choicescarf" in b.impossible_items
    assert "choicespecs" in b.impossible_items


def test_R1_early_disprove_protect():
    """Protection moves (Protect, Detect, Spiky Shield, etc.) are
    Choice-impossible — they're status-category and not damaging."""
    t = BeliefTracker()
    t.on_switch_in("Toxapex")
    t.on_move("Toxapex", "Protect",
              split_msg=["|move|", "p2a: Toxapex", "Protect"])
    b = t.get("Toxapex")
    assert "choiceband" in b.impossible_items


def test_R1_early_disprove_leechseed():
    """Leech Seed is status-category and Choice-impossible."""
    t = BeliefTracker()
    t.on_switch_in("Ferrothorn")
    t.on_move("Ferrothorn", "Leech Seed",
              split_msg=["|move|", "p2a: Ferrothorn", "Leech Seed"])
    b = t.get("Ferrothorn")
    assert "choiceband" in b.impossible_items


def test_R1_post_trick_resumes_normally_on_subsequent_two_different_moves():
    """REGRESSION (Plan H Task 7 review): on_item_swapped resets R1 state
    (last_used_move=None) so the IMMEDIATE next move can't fire R1.
    But subsequent moves CAN fire R1 normally — if a Tricked-CB Pokemon
    uses 2 different moves WITHOUT switching out, that's evidence they
    are NOT actually Choice-locked (a real CB user is locked to one move
    until switch-out).

    Pins the contract that 'reset is one-shot, R1 resumes after.'"""
    t = BeliefTracker()
    t.on_switch_in("Mew")
    # Pre-Trick move
    t.on_move("Mew", "Earthquake",
              split_msg=["|move|", "p2a: Mew", "Earthquake"])
    # Trick fires — Mew now has CB, last_used_move reset to None
    t.on_item_swapped("Mew", new_item="Choice Band", old_item="Lagging Tail")
    # First post-Trick move: R1 doesn't fire (last_used_move was None)
    t.on_move("Mew", "Stone Edge",
              split_msg=["|move|", "p2a: Mew", "Stone Edge"])
    b = t.get("Mew")
    assert "choiceband" not in b.impossible_items, (
        "first post-Trick move shouldn't trigger R1 — no comparison baseline"
    )
    # Second post-Trick move (different): R1 fires normally — observing
    # 2 different moves WITHOUT a switch proves Mew isn't Choice-locked,
    # despite the CB reveal. (If they were locked to Stone Edge, they
    # couldn't use Earthquake without switching first.)
    t.on_move("Mew", "Earthquake",
              split_msg=["|move|", "p2a: Mew", "Earthquake"])
    b = t.get("Mew")
    assert "choiceband" in b.impossible_items, (
        "R1 should resume on subsequent moves — 2 different moves "
        "post-Trick without a switch proves the holder isn't Choice-locked"
    )


def test_CHOICE_INCOMPATIBLE_MOVES_subset_of_STATUS_MOVES():
    """REGRESSION (Plan H Task 7 review): _CHOICE_INCOMPATIBLE_MOVES
    must be a subset of _STATUS_MOVES (minus Trick/Switcheroo which
    are status-category but Choice-COMPATIBLE — a Choice user can run
    Trick to pass the item).

    If a future contributor adds a damaging move (e.g., misclassifies
    U-turn) to _CHOICE_INCOMPATIBLE_MOVES, R1 early-disprove would
    silently fire on a Choice-COMPATIBLE move and produce wrong
    inferences. This test catches that drift at commit time.
    """
    from showdown_copilot.belief import (
        _CHOICE_INCOMPATIBLE_MOVES,
        _STATUS_MOVES,
    )
    leaks = _CHOICE_INCOMPATIBLE_MOVES - _STATUS_MOVES
    assert not leaks, (
        f"R1 early-disprove would mis-fire on non-status moves: {sorted(leaks)}"
    )
    # And the explicit exclusions: Trick/Switcheroo are in _STATUS_MOVES
    # (R2 fires on them) but NOT in _CHOICE_INCOMPATIBLE_MOVES (Choice-
    # compatible).
    assert "trick" not in _CHOICE_INCOMPATIBLE_MOVES
    assert "switcheroo" not in _CHOICE_INCOMPATIBLE_MOVES
    assert "trick" in _STATUS_MOVES
    assert "switcheroo" in _STATUS_MOVES


# ---------- R4 (Task 8): Heavy-Duty Boots from hazard immunity ----------


def test_R4_heavy_duty_boots_from_stealth_rock_immunity():
    """Skarmory (no Levitate, no Magic Guard) switches in to active SR
    with no hazard damage observed by turn boundary → HDB inferred."""
    t = BeliefTracker()
    t.on_switch_in("Skarmory", side_hazards={"stealthrock": 1})
    # No on_hazard_damage call → flag stays False
    t.on_turn_boundary()
    b = t.get("Skarmory")
    assert "leftovers" in b.impossible_items
    assert "rockyhelmet" in b.impossible_items
    assert "choiceband" in b.impossible_items
    # And HDB is NOT in impossible_items (it's the conclusion)
    assert "heavydutyboots" not in b.impossible_items


def test_R4_no_fire_when_hazard_damage_observed():
    """Skarmory takes SR damage on switch-in (would happen normally
    without HDB) → R4 doesn't fire."""
    t = BeliefTracker()
    t.on_switch_in("Skarmory", side_hazards={"stealthrock": 1})
    t.on_hazard_damage("Skarmory")  # sets took_hazard_damage_this_stretch
    t.on_turn_boundary()
    b = t.get("Skarmory")
    # Free-win additions still fire (boosterenergy, airballoon)
    assert "airballoon" in b.impossible_items
    assert "boosterenergy" in b.impossible_items
    # But R4-specific items NOT in impossible_items
    assert "leftovers" not in b.impossible_items
    assert "choiceband" not in b.impossible_items


def test_R4_no_hdb_for_levitate_candidate_with_spikes():
    """Rotom-Wash (Levitate possible) switches in to active Spikes →
    Levitate could explain absence of damage. R4 doesn't fire."""
    t = BeliefTracker()
    t.on_switch_in("Rotom-Wash", side_hazards={"spikes": 1})
    t.on_turn_boundary()
    b = t.get("Rotom-Wash")
    assert "leftovers" not in b.impossible_items


def test_R4_no_hdb_for_magicguard_candidate():
    """Sigilyph (Magic Guard possible) switches in to SR + Spikes →
    Magic Guard explains absence of all hazard damage. R4 doesn't fire."""
    t = BeliefTracker()
    t.on_switch_in("Sigilyph", side_hazards={"stealthrock": 1, "spikes": 2})
    t.on_turn_boundary()
    b = t.get("Sigilyph")
    assert "leftovers" not in b.impossible_items


def test_R4_tera_flying_carve_out_for_spikes():
    """Garchomp Tera Flying switches in to active Spikes → Tera Flying
    ignores Spikes; R4 doesn't fire even though Garchomp doesn't have
    Levitate."""
    t = BeliefTracker()
    t.on_switch_in("Garchomp", side_hazards={"spikes": 1})
    t.on_terastallize("Garchomp", "Flying")
    t.on_turn_boundary()
    b = t.get("Garchomp")
    assert "leftovers" not in b.impossible_items


def test_R4_base_flying_carve_out_for_spikes_via_poke_env_pokedex():
    """REGRESSION (Plan H Task 8 review): _BASE_TYPES is now derived
    from poke-env's gen-9 pokedex at module load, not a hand-curated
    8-entry dict. So R4 must correctly carve out base-Flying species
    (Charizard, Talonflame, Yveltal, etc.) on Spikes — not just
    Skarmory/Corviknight from the old dict.

    The original _BASE_TYPES dict only had 8 species; Charizard/Talonflame/
    etc. defaulted to () → has_type(b, 'Flying', ()) returned False →
    Tera-Flying carve-out skipped → R4 over-fired HDB on every
    base-Flying Pokemon NOT in the dict.
    """
    t = BeliefTracker()
    # Charizard is base Fire/Flying — Spikes don't hit Flying.
    t.on_switch_in("Charizard", side_hazards={"spikes": 1})
    t.on_turn_boundary()
    b = t.get("Charizard")
    # Tera-Flying carve-out fires (via base-type lookup, no Tera needed).
    # R4 should NOT fire — leftovers stays possible.
    assert "leftovers" not in b.impossible_items, (
        "Charizard is base Flying-type per poke-env pokedex — Spikes "
        "don't damage Flying types. R4 must not over-fire HDB."
    )

    # Same for Talonflame (base Fire/Flying, also not in old _BASE_TYPES)
    t.on_switch_in("Talonflame", side_hazards={"spikes": 1})
    t.on_turn_boundary()
    assert "leftovers" not in t.get("Talonflame").impossible_items


def test_R4_no_fire_without_active_hazards():
    """No hazards → R4 doesn't fire (no inference possible)."""
    t = BeliefTracker()
    t.on_switch_in("Garchomp", side_hazards={})
    t.on_turn_boundary()
    b = t.get("Garchomp")
    # Free wins still fire
    assert "airballoon" in b.impossible_items
    # R4-specific items not added
    assert "leftovers" not in b.impossible_items


def test_R4_free_wins_always_fire_on_switch_in():
    """boosterenergy and airballoon are ruled out on EVERY switch-in
    (they announce themselves; absence rules them out unconditionally)."""
    t = BeliefTracker()
    t.on_switch_in("Garchomp", side_hazards={})
    b = t.get("Garchomp")
    assert "airballoon" in b.impossible_items
    assert "boosterenergy" in b.impossible_items


def test_LEVITATE_SPECIES_pool_membership():
    """REGRESSION (mirror of Task 6 sanity test): every species in
    `_LEVITATE_SPECIES` must ACTUALLY have Levitate in its gen 9 ability
    pool per poke-env's authoritative pokedex.

    Without this guard, a stale chaos-cache entry (e.g., gen-9 Gengar
    which lost Levitate in gen 7) would cause R4 to silently fail to
    rule out HDB on switch-ins where it should — a false-negative on a
    rule with thousands of firings per battle.
    """
    from poke_env.data import GenData

    from showdown_copilot._ability_pools import _LEVITATE_SPECIES
    from showdown_copilot.belief import _normalize

    pokedex = GenData.from_gen(9).pokedex
    failures = []
    for species_id in _LEVITATE_SPECIES:
        if species_id not in pokedex:
            failures.append(f"{species_id!r}: not in poke-env gen9 pokedex")
            continue
        abilities = pokedex[species_id].get("abilities", {})
        normalized = {_normalize(name) for name in abilities.values()}
        if "levitate" not in normalized:
            failures.append(
                f"{species_id!r}: ability pool {sorted(normalized)} does not contain Levitate"
            )

    assert not failures, (
        "Some species in _LEVITATE_SPECIES don't actually have Levitate "
        "in gen 9 — these would cause R4 to mis-fire HDB inference:\n  "
        + "\n  ".join(failures)
    )


def test_MAGICGUARD_SPECIES_pool_membership():
    """REGRESSION (mirror of Task 6 sanity test): every species in
    `_MAGICGUARD_SPECIES` must ACTUALLY have Magic Guard in its gen 9
    ability pool per poke-env's authoritative pokedex.

    Magic Guard is the most-impactful R4 carve-out (immune to ALL
    indirect damage), so a stale entry would cause R4 to falsely conclude
    HDB on a Magic Guard candidate that took no hazard damage simply
    because of MG.
    """
    from poke_env.data import GenData

    from showdown_copilot._ability_pools import _MAGICGUARD_SPECIES
    from showdown_copilot.belief import _normalize

    pokedex = GenData.from_gen(9).pokedex
    failures = []
    for species_id in _MAGICGUARD_SPECIES:
        if species_id not in pokedex:
            failures.append(f"{species_id!r}: not in poke-env gen9 pokedex")
            continue
        abilities = pokedex[species_id].get("abilities", {})
        normalized = {_normalize(name) for name in abilities.values()}
        if "magicguard" not in normalized:
            failures.append(
                f"{species_id!r}: ability pool {sorted(normalized)} does not contain Magic Guard"
            )

    assert not failures, (
        "Some species in _MAGICGUARD_SPECIES don't actually have Magic Guard "
        "in gen 9 — these would cause R4 to mis-fire HDB inference:\n  "
        + "\n  ".join(failures)
    )


def test_all_beliefs_returns_dict_keyed_by_normalized_species():
    tracker = BeliefTracker()
    tracker.on_reveal_move("Garchomp", "earthquake")
    tracker.on_reveal_move("Tyranitar", "stoneedge")
    result = tracker.all_beliefs()
    assert set(result.keys()) == {"garchomp", "tyranitar"}
    assert "earthquake" in result["garchomp"].revealed_moves
    assert "stoneedge" in result["tyranitar"].revealed_moves
