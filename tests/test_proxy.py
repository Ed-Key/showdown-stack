"""Tests for the Plan H proxy sidecar (`showdown_copilot.proxy`).

Covers belief-state derivation from BattleRequest, modal overlay onto opp
Pokemon, LRU eviction, and back-compat passthrough when `_planH` is absent.
The HTTP layer (FastAPI streaming) is NOT exercised here — those are smoked
manually via curl in the run instructions.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from showdown_copilot import proxy
from showdown_copilot.priors import PriorsSource

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def chaos_cache(tmp_path):
    """Mirror tests/test_priors.py: stage the natdex mini chaos fixture as a
    `gen9ou-1630.json` file so PriorsSource picks it up without network I/O."""
    src = (FIXTURE_DIR / "mini_chaos_natdex.json").read_text()
    (tmp_path / "gen9ou-1630.json").write_text(src)
    return tmp_path


@pytest.fixture(autouse=True)
def install_priors(chaos_cache, monkeypatch):
    """Replace proxy module's `_priors` global with a fixture-backed source.
    Reset trackers between tests so LRU/state doesn't bleed."""
    monkeypatch.setattr(proxy, "_priors", PriorsSource(cache_dir=chaos_cache))
    proxy._trackers.clear()
    proxy._display_cache.clear()
    yield
    proxy._trackers.clear()
    proxy._display_cache.clear()


def _opp_mon(species, item="none", ability="none", tera="", terastallized=False):
    """Build a minimal opp Pokemon dict matching what the extension sends."""
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
    """Minimal BattleRequest envelope. Caller can omit `planh` to test passthrough."""
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


def test_no_planh_is_passthrough_unchanged():
    """Without `_planH`, apply_belief returns the request unchanged.
    Back-compat for any caller that hits the proxy without metadata."""
    req = _request([_opp_mon("dragapult")])
    original_moves = list(req["sideTwo"]["pokemon"][0]["moves"])
    out = proxy.apply_belief(req)
    assert out["sideTwo"]["pokemon"][0]["moves"] == original_moves
    assert "_planH" not in out


def test_planh_is_stripped_before_forwarding():
    """The engine doesn't speak `_planH`; apply_belief must remove it."""
    req = _request(
        [_opp_mon("dragapult")],
        planh={"battleId": "battle-gen9ou-1", "format": "gen9ou", "oppRevealedMoves": {}},
    )
    out = proxy.apply_belief(req)
    assert "_planH" not in out


def test_modal_replaces_moves_for_unrevealed_pokemon():
    """No reveals → modal == top-4 chaos moves. The fixture's Dragapult has
    ['dracometeor', 'shadowball', 'uturn', 'flamethrower'] as top-4."""
    req = _request(
        [_opp_mon("dragapult")],
        planh={"battleId": "b1", "format": "gen9ou", "oppRevealedMoves": {}},
    )
    out = proxy.apply_belief(req)
    moves = [m["id"] for m in out["sideTwo"]["pokemon"][0]["moves"]]
    assert moves == ["dracometeor", "shadowball", "uturn", "flamethrower"]


def test_revealed_moves_persist_through_modal_filter():
    """Revealed moves MUST appear in the post-overlay move list — the modal
    is filtered to be a superset of belief.revealed_moves. Hex is a low-
    frequency chaos pick (0.18); without belief it'd be dropped, but with
    a Hex reveal it must survive."""
    req = _request(
        [_opp_mon("dragapult")],
        planh={
            "battleId": "b1",
            "format": "gen9ou",
            "oppRevealedMoves": {"dragapult": ["hex"]},
        },
    )
    out = proxy.apply_belief(req)
    moves = [m["id"] for m in out["sideTwo"]["pokemon"][0]["moves"]]
    assert "hex" in moves


def test_item_backfilled_when_extension_sent_none():
    """Unrevealed item ('none') → modal item ('choicespecs' for Dragapult)."""
    req = _request(
        [_opp_mon("dragapult", item="none")],
        planh={"battleId": "b1", "format": "gen9ou", "oppRevealedMoves": {}},
    )
    out = proxy.apply_belief(req)
    assert out["sideTwo"]["pokemon"][0]["item"] == "choicespecs"


def test_item_preserved_when_extension_sent_real_value():
    """Revealed item must NOT be overwritten by modal."""
    req = _request(
        [_opp_mon("dragapult", item="lifeorb")],
        planh={"battleId": "b1", "format": "gen9ou", "oppRevealedMoves": {}},
    )
    out = proxy.apply_belief(req)
    assert out["sideTwo"]["pokemon"][0]["item"] == "lifeorb"


def test_ability_backfilled_when_extension_sent_none():
    """Same logic as item: 'none' → modal ability ('infiltrator')."""
    req = _request(
        [_opp_mon("dragapult", ability="none")],
        planh={"battleId": "b1", "format": "gen9ou", "oppRevealedMoves": {}},
    )
    out = proxy.apply_belief(req)
    assert out["sideTwo"]["pokemon"][0]["ability"] == "infiltrator"


def test_tera_type_backfilled_when_extension_sent_empty():
    """Extension hardcodes teraType='' for opp (content.ts:215). The proxy
    fills it with the modal Tera type (Ghost for fixture Dragapult)."""
    req = _request(
        [_opp_mon("dragapult", tera="")],
        planh={"battleId": "b1", "format": "gen9ou", "oppRevealedMoves": {}},
    )
    out = proxy.apply_belief(req)
    assert out["sideTwo"]["pokemon"][0]["teraType"] == "Ghost"


def test_revealed_item_feeds_belief_tracker():
    """Item reveal in the BattleRequest must be ingested into the tracker
    so subsequent turns see it as `belief.revealed_item`."""
    planh = {"battleId": "b1", "format": "gen9ou", "oppRevealedMoves": {}}
    req = _request([_opp_mon("dragapult", item="choicespecs")], planh=planh)
    proxy.apply_belief(req)

    tracker = proxy._trackers["b1"]
    assert tracker.get("dragapult").revealed_item == "choicespecs"


def test_lru_evicts_oldest_battle_at_cap():
    """When the tracker count exceeds MAX_TRACKERS, the oldest entry drops."""
    proxy._trackers.clear()
    for i in range(proxy.MAX_TRACKERS + 1):
        req = _request(
            [_opp_mon("dragapult")],
            planh={"battleId": f"battle-{i}", "format": "gen9ou", "oppRevealedMoves": {}},
        )
        proxy.apply_belief(req)
    assert "battle-0" not in proxy._trackers
    assert f"battle-{proxy.MAX_TRACKERS}" in proxy._trackers
    assert len(proxy._trackers) == proxy.MAX_TRACKERS


def test_same_battle_id_reuses_tracker_across_turns():
    """Turn 2 must see the reveals from turn 1 — i.e., the tracker is the
    same instance, not a fresh one created per request."""
    planh1 = {"battleId": "b1", "format": "gen9ou", "oppRevealedMoves": {"dragapult": ["hex"]}}
    proxy.apply_belief(_request([_opp_mon("dragapult")], planh=planh1))

    planh2 = {"battleId": "b1", "format": "gen9ou", "oppRevealedMoves": {"dragapult": ["hex", "uturn"]}}
    proxy.apply_belief(_request([_opp_mon("dragapult")], planh=planh2))

    tracker = proxy._trackers["b1"]
    assert tracker.get("dragapult").revealed_moves == {"hex", "uturn"}


def test_missing_battle_id_skips_overlay_safely():
    """A `_planH` block without `battleId` should log a warning but not crash.
    Output moves should match the unmodified input (no overlay applied)."""
    req = _request(
        [_opp_mon("dragapult")],
        planh={"format": "gen9ou", "oppRevealedMoves": {}},  # no battleId
    )
    original_moves = list(req["sideTwo"]["pokemon"][0]["moves"])
    out = proxy.apply_belief(req)
    assert out["sideTwo"]["pokemon"][0]["moves"] == original_moves


# ----- Tera-ban derivation -----


def test_tera_banned_set_for_gen9ou():
    """Smogon OU bans Tera since late 2025 → flag forwarded to engine."""
    req = _request(
        [_opp_mon("dragapult")],
        planh={"battleId": "b1", "format": "gen9ou", "oppRevealedMoves": {}},
    )
    out = proxy.apply_belief(req)
    assert out["teraBanned"] is True


def test_tera_banned_set_for_gen9nationaldex():
    """NatDex OU also bans Tera. Format display strings get normalized."""
    req = _request(
        [_opp_mon("dragapult")],
        planh={"battleId": "b1", "format": "[Gen 9] National Dex", "oppRevealedMoves": {}},
    )
    out = proxy.apply_belief(req)
    assert out["teraBanned"] is True


def test_tera_unbanned_for_gen9ag():
    """Anything Goes / Ubers permit Tera."""
    req = _request(
        [_opp_mon("dragapult")],
        planh={"battleId": "b1", "format": "gen9anythinggoes", "oppRevealedMoves": {}},
    )
    out = proxy.apply_belief(req)
    assert out["teraBanned"] is False


def test_tera_extension_override_wins():
    """Extension can override the auto-derived ban via _planH.teraBanned."""
    req = _request(
        [_opp_mon("dragapult")],
        planh={
            "battleId": "b1", "format": "gen9ou", "oppRevealedMoves": {},
            "teraBanned": False,  # contradicts default
        },
    )
    out = proxy.apply_belief(req)
    assert out["teraBanned"] is False
