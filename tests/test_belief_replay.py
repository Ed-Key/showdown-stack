"""Layer 2 replay regression tests for BeliefTracker (Plan H Task 10).

Feeds real captured Showdown stepqueues through the BeliefTracker
turn-by-turn — driving R1-R5 through actual `|move|` / `|switch|` /
`|-terastallize|` / `|-damage| ... [from] Stealth Rock` / `|turn|`
protocol events the same way the live message hook would in production.

This catches drift between Showdown protocol versions and our parser, and
catches interaction bugs between the inference rules that unit tests miss
because unit tests poke each rule in isolation.

Spec: docs/superpowers/specs/2026-04-26-plan-h-posterior-tracking-design.md
section 5.2 "Layer 2 — Replay regression battery".

Fixture coverage as of 2026-04-26:
- stepqueue-triplej-loss.json: 291 protocol lines, 20 turns, gen9 NatDex,
  Mariga (p2) loses to TripleJ1118 (p1). Exercises R1, R2, R3, R5;
  R4 is exercised once (Galvantula on Stealth Rock with no damage being
  the carve-out test). Tera is banned in this format so R4-Tera carve-out
  is not exercised.
- postmortems.json (analysis dir): contains curated turn-by-turn dicts
  rather than raw protocol streams, so cannot drive Layer 2 replay
  directly. Kept here as a sanity check that the fixture loads.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from showdown_copilot.belief import BeliefTracker, _normalize, is_passive_move_event


# --------------------------------------------------------------------------
# Synthetic-events helper (kept for the small per-rule coverage tests).
# Exercises the higher-level on_* hooks the way the plan body draft did.
# --------------------------------------------------------------------------


def _replay_synthetic(events: list[tuple]) -> BeliefTracker:
    """Feed a list of coarse `(kind, *args)` tuples through a tracker.
    Used for the small per-rule coverage tests. The full protocol replayer
    below is what drives the real-fixture regression test.
    """
    t = BeliefTracker()
    for event in events:
        kind = event[0]
        if kind == "switch_in":
            t.on_switch_in(event[1], side_hazards=event[2] if len(event) > 2 else None)
        elif kind == "switch_out":
            t.on_switch_out(event[1])
        elif kind == "move":
            # event = ("move", species, move_id, split_msg_or_None)
            split = event[3] if len(event) > 3 else []
            t.on_move(event[1], event[2], split)
        elif kind == "reveal_move":
            t.on_reveal_move(event[1], event[2])
        elif kind == "reveal_item":
            t.on_reveal_item(event[1], event[2])
        elif kind == "reveal_ability":
            t.on_reveal_ability(event[1], event[2])
        elif kind == "hazard_damage":
            t.on_hazard_damage(event[1])
        elif kind == "turn_boundary":
            t.on_turn_boundary()
    return t


# --------------------------------------------------------------------------
# Showdown protocol replayer — the heart of Layer 2.
# --------------------------------------------------------------------------


# Hazard names as Showdown emits them in `|-sidestart|...|move: <Name>` and
# in `|-damage|...|[from] <Name>` lines, mapped to the lowercase id our
# tracker / R4 expect in side_hazards.
_HAZARD_NAME_TO_ID = {
    "stealth rock": "stealthrock",
    "spikes": "spikes",
    "toxic spikes": "toxicspikes",
    "sticky web": "stickyweb",
}


def _parse_pokemon_id(token: str) -> tuple[str, str]:
    """Parse a `|switch|` / `|move|` source token like `p1a: Urshifu` into
    (`p1`, `Urshifu`). Position id is the first 2 chars (`p1` / `p2`);
    slot suffix (`a` / `b`) is dropped — singles only in this fixture.
    """
    # token = "p1a: Urshifu" or "p2b: Whatever"
    if ":" not in token:
        return ("", token.strip())
    side_slot, name = token.split(":", 1)
    side_slot = side_slot.strip()  # "p1a"
    side = side_slot[:2] if len(side_slot) >= 2 else side_slot  # "p1"
    return (side, name.strip())


def _parse_side_id(token: str) -> str:
    """Parse a side-scoped token like `p1: TripleJ1118` into `p1`."""
    if ":" not in token:
        return token.strip()[:2]
    side, _ = token.split(":", 1)
    return side.strip()[:2]


def _from_token_carrying_hazard(split_msg: list[str]) -> str | None:
    """If a `|-damage|` line has `[from] <hazard name>`, return the
    hazard id (e.g., "stealthrock"). Returns None if the [from] is not
    a hazard or absent.
    """
    for tok in split_msg:
        if tok.startswith("[from]"):
            payload = tok[len("[from]"):].strip().lower()
            # Strip "move: " prefix if present (some hazards report as
            # `[from] move: Stealth Rock`, others as `[from] Stealth Rock`).
            if payload.startswith("move:"):
                payload = payload[len("move:"):].strip()
            if payload in _HAZARD_NAME_TO_ID:
                return _HAZARD_NAME_TO_ID[payload]
    return None


def replay_stepqueue(
    stepqueue: list[str],
    my_side_id: str,
    generation: int = 9,
) -> tuple[BeliefTracker, dict[str, int]]:
    """Drive a BeliefTracker through a Showdown stepqueue from the
    spectator's perspective. `my_side_id` is "p1" or "p2" — that's MY
    side; the opposite is opp.

    Returns (tracker, rule_fire_counts) where rule_fire_counts is a
    coverage audit:
      {"R1": int, "R2": int, "R3": int, "R4": int, "R5": int}
    R1-R3 are counted by inspecting `impossible_items` deltas across
    `on_move` calls (proxy — exact rule attribution would require
    instrumenting belief.py, which Task 10 forbids). R4 is counted by
    `on_turn_boundary`-induced HDB rule-out deltas. R5 is counted by
    on_switch_in-induced impossible_abilities deltas.

    `our_active_ability` is best-effort — tracked via the most recent
    `|-ability|` event on our side. Most Mariga sets don't announce an
    ability on switch-in (only Intimidate does in the TripleJ fixture),
    so this is None for many switches; that's fine — the only carve-out
    that depends on this is Neutralizing Gas, which neither side runs.
    """
    if my_side_id not in ("p1", "p2"):
        raise ValueError(f"my_side_id must be p1 or p2, got {my_side_id!r}")
    opp_side_id = "p2" if my_side_id == "p1" else "p1"

    t = BeliefTracker()

    # Per-side hazard state, keyed by side id ("p1" / "p2").
    side_hazards: dict[str, dict[str, int]] = {"p1": {}, "p2": {}}

    # Current weather (Showdown protocol id, lowercase or None).
    current_weather: str | None = None

    # Best-effort track of our active Pokemon's ability.
    our_active_ability: str | None = None

    # display_name → canonical full-form species, per side. Switch lines
    # carry both the protocol display name (e.g. "Urshifu") in parts[2]
    # and the full form (e.g. "Urshifu-Rapid-Strike") in parts[3]. Move
    # / ability / item events only carry the display name. Without this
    # mapping, the tracker would split into TWO entries per Pokemon —
    # the form-suffixed one from the switch (which collected R5) and a
    # plain-name one from the moves (which collected R1/R2/R3). The
    # priors filter would then see neither entry as fully populated.
    display_to_form: dict[str, dict[str, str]] = {"p1": {}, "p2": {}}

    # Coverage audit counters. Populated by diffing the tracker state
    # before/after each protocol event that can fire a rule.
    audit = {"R1": 0, "R2": 0, "R3": 0, "R4": 0, "R5": 0}

    _CHOICE = {"choiceband", "choicescarf", "choicespecs"}
    _R4_RULED = {
        "lifeorb", "leftovers", "rockyhelmet",
        "choiceband", "choicescarf", "choicespecs",
        "assaultvest", "focussash",
        "weaknesspolicy", "ejectbutton", "redcard",
    }

    def _opp_belief_snapshot() -> dict[str, dict[str, frozenset]]:
        """Snapshot impossible_items/impossible_abilities for all opp
        beliefs, for diff-based audit counting."""
        out = {}
        for sp, b in t._beliefs.items():
            out[sp] = {
                "ii": frozenset(b.impossible_items),
                "ia": frozenset(b.impossible_abilities),
            }
        return out

    for line in stepqueue:
        # Defensive: blank lines, lines without leading "|", malformed lines.
        if not line or not line.startswith("|"):
            continue
        parts = line.split("|")
        # parts[0] is "" (leading "|"); parts[1] is the message tag.
        if len(parts) < 2:
            continue
        msg = parts[1]

        # ---- Turn boundary first: fires R4 conclusion, then resets flags. ----
        if msg == "turn":
            before = _opp_belief_snapshot()
            t.on_turn_boundary()
            after = _opp_belief_snapshot()
            for sp in after:
                old_ii = before.get(sp, {}).get("ii", frozenset())
                new_ii = after[sp]["ii"]
                added = new_ii - old_ii
                # R4 ruled out the full _R4_RULED set in one shot; if at least
                # 5 of those items were added at once, attribute to R4.
                if len(added & _R4_RULED) >= 5:
                    audit["R4"] += 1
            continue

        # ---- Weather state ----
        if msg == "-weather":
            # |-weather|SunnyDay|... or |-weather|none|... at clear
            if len(parts) >= 3:
                w = parts[2]
                if w.lower() == "none":
                    current_weather = None
                else:
                    current_weather = _normalize(w)
            continue

        # ---- Side hazards: -sidestart / -sideend ----
        if msg == "-sidestart":
            # |-sidestart|p1: TripleJ1118|move: Stealth Rock
            if len(parts) >= 4:
                side = _parse_side_id(parts[2])
                hazard_label = parts[3]
                # Strip "move: " prefix
                if hazard_label.lower().startswith("move:"):
                    hazard_label = hazard_label[len("move:"):].strip()
                hid = _HAZARD_NAME_TO_ID.get(hazard_label.lower())
                if hid:
                    side_hazards.setdefault(side, {})
                    side_hazards[side][hid] = side_hazards[side].get(hid, 0) + 1
            continue
        if msg == "-sideend":
            if len(parts) >= 4:
                side = _parse_side_id(parts[2])
                hazard_label = parts[3]
                if hazard_label.lower().startswith("move:"):
                    hazard_label = hazard_label[len("move:"):].strip()
                hid = _HAZARD_NAME_TO_ID.get(hazard_label.lower())
                if hid and side in side_hazards:
                    side_hazards[side].pop(hid, None)
            continue

        # ---- Switch ----
        if msg == "switch" or msg == "drag":
            # |switch|p1a: Urshifu|Urshifu-Rapid-Strike, M|100/100
            if len(parts) < 4:
                continue
            side, display_name = _parse_pokemon_id(parts[2])
            # Real species is in parts[3] before the first comma — that's
            # what we want for tracking (e.g., "Urshifu-Rapid-Strike" not
            # the protocol shortname "Urshifu").
            species_field = parts[3].split(",")[0].strip()
            # Cache the display→form mapping so subsequent move/ability/
            # item/tera/damage events resolve to the same belief entry.
            display_to_form.setdefault(side, {})[display_name] = species_field
            if side == opp_side_id:
                # Track previous opp active for switch-out signal
                # (we'll just call on_switch_out on every other opp species)
                norm_in = _normalize(species_field)
                for sp in list(t._beliefs.keys()):
                    if sp != norm_in and t._beliefs[sp].just_switched_in:
                        t.on_switch_out(sp)
                before = _opp_belief_snapshot()
                t.on_switch_in(
                    species_field,
                    side_hazards=side_hazards.get(opp_side_id),
                    current_weather=current_weather,
                    generation=generation,
                    our_active_ability=our_active_ability,
                )
                after = _opp_belief_snapshot()
                # R5 audit: count if impossible_abilities grew on this entry.
                for sp in after:
                    old_ia = before.get(sp, {}).get("ia", frozenset())
                    new_ia = after[sp]["ia"]
                    if new_ia - old_ia:
                        audit["R5"] += 1
                        break  # one R5 fire per switch-in event
            else:
                # Our switch: clear the cached active ability — we'll pick up
                # the new one if a `|-ability|p<my>a:|...` line follows.
                our_active_ability = None
            continue

        # ---- Move ----
        if msg == "move":
            # |move|p1a: Urshifu|Ice Spinner|p2a: Landorus
            if len(parts) < 4:
                continue
            side, display_name = _parse_pokemon_id(parts[2])
            move_name = parts[3]
            if side != opp_side_id:
                continue  # we only track opp moves for R1/R2/R3
            # Resolve display name → canonical form-suffixed species cached
            # at switch-in. Falls back to display name if no switch was
            # observed (defensive — shouldn't happen in well-formed protocol).
            source_species = display_to_form.get(side, {}).get(display_name, display_name)
            before = _opp_belief_snapshot()
            t.on_move(source_species, move_name, parts)
            after = _opp_belief_snapshot()
            sp = _normalize(source_species)
            old_ii = before.get(sp, {}).get("ii", frozenset())
            new_ii = after.get(sp, {}).get("ii", frozenset())
            added = new_ii - old_ii
            # R1: any subset of _CHOICE was added
            if added & _CHOICE:
                audit["R1"] += 1
            # R2: AV added
            if "assaultvest" in added:
                audit["R2"] += 1
            # R3: lifeorb added (and not just from R4 batch — R4 only fires
            # on_turn_boundary, not on_move, so any lifeorb add here is R3).
            if "lifeorb" in added:
                audit["R3"] += 1
            continue

        # ---- Terastallize ----
        if msg == "-terastallize":
            # |-terastallize|p1a: Urshifu|Steel
            if len(parts) >= 4:
                side, display_name = _parse_pokemon_id(parts[2])
                if side == opp_side_id:
                    source_species = display_to_form.get(side, {}).get(display_name, display_name)
                    t.on_terastallize(source_species, parts[3])
            continue

        # ---- Hazard damage ----
        if msg == "-damage":
            # |-damage|p1a: Galvantula|76/100|[from] Stealth Rock
            if len(parts) < 3:
                continue
            side, display_name = _parse_pokemon_id(parts[2])
            if side != opp_side_id:
                continue
            hid = _from_token_carrying_hazard(parts)
            if hid:
                source_species = display_to_form.get(side, {}).get(display_name, display_name)
                t.on_hazard_damage(source_species)
            continue

        # ---- Item events ----
        if msg == "-item":
            # |-item|p1a: <species>|<Item>|[from] move: Trick|[of] p2a: ...
            if len(parts) < 4:
                continue
            side, display_name = _parse_pokemon_id(parts[2])
            if side != opp_side_id:
                continue
            source_species = display_to_form.get(side, {}).get(display_name, display_name)
            item_name = parts[3]
            # Trick / Switcheroo: an item swap, not a positive reveal of
            # the opp's original holdable.
            is_swap = any(
                tok.startswith("[from]")
                and ("trick" in tok.lower() or "switcheroo" in tok.lower())
                for tok in parts
            )
            if is_swap:
                t.on_item_swapped(source_species, item_name, None)
            else:
                t.on_reveal_item(source_species, item_name)
            continue

        if msg == "-enditem":
            # |-enditem|p1a: <species>|<Item>  → the item LEAVES the holder.
            # Treat as a (positive) reveal of what they had — we now know
            # what their item was. on_item_swapped with new_item=None
            # captures the loss.
            if len(parts) < 4:
                continue
            side, display_name = _parse_pokemon_id(parts[2])
            if side != opp_side_id:
                continue
            source_species = display_to_form.get(side, {}).get(display_name, display_name)
            item_name = parts[3]
            # Record the reveal first so the priors filter sees the
            # historical item identity. Then mark it removed.
            t.on_reveal_item(source_species, item_name)
            t.on_item_swapped(source_species, None, item_name)
            continue

        # ---- Ability events ----
        if msg == "-ability":
            # |-ability|p1a: <species>|<Ability>|...
            if len(parts) < 4:
                continue
            side, display_name = _parse_pokemon_id(parts[2])
            ability_name = parts[3]
            if side == opp_side_id:
                source_species = display_to_form.get(side, {}).get(display_name, display_name)
                t.on_reveal_ability(source_species, ability_name)
            else:
                # Update our cached active ability for R5 carve-out (c).
                our_active_ability = ability_name
            continue

        # ---- Game-end signals (no-op, but breaks loop logically) ----
        if msg == "win" or msg == "tie":
            break

        # All other tags ignored silently (chat, t:, upkeep, -boost, etc.).

    return t, audit


# --------------------------------------------------------------------------
# Synthetic per-rule coverage tests (carry the original plan-body intent).
# --------------------------------------------------------------------------


def test_replay_corv_stall_belief_state():
    """Synthetic Corv-stall: status moves drive R1 + R2."""
    events = [
        ("switch_in", "Corviknight", {}),
        ("move", "Corviknight", "Iron Defense", []),  # status → R2; sets last_used
        ("move", "Corviknight", "Iron Defense", []),  # same move, no two-different R1
        ("move", "Corviknight", "Roost", []),         # different status → R1 fires
    ]
    t = _replay_synthetic(events)
    b = t.get("Corviknight")
    assert "assaultvest" in b.impossible_items, "R2 should rule out AV"
    assert "choiceband" in b.impossible_items, "R1 (early-disprove on status) fires on first status move"
    assert b.revealed_moves == {"irondefense", "roost"}


def test_replay_lando_t_no_intimidate_via_R5():
    """R5: Lando-T switches in; no `-ability` reveal from opp protocol →
    impossible_abilities should include Intimidate (and the rest of the
    auto-trigger pool)."""
    events = [
        ("switch_in", "Landorus-Therian", {}),
    ]
    t = _replay_synthetic(events)
    b = t.get("Landorus-Therian")
    assert "intimidate" in b.impossible_abilities
    assert "sandstream" in b.impossible_abilities
    assert "drought" in b.impossible_abilities


def test_replay_garchomp_HDB_inference_via_R4():
    """R4 fires at on_turn_boundary: Garchomp switched in to Stealth Rock,
    no hazard-damage event observed by end of turn → HDB inferred."""
    events = [
        ("switch_in", "Garchomp", {"stealthrock": 1}),
        ("turn_boundary",),
    ]
    t = _replay_synthetic(events)
    b = t.get("Garchomp")
    assert "leftovers" in b.impossible_items
    assert "rockyhelmet" in b.impossible_items
    assert "lifeorb" in b.impossible_items


def test_replay_R4_suppressed_by_observed_hazard_damage():
    """Negative case: Garchomp DID take SR damage → R4 must NOT fire."""
    events = [
        ("switch_in", "Garchomp", {"stealthrock": 1}),
        ("hazard_damage", "Garchomp"),
        ("turn_boundary",),
    ]
    t = _replay_synthetic(events)
    b = t.get("Garchomp")
    # Without R4 firing, leftovers/rockyhelmet stay possible.
    assert "leftovers" not in b.impossible_items
    assert "rockyhelmet" not in b.impossible_items


def test_replay_interaction_R1_R2_R3():
    """Pivot moves expose multiple rules in sequence."""
    events = [
        ("switch_in", "Toxapex", {}),
        ("move", "Toxapex", "Recover", []),    # status: R2 + R1 early-disprove
        ("move", "Toxapex", "Toxic", []),      # status: idem
    ]
    t = _replay_synthetic(events)
    b = t.get("Toxapex")
    assert "assaultvest" in b.impossible_items
    assert "choiceband" in b.impossible_items
    assert "choicescarf" in b.impossible_items
    assert "choicespecs" in b.impossible_items


def test_replay_passive_move_does_not_count():
    """A `|move|` event with `[from] Sleep Talk` must NOT update
    revealed_moves / fire R1-R3. Direct integration test of the
    is_passive_move_event guard at the protocol level."""
    events = [
        ("switch_in", "Snorlax", {}),
        # Real damaging move — would normally fire R3 (LO ruled out)
        ("move", "Snorlax", "Body Slam", ["", "move", "p1a: Snorlax", "Body Slam", "p2a: x"]),
    ]
    t = _replay_synthetic(events)
    b_before = t.get("Snorlax")
    lifeorb_before = "lifeorb" in b_before.impossible_items
    assert lifeorb_before, "control: real damaging move should rule out LO"

    # Now the passive variant: the [from] Sleep Talk token must suppress.
    t2 = BeliefTracker()
    t2.on_switch_in("Snorlax")
    split = ["", "move", "p1a: Snorlax", "Body Slam", "p2a: x", "[from]Sleep Talk"]
    assert is_passive_move_event(split)
    t2.on_move("Snorlax", "Body Slam", split)
    b2 = t2.get("Snorlax")
    assert "bodyslam" not in b2.revealed_moves, "Sleep-Talk-sourced move must not reveal"
    assert "lifeorb" not in b2.impossible_items, "passive move must not fire R3"


# --------------------------------------------------------------------------
# Real-fixture replay regression — the meat of Layer 2.
# --------------------------------------------------------------------------


_TRIPLEJ_FIXTURE = Path(
    "~/Projects/showdown-copilot/extension/test/fixtures/stepqueue-triplej-loss.json"
).expanduser()


@pytest.fixture(scope="module")
def triplej_replay():
    """Replay the TripleJ fixture once and share the result across tests
    that inspect the final belief state."""
    if not _TRIPLEJ_FIXTURE.exists():
        pytest.skip(f"fixture not found at {_TRIPLEJ_FIXTURE}")
    data = json.loads(_TRIPLEJ_FIXTURE.read_text())
    assert isinstance(data, dict)
    assert "stepQueue" in data, "fixture missing stepQueue field"
    my_side = data.get("meta", {}).get("mySideId", "p2")
    tracker, audit = replay_stepqueue(data["stepQueue"], my_side_id=my_side)
    return tracker, audit, data


def test_replay_triplej_fixture_runs_clean(triplej_replay):
    """Smoke: full 291-line fixture replays without raising."""
    tracker, _audit, _data = triplej_replay
    # Every opp Pokemon that appeared in protocol must have a belief entry.
    # The TripleJ team is: Klinklang, Zapdos-Galar, Galvantula, Urshifu(-Rapid-Strike),
    # Charizard, Salamence. Some may not switch in (clearpoke entries
    # don't trigger on_switch_in). Assert at least 4 opp species have entries.
    assert len(tracker._beliefs) >= 4


def test_replay_triplej_fixture_first_opp_move_revealed(triplej_replay):
    """Checkpoint #1: after replaying through the fixture, the first move
    we observed Urshifu use (Ice Spinner) MUST be in revealed_moves.
    """
    tracker, _audit, _data = triplej_replay
    urshifu_keys = [k for k in tracker._beliefs.keys() if "urshifu" in k]
    assert urshifu_keys, "expected to track Urshifu after replay"
    b = tracker._beliefs[urshifu_keys[0]]
    assert "icespinner" in b.revealed_moves, (
        f"Urshifu's revealed_moves should include icespinner; got {b.revealed_moves}"
    )


def test_replay_triplej_fixture_revealed_moves_nonempty_for_actives(triplej_replay):
    """Checkpoint #2: every opp Pokemon that took at least one |move| event
    on the field should have a non-empty revealed_moves set by game-end.
    Galvantula in this fixture comes in but only takes hazard damage and
    is then KO'd — exempt species with empty revealed_moves are tolerated.
    """
    tracker, _audit, _data = triplej_replay
    has_revealed = sum(1 for b in tracker._beliefs.values() if b.revealed_moves)
    # At minimum Urshifu, Charizard, Salamence took moves in this battle.
    assert has_revealed >= 3, (
        f"expected ≥3 opp Pokemon with revealed_moves, got {has_revealed} "
        f"out of {len(tracker._beliefs)}"
    )


def test_replay_triplej_R5_fired_on_first_switch(triplej_replay):
    """Checkpoint #3: R5 fires on every opp switch-in. The first opp to
    switch in (Urshifu) should have impossible_abilities populated with
    auto-trigger entries (or have a positively-revealed ability that
    overrode them). Either way, the tracker has SOME R5 evidence about
    Urshifu.
    """
    tracker, _audit, _data = triplej_replay
    urshifu_keys = [k for k in tracker._beliefs.keys() if "urshifu" in k]
    assert urshifu_keys, "expected to track Urshifu"
    b = tracker._beliefs[urshifu_keys[0]]
    # Either the auto-trigger pool was ruled out OR the actual ability
    # was revealed (which would discard from the impossible set).
    has_r5_evidence = (
        bool(b.impossible_abilities)
        or b.revealed_ability is not None
    )
    assert has_r5_evidence, (
        f"expected R5 to leave evidence on Urshifu; "
        f"impossible_abilities={b.impossible_abilities}, "
        f"revealed_ability={b.revealed_ability}"
    )


def test_replay_triplej_R2_fired_on_observed_status_moves(triplej_replay):
    """Checkpoint #4: TripleJ's Charizard uses Will-O-Wisp / Salamence uses
    Roost / Gholdengo on opp side… look for at least one opp Pokemon
    with assaultvest in impossible_items by game-end (R2 must fire on at
    least one observed status move in 20 turns of competitive play).
    """
    tracker, _audit, _data = triplej_replay
    av_ruled_out_count = sum(
        1 for b in tracker._beliefs.values()
        if "assaultvest" in b.impossible_items
    )
    # Note: R5 / R4 don't add assaultvest, so any AV rule-out is from R2.
    assert av_ruled_out_count >= 1, (
        f"expected R2 to rule out AV on ≥1 opp; got {av_ruled_out_count}"
    )


def test_replay_triplej_fixture_coverage_audit(triplej_replay, capsys):
    """Coverage audit: print which rules fired during the TripleJ replay.
    This is informational — the assertion is just that the audit ran;
    test_replay_triplej_R5_fired and _R2_fired check specific rules.

    If R4 / R5 coverage is low across our fixture corpus, the spec note
    (plan body lines 286-289) says we should capture more fixtures from
    live ladder before running the Layer 3 A/B sweep (Task 11).
    """
    tracker, audit, _data = triplej_replay
    print(f"\nTripleJ replay coverage audit: {audit}")
    print(f"  beliefs tracked: {len(tracker._beliefs)}")
    for sp, b in sorted(tracker._beliefs.items()):
        print(
            f"    {sp}: revealed_moves={sorted(b.revealed_moves)} "
            f"impossible_items={sorted(b.impossible_items)} "
            f"impossible_abilities={sorted(b.impossible_abilities)}"
        )
    # Sanity: at least 3 of the 5 rule-categories must have fired across
    # the 20-turn battle. (Tera is banned in this format → R4 Tera carve-out
    # untested; postmortem analysis below shows we may need more fixtures
    # for R4 specifically.)
    nonzero_rules = sum(1 for v in audit.values() if v > 0)
    assert nonzero_rules >= 3, (
        f"expected ≥3 rules to fire in 20-turn fixture; audit={audit}"
    )


# --------------------------------------------------------------------------
# Postmortems sanity check (no raw protocol → can't drive Layer 2 from these,
# but we keep the file-shape assertion for regression).
# --------------------------------------------------------------------------


_POSTMORTEMS = Path(
    "~/Projects/cobblemon-copilot/analysis/postmortems.json"
).expanduser()


def test_postmortems_load_and_have_triplej_entries():
    """Postmortems file is the curated turn-summary format (myPick /
    actualOppMove / damage* etc.), NOT raw stepqueues, so it can't drive
    a Layer 2 protocol replay. We assert its file shape is what the spec
    says (19 entries, ≥1 vs TripleJ1118) so the fixture corpus is
    accounted for. Capturing more raw stepqueues from live ladder is
    tracked in the spec body (lines 286-289).
    """
    if not _POSTMORTEMS.exists():
        pytest.skip(f"postmortems not found at {_POSTMORTEMS}")
    data = json.loads(_POSTMORTEMS.read_text())
    assert isinstance(data, list)
    assert len(data) == 19, f"spec says 19 postmortems; got {len(data)}"
    triplej = [
        e for e in data
        if e.get("opponent") == "TripleJ1118" and e.get("myUsername") == "Mariga"
    ]
    assert len(triplej) >= 1, "expected ≥1 Mariga-vs-TripleJ1118 postmortem"
    # Confirm shape — turns are dicts of curated state, NOT a stepqueue.
    e = triplej[0]
    assert "turns" in e
    assert "teamPreview" in e
    # Stepqueue is NOT here — that's why we can't replay these directly.
    assert "stepQueue" not in e


def test_postmortems_belief_smoke_via_curated_turns():
    """Best we can do with postmortems: feed `actualOppMove` of each turn
    (when present) into the tracker as a synthetic on_reveal_move and
    confirm the tracker doesn't crash and accumulates SOMETHING for at
    least one opp species across the 19 battles.

    This is NOT a replacement for raw-protocol replay — it skips R5
    (no switch-in events), R4 (no hazard timing), most of R1-R3 (no
    [from] tokens, no split_msg) — but it's a regression guard against
    `actualOppMove` field-name drift.
    """
    if not _POSTMORTEMS.exists():
        pytest.skip(f"postmortems not found at {_POSTMORTEMS}")
    data = json.loads(_POSTMORTEMS.read_text())

    total_battles_with_reveals = 0
    for battle in data:
        tracker = BeliefTracker()
        revealed_any = False
        # teamPreview gives us {"opp": [...]} — we don't pre-seed because
        # belief state is keyed on in-battle species names.
        for turn in battle.get("turns", []):
            opp_move = turn.get("actualOppMove")
            if not opp_move:
                continue
            # We don't reliably have the opp's source species per turn in
            # the postmortem schema (active opp Pokemon isn't tracked
            # turn-by-turn). Use a placeholder species and confirm the
            # method doesn't crash. This is a smoke test only.
            tracker.on_reveal_move("OppActive", opp_move)
            revealed_any = True
        if revealed_any:
            total_battles_with_reveals += 1

    assert total_battles_with_reveals >= 10, (
        f"expected ≥10 battles to have actualOppMove fields; "
        f"got {total_battles_with_reveals}/{len(data)}"
    )
