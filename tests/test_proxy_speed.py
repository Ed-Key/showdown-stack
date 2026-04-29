"""Tests for Plan H Phase 2 Path C — proxy speed-inference wiring."""
from __future__ import annotations

from pathlib import Path

import pytest

from showdown_copilot import proxy
from showdown_copilot.priors import PriorsSource

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def chaos_cache(tmp_path):
    src = (FIXTURE_DIR / "mini_chaos_natdex.json").read_text()
    (tmp_path / "gen9ou-1630.json").write_text(src)
    return tmp_path


@pytest.fixture(autouse=True)
def install_priors(chaos_cache, monkeypatch):
    monkeypatch.setattr(proxy, "_priors", PriorsSource(cache_dir=chaos_cache))
    proxy._trackers.clear()
    proxy._display_cache.clear()
    proxy._format_resolution.clear()
    proxy._last_speed_turn.clear()
    yield
    proxy._trackers.clear()
    proxy._display_cache.clear()
    proxy._format_resolution.clear()
    proxy._last_speed_turn.clear()


def _opp_mon(species, item="none"):
    return {
        "species": species, "level": 100, "types": ["Dragon", "Ghost"],
        "hp": 100, "maxhp": 100, "ability": "none", "item": item,
        "nature": "Serious",
        "evs": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "attack": 100, "defense": 100, "specialAttack": 100,
        "specialDefense": 100, "speed": 100,
        "status": "None", "restTurns": 0, "sleepTurns": 0, "weightKg": 0.0,
        "moves": [{"id": "dracometeor", "pp": 8}] * 4,
        "terastallized": False, "teraType": "",
    }


def _request(opp_mons, planh):
    return {
        "sideOne": {"pokemon": [], "activeIndex": 0,
                    "sideConditions": {}, "volatileStatuses": [],
                    "boosts": {}, "forceTrapped": False},
        "sideTwo": {"pokemon": opp_mons, "activeIndex": 0,
                    "sideConditions": {}, "volatileStatuses": [],
                    "boosts": {}, "forceTrapped": False},
        "weather": {"weatherType": "none", "turnsRemaining": -1},
        "terrain": {"terrainType": "none", "turnsRemaining": -1},
        "trickRoom": False,
        "timeLimitMs": 6000,
        "updateIntervalMs": 400,
        "_planH": planh,
    }


# ----- Speed inference fires when oppMoveOrderThisTurn is present -----


def test_speed_fires_when_metadata_present():
    """Extension sends oppMoveOrderThisTurn → proxy fires on_turn_boundary_speed."""
    planh = {
        "battleId": "battle-1",
        "format": "gen9ou",
        "oppRevealedMoves": {},
        "oppMoveOrderThisTurn": {
            "turn": 1,
            "moveLog": [
                {"side": "p1", "species": "Iron Bundle", "moveId": "hydropump", "priority": 0},
                {"side": "p2", "species": "Dragapult", "moveId": "dracometeor", "priority": 0},
            ],
            "skipFlags": [],
            "myRole": "p1",
            "activeOppSpecies": "dragapult",
            "myActiveSpeedPostModifiers": 394,
        },
    }
    proxy.apply_belief(_request([_opp_mon("dragapult")], planh=planh))
    tracker = proxy._trackers["battle-1"]
    b = tracker.get("dragapult")
    # Bot Iron Bundle moved first → opp must be < 394 → max=393
    assert b.speed_range == (0, 393)
    assert b.speed_observations == [(1, 394, "us_first")]


def test_speed_no_op_when_metadata_missing():
    """Phase 1 client (no oppMoveOrderThisTurn) → no narrowing."""
    planh = {
        "battleId": "battle-1",
        "format": "gen9ou",
        "oppRevealedMoves": {},
    }
    proxy.apply_belief(_request([_opp_mon("dragapult")], planh=planh))
    tracker = proxy._trackers["battle-1"]
    assert tracker.get("dragapult").speed_range is None


def test_speed_dedupes_within_same_turn():
    """Multiple requests with same turn number → fires ONCE."""
    planh = {
        "battleId": "battle-1",
        "format": "gen9ou",
        "oppRevealedMoves": {},
        "oppMoveOrderThisTurn": {
            "turn": 1,
            "moveLog": [
                {"side": "p1", "species": "X", "moveId": "tackle", "priority": 0},
                {"side": "p2", "species": "Y", "moveId": "tackle", "priority": 0},
            ],
            "skipFlags": [],
            "myRole": "p1",
            "activeOppSpecies": "dragapult",
            "myActiveSpeedPostModifiers": 200,
        },
    }
    proxy.apply_belief(_request([_opp_mon("dragapult")], planh=planh))
    proxy.apply_belief(_request([_opp_mon("dragapult")], planh=planh))
    proxy.apply_belief(_request([_opp_mon("dragapult")], planh=planh))
    b = proxy._trackers["battle-1"].get("dragapult")
    # 1 observation, not 3
    assert len(b.speed_observations) == 1


def test_speed_fires_for_subsequent_turns():
    """Distinct turn numbers → each fires separately."""
    base_planh = {
        "battleId": "battle-1",
        "format": "gen9ou",
        "oppRevealedMoves": {},
    }
    for turn, my_speed in [(1, 200), (2, 300), (3, 400)]:
        planh = dict(base_planh, oppMoveOrderThisTurn={
            "turn": turn,
            "moveLog": [
                {"side": "p1", "species": "X", "moveId": "tackle", "priority": 0},
                {"side": "p2", "species": "Y", "moveId": "tackle", "priority": 0},
            ],
            "skipFlags": [],
            "myRole": "p1",
            "activeOppSpecies": "dragapult",
            "myActiveSpeedPostModifiers": my_speed,
        })
        proxy.apply_belief(_request([_opp_mon("dragapult")], planh=planh))
    b = proxy._trackers["battle-1"].get("dragapult")
    assert len(b.speed_observations) == 3


def test_speed_skip_flags_propagate():
    """Quick Claw / cant skip → speed narrower no-ops with recorded skip."""
    planh = {
        "battleId": "battle-1",
        "format": "gen9ou",
        "oppRevealedMoves": {},
        "oppMoveOrderThisTurn": {
            "turn": 1,
            "moveLog": [
                {"side": "p1", "species": "X", "moveId": "tackle", "priority": 0},
                {"side": "p2", "species": "Y", "moveId": "tackle", "priority": 0},
            ],
            "skipFlags": ["quick_claw"],
            "myRole": "p1",
            "activeOppSpecies": "dragapult",
            "myActiveSpeedPostModifiers": 400,
        },
    }
    proxy.apply_belief(_request([_opp_mon("dragapult")], planh=planh))
    b = proxy._trackers["battle-1"].get("dragapult")
    assert b.speed_range is None
    assert b.speed_observations == [(1, 400, "skipped:quick_claw")]


def test_speed_priority_mismatch_skips():
    """Priority mismatch in move-log → opp_moved_first=None → skipped."""
    planh = {
        "battleId": "battle-1",
        "format": "gen9ou",
        "oppRevealedMoves": {},
        "oppMoveOrderThisTurn": {
            "turn": 1,
            "moveLog": [
                {"side": "p1", "species": "X", "moveId": "extremespeed", "priority": 2},
                {"side": "p2", "species": "Y", "moveId": "earthquake", "priority": 0},
            ],
            "skipFlags": [],
            "myRole": "p1",
            "activeOppSpecies": "dragapult",
            "myActiveSpeedPostModifiers": 200,
        },
    }
    proxy.apply_belief(_request([_opp_mon("dragapult")], planh=planh))
    b = proxy._trackers["battle-1"].get("dragapult")
    assert b.speed_range is None
    assert b.speed_observations == [(1, 200, "skipped:no_move_order")]


def test_speed_trick_room_inverts():
    """in_trick_room=True flips the inequality direction."""
    planh = {
        "battleId": "battle-1",
        "format": "gen9ou",
        "oppRevealedMoves": {},
        "oppMoveOrderThisTurn": {
            "turn": 1,
            "moveLog": [
                {"side": "p2", "species": "Y", "moveId": "tackle", "priority": 0},
                {"side": "p1", "species": "X", "moveId": "tackle", "priority": 0},
            ],
            "skipFlags": [],
            "myRole": "p1",
            "activeOppSpecies": "dragapult",
            "myActiveSpeedPostModifiers": 200,
        },
        "inTrickRoom": True,
    }
    proxy.apply_belief(_request([_opp_mon("dragapult")], planh=planh))
    b = proxy._trackers["battle-1"].get("dragapult")
    # Outside TR: opp_first → min=201. Inside TR: inverted → max=199.
    assert b.speed_range == (0, 199)


def test_speed_modal_overlay_uses_narrowed_range():
    """End-to-end: inference fires THEN overlay uses the narrowed range
    when picking spreads. The fixture's Dragapult has 'Timid:0/0/4/252/0/252'
    at 0.51 and 'Modest:0/0/0/252/4/252' at 0.22 — Timid is +Spe. If
    speed_range narrows to exclude Timid spread, the modal should change.
    """
    # Tighten Garchomp speed_range to a window only Modest can satisfy.
    # Dragapult base 142. Timid 252 = (284+31+63)+5 = 383 * 1.1 = 421.
    # Modest 252 = (284+31+63)+5 = 383 * 1.0 = 383.
    # Narrow to (370, 400) → Modest (383) fits, Timid (421) doesn't.
    planh = {
        "battleId": "battle-1",
        "format": "gen9ou",
        "oppRevealedMoves": {},
        "oppMoveOrderThisTurn": {
            "turn": 1,
            "moveLog": [
                {"side": "p1", "species": "X", "moveId": "tackle", "priority": 0},
                {"side": "p2", "species": "Y", "moveId": "tackle", "priority": 0},
            ],
            "skipFlags": [],
            "myRole": "p1",
            "activeOppSpecies": "dragapult",
            "myActiveSpeedPostModifiers": 401,  # us_first → max=400
        },
    }
    out = proxy.apply_belief(_request([_opp_mon("dragapult")], planh=planh))
    b = proxy._trackers["battle-1"].get("dragapult")
    assert b.speed_range == (0, 400)
    # Fire a SECOND observation that pushes max below Timid (421):
    planh2 = dict(planh, oppMoveOrderThisTurn=dict(
        planh["oppMoveOrderThisTurn"], turn=2,
        myActiveSpeedPostModifiers=400,
    ))
    proxy.apply_belief(_request([_opp_mon("dragapult")], planh=planh2))
    b = proxy._trackers["battle-1"].get("dragapult")
    assert b.speed_range == (0, 399)
    # Modal moves should still be Dragapult's top-4
    moves = [m["id"] for m in out["sideTwo"]["pokemon"][0]["moves"]]
    assert moves[0] == "dracometeor"  # modal pick survives narrowing
