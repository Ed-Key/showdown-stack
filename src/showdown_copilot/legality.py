"""Set legality / plausibility filter — port of Foul Play's
`smogon_set_makes_sense` and `set_makes_sense`.

PURPOSE
-------
The chaos-modal pipeline picks the most-popular item, ability, 4 moves and
spread INDEPENDENTLY from Smogon's per-category usage distributions and
combines them. The combination is sometimes nonsense (e.g. Choice Band
Garchomp with Stealth Rock — CB locks moves so a status hazard can never
fire). This filter rejects implausible combinations BEFORE they reach the
engine as the modal-set guess for an opponent Pokemon.

DESIGN
------
- Pure logic, no I/O. All inputs are already-normalized strings (lowercase
  alphanumeric) and a flat EV dict.
- Two layers, mirroring Foul Play:
  1. `set_makes_sense_intrinsic` — Pokemon-mechanics rules that don't need
     belief (e.g. Choice item + status move = nonsense).
  2. `set_makes_sense` — wraps the intrinsic check AND additionally
     reconciles the candidate against `OpponentBelief` (revealed item,
     revealed ability, speed range, scarf inference).
- Default behavior is conservative: any rule we can't safely evaluate
  (e.g. unknown move category) returns True (don't reject) rather than
  False. Rejecting requires affirmative evidence.

REFERENCES
----------
- /tmp/foul-play-upstream/fp/search/standard_battles.py:96-184
- /tmp/foul-play-upstream/data/pkmn_sets.py:226-246
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from showdown_copilot.belief import _is_status_move, _STATUS_MOVES
from showdown_copilot.stats import _NATURE_TO_SPE_MULT, compute_speed_stat

if TYPE_CHECKING:
    from showdown_copilot.belief import OpponentBelief


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — ported from foul-play's `constants` module + standard_battles.py
# ---------------------------------------------------------------------------

CHOICE_ITEMS: frozenset[str] = frozenset({
    "choiceband", "choicespecs", "choicescarf",
})

# Items that can be Tricked / Switcherooed onto an opponent. Source:
# foul-play standard_battles.py:24-33. (Choice trio + AV + Black Sludge +
# Sticky Barb + status orbs.)
TRICKABLE_ITEMS: frozenset[str] = frozenset({
    "choicespecs", "choicescarf", "choiceband",
    "assaultvest",
    "blacksludge", "stickybarb",
    "flameorb", "toxicorb",
})

# Toxic Orb is only sensible on these abilities (poisonheal heals, the
# others convert the burn/poison status to a stat boost or no-op it).
TOXIC_ORB_ABILITIES: frozenset[str] = frozenset({
    "poisonheal", "quickfeet", "magicguard", "marvelscale",
    "guts", "toxicboost",
})

# Same for Flame Orb (note: poisonheal isn't in here — it heals POISON not BURN).
FLAME_ORB_ABILITIES: frozenset[str] = frozenset({
    "quickfeet", "magicguard", "guts", "flareboost",
})

# Pivot moves that are exempt from the "Choice user only uses physical /
# special damaging moves" rule because their purpose is to switch out
# (which clears the Choice lock). Source: foul-play standard_battles.py:84-91.
PIVOT_MOVES_EXEMPT_FROM_CHOICE: frozenset[str] = frozenset({
    "trick", "switcheroo",
    "uturn", "voltswitch", "flipturn",
})

# Physical-boost setup moves (raise Atk). Foul Play rule: Choice items
# disallow these, AND we expect <= 1 non-physical move in the set (excluding
# the boosting move itself).
PHYSICAL_BOOST_MOVES: frozenset[str] = frozenset({
    "swordsdance", "dragondance", "tidyup", "sharpen",
    "meditate", "honeclaws", "bellydrum", "howl", "shiftgear",
})

# Special-boost setup moves (raise SpA).
SPECIAL_BOOST_MOVES: frozenset[str] = frozenset({
    "nastyplot", "tailglow",
})


# Nature → primary stat boosted (None for neutral natures). Used to detect
# "Bulk Up + Modest" style contradictions.
_NATURE_PLUS: dict[str, str | None] = {
    "Adamant": "atk", "Brave": "atk", "Lonely": "atk", "Naughty": "atk",
    "Bold": "def", "Impish": "def", "Lax": "def", "Relaxed": "def",
    "Modest": "spa", "Mild": "spa", "Quiet": "spa", "Rash": "spa",
    "Calm": "spd", "Careful": "spd", "Gentle": "spd", "Sassy": "spd",
    "Hasty": "spe", "Jolly": "spe", "Naive": "spe", "Timid": "spe",
    "Bashful": None, "Docile": None, "Hardy": None, "Quirky": None,
    "Serious": None,
}


# ---------------------------------------------------------------------------
# Move-category lookup
# ---------------------------------------------------------------------------
#
# Foul Play queries `all_move_json[mv][CATEGORY]` against Showdown data.
# We don't ship that table; instead we use poke-env's MoveCategory if it's
# importable (it is in our env), and fall back to a small hardcoded set
# that's sufficient for the rules below. Returning None means "unknown" —
# the caller treats unknown as non-status (safer: don't trigger the AV
# filter for moves we can't classify).

try:  # pragma: no cover — environment-dependent import
    from poke_env.data import GenData

    _GEN9_MOVES: dict[str, dict] = GenData.from_gen(9).moves

    def _move_category(move_id: str) -> str | None:
        """Return 'physical' / 'special' / 'status' / None (unknown).

        Reads gen-9 Move data shipped with poke-env. Falls through to
        None on any lookup error — better to under-fire than mis-fire.
        """
        entry = _GEN9_MOVES.get(move_id)
        if not entry:
            return None
        cat = entry.get("category")
        if not cat:
            return None
        return str(cat).lower()
except Exception:  # pragma: no cover

    def _move_category(move_id: str) -> str | None:
        return None


def _is_status(move_id: str) -> bool:
    """True iff `move_id` is known-status. Falls back to belief.py's
    hardcoded `_STATUS_MOVES` set when poke-env can't classify."""
    cat = _move_category(move_id)
    if cat is not None:
        return cat == "status"
    # Conservative fallback: only the curated status set in belief.py.
    return _is_status_move(move_id)


def _is_physical(move_id: str) -> bool:
    return _move_category(move_id) == "physical"


def _is_special(move_id: str) -> bool:
    return _move_category(move_id) == "special"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def set_makes_sense(
    item: str,
    ability: str,
    moves: list[str],
    nature: str,
    evs: dict,
    belief: "OpponentBelief | None" = None,
    species: str | None = None,
    base_speed: int | None = None,
) -> bool:
    """Return True iff this candidate set is plausible.

    Combines the intrinsic Pokemon-mechanics filter (foul-play
    `smogon_set_makes_sense`) with belief-aware reconciliation
    (foul-play `set_makes_sense`). All string args are expected to be
    in our normalized form (lowercase alphanumeric, no spaces / dashes).

    Args:
        item: e.g. "choiceband", "leftovers", "none".
        ability: e.g. "intimidate", "poisonheal", "none".
        moves: 1-4 normalized move ids (e.g. ["earthquake", "stealthrock"]).
        nature: capitalized nature ("Jolly", "Modest", "Serious").
        evs: dict with keys hp/atk/def/spa/spd/spe → 0..252.
        belief: optional OpponentBelief. If provided, additionally checks
            revealed_item, revealed_ability, impossible_*, speed_range
            and item_inferred_choicescarf consistency.
        species: optional canonical species id, used for debug logging.
        base_speed: optional base Speed stat — required only for the
            belief speed_range reconciliation. Without it the speed
            check is silently skipped.

    Returns True if the set passes ALL applied rules.
    """
    if not _intrinsic_ok(item, ability, moves, nature, evs, species=species):
        return False
    if belief is None:
        return True
    return _belief_ok(item, ability, moves, nature, evs, belief, base_speed)


# Backwards-friendly aliases (mirrors the two foul-play function names so
# future readers can grep for either).
def smogon_set_makes_sense(
    item: str, ability: str, moves: list[str], nature: str, evs: dict,
    species: str | None = None,
) -> bool:
    """Intrinsic-only check (foul-play `smogon_set_makes_sense` analog)."""
    return _intrinsic_ok(item, ability, moves, nature, evs, species=species)


# ---------------------------------------------------------------------------
# Intrinsic rules (no belief required)
# ---------------------------------------------------------------------------


def _intrinsic_ok(
    item: str,
    ability: str,
    moves: list[str],
    nature: str,
    evs: dict,
    species: str | None = None,
) -> bool:
    item = (item or "none").lower()
    ability = (ability or "none").lower()
    move_list = [m.lower() for m in moves if m and m != "none"]

    # ---- ITEM rules --------------------------------------------------------

    # Toxic Orb only makes sense on abilities that benefit from being
    # poisoned (Poison Heal heals, Guts/Quick Feet/Marvel Scale boost a
    # stat, Magic Guard / Toxic Boost negate-or-exploit chip damage).
    if item == "toxicorb" and ability not in TOXIC_ORB_ABILITIES:
        return _reject("toxicorb_needs_status_ability", species, item, ability, move_list)

    # Flame Orb is the same idea but for burn — ability list is narrower
    # (no Poison Heal / Marvel Scale; those only react to poison).
    if item == "flameorb" and ability not in FLAME_ORB_ABILITIES:
        return _reject("flameorb_needs_burn_ability", species, item, ability, move_list)

    # Choice trio locks the user into one move per switch-in. Therefore the
    # set must contain at most one non-on-category attack (excluding Trick
    # and pivot moves which legitimately appear on Choice sets).
    if item in CHOICE_ITEMS:
        if not _choice_set_makes_sense(item, move_list):
            return _reject("choice_set_needs_attack_only", species, item, ability, move_list)

    # Assault Vest forbids status moves (the item itself blocks them).
    # Klutz disables the item entirely — so AV+Klutz is fine. Moves we
    # CAN'T classify are skipped (better to not reject than mis-reject).
    if item == "assaultvest" and ability != "klutz":
        if any(_is_status(mv) for mv in move_list):
            return _reject("assaultvest_no_status", species, item, ability, move_list)

    # ---- ABILITY rules -----------------------------------------------------

    # Poison Heal is dead weight without Toxic Orb (or another way to
    # self-poison, which doesn't show up in chaos modal sets). Reject the
    # combination outright.
    if ability == "poisonheal" and item != "toxicorb":
        return _reject("poisonheal_needs_toxicorb", species, item, ability, move_list)

    # ---- MOVE rules --------------------------------------------------------

    for mv in move_list:
        # Protect on a Choice item is impossible — Choice locks the user
        # into the previously selected move, so Protect can never fire.
        if mv == "protect" and item in CHOICE_ITEMS:
            return _reject("protect_with_choice", species, item, ability, move_list)

        # Physical-boost setup moves (Swords Dance, Dragon Dance, ...)
        # don't make sense on Choice items (can't set up while locked) and
        # the rest of the moveset should be predominantly physical.
        if mv in PHYSICAL_BOOST_MOVES:
            if not _physical_boost_ok(mv, item, move_list):
                return _reject("physical_boost_misfit", species, item, ability, move_list)

        # Same for special-boost setup moves (Nasty Plot, Tail Glow).
        if mv in SPECIAL_BOOST_MOVES:
            if not _special_boost_ok(mv, item, move_list):
                return _reject("special_boost_misfit", species, item, ability, move_list)

        # Bulk Up / Curse boost Atk + Def. They're nonsense on Choice
        # (locked) and contradicted by SpA EVs > 0 or a +SpA nature.
        if mv in {"bulkup", "curse"}:
            if item in CHOICE_ITEMS:
                return _reject("bulkup_with_choice", species, item, ability, move_list)
            if int(evs.get("spa", 0)) > 0:
                return _reject("bulkup_with_spa_evs", species, item, ability, move_list)
            if _NATURE_PLUS.get(nature) == "spa":
                return _reject("bulkup_with_spa_nature", species, item, ability, move_list)

        # Calm Mind boosts SpA + SpD. Same shape as Bulk Up but flipped:
        # nonsense on Choice, contradicted by Atk EVs or a +Atk nature.
        if mv == "calmmind":
            if item in CHOICE_ITEMS:
                return _reject("calmmind_with_choice", species, item, ability, move_list)
            if int(evs.get("atk", 0)) > 0:
                return _reject("calmmind_with_atk_evs", species, item, ability, move_list)
            if _NATURE_PLUS.get(nature) == "atk":
                return _reject("calmmind_with_atk_nature", species, item, ability, move_list)

        # Trick / Switcheroo only make sense if the item is something
        # opponents would NOT want (Choice locks, status orbs, Sticky Barb
        # chip, AV no-status, Black Sludge non-Poison damage).
        if mv in {"trick", "switcheroo"}:
            if item not in TRICKABLE_ITEMS:
                return _reject("trick_with_untrickable_item", species, item, ability, move_list)

    return True


def _choice_set_makes_sense(item: str, moves: list[str]) -> bool:
    """Mirror of foul-play `choice_item`: at most 1 illogical (non-on-category,
    non-pivot) move on a Choice item."""
    if item == "choiceband":
        ok_categories = {"physical"}
    elif item == "choicespecs":
        ok_categories = {"special"}
    else:  # choicescarf
        ok_categories = {"physical", "special"}

    illogical = 0
    for mv in moves:
        if mv in PIVOT_MOVES_EXEMPT_FROM_CHOICE:
            continue
        cat = _move_category(mv)
        if cat is None:
            # Unknown — be conservative (don't count).
            continue
        if cat not in ok_categories:
            illogical += 1
    return illogical <= 1


def _physical_boost_ok(boost_move: str, item: str, moves: list[str]) -> bool:
    if item in CHOICE_ITEMS:
        return False
    # Allow at most one non-physical move other than the boost itself
    # (e.g. SD + EQ + Stone Edge + Roost is fine; SD + 3 status is not).
    non_phys = sum(
        1 for m in moves
        if m != boost_move and _move_category(m) is not None and not _is_physical(m)
    )
    return non_phys <= 1


def _special_boost_ok(boost_move: str, item: str, moves: list[str]) -> bool:
    if item in CHOICE_ITEMS:
        return False
    non_spec = sum(
        1 for m in moves
        if m != boost_move and _move_category(m) is not None and not _is_special(m)
    )
    return non_spec <= 1


# ---------------------------------------------------------------------------
# Belief reconciliation
# ---------------------------------------------------------------------------


def _belief_ok(
    item: str,
    ability: str,
    moves: list[str],
    nature: str,
    evs: dict,
    belief: "OpponentBelief",
    base_speed: int | None,
) -> bool:
    """Mirror of foul-play `set_makes_sense(set_dict, belief)`.

    Reconciles the candidate set against revealed-info and inferred
    constraints. Per-rule rationale is documented inline.
    """
    norm_item = (item or "none").lower()
    norm_ability = (ability or "none").lower()

    # Revealed item is GROUND TRUTH. Reject any candidate item that
    # contradicts what we've actually seen revealed (Trick reveal,
    # |-item| handler, etc.).
    if belief.revealed_item is not None and norm_item != belief.revealed_item:
        return False

    # impossible_items is the inference store (R1 firing for non-Choice
    # status move on a Choice candidate, etc.). Reject if our candidate
    # is in the ruled-out set.
    if norm_item in belief.impossible_items:
        return False

    # Forced Choice Scarf inference: bracket math has already proven the
    # opponent moved faster than any non-scarf bracket allows. Reject any
    # candidate that isn't Choice Scarf (only fires when no other item is
    # also revealed — revealed_item supersedes everything).
    if (
        belief.item_inferred_choicescarf
        and belief.revealed_item is None
        and norm_item != "choicescarf"
    ):
        return False

    # Symmetric ability checks (revealed_ability is ground truth;
    # impossible_abilities is the inferred ruled-out set from R3/R4/R5).
    if belief.revealed_ability is not None and norm_ability != belief.revealed_ability:
        return False
    if norm_ability in belief.impossible_abilities:
        return False

    # Speed range reconciliation: the candidate spread, run through the
    # canonical Speed formula, must fall within the observed bracket.
    # We ALSO consider the scarfed bracket when scarf is still allowed.
    # Mirrors PokemonSet.speed_check in foul-play. Skipped silently when
    # base_speed is unknown (no species data) — better to under-fire
    # than to falsely reject every candidate.
    if belief.speed_range is not None and base_speed is not None:
        nat_mult = _NATURE_TO_SPE_MULT.get(nature, 1.0)
        ev_spe = int(evs.get("spe", 0))
        raw = compute_speed_stat(base_speed, ev_spe, 31, nat_mult, 100)
        lo, hi = belief.speed_range

        if belief.item_inferred_choicescarf:
            scarfed = int(raw * 1.5)
            if not (lo <= scarfed <= hi):
                return False
        else:
            in_unscarfed = lo <= raw <= hi
            in_scarfed = (
                "choicescarf" not in belief.impossible_items
                and lo <= int(raw * 1.5) <= hi
            )
            if not (in_unscarfed or in_scarfed):
                return False

    return True


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def _reject(rule: str, species, item, ability, moves) -> bool:
    """Log the rejection at debug level and return False. Centralized so
    that turning on debug logging gives a clean stream of (rule, species,
    triple) lines for postmortem auditing."""
    logger.debug(
        "legality.reject rule=%s species=%s item=%s ability=%s moves=%s",
        rule, species, item, ability, moves,
    )
    return False


__all__ = [
    "set_makes_sense",
    "smogon_set_makes_sense",
    "CHOICE_ITEMS",
    "TRICKABLE_ITEMS",
]
