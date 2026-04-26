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


# Status moves (category = "Status" in poke-env / Showdown). This is a
# Phase 1 hardcoded subset covering the most-used status moves on
# competitive ladder. Phase 2 should replace this with a lookup against
# poke-env's Move.category or a generated complete list.
#
# EXCLUDED (these are damaging-category in gen 5+ — AV holders CAN use them):
# - Knock Off (Dark physical)
# - U-turn / Volt Switch / Flip Turn (physical/special pivoting)
# Including these would mis-fire R2 and incorrectly rule out AV.
#
# INCLUDED (status-category pivoting/utility — AV holders CANNOT use them):
# - Teleport (status in gen 8+, gen 8+ pivots; before gen 8 was useless but
#   still status). Used by AV-eligible defensive pivots like Slowbro/Blissey.
# - Parting Shot (status in gen 6+, debuffs target Atk/SpA and pivots).
#   Used by AV-eligible defensive pivots like Pangoro/Whimsicott.
_STATUS_MOVES: frozenset[str] = frozenset({
    "stealthrock", "spikes", "toxicspikes", "stickyweb",
    "willowisp", "toxic", "thunderwave", "glare", "sleeppowder",
    "spore", "yawn", "lovelykiss",
    "swordsdance", "nastyplot", "calmmind", "bulkup", "irondefense",
    "shellsmash", "dragondance", "quiverdance", "tailglow",
    "roost", "recover", "softboiled", "synthesis", "moonlight",
    "morningsun", "milkdrink", "wish", "healingwish",
    "protect", "detect", "kingsshield", "spikyshield", "obstruct",
    "banefulbunker", "burningbulwark", "silktrap", "maxguard",
    "taunt", "encore", "torment", "disable",
    "lightscreen", "reflect", "auroraveil",
    "trick", "switcheroo",
    "trickroom", "tailwind",
    "defog", "rapidspin", "courtchange",
    "haze", "clearsmog",
    "leechseed", "substitute",
    "teleport", "partingshot",  # status-category pivoting moves
})


def _is_status_move(move_id: str) -> bool:
    """True if move_id is a known status-category move.

    Phase 1: hardcoded set above. Returns False for unknown moves to
    avoid false positives (better to miss an AV inference than to
    incorrectly rule out AV when the move is actually damaging).
    """
    return _normalize(move_id) in _STATUS_MOVES


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


# Species whose Phase-1 ability pool includes Sheer Force or Magic Guard.
# If opp's species is in this set, R3 does NOT fire (Life Orb stays
# possible). Reasoning: Sheer Force suppresses LO recoil entirely on
# secondary-effect moves; Magic Guard is immune to all indirect damage
# (including LO recoil). For species that could have either ability, we
# can't conclude "not LO" merely from absence-of-recoil on a single
# damaging move.
#
# Phase 2 should derive this set programmatically from chaos data — see
# Task 8 (R4) for the same data-driven approach to Levitate / Magic
# Guard. Use normalized species ids (lowercase alphanumeric) — they're
# compared against `_normalize(species)`.
_SHEERFORCE_OR_MAGICGUARD_SPECIES: frozenset[str] = frozenset({
    # Sheer Force candidates (gen9 NatDex pool). Normalized species ids
    # match what `_normalize(showdown_species_name)` produces — for
    # Paldean Tauros forms that's "taurospaldea{combat,blaze,aqua}"
    # (Showdown form names are Combat / Blaze / Aqua, not Fire / Water).
    "tauros",
    "taurospaldeacombat", "taurospaldeablaze", "taurospaldeaaqua",
    "darmanitan", "darmanitangalar", "darmanitangalarzen",
    "feraligatr", "krookodile", "mienshao",
    "nidoking", "nidoqueen",
    "ursaring", "rampardos", "bouffalant",
    "irontreads",
    # Magic Guard candidates
    "sigilyph", "alakazam", "alakazammega",
    "clefable", "clefairy", "cleffa",
    "reuniclus", "duosion", "solosis",
    "spinda",
    # Not exhaustive — Phase 2 should derive from chaos data
})


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

    def on_move(
        self, species: str, move_id: str, split_msg: list[str] | None
    ) -> None:
        """Called on every |move| protocol event. The `[from]`-token guard
        suppresses R1/R2/R3 on passive-source moves (Sleep Talk, Dancer,
        Future Sight impact, etc.). Locked Move (Outrage) is NOT passive
        and DOES fire the rules normally.

        This is the protocol-level entry point — callers from the live
        message hook should call `on_move`, NOT `on_reveal_move` directly,
        so the `[from]` guard runs. `on_reveal_move` is an internal state
        recorder used by `on_move` and the harness fallback path.

        Task 7 will extend this method with R1 logic. R2 (Task 5): if the
        opp uses a status-category move, Assault Vest is ruled out (AV
        blocks status moves entirely). R3 (this task): if the opp uses
        any damaging move and the species is NOT in the SF/MG ability
        pool, Life Orb is ruled out — LO recoil announces itself in the
        protocol, so absence of recoil on a damaging move from a
        non-SF/MG candidate is free evidence that LO is impossible.

        ORDER NOTE for Task 7: R1 (two-different-moves Choice disproof)
        must fire BEFORE the `on_reveal_move` call — R1 needs to compare
        the new move against the PREVIOUS `last_used_move`, which
        `on_reveal_move` overwrites. R3 fires AFTER state recording,
        alongside R2. Final order:
            [from] guard → R1 → on_reveal_move → R2 → R3
        """
        # Defensive: callers should pass a list, but Task 9 harness wiring
        # is the first real producer and a malformed protocol line could
        # surface as None. Treat None as "no [from] tokens" (active move).
        if split_msg is None:
            split_msg = []
        if is_passive_move_event(split_msg):
            return  # Passive — don't update revealed_moves or fire rules
        # Pass through to skeleton state recording (revealed_moves, last_used)
        self.on_reveal_move(species, move_id)
        b = self.get(species)
        norm_move = _normalize(move_id)
        norm_species = _normalize(species)

        # R2: Assault Vest blocks status moves; if a status move was used,
        # AV is ruled out. Status moves don't trigger R3 (LO doesn't
        # recoil on status moves anyway, so firing R3 on a status move
        # would be a category error) — early return after R2.
        if _is_status_move(norm_move):
            b.impossible_items.add("assaultvest")
            b.used_status_move = True
            return

        # R3 (Task 6): damaging move → LO ruled out except for SF/MG
        # candidates. Sheer Force suppresses LO recoil; Magic Guard is
        # immune to indirect damage. If the species could have either
        # ability, absence of recoil isn't evidence — leave LO possible.
        if norm_species not in _SHEERFORCE_OR_MAGICGUARD_SPECIES:
            b.impossible_items.add("lifeorb")

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
