"""Per-opponent-Pokemon belief tracking for the Showdown copilot.

Phase 1 ships the OpponentBelief dataclass and the BeliefTracker class.
Inference rules R1-R5 are added in subsequent tasks (Tasks 4-8). The
skeleton compiles and the state-recording (on_reveal_*) methods work;
the rule-firing methods (on_switch_in, on_move_used) are stubs that
record state without yet emitting impossible_items / impossible_abilities
inferences.

See docs/superpowers/specs/2026-04-26-plan-h-posterior-tracking-design.md
"""
from __future__ import annotations

from dataclasses import dataclass, field


def _normalize(name: str) -> str:
    """Match the same normalization the rest of the codebase uses."""
    return "".join(c.lower() for c in name if c.isalnum())


@dataclass
class OpponentBelief:
    """Per-opp-Pokemon belief state. Mutated turn by turn.

    Field set follows foul-play's algorithmic shape (see notes in
    docs/superpowers/notes/foul-play-battle-modifier-paraphrase.md
    section 2 + 8 for the field-by-field correspondence). Notably:
    - R3 fires inline on the move event → no `used_damaging_move`.
    - R5 fires eagerly on switch-in → no `observed_*_on_switch_in`.
    - R4 conclusion is deferred to end-of-turn → no
      `observed_hazard_damage_on_switch_in` (the SR damage line
      arrives AFTER the switch line in the same buffer flush).

    Phase 1 omits speed_range, hidden_power_possibilities, boosts,
    and volatile_statuses — those are Phase 2 (speed_range is queued
    as Phase 2 task #1).
    """
    species: str  # canonical (lowercase normalized)
    revealed_moves: set[str] = field(default_factory=set)
    revealed_item: str | None = None
    revealed_ability: str | None = None
    impossible_items: set[str] = field(default_factory=set)
    impossible_abilities: set[str] = field(default_factory=set)
    # Item-swap tracking (Trick / Switcheroo / Knock Off — for R1 correctness)
    removed_item: str | None = None
    # Tera tracking (R4 hazard-immunity carve-outs depend on Tera type)
    terastallized: bool = False
    tera_type: str | None = None
    # Move-history tracking (drives R1; reset on switch-in and on item-swap)
    last_used_move: str | None = None
    moves_used_since_switch_in: list[str] = field(default_factory=list)
    used_status_move: bool = False  # diagnostic only; R2 fires inline
    # Switch-in context (drives R4 — consumed at on_turn_boundary).
    # took_hazard_damage_this_stretch is set True by on_hazard_damage when
    # SR / Spikes / T-Spikes hits this Pokemon. R4 (Task 8) fires only
    # when (just_switched_in AND hazards active AND NOT took_damage).
    just_switched_in: bool = False
    side_hazards_at_switch_in: dict[str, int] = field(default_factory=dict)
    took_hazard_damage_this_stretch: bool = False


# ---------- Module-level helpers ----------


_PASSIVE_FROM_TOKENS_ALWAYS: frozenset[str] = frozenset({
    "[from]Sleep Talk", "[from] Sleep Talk",
    "[from]move: Sleep Talk", "[from] move: Sleep Talk",
})

_PASSIVE_FROM_TOKENS_EXEMPT: frozenset[str] = frozenset({
    "[from]lockedmove", "[from] lockedmove",
})


def is_passive_move_event(split_msg: list[str]) -> bool:
    """True if this |move| event is from a passive source (Sleep Talk,
    Dancer copy, Future Sight delayed hit, Pursuit on switch, Z/Max move,
    etc.) that should NOT count as the mover's free choice. R1/R2/R3
    short-circuit when True.

    `[from]lockedmove` (Outrage / Petal Dance / Thrash) is NOT passive —
    it IS the mover's choice, just constrained to repeat. Don't treat
    it as passive.
    """
    for tok in split_msg:
        if tok in _PASSIVE_FROM_TOKENS_ALWAYS:
            return True
        if tok.startswith("[from]") and tok not in _PASSIVE_FROM_TOKENS_EXEMPT:
            return True
    return False


def has_type(
    belief: OpponentBelief,
    target_type: str,
    base_types: tuple[str, ...],
) -> bool:
    """Tera-aware type check. After terastallization, Tera type REPLACES
    base types for type-effectiveness purposes (gen 9). Used by R4
    hazard-immunity carve-outs (Tera Flying ignores Spikes; Tera Steel
    ignores T-Spikes; etc.).
    """
    if belief.terastallized and belief.tera_type:
        return target_type.lower() == belief.tera_type.lower()
    return any(target_type.lower() == t.lower() for t in base_types)


# Abilities that announce themselves on switch-in. Absence of the
# announcement → ability ruled out, modulo the carve-outs below.
# Drives R5 (Task 4): on every opp switch-in we eagerly add ALL of these
# to impossible_abilities, with three carve-outs (gen3 Pressure silent,
# weather-setter when matching weather already up, our active has
# Neutralizing Gas which suppresses the entire pass).
_AUTO_TRIGGER_ABILITIES_ON_SWITCH_IN: frozenset[str] = frozenset({
    "intimidate",
    "sandstream",
    "drought",
    "drizzle",
    "snowwarning",
    "pressure",
    "neutralizinggas",
})

# Map weather-setter ability → matching weather (Showdown protocol id,
# lowercase). Used by R5 carve-out (b): a weather-setter is silent on
# switch-in if the matching weather is already up.
_ABILITY_TO_WEATHER: dict[str, str] = {
    "sandstream": "sandstorm",
    "drought": "sunnyday",
    "drizzle": "raindance",
    "snowwarning": "snow",
}


# ---------- BeliefTracker ----------


class BeliefTracker:
    """Tracks per-Pokemon belief for one opp side across a single battle.

    Stateless across battles — caller creates a fresh BeliefTracker() per
    battle. Pokemon entries are created lazily on first reference; we
    don't pre-seed from team preview because team-preview species names
    may not exactly match in-battle species (e.g., Urshifu base form
    vs Urshifu-Rapid-Strike).
    """
    def __init__(self) -> None:
        self._beliefs: dict[str, OpponentBelief] = {}

    def get(self, species: str) -> OpponentBelief:
        """Return the belief entry for `species` (creating if absent)."""
        norm = _normalize(species)
        if norm not in self._beliefs:
            self._beliefs[norm] = OpponentBelief(species=norm)
        return self._beliefs[norm]

    def clear(self) -> None:
        """Drop all per-Pokemon belief entries — but preserve the tracker
        instance itself. Used by SpectatorAdapter.on_team_preview at the
        start of each new battle. Replacing `self._beliefs` (rather than
        the tracker as a whole) keeps any external references alive — the
        harness / live message hook can hold a reference to the tracker
        across battles without losing the connection.
        """
        self._beliefs = {}

    # --- State-recording API (called by the live message hook) ---

    def on_reveal_move(self, species: str, move_id: str) -> None:
        """Record that `species` used move `move_id`. Phase 1 skeleton
        only updates revealed_moves and the move-history fields; the
        inference-rule firings (R1, R2, R3) are added in Tasks 4-7
        with `[from]`-token guard at the call site (caller must check
        is_passive_move_event before calling this method).

        Empty / whitespace-only `move_id` is ignored — _normalize would
        produce "", which would silently corrupt R1 state (last_used_move
        would compare unequal to any real move on the next call).
        """
        norm_move = _normalize(move_id)
        if not norm_move:
            return
        b = self.get(species)
        b.revealed_moves.add(norm_move)
        b.last_used_move = norm_move
        b.moves_used_since_switch_in.append(norm_move)

    def on_reveal_item(self, species: str, item_id: str) -> None:
        """Record opp's item identity (PROTOCOL-asserted, not inferred).
        Empty / whitespace-only `item_id` is ignored.
        """
        norm_item = _normalize(item_id)
        if not norm_item:
            return
        b = self.get(species)
        b.revealed_item = norm_item

    def on_reveal_ability(self, species: str, ability_id: str) -> None:
        """Record opp's ability identity. Empty / whitespace-only
        `ability_id` is ignored.
        """
        norm_ability = _normalize(ability_id)
        if not norm_ability:
            return
        b = self.get(species)
        b.revealed_ability = norm_ability

    def on_item_swapped(
        self, species: str, new_item: str | None, old_item: str | None
    ) -> None:
        """Called on Trick / Switcheroo / Knock Off events that swap or
        remove items. Resets R1's move-history fields, since the opp's
        strategic state has flipped and prior move-history is no longer
        evidence about their item. Without this hook, R1 mis-fires
        after a Trick (the opp gets a new item; the next move looks
        like 'two different moves used' to a naive R1).
        """
        b = self.get(species)
        b.removed_item = old_item
        b.revealed_item = _normalize(new_item) if new_item else None
        b.last_used_move = None
        b.moves_used_since_switch_in = []

    def on_terastallize(self, species: str, tera_type: str) -> None:
        """Called on `|-terastallize|` protocol message. Sets the Tera
        flags so `has_type()` and R4 carve-outs see the new type.
        """
        b = self.get(species)
        b.terastallized = True
        b.tera_type = _normalize(tera_type)

    def on_switch_in(
        self,
        species: str,
        side_hazards: dict[str, int] | None = None,
        current_weather: str | None = None,
        generation: int = 9,
        our_active_ability: str | None = None,
    ) -> None:
        """Called when `species` switches in to the opp's active slot.

        Resets per-Pokemon switch-in state and records active hazards on
        the opp side. R4 reads `side_hazards_at_switch_in` at end-of-turn;
        R5 fires inline here via `_eagerly_rule_out_auto_trigger_abilities`.

        Args:
            species: opp Pokemon switching in (display or normalized form).
            side_hazards: hazard ids → layer count active on the opp side.
            current_weather: Showdown protocol weather id (lowercase) —
                "sandstorm" / "sunnyday" / "raindance" / "snow" / None.
                Drives R5 carve-out (b): a weather-setter is silent on
                switch-in if the matching weather is already up.
            generation: gen number; drives R5 carve-out (a) — gen 3
                Pressure is silent on switch-in. Defaults to 9.
            our_active_ability: our active Pokemon's ability (display or
                normalized form). Drives R5 carve-out (c) — Neutralizing
                Gas suppresses ALL opp on-switch-in announcements.
        """
        b = self.get(species)
        b.just_switched_in = True
        b.took_hazard_damage_this_stretch = False
        b.side_hazards_at_switch_in = dict(side_hazards) if side_hazards else {}
        # Move-history is per-stretch-on-field; reset on switch in
        b.moves_used_since_switch_in = []
        b.last_used_move = None
        # R5: eager rule-out of auto-trigger abilities (constant-size loop)
        self._eagerly_rule_out_auto_trigger_abilities(
            species=species,
            current_weather=current_weather,
            generation=generation,
            our_active_ability=our_active_ability,
        )

    def _eagerly_rule_out_auto_trigger_abilities(
        self,
        species: str,
        current_weather: str | None,
        generation: int,
        our_active_ability: str | None,
    ) -> None:
        """R5 — opponent just switched in. For each auto-trigger ability,
        if the announcement *would* have fired and didn't, rule it out by
        adding it to `impossible_abilities`.

        Carve-outs (skip = leave the ability possible):
        (a) gen 3 Pressure is silent in that gen — skip Pressure if gen==3.
        (b) Weather-setter abilities are silent if the matching weather
            is already up — skip the matching one in that case.
        (c) Our active has Neutralizing Gas — suppresses ALL opp
            on-switch-in announcements; skip the entire pass.

        Note: this fires unconditionally on every switch-in. If the opp
        DOES have one of these abilities, the protocol's separate ability
        announcement will trigger `on_reveal_ability`, setting
        `revealed_ability` positively. The priors filter consults
        `revealed_ability` first, then `impossible_abilities` — so a
        positive reveal overrides any false impossibility recorded here.
        """
        # Carve-out (c): if our active suppresses ALL announcements, bail.
        if our_active_ability and _normalize(our_active_ability) == "neutralizinggas":
            return

        # Normalize current_weather once so carve-out (b) compares cleanly.
        norm_weather = _normalize(current_weather) if current_weather else None

        b = self.get(species)
        for ab in _AUTO_TRIGGER_ABILITIES_ON_SWITCH_IN:
            # Carve-out (a): gen 3 Pressure is silent.
            if ab == "pressure" and generation == 3:
                continue
            # Carve-out (b): weather-setter when matching weather already up.
            matching_weather = _ABILITY_TO_WEATHER.get(ab)
            if matching_weather and norm_weather == matching_weather:
                continue
            b.impossible_abilities.add(ab)

    def on_switch_out(self, species: str) -> None:
        """Called when `species` switches out. Clears the just-switched-in
        flag (the next on_switch_in call will reset it again on return).
        """
        b = self.get(species)
        b.just_switched_in = False

    def on_hazard_damage(self, species: str) -> None:
        """Called when `species` takes damage from an entry hazard
        (Stealth Rock / Spikes / Toxic Spikes). Sets the per-Pokemon
        flag that on_turn_boundary reads to suppress R4. Without this
        flag, R4 (Task 8) would over-fire HDB on every switch-in to a
        hazardy side, including the cases where damage actually happened.
        """
        b = self.get(species)
        b.took_hazard_damage_this_stretch = True

    def on_turn_boundary(self) -> None:
        """Called once per `|turn|` protocol event. R4 (HDB inference)
        fires here for any Pokemon that just switched in this turn AND
        had hazards active on its side AND took no hazard damage. The
        end-of-turn timing is required because the protocol emits the
        switch event BEFORE the hazard-damage event in the same buffer
        flush — we can't conclude inline on the switch event.

        Phase-1 skeleton: just consume `just_switched_in` and
        `took_hazard_damage_this_stretch` flags. Task 8 (R4) implements
        the actual conclusion logic.
        """
        for b in self._beliefs.values():
            b.just_switched_in = False
            b.took_hazard_damage_this_stretch = False
