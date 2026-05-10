"""Tests for PIMC v2 Phase 1 (proxy fan-out).

Covers:
  - K=0 / unset env → bit-identical to the legacy single-modal path
  - K>=2 → returns `{"hypotheses": [K items]}` with correct envelope shape
  - revealed moves are honored across ALL K sampled sets (per opp mon)
  - K auto-tune: pre-reveal → 8, mid-game → 6, late-game → 2

The HTTP layer is NOT exercised here (same convention as test_proxy.py);
the wiring into `analyze_stream` is exercised by manual smoke testing
under `POKE_PROXY_PIMC_K=4 python -m showdown_copilot.proxy`.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from showdown_copilot import proxy
from showdown_copilot.belief import BeliefTracker
from showdown_copilot.priors import PriorsSource

FIXTURE_DIR = Path(__file__).parent / "fixtures"


# --- Fixtures (mirror test_proxy.py so the chaos cache is available) -----


@pytest.fixture
def chaos_cache(tmp_path):
    src = (FIXTURE_DIR / "mini_chaos_natdex.json").read_text()
    (tmp_path / "gen9ou-1630.json").write_text(src)
    return tmp_path


@pytest.fixture(autouse=True)
def install_priors(chaos_cache, monkeypatch):
    """Install a fixture-backed PriorsSource and reset proxy global state."""
    monkeypatch.setattr(proxy, "_priors", PriorsSource(cache_dir=chaos_cache))
    proxy._trackers.clear()
    proxy._display_cache.clear()
    proxy._format_by_battle.clear()
    proxy._format_resolution.clear()
    # Make sure the env var is OFF for tests that don't explicitly set it.
    monkeypatch.delenv("POKE_PROXY_PIMC_K", raising=False)
    yield
    proxy._trackers.clear()
    proxy._display_cache.clear()
    proxy._format_by_battle.clear()
    proxy._format_resolution.clear()


def _opp_mon(species, item="none", ability="none", tera="", terastallized=False):
    """Mirrors test_proxy.py — minimal opp Pokemon dict."""
    return {
        "species": species,
        "level": 100,
        "types": ["Dragon", "Ghost"],
        "hp": 100, "maxhp": 100,
        "ability": ability,
        "item": item,
        "nature": "Serious",
        "evs": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "attack": 100, "defense": 100, "specialAttack": 100,
        "specialDefense": 100, "speed": 100,
        "status": "None", "restTurns": 0, "sleepTurns": 0, "weightKg": 0.0,
        "moves": [
            {"id": "dracometeor", "pp": 8},
            {"id": "shadowball", "pp": 8},
            {"id": "uturn", "pp": 8},
            {"id": "flamethrower", "pp": 8},
        ],
        "terastallized": terastallized,
        "teraType": tera,
    }


def _request(opp_mons, planh=None):
    """Mirrors test_proxy.py — minimal BattleRequest envelope."""
    req = {
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
    }
    if planh is not None:
        req["_planH"] = planh
    return req


# ----- env-flag parsing ----------------------------------------------------


def test_env_flag_default_is_zero(monkeypatch):
    """No env set → K=0 (PIMC OFF)."""
    monkeypatch.delenv("POKE_PROXY_PIMC_K", raising=False)
    assert proxy._read_pimc_k_env() == 0


def test_env_flag_parses_valid_int(monkeypatch):
    monkeypatch.setenv("POKE_PROXY_PIMC_K", "4")
    assert proxy._read_pimc_k_env() == 4


def test_env_flag_clamps_negative_to_zero(monkeypatch):
    monkeypatch.setenv("POKE_PROXY_PIMC_K", "-3")
    assert proxy._read_pimc_k_env() == 0


def test_env_flag_clamps_oversized_to_eight(monkeypatch):
    monkeypatch.setenv("POKE_PROXY_PIMC_K", "100")
    assert proxy._read_pimc_k_env() == 8


def test_env_flag_invalid_falls_back_to_zero(monkeypatch):
    monkeypatch.setenv("POKE_PROXY_PIMC_K", "banana")
    assert proxy._read_pimc_k_env() == 0


# ----- apply_belief_pimc back-compat passes when K<=1 ---------------------


def test_pimc_k0_bit_identical_to_apply_belief():
    """K=0 (and K=1) MUST defer to apply_belief — no behavior change."""
    req_a = _request(
        [_opp_mon("dragapult")],
        planh={"battleId": "b1", "format": "gen9ou", "oppRevealedMoves": {}},
    )
    req_b = _request(
        [_opp_mon("dragapult")],
        planh={"battleId": "b1-pimc", "format": "gen9ou", "oppRevealedMoves": {}},
    )
    out_legacy = proxy.apply_belief(req_a)
    proxy._trackers.clear()  # reset before second pass to keep state clean
    out_pimc_k0 = proxy.apply_belief_pimc(req_b, k=0)

    # Move list, item, ability, teraType all overlay-overlaid identically.
    assert out_legacy["sideTwo"]["pokemon"][0]["moves"] == \
        out_pimc_k0["sideTwo"]["pokemon"][0]["moves"]
    assert out_legacy["sideTwo"]["pokemon"][0]["item"] == \
        out_pimc_k0["sideTwo"]["pokemon"][0]["item"]
    assert out_legacy["sideTwo"]["pokemon"][0]["ability"] == \
        out_pimc_k0["sideTwo"]["pokemon"][0]["ability"]
    # No hypotheses envelope when K<=1.
    assert "hypotheses" not in out_legacy
    assert "hypotheses" not in out_pimc_k0


def test_pimc_k1_bit_identical_to_apply_belief():
    """K=1 also short-circuits to single-modal."""
    req = _request(
        [_opp_mon("dragapult")],
        planh={"battleId": "b1", "format": "gen9ou", "oppRevealedMoves": {}},
    )
    out = proxy.apply_belief_pimc(req, k=1)
    assert "hypotheses" not in out
    # Should look like apply_belief output: top-level battleId + teraBanned set.
    assert out.get("battleId") == "b1"
    assert "_planH" not in out


# ----- apply_belief_pimc fan-out shape ------------------------------------


def test_pimc_k4_returns_hypotheses_envelope():
    """K=4 → top-level `hypotheses` array of length 4. Each hypothesis is a
    full BattleRequest with `sideOne` + `sideTwo`."""
    req = _request(
        [_opp_mon("dragapult")],
        planh={"battleId": "b1", "format": "gen9ou", "oppRevealedMoves": {}},
    )
    out = proxy.apply_belief_pimc(req, k=4)
    assert "hypotheses" in out
    assert isinstance(out["hypotheses"], list)
    assert len(out["hypotheses"]) == 4
    for hyp in out["hypotheses"]:
        assert "sideOne" in hyp
        assert "sideTwo" in hyp
        assert "pokemon" in hyp["sideTwo"]
        # Top-level fields the engine expects also propagate per-hypothesis.
        # (Each hypothesis is a complete state the engine can parse.)


def test_pimc_envelope_carries_battle_id_and_budget():
    """The envelope MUST forward battleId / turn / budget at the top level so
    the engine's instrument log + per-hypothesis budget division work."""
    req = _request(
        [_opp_mon("dragapult")],
        planh={"battleId": "b1", "turn": 7, "format": "gen9ou", "oppRevealedMoves": {}},
    )
    out = proxy.apply_belief_pimc(req, k=3)
    assert out["battleId"] == "b1"
    assert out["turn"] == 7
    assert out["timeLimitMs"] == 6000
    assert out["updateIntervalMs"] == 400


def test_pimc_falls_back_to_apply_belief_without_planh():
    """No `_planH` metadata → fall through to apply_belief (legacy path).
    PIMC fan-out requires belief to be useful, so this is a clean degrade."""
    req = _request([_opp_mon("dragapult")])  # no planh
    original_moves = list(req["sideTwo"]["pokemon"][0]["moves"])
    out = proxy.apply_belief_pimc(req, k=4)
    assert "hypotheses" not in out
    assert out["sideTwo"]["pokemon"][0]["moves"] == original_moves


# ----- reveals are honored across all K hypotheses ------------------------


def test_pimc_revealed_moves_appear_in_every_hypothesis():
    """The PIMC contract: every one of the K sampled sets MUST include all
    revealed moves for that mon. If Hex was revealed for Dragapult, all K
    of Dragapult's sampled-set move lists contain 'hex'."""
    req = _request(
        [_opp_mon("dragapult")],
        planh={
            "battleId": "b1",
            "format": "gen9ou",
            "oppRevealedMoves": {"dragapult": ["hex"]},
        },
    )
    out = proxy.apply_belief_pimc(req, k=6)
    assert len(out["hypotheses"]) == 6
    for hyp in out["hypotheses"]:
        moves = [m["id"] for m in hyp["sideTwo"]["pokemon"][0]["moves"]]
        assert "hex" in moves, f"hypothesis missing revealed move 'hex': {moves}"


def test_pimc_revealed_item_persists_across_hypotheses():
    """Revealed items are NOT overwritten by sampled draws — the proxy
    leaves a real item value alone, same as in the modal path."""
    req = _request(
        [_opp_mon("dragapult", item="lifeorb")],
        planh={"battleId": "b1", "format": "gen9ou", "oppRevealedMoves": {}},
    )
    out = proxy.apply_belief_pimc(req, k=4)
    for hyp in out["hypotheses"]:
        assert hyp["sideTwo"]["pokemon"][0]["item"] == "lifeorb"


# ----- K auto-tune --------------------------------------------------------


def test_autotune_returns_zero_when_requested_zero():
    tracker = BeliefTracker()
    assert proxy._choose_pimc_k_from_belief(0, tracker, []) == 0


def test_autotune_returns_one_when_requested_one():
    tracker = BeliefTracker()
    assert proxy._choose_pimc_k_from_belief(1, tracker, []) == 1


def test_autotune_pre_reveal_returns_eight_when_ceiling_high():
    """Auto-tune disabled (Option A 2026-05-10): always returns requested_k.
    Was: pre-reveal returns K=8."""
    tracker = BeliefTracker()
    opp = [_opp_mon("dragapult")]
    assert proxy._choose_pimc_k_from_belief(8, tracker, opp) == 8


def test_autotune_pre_reveal_caps_at_requested_ceiling():
    """Auto-tune disabled: K=4 ceiling stays K=4 (also matched old behavior)."""
    tracker = BeliefTracker()
    opp = [_opp_mon("dragapult")]
    assert proxy._choose_pimc_k_from_belief(4, tracker, opp) == 4


def test_autotune_mid_game_returns_requested_k():
    """Auto-tune disabled: returns requested_k unmodified.
    Was: mid-game returned K=6 regardless of higher requested_k."""
    tracker = BeliefTracker()
    tracker.on_reveal_move("dragapult", "dracometeor")
    tracker.on_reveal_move("dragapult", "shadowball")
    tracker.on_reveal_move("dragapult", "uturn")
    opp = [_opp_mon("dragapult")]
    # Was 6 with auto-tune, now 8 (the requested ceiling).
    assert proxy._choose_pimc_k_from_belief(8, tracker, opp) == 8


def test_autotune_late_game_returns_requested_k():
    """Auto-tune disabled: returns requested_k unmodified.
    Was: late-game returned K=2 regardless of higher requested_k.
    THIS WAS THE BUG that prompted Option A — env K=4 dropping to 2."""
    tracker = BeliefTracker()
    tracker.on_reveal_move("dragapult", "dracometeor")
    tracker.on_reveal_move("dragapult", "shadowball")
    tracker.on_reveal_move("dragapult", "uturn")
    tracker.on_reveal_move("dragapult", "flamethrower")
    tracker.on_reveal_item("dragapult", "choicespecs")
    opp = [_opp_mon("dragapult", item="choicespecs")]
    # Was 2 with auto-tune, now 8 (the requested ceiling).
    assert proxy._choose_pimc_k_from_belief(8, tracker, opp) == 8


def test_autotune_late_game_caps_at_requested_ceiling():
    """Even at late-game, requested_k=2 returns 2 (no upgrade)."""
    tracker = BeliefTracker()
    tracker.on_reveal_move("dragapult", "dracometeor")
    tracker.on_reveal_move("dragapult", "shadowball")
    tracker.on_reveal_move("dragapult", "uturn")
    tracker.on_reveal_move("dragapult", "flamethrower")
    tracker.on_reveal_item("dragapult", "choicespecs")
    opp = [_opp_mon("dragapult", item="choicespecs")]
    assert proxy._choose_pimc_k_from_belief(2, tracker, opp) == 2


def test_autotune_skips_empty_opp_slots():
    """`species == 'none'` slots don't contribute reveal points."""
    tracker = BeliefTracker()
    opp = [
        _opp_mon("dragapult"),
        _opp_mon("none"),  # empty reserve
    ]
    # No reveals — pre-reveal regardless of empty slots.
    assert proxy._choose_pimc_k_from_belief(8, tracker, opp) == 8
