"""Tests for the shared speed_inference_hooks module + CopilotSpectator
Path B wiring (Plan H Phase 2 Day 5).
"""
from __future__ import annotations

import pytest

from showdown_copilot.speed_inference_hooks import (
    derive_opp_moved_first,
    lookup_move_priority,
    normalize_move_id,
    sniff_for_speed,
)


# ----- normalize_move_id -----


@pytest.mark.parametrize("name,expected", [
    ("Earthquake", "earthquake"),
    ("Close Combat", "closecombat"),
    ("U-turn", "uturn"),
    ("Stone Edge", "stoneedge"),
    ("Extreme Speed", "extremespeed"),
])
def test_normalize_move_id(name, expected):
    assert normalize_move_id(name) == expected


# ----- lookup_move_priority -----


@pytest.mark.parametrize("move_id,expected", [
    ("aquajet", 1),
    ("extremespeed", 2),
    ("fakeout", 3),
    ("earthquake", 0),
    ("madeupmove", 0),
])
def test_lookup_move_priority(move_id, expected):
    assert lookup_move_priority(move_id) == expected


# ----- sniff_for_speed -----


def test_sniff_move_appends_to_log():
    log, skips = [], []
    sniff_for_speed(
        ["", "move", "p2a: Garchomp", "Earthquake", "p1a: Tusk"], log, skips
    )
    assert len(log) == 1
    side, species, move_id, priority = log[0]
    assert (side, species, move_id, priority) == ("p2", "Garchomp", "earthquake", 0)
    assert skips == []


def test_sniff_priority_move():
    log, skips = [], []
    sniff_for_speed(
        ["", "move", "p1a: Dragonite", "Extreme Speed", "p2a: Mon"], log, skips
    )
    _, _, move_id, priority = log[0]
    assert priority == 2


def test_sniff_cant_event():
    log, skips = [], []
    sniff_for_speed(["", "cant", "p2a: Garchomp", "par"], log, skips)
    assert "cant" in skips
    assert log == []


def test_sniff_switch_event():
    log, skips = [], []
    sniff_for_speed(
        ["", "switch", "p2a: Mon", "Mon, L100, M", "100/100"], log, skips
    )
    assert "switch" in skips


def test_sniff_confusion_activate():
    log, skips = [], []
    sniff_for_speed(["", "-activate", "p2a: Mon", "confusion"], log, skips)
    assert "confusion" in skips


def test_sniff_quick_claw_activate():
    log, skips = [], []
    sniff_for_speed(
        ["", "-activate", "p2a: Mon", "item: Quick Claw"], log, skips
    )
    assert "quick_claw" in skips


def test_sniff_custap_enditem():
    log, skips = [], []
    sniff_for_speed(
        ["", "-enditem", "p2a: Mon", "Custap Berry", "[eat]"], log, skips
    )
    assert "custap" in skips


# ----- derive_opp_moved_first -----


def test_derive_empty_log_returns_None():
    assert derive_opp_moved_first([], my_role="p1") is None


def test_derive_single_move_returns_None():
    assert derive_opp_moved_first(
        [("p2", "Mon", "earthquake", 0)], my_role="p1"
    ) is None


def test_derive_priority_mismatch_returns_None():
    log = [
        ("p1", "Mon", "extremespeed", 2),
        ("p2", "Mon", "earthquake", 0),
    ]
    assert derive_opp_moved_first(log, my_role="p1") is None


def test_derive_no_role_returns_None():
    log = [
        ("p2", "Mon", "earthquake", 0),
        ("p1", "Mon", "stoneedge", 0),
    ]
    assert derive_opp_moved_first(log, my_role=None) is None


def test_derive_opp_first():
    log = [
        ("p2", "Garchomp", "earthquake", 0),
        ("p1", "Tusk", "stoneedge", 0),
    ]
    assert derive_opp_moved_first(log, my_role="p1") is True


def test_derive_us_first():
    log = [
        ("p1", "Iron Bundle", "hydropump", 0),
        ("p2", "Garchomp", "earthquake", 0),
    ]
    assert derive_opp_moved_first(log, my_role="p1") is False


# ----- Integration: synthetic protocol stream → fire observer -----


def _consume_messages(observer, msgs):
    """Mimic CopilotSpectator's per-batch sniff + fire-on-|turn| pattern."""
    log: list = []
    skips: list[str] = []
    fired: list[tuple[int, list, list]] = []
    for split in msgs:
        sniff_for_speed(split, log, skips)
    for split in msgs:
        if len(split) >= 3 and split[1] == "turn":
            try:
                new_turn = int(split[2])
            except (ValueError, TypeError):
                continue
            fired.append((new_turn - 1, list(log), list(skips)))
            log = []
            skips = []
            break
    return fired


def test_synthetic_stream_fires_observer_on_turn_boundary():
    msgs = [
        ["", "move", "p2a: Garchomp", "Earthquake", "p1a: Tusk"],
        ["", "move", "p1a: Tusk", "Stone Edge", "p2a: Garchomp"],
        ["", "turn", "2"],  # closes turn 1
    ]
    fired = _consume_messages(observer=None, msgs=msgs)
    assert len(fired) == 1
    turn, log, skips = fired[0]
    assert turn == 1
    assert len(log) == 2
    assert log[0][0] == "p2"  # opp moved first
    assert log[1][0] == "p1"
    assert skips == []


def test_synthetic_stream_with_quickclaw_skip():
    msgs = [
        ["", "-activate", "p2a: Conkeldurr", "item: Quick Claw"],
        ["", "move", "p2a: Conkeldurr", "Mach Punch", "p1a: Tusk"],
        ["", "move", "p1a: Tusk", "Earthquake", "p2a: Conkeldurr"],
        ["", "turn", "2"],
    ]
    fired = _consume_messages(observer=None, msgs=msgs)
    assert len(fired) == 1
    turn, log, skips = fired[0]
    assert "quick_claw" in skips


def test_synthetic_stream_with_switch_skip():
    msgs = [
        ["", "switch", "p2a: NewMon", "NewMon, L100, M", "100/100"],
        ["", "move", "p1a: Tusk", "Earthquake", "p2a: NewMon"],
        ["", "turn", "2"],
    ]
    fired = _consume_messages(observer=None, msgs=msgs)
    turn, log, skips = fired[0]
    assert "switch" in skips
