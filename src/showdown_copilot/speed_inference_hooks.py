"""Shared protocol-message sniffing for Plan H Phase 2 speed inference.

Used by:
- battle-testing/mcts_player.py (Path A — harness)
- showdown-copilot/spectator.py (Path B — TUI/spectator)

Extracts per-turn move-order and skip-flag events from raw Showdown
protocol lines BEFORE poke-env strips state. Stateless functions that
take the per-turn buffers as arguments; the host class (MCTSPlayer or
CopilotSpectator) owns the buffers and decides when to fire the
narrower.
"""
from __future__ import annotations

# Hardcoded priority-move table (subset of gen 9 priority moves that
# matter for speed-bracket comparison). Unknown moves return 0, which
# is the safe default — the skip-list catches the dangerous cases
# (|switch|, |cant|, Quick Claw activation) so a missed +N classification
# only reduces inference power, never corrupts it.
_PRIORITY_PLUS_ONE: frozenset[str] = frozenset({
    "aquajet", "bulletpunch", "iceshard", "machpunch", "quickattack",
    "shadowsneak", "suckerpunch", "vacuumwave", "watershuriken",
    "accelerock", "jetpunch", "icicleshard", "manfistfury",
    # Trickroom, Tailwind have priority +0 since gen 5 (intentionally
    # excluded — we treat them as priority-0 turns).
})
_PRIORITY_PLUS_TWO: frozenset[str] = frozenset({"extremespeed", "feint"})
_PRIORITY_PLUS_THREE: frozenset[str] = frozenset({"fakeout", "firstimpression"})


def lookup_move_priority(move_id: str) -> int:
    """Return move's priority bracket for speed-comparison purposes.

    Returns 0 for unknown moves (safe default — see module docstring).
    """
    if move_id in _PRIORITY_PLUS_ONE:
        return 1
    if move_id in _PRIORITY_PLUS_TWO:
        return 2
    if move_id in _PRIORITY_PLUS_THREE:
        return 3
    return 0


def normalize_move_id(name: str) -> str:
    return "".join(c for c in name.lower() if c.isalnum())


def sniff_for_speed(
    split_message: list[str],
    move_log: list[tuple[str, str, str, int]],
    skip_flags: list[str],
) -> None:
    """Inspect one Showdown protocol message and update per-turn buffers.

    Args:
      split_message: tokenized protocol line, e.g.
        ["", "move", "p2a: Garchomp", "Earthquake", "p1a: Tusk"]
      move_log: per-turn list of (side, species, move_id, priority).
        Mutated by appending |move| events.
      skip_flags: per-turn list of skip reasons. Mutated by appending
        |cant|, |switch|, confusion, Quick Claw, Custap events.

    Caller (MCTSPlayer or CopilotSpectator) owns the buffers and is
    responsible for resetting them on the |turn| boundary.
    """
    if len(split_message) < 2:
        return
    kind = split_message[1]

    if kind == "move":
        # Format: ["", "move", "p2a: Garchomp", "Earthquake", "p1a: Tusk"]
        if len(split_message) < 4:
            return
        actor = split_message[2]
        move_name = split_message[3]
        # Extract side ("p1"/"p2") and species
        side = ""
        if "a:" in actor:
            side = actor.split("a:")[0]
        elif "b:" in actor:
            side = actor.split("b:")[0]
        species = actor.split(": ", 1)[-1].strip() if ": " in actor else ""
        move_id = normalize_move_id(move_name)
        priority = lookup_move_priority(move_id)
        move_log.append((side, species, move_id, priority))

    elif kind == "cant":
        skip_flags.append("cant")

    elif kind == "switch":
        # Switches happen before any move regardless of speed.
        skip_flags.append("switch")

    elif kind == "-activate":
        if len(split_message) < 3:
            return
        joined_lower = " ".join(split_message).lower()
        # Confusion: |-activate|p2a: Mon|confusion
        if split_message[-1].lower().endswith("confusion"):
            skip_flags.append("confusion")
        elif "quick claw" in joined_lower:
            skip_flags.append("quick_claw")
        elif "quick draw" in joined_lower:
            skip_flags.append("quick_draw")

    elif kind == "-enditem":
        joined_lower = " ".join(split_message).lower()
        if "custap berry" in joined_lower or "custapberry" in joined_lower:
            skip_flags.append("custap")


def derive_opp_moved_first(
    move_log: list[tuple[str, str, str, int]],
    my_role: str | None,
) -> bool | None:
    """Return True if opp's |move| event preceded ours, False if ours first,
    None if uninformative (priority mismatch, single move, no role known).
    """
    if len(move_log) < 2:
        return None
    if my_role is None:
        return None

    first, second = move_log[0], move_log[1]
    side_first, _sp1, _mid1, prio_first = first
    side_second, _sp2, _mid2, prio_second = second

    if prio_first != prio_second:
        return None  # priority mismatch — uninformative

    return side_first != my_role
