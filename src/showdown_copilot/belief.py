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

from showdown_copilot._ability_pools import (
    _CHLOROPHYLL_SPECIES,
    _LEVITATE_SPECIES,
    _MAGICGUARD_SPECIES,
    _PROTOSYNTHESIS_SPECIES,
    _QUARKDRIVE_SPECIES,
    _QUICKFEET_SPECIES,
    _SANDRUSH_SPECIES,
    _SLUSHRUSH_SPECIES,
    _SURGESURFER_SPECIES,
    _SWIFTSWIM_SPECIES,
    _UNBURDEN_SPECIES,
)
from showdown_copilot.stats import (
    _NATURE_TO_SPE_MULT,
    apply_bot_speed_modifier_chain,
    compute_speed_stat,
)


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

    # ----- Speed inference (Phase 2) -----
    # Final Speed-stat range at level 100. None = "not narrowed yet" (priors
    # filter falls through to all spreads). Inclusive bounds. Updated by
    # BeliefTracker.on_turn_boundary_speed; reset by on_item_swapped.
    speed_range: tuple[int, int] | None = None

    # True iff bracket math forces the Choice Scarf hypothesis. When set,
    # the priors filter forces item == "choicescarf" (subject to existing
    # impossible_items rule-outs). Cleared on rollback when contradicting
    # evidence (R1 firing in on_move, positive item reveal of non-scarf
    # Choice item, item swap) lands.
    item_inferred_choicescarf: bool = False

    # Audit history of every (turn, my_active_speed_post_modifiers, kind)
    # observation. kind ∈ {"opp_first", "us_first", "skipped:cant", ...}.
    # Used for: (a) replay-test asserting expected narrowings,
    # (b) rollback — when scarf is disproved we recompute speed_range
    # from the non-scarf branch of every observation.
    speed_observations: list[tuple[int, int, str]] = field(default_factory=list)


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
#
# AUDITED 2026-04-26 against poke-env's gen9 pokedex (Plan H Task 6
# review). Removed 11 species that DO NOT actually have SF or MG in
# their gen-9 ability pool — each was a silent R3 false-negative:
# - taurospaldea{combat,blaze,aqua} (Intimidate / Anger Point / Cud Chew)
# - darmanitangalar / darmanitangalarzen (Gorilla Tactics / Zen Mode)
# - krookodile (Intimidate / Moxie / Anger Point)
# - mienshao (Inner Focus / Regenerator / Reckless)
# - bouffalant (Reckless / Sap Sipper / Soundproof)
# - irontreads (Quark Drive only — Paradox)
# - ursaring (Guts / Quick Feet / Unnerve)
# - spinda (Own Tempo / Tangled Feet / Contrary)
# - alakazammega (Trace only — Mega replaces base ability)
# Only base Tauros (not Paldean) has Sheer Force in its pool.
# `test_pool_membership_sanity` enforces this set against poke-env
# data so future drift is caught at commit time.
_SHEERFORCE_OR_MAGICGUARD_SPECIES: frozenset[str] = frozenset({
    # Sheer Force candidates (verified gen9 NatDex pool)
    "tauros",                                # Anger Point / Sheer Force / Cud Chew
    "darmanitan",                            # Sheer Force / Zen Mode (HA)
    "feraligatr",                            # Torrent / Sheer Force (HA)
    "nidoking", "nidoqueen",                 # Poison Point / Rivalry / Sheer Force (HA)
    "rampardos",                             # Mold Breaker / Sheer Force (HA)
    # Magic Guard candidates (verified gen9 NatDex pool)
    "sigilyph",                              # Wonder Skin / Magic Guard / Tinted Lens
    "alakazam",                              # Synchronize / Inner Focus / Magic Guard (HA)
    "clefable", "clefairy", "cleffa",        # Cute Charm / Magic Guard / Unaware
    "reuniclus", "duosion", "solosis",       # Overcoat / Magic Guard / Regenerator
    # Not exhaustive — Phase 2 should derive from chaos data
})


# R1: Choice items lock the holder into the first-used move until switch-out.
# Two-different-moves observed without an intervening switch (or item swap)
# disproves the entire Choice trio.
_CHOICE_ITEMS: frozenset[str] = frozenset({
    "choiceband", "choicescarf", "choicespecs",
})

# Moves that are categorically incompatible with Choice items. The
# categorical fact is: Choice locks the holder to its first move AND
# requires that move to be damaging-category. Therefore observing the
# opp use any STATUS-category move proves they aren't Choice-locked
# (the move would have failed under the lock).
#
# Derived from _STATUS_MOVES, with two exceptions where status-and-
# Choice-compatible:
# - Trick / Switcheroo: a Choice user CAN use these as their locked-
#   into first move to pass the Choice item to opp (a real strategy).
#   Observing Trick/Switcheroo doesn't prove the user isn't Choice-locked.
#
# Damaging pivots (Knock Off, U-turn, Volt Switch, Flip Turn) are not
# here because they're not in _STATUS_MOVES — they're damaging-category
# and Choice users routinely run U-turn for momentum.
#
# `test_CHOICE_INCOMPATIBLE_MOVES_subset_of_STATUS_MOVES` enforces this
# derivation invariant at commit time so future drift is caught.
_CHOICE_INCOMPATIBLE_MOVES: frozenset[str] = _STATUS_MOVES - frozenset({
    "trick", "switcheroo",
})


# ---------- R4 (Task 8): Heavy-Duty Boots from hazard immunity ----------

# Base-types lookup. Sourced from poke-env's authoritative gen-9 pokedex
# at module load time so R4's Tera-aware type carve-outs work for ANY
# species — not just an 8-entry hand-curated subset.
#
# Without this, R4 over-fires HDB on common base-Flying types not in the
# old hand-curated dict (Charizard, Talonflame, Gholdengo, Salamence,
# Dragonite, Yveltal, etc.) — `has_type(b, "Flying", ())` returns False
# for unlisted species, so the Tera-Flying carve-out is silently skipped
# and the Pokemon falsely gets HDB inferred when Spikes is active.
#
# Same poke-env-as-ground-truth pattern as scripts/extract_ability_pools.py.
def _build_base_types_table() -> dict[str, tuple[str, ...]]:
    """Build {normalized_species → (Type1, Type2)} from poke-env's gen-9
    pokedex. Called once at module load.
    """
    from poke_env.data import GenData

    pokedex = GenData.from_gen(9).pokedex
    out: dict[str, tuple[str, ...]] = {}
    for species_id, entry in pokedex.items():
        types = entry.get("types") or []
        if types:
            out[species_id] = tuple(types)
    return out


_BASE_TYPES: dict[str, tuple[str, ...]] = _build_base_types_table()


# Phase 2 (speed-range narrowing): mirror of _BASE_TYPES for base Speed
# stat lookup. Used by on_turn_boundary_speed's forced-scarf check
# (max_non_scarf comparison) and by priors.py's spread filter.
def _build_base_speeds_table() -> dict[str, int]:
    """Build {normalized_species → base_speed_stat} from poke-env's gen-9
    pokedex. Called once at module load.
    """
    from poke_env.data import GenData

    pokedex = GenData.from_gen(9).pokedex
    out: dict[str, int] = {}
    for species_id, entry in pokedex.items():
        stats = entry.get("baseStats") or {}
        spe = stats.get("spe")
        if spe is not None:
            out[species_id] = int(spe)
    return out


_BASE_SPEEDS: dict[str, int] = _build_base_speeds_table()


# Sentinel for "no upper bound on opp speed". Used both in the active
# narrowing algorithm (when only the lower bound has been narrowed) and
# in the rollback-replay path. Picked at 9999 because no real Pokemon
# has Speed > 1500 even with maximum buffs.
_SPEED_HI_SENTINEL: int = 9999

# Choice items minus scarf — used by on_reveal_item rollback path to
# detect "we inferred scarf, but Showdown just told us it's actually
# Band/Specs". When that mismatch lands, _recompute_speed_range_no_scarf
# replays observations under non-scarf assumption.
_NON_SCARF_CHOICE: frozenset[str] = frozenset({"choiceband", "choicespecs"})


def can_have_speed_modified(
    belief: "OpponentBelief",
    weather: str | None,
    terrain: str | None,
) -> bool:
    """True iff the opp's species could have an unobserved speed-boosting
    condition. Speed inference must SKIP the turn when this returns True.

    Mirrors foul-play's `can_have_speed_modified` (paraphrased — see
    `analysis/plan-h-phase2-research/foul-play-speed.md` Part A.6) but
    EXTENDED with a Booster Energy / Quark Drive gate to fix the bug
    flagged in research D.4 (foul-play silently mis-narrows when opp's
    species could have protosynthesisspe but the volatile hasn't fired
    yet).

    Args:
      belief: the opp's OpponentBelief (we read revealed_ability +
        removed_item to short-circuit on known abilities).
      weather: normalized Showdown weather string ("RainDance",
        "SunnyDay", "Sandstorm", "Hail", "Snow") or None.
      terrain: normalized terrain string ("ELECTRIC_TERRAIN", etc.) or None.

    Returns True if speed inference is unsafe this turn.
    """
    species = belief.species
    if belief.revealed_ability is not None:
        return False  # ability known, no hidden boost possible

    # 1. Unburden post-item-loss
    if belief.removed_item is not None and species in _UNBURDEN_SPECIES:
        return True

    # 2. Weather-conditional ability speedups
    if weather == "RainDance" and species in _SWIFTSWIM_SPECIES:
        return True
    if weather == "SunnyDay" and species in _CHLOROPHYLL_SPECIES:
        return True
    if weather == "Sandstorm" and species in _SANDRUSH_SPECIES:
        return True
    if weather in ("Hail", "Snow") and species in _SLUSHRUSH_SPECIES:
        return True

    # 3. Electric Terrain Surge Surfer
    if terrain == "ELECTRIC_TERRAIN" and species in _SURGESURFER_SPECIES:
        return True

    # 4. Quick Feet on paralysis (Status.PAR equivalent)
    # Note: we don't track opp's status directly; assume worst-case "could
    # be paralyzed" only when we have an active belief flag. For now
    # check species-pool gate only — if species could have Quick Feet
    # AND any non-empty status was observed, skip. Conservative: skip
    # whenever Quick Feet is in the pool (status flag added later if
    # we add per-belief status tracking).
    if species in _QUICKFEET_SPECIES:
        return True

    # 5. PHASE 2 ADDITION: Booster Energy / Protosynthesis-Speed (gen 9)
    # Foul-play's bug: doesn't gate on this. Plan H fixes.
    if (
        weather == "SunnyDay"
        or terrain == "ELECTRIC_TERRAIN"
        or belief.removed_item == "boosterenergy"
    ):
        if species in _PROTOSYNTHESIS_SPECIES or species in _QUARKDRIVE_SPECIES:
            return True

    return False

# Items ruled out when R4 fires (= every other plausible item becomes
# impossible because only HDB explains the absence of hazard damage in
# the carve-out-failed branch). HDB itself is excluded — it's the
# inferred conclusion, not a rule-out.
_R4_RULED_OUT_ITEMS: frozenset[str] = frozenset({
    "lifeorb", "leftovers", "rockyhelmet",
    "choiceband", "choicescarf", "choicespecs",
    "assaultvest", "focussash",
    "weaknesspolicy", "ejectbutton", "redcard",
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

        R1 (Task 7): Choice items lock the holder to one move; using two
        different moves on the same active stretch (no switch, no item swap)
        disproves Choice Band / Scarf / Specs. Additionally, certain moves
        (status setups, recovery, Substitute, protection, Leech Seed) are
        categorically Choice-impossible and disprove on the FIRST observation.
        R2 (Task 5): if the opp uses a status-category move, Assault Vest
        is ruled out (AV blocks status moves entirely). R3 (Task 6): if
        the opp uses any damaging move and the species is NOT in the SF/MG
        ability pool, Life Orb is ruled out — LO recoil announces itself
        in the protocol, so absence of recoil on a damaging move from a
        non-SF/MG candidate is free evidence that LO is impossible.

        ORDER: R1 fires BEFORE `on_reveal_move` — it compares the new move
        against the PREVIOUS `last_used_move`, which `on_reveal_move`
        overwrites. R2 / R3 fire AFTER state recording. Final order:
            [from] guard → R1 → on_reveal_move → R2 → R3
        """
        # Defensive: callers should pass a list, but Task 9 harness wiring
        # is the first real producer and a malformed protocol line could
        # surface as None. Treat None as "no [from] tokens" (active move).
        if split_msg is None:
            split_msg = []
        if is_passive_move_event(split_msg):
            return  # Passive — don't update revealed_moves or fire rules

        b = self.get(species)
        norm_move = _normalize(move_id)
        norm_species = _normalize(species)

        # Empty-move guard. _normalize("") returns ""; without this guard
        # the two-different-moves R1 branch could fire spuriously when
        # last_used_move is set to a real move and the new (empty) move
        # compares unequal. on_reveal_move also early-returns on empty.
        if not norm_move:
            return

        # R1 (early-disprove): certain moves are categorically Choice-
        # impossible. Fires on the FIRST observation, no history needed.
        if norm_move in _CHOICE_INCOMPATIBLE_MOVES:
            b.impossible_items.update(_CHOICE_ITEMS)
            # Phase 2: scarf rollback — R1 disproves the entire Choice
            # family, so an inferred-scarf hypothesis must be retracted.
            if b.item_inferred_choicescarf:
                self._recompute_speed_range_no_scarf(species)

        # R1 (two-different-moves): if opp used a different move last,
        # without switching since (last_used_move is None after switch-in
        # or after on_item_swapped), Choice items are impossible.
        if (
            b.last_used_move is not None
            and norm_move != b.last_used_move
        ):
            b.impossible_items.update(_CHOICE_ITEMS)
            # Phase 2: scarf rollback (same rationale as early-disprove).
            if b.item_inferred_choicescarf:
                self._recompute_speed_range_no_scarf(species)

        # State recording (existing skeleton path)
        self.on_reveal_move(species, move_id)

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

        Discards the revealed item from `impossible_items` — a positive
        protocol-asserted reveal SUPERSEDES any prior false impossibility
        (e.g., R4 / Task 8's eager `airballoon` rule-out on every
        switch-in must be rolled back when Air Balloon actually
        announces). Without this discard, the priors filter at
        `priors.py:get_set` would reject every candidate set because
        the revealed item is in impossible_items AND the equality check
        for revealed_item would fail on every other candidate — empty
        filter → fallback to unfiltered modal.
        """
        norm_item = _normalize(item_id)
        if not norm_item:
            return
        b = self.get(species)
        b.revealed_item = norm_item
        b.impossible_items.discard(norm_item)
        # Phase 2: scarf rollback when revealed item contradicts inferred scarf.
        if b.item_inferred_choicescarf and norm_item in _NON_SCARF_CHOICE:
            self._recompute_speed_range_no_scarf(species)

    def on_reveal_ability(self, species: str, ability_id: str) -> None:
        """Record opp's ability identity. Empty / whitespace-only
        `ability_id` is ignored.

        Discards the revealed ability from `impossible_abilities` — a
        positive reveal supersedes any prior false impossibility from
        R5's eager rule-out on switch-in. Same priors-filter rationale
        as on_reveal_item.
        """
        norm_ability = _normalize(ability_id)
        if not norm_ability:
            return
        b = self.get(species)
        b.revealed_ability = norm_ability
        b.impossible_abilities.discard(norm_ability)

    def on_item_swapped(
        self, species: str, new_item: str | None, old_item: str | None
    ) -> None:
        """Called on Trick / Switcheroo / Knock Off events that swap or
        remove items. Resets R1's move-history fields, since the opp's
        strategic state has flipped and prior move-history is no longer
        evidence about their item. Without this hook, R1 mis-fires
        after a Trick (the opp gets a new item; the next move looks
        like 'two different moves used' to a naive R1).

        Discards the new item from `impossible_items` (positive reveal
        supersedes; same rationale as on_reveal_item).
        """
        b = self.get(species)
        b.removed_item = old_item
        norm_new = _normalize(new_item) if new_item else None
        b.revealed_item = norm_new
        if norm_new:
            b.impossible_items.discard(norm_new)
        b.last_used_move = None
        b.moves_used_since_switch_in = []
        # Phase 2: speed bracket flips when item changes; clear all cached
        # speed inference. Future observations will repopulate.
        b.speed_range = None
        b.item_inferred_choicescarf = False
        b.speed_observations = []

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
        # Free wins: these items announce themselves on switch-in via
        # explicit protocol messages (Air Balloon "popped" on hazard hit;
        # Booster Energy's "Booster Energy activated!" on entry). Their
        # absence rules them out unconditionally — no carve-outs needed.
        b.impossible_items.add("airballoon")
        b.impossible_items.add("boosterenergy")
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

        Order is significant: R4 must fire BEFORE the per-Pokemon flags
        are cleared, since `_fire_r4` reads them.
        """
        for b in self._beliefs.values():
            if (
                b.just_switched_in
                and b.side_hazards_at_switch_in
                and not b.took_hazard_damage_this_stretch
            ):
                self._fire_r4(b)
            b.just_switched_in = False
            b.took_hazard_damage_this_stretch = False

    def _fire_r4(self, b: OpponentBelief) -> None:
        """R4 — Heavy-Duty Boots inference. Called from `on_turn_boundary`
        when the Pokemon just switched in this turn, hazards were active
        on its side at switch-in, and no hazard damage was observed by
        end-of-turn.

        Carve-outs (return without ruling out — leave items still possible):

        - Species ability pool includes Magic Guard → Magic Guard makes
          the Pokemon immune to ALL indirect damage, so absence of hazard
          damage is uninformative.

        For type-based carve-outs (only relevant when at least one of
        Spikes / T-Spikes is active — SR is rock-typed and hits Flying
        and Levitate alike):

        - Tera Flying / base Flying-typed → ignores Spikes / T-Spikes
          entirely (grounded check fails post-Tera-Flying).
        - Levitate-pool species → ignores ground-based hazards (Spikes /
          T-Spikes), so absence is unconditionally consistent with
          Levitate. (SR still hits Levitate, so SR-only hazards bypass
          this carve-out.)
        - Tera Steel / base Steel-typed + T-Spikes only → Steel is
          immune to T-Spikes, so absence is consistent with Steel typing
          rather than HDB.

        If none of the carve-outs apply, conclude HDB by adding every
        other plausible item to `impossible_items`.
        """
        norm_species = b.species  # already normalized via BeliefTracker.get
        active_hazards = set(b.side_hazards_at_switch_in.keys())

        # Magic Guard makes ALL hazard absence uninformative — short-circuit.
        if norm_species in _MAGICGUARD_SPECIES:
            return

        # Filter to damaging hazards (Phase 1 scope: SR / Spikes / T-Spikes;
        # Sticky Web inflicts no damage so it's outside R4's purview).
        damaging_hazards = active_hazards & {"stealthrock", "spikes", "toxicspikes"}
        if not damaging_hazards:
            return

        non_sr_hazards = damaging_hazards - {"stealthrock"}

        # Tera-aware type checks (consult has_type once each).
        base_types = _BASE_TYPES.get(norm_species, ())
        is_flying = has_type(b, "Flying", base_types)
        is_steel = has_type(b, "Steel", base_types)

        if non_sr_hazards:
            # Tera Flying ignores Spikes / T-Spikes entirely.
            if is_flying:
                return
            # Levitate-pool species ignore Spikes / T-Spikes.
            if norm_species in _LEVITATE_SPECIES:
                return
            # If only T-Spikes is the non-SR damaging hazard, Steel typing
            # explains the absence (Steel is immune to T-Spikes; SR-only
            # branch already handled above when non_sr_hazards is empty).
            if non_sr_hazards == {"toxicspikes"} and is_steel:
                return

        # All carve-outs failed → HDB is the only consistent item.
        b.impossible_items.update(_R4_RULED_OUT_ITEMS)

    # ------------------------------------------------------------------
    # Phase 2 — Speed inference (R6 in our numbering, mirrors foul-play's
    # check_speed_ranges). See spec at
    # docs/superpowers/specs/2026-04-29-plan-h-phase2-speed-range-design.md
    # ------------------------------------------------------------------

    def on_turn_boundary_speed(
        self,
        species: str,
        turn: int,
        my_active_speed_post_modifiers: int,
        opp_moved_first: bool | None,
        skip_reasons: list[str] | None = None,
        in_trick_room: bool = False,
        weather: str | None = None,
        terrain: str | None = None,
    ) -> None:
        """Narrow opp's speed_range based on this turn's move ordering.

        Mirrors foul-play's check_speed_ranges (paraphrased — see
        analysis/plan-h-phase2-research/foul-play-speed.md). Algorithm:

        1. Message-based skip-list (caller passes via skip_reasons).
        2. opp_moved_first==None → caller couldn't determine order
           (harness path without move-order capture). Record + skip.
        3. State-based skip: opp's scarf already revealed.
        4. State-based skip: opp's species could have unobserved
           speed-mod ability (Swift Swim under rain, etc.).
        5. Record observation, apply Trick Room inversion.
        6. Tighten speed_range: opp_moved_first ⇒ raise min; else ⇒ lower max.
        7. Forced-scarf check: if min > max_non_scarf, infer scarf and
           lift the upper bound into scarf-bracket territory.

        Args:
          species: opp Pokemon's normalized species string.
          turn: Showdown turn number (for audit history).
          my_active_speed_post_modifiers: bot's speed AFTER all modifiers
            (boost stage, paralysis, Tailwind, Choice Scarf,
            protosynthesis-Spe). Caller computes via
            stats.apply_bot_speed_modifier_chain.
          opp_moved_first: True iff opp's |move| event preceded ours.
            None when caller can't determine (harness w/o move-order).
          skip_reasons: list of conditions making this turn uninformative.
            Non-empty → record "skipped:<first_reason>" and NO-OP.
          in_trick_room: from battle.fields[Field.TRICK_ROOM] presence.
          weather: Showdown weather string ("RainDance", etc.) or None.
          terrain: Showdown terrain string ("ELECTRIC_TERRAIN", etc.) or None.
        """
        b = self.get(species)
        my_speed = my_active_speed_post_modifiers

        # 1. Message-based skip-list
        if skip_reasons:
            b.speed_observations.append(
                (turn, my_speed, f"skipped:{skip_reasons[0]}")
            )
            return

        # 2. Move-order unknown (harness path with no override)
        if opp_moved_first is None:
            b.speed_observations.append((turn, my_speed, "skipped:no_move_order"))
            return

        # 3. State-based skip: opp scarf already revealed
        if b.revealed_item == "choicescarf":
            return

        # 4. State-based skip: opp could have unobserved speed-mod ability
        if can_have_speed_modified(b, weather=weather, terrain=terrain):
            b.speed_observations.append(
                (turn, my_speed, "skipped:speed_modifier")
            )
            return

        # 5. Record observation BEFORE applying narrowing
        kind = "opp_first" if opp_moved_first else "us_first"
        b.speed_observations.append((turn, my_speed, kind))

        # 6. Trick Room inversion (slow goes first → invert)
        effective_opp_first = (not opp_moved_first) if in_trick_room else opp_moved_first

        # 7. Apply narrowing
        if effective_opp_first:
            new_min = my_speed + 1
            if b.speed_range is None:
                b.speed_range = (new_min, _SPEED_HI_SENTINEL)
            else:
                b.speed_range = (
                    max(b.speed_range[0], new_min),
                    b.speed_range[1],
                )
        else:
            new_max = my_speed - 1
            if b.speed_range is None:
                b.speed_range = (0, new_max)
            else:
                b.speed_range = (
                    b.speed_range[0],
                    min(b.speed_range[1], new_max),
                )

        # 8. Forced-scarf check (fires only when narrowed range exceeds
        # max-non-scarf bracket — opp moved too fast to be non-scarf)
        base_speed = _BASE_SPEEDS.get(_normalize(species))
        if base_speed is None:
            return
        # Max non-scarf Speed: 252 EV / +nature / 31 IV / level 100
        max_non_scarf = compute_speed_stat(base_speed, 252, 31, 1.1, 100)
        if (
            b.speed_range[0] > max_non_scarf
            and "choicescarf" not in b.impossible_items
        ):
            self.infer_choicescarf(species)
            # Lift the upper bound: a us_first observation that gave us
            # max=393 means "non-scarf opp slower than 393" which is now
            # invalid; the scarf-aware equivalent of 393 is 589.
            current_max = b.speed_range[1]
            if current_max < _SPEED_HI_SENTINEL:
                scarf_max = int(current_max * 1.5)
                # If even scarf-bracket can't reconcile, fall to sentinel
                # (the spread filter will fall through to unfiltered modal).
                if scarf_max < b.speed_range[0]:
                    scarf_max = _SPEED_HI_SENTINEL
                b.speed_range = (b.speed_range[0], max(current_max, scarf_max))

    def infer_choicescarf(self, species: str) -> None:
        """Promote ``item_inferred_choicescarf=True``. Adds non-scarf
        Choice items to ``impossible_items`` (the Choice trio is mutex —
        you can only hold one). Idempotent.

        Called by:
        - ``on_turn_boundary_speed`` when bracket math forces it
          (``speed_range[0] > max_non_scarf``).
        - External code that has independent evidence (chaos prior shape,
          live-protocol signal).
        """
        b = self.get(species)
        if not b.item_inferred_choicescarf:
            b.item_inferred_choicescarf = True
            b.impossible_items.add("choiceband")
            b.impossible_items.add("choicespecs")

    def _recompute_speed_range_no_scarf(self, species: str) -> None:
        """Rollback helper. Recompute ``speed_range`` under non-scarf
        assumption by replaying ``speed_observations``.

        Called when contradicting evidence lands (Section 5.2.5 of spec):
        - ``on_reveal_item`` with non-scarf Choice item
        - ``on_item_swapped`` (Trick / Switcheroo / Knock Off)
        - ``on_move`` R1 branch (two-different-moves OR early-disprove)

        CRITICAL guard at end: if the replay produces a degenerate range
        (min > max), drop to None. This happens when a prior opp_first
        observation only made sense under scarf — replaying under
        non-scarf assumption produces an inconsistency. Without the guard
        the spread filter rejects every spread forever (T-C2 catches).
        """
        b = self.get(species)
        b.speed_range = None
        b.item_inferred_choicescarf = False

        for _turn, my_speed, kind in b.speed_observations:
            if kind == "opp_first":
                new_min = my_speed + 1
                if b.speed_range is None:
                    b.speed_range = (new_min, _SPEED_HI_SENTINEL)
                else:
                    b.speed_range = (
                        max(b.speed_range[0], new_min),
                        b.speed_range[1],
                    )
            elif kind == "us_first":
                new_max = my_speed - 1
                if b.speed_range is None:
                    b.speed_range = (0, new_max)
                else:
                    b.speed_range = (
                        b.speed_range[0],
                        min(b.speed_range[1], new_max),
                    )
            # skipped:* kinds → no contribution (they were uninformative
            # in the original pass, still uninformative under non-scarf
            # assumption).

        # Degenerate-range guard. See docstring.
        if b.speed_range and b.speed_range[0] > b.speed_range[1]:
            b.speed_range = None
