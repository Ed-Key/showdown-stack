"""Tests for the Plan H proxy sidecar (`showdown_copilot.proxy`).

Covers belief-state derivation from BattleRequest, modal overlay onto opp
Pokemon, LRU eviction, and back-compat passthrough when `_planH` is absent.
The HTTP layer (FastAPI streaming) is NOT exercised here — those are smoked
manually via curl in the run instructions.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

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
    proxy._format_by_battle.clear()
    yield
    proxy._trackers.clear()
    proxy._display_cache.clear()
    proxy._format_by_battle.clear()


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


# ---- /belief endpoint tests ----------------------------------------------

@pytest.fixture
def belief_chaos(tmp_path, monkeypatch):
    """Chaos data containing Garchomp + Tyranitar for /belief endpoint tests.
    Overrides the `install_priors` fixture's PriorsSource for this scope."""
    data = {
        "data": {
            "Garchomp": {
                "Moves": {
                    "earthquake": 0.50, "stoneedge": 0.30,
                    "swordsdance": 0.20, "scaleshot": 0.18,
                },
                "Items": {"rockyhelmet": 0.40, "choiceband": 0.25, "lifeorb": 0.20},
                "Abilities": {"roughskin": 0.90, "sandveil": 0.10},
                "Spreads": {"Adamant:0/252/0/0/4/252": 0.80},
                "Tera Types": {"Steel": 0.50, "Fire": 0.30},
            },
            "Tyranitar": {
                "Moves": {
                    "stoneedge": 0.60, "earthquake": 0.40,
                    "knockoff": 0.30, "stealthrock": 0.25,
                },
                "Items": {"smoothrock": 0.40, "leftovers": 0.30, "assaultvest": 0.15},
                "Abilities": {"sandstream": 0.95, "unnerve": 0.05},
                "Spreads": {"Adamant:252/64/0/0/192/0": 0.70},
                "Tera Types": {"Steel": 0.40, "Flying": 0.30},
            },
        }
    }
    (tmp_path / "gen9ou-1630.json").write_text(json.dumps(data))
    monkeypatch.setattr(proxy, "_priors", PriorsSource(cache_dir=tmp_path))
    proxy._display_cache.clear()


@pytest.fixture
def sample_belief_state(belief_chaos):
    """Seed proxy._trackers and per-battle format with garchomp (revealed: EQ,
    item) + tyranitar (no reveals) for battle_id 'test-battle-1'."""
    bid = "test-battle-1"
    tracker = proxy._get_tracker(bid)
    tracker.on_reveal_move("Garchomp", "earthquake")
    tracker.on_reveal_item("Garchomp", "choiceband")
    tracker.get("Tyranitar")  # entry exists, no reveals
    proxy._format_by_battle[bid] = "gen9ou"
    return bid


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    return TestClient(proxy.app)


# ---- /explain endpoint tests ---------------------------------------------

@pytest.fixture(autouse=True)
def _reset_explain_cache():
    """Clear the LRU cache between tests so identical (battle_id, turn, rqid)
    requests don't cross-pollinate the cache from a prior test case."""
    proxy._explain_cache.clear()
    yield
    proxy._explain_cache.clear()


def _explain_body(**overrides):
    """Default valid body for POST /explain. Override any field as kwargs."""
    body = {
        "battle_id": "b-explain-1",
        "turn": 5,
        "rqid": 12,
        "snapshot": {
            "mine": {
                "activeSpecies": "Heatran",
                "activeHp": 80,
                "activeAbility": "Flash Fire",
                "activeItem": "Leftovers",
                "activeMoves": ["Magma Storm", "Earth Power", "Taunt", "Stealth Rock"],
            },
            "opp": {
                "activeSpecies": "Garchomp",
                "activeHp": 100,
                "activeBoosts": {"atk": 1},
            },
            "weather": {"weatherType": "sand", "turnsRemaining": 4},
            "terrain": {"terrainType": "none", "turnsRemaining": -1},
            "trickRoom": False,
        },
        "engine_result": {
            "bestMove": "uturn",
            "confidence": 0.47,
            "sims": 1500000,
            "depth": 4,
            "pv": ["uturn", "earthquake"],
            "alternatives": [
                {"move": "magmastorm", "confidence": 0.45},
                {"move": "switch tyranitar", "confidence": 0.41},
            ],
        },
        "last_steps": [
            "|move|p1a: Heatran|Magma Storm|p2a: Garchomp",
            "|-damage|p2a: Garchomp|65/100",
        ],
    }
    body.update(overrides)
    return body


async def test_explain_returns_string_and_caches(client, monkeypatch):
    """First call hits the LLM; second identical call hits the cache and the
    LLM is NOT invoked again. Validates both happy-path return AND the cache
    semantics that protect against accidental double-billing during repeated
    UI requests for the same turn."""
    fake_llm = AsyncMock()
    fake_llm.complete.return_value = "test explanation"
    monkeypatch.setattr(proxy, "_llm", fake_llm)

    body = _explain_body()
    r1 = client.post("/explain", json=body)
    assert r1.status_code == 200, r1.text
    payload1 = r1.json()
    assert payload1["explanation"] == "test explanation"
    assert payload1["cached"] is False

    r2 = client.post("/explain", json=body)
    assert r2.status_code == 200
    payload2 = r2.json()
    assert payload2["explanation"] == "test explanation"
    assert payload2["cached"] is True
    assert fake_llm.complete.call_count == 1


def test_explain_503_when_llm_missing(client, monkeypatch):
    """When no GROQ_API_KEY was found at startup `_llm` is None; /explain
    must return 503 instead of crashing or silently returning empty text."""
    monkeypatch.setattr(proxy, "_llm", None)
    r = client.post("/explain", json=_explain_body())
    assert r.status_code == 503
    assert "GROQ_API_KEY" in r.json()["detail"]


def test_explain_prompt_includes_matrix_summary(client, monkeypatch):
    """When matrix_summary is present, the rendered user prompt must include
    the threat lines (attacker, move, target). Captures the prompt via the
    mock's call args so we assert on what actually went to Groq."""
    captured = {}

    async def _fake_complete(system, user, max_tokens=400):
        captured["system"] = system
        captured["user"] = user
        return "matrix-aware explanation"

    fake_llm = AsyncMock()
    fake_llm.complete.side_effect = _fake_complete
    monkeypatch.setattr(proxy, "_llm", fake_llm)

    body = _explain_body(
        battle_id="b-matrix-1",
        matrix_summary={
            "opp_attacks_me": [
                {
                    "opp": "Garchomp", "move": "Earthquake",
                    "source": "revealed", "target": "Tyranitar",
                    "dmg_pct_max": 142, "ohko": True, "two_hko": False,
                },
            ],
            "me_attacks_opp": [
                {
                    "me": "Gholdengo", "move": "Make It Rain",
                    "source": "revealed", "target": "Garchomp",
                    "dmg_pct_max": 78, "ohko": False, "two_hko": True,
                },
            ],
        },
    )
    r = client.post("/explain", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["explanation"] == "matrix-aware explanation"

    user_prompt = captured["user"]
    # Section header + both directions + key cell content.
    assert "Damage Matrix" in user_prompt
    assert "Garchomp Earthquake" in user_prompt
    assert "Tyranitar" in user_prompt
    assert "OHKO" in user_prompt
    assert "Gholdengo Make It Rain" in user_prompt
    assert "78%" in user_prompt
    # System prompt must carry the grounding rules so they reach the LLM.
    assert "GROUNDING RULES" in captured["system"]


def test_get_belief_returns_per_opp_revealed_and_modal(client, sample_belief_state):
    response = client.get(f"/belief/{sample_belief_state}")
    assert response.status_code == 200
    body = response.json()
    assert "format" in body
    assert "opponents" in body
    assert "garchomp" in body["opponents"]
    g = body["opponents"]["garchomp"]
    assert g["revealed"]["moves"] == ["earthquake"]
    assert g["revealed"]["item"] == "choiceband"
    assert g["modal"]["moves"]  # list of {name, pct}
    assert all(set(m.keys()) == {"name", "pct"} for m in g["modal"]["moves"])
    # tyranitar has no reveals; modal still populated from chaos
    t = body["opponents"]["tyranitar"]
    assert t["revealed"]["moves"] == []
    assert len(t["modal"]["moves"]) >= 1


# ---- /annotation endpoint tests ------------------------------------------

def test_annotation_persists_override_tag(client, monkeypatch, tmp_path):
    """Per-turn annotations submitted with `overrideTag` (e.g. user picked
    'item_assumption' from the dropdown) must serialize that field into the
    JSONL line so the engine-debug corpus pipeline can later aggregate
    overrides by error category."""
    monkeypatch.setattr(proxy, "_NOTES_DIR", tmp_path)

    body = {
        "battleId": "battle-gen9ou-override-1",
        "turn": 7,
        "kind": "turn",
        "text": "engine recced stay-in but Garchomp was clearly CB",
        "overrideTag": "item_assumption",
        "timestampMs": 1714723200000,
    }
    r = client.post("/annotation", json=body)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}

    from datetime import datetime as _dt
    out = tmp_path / f"{_dt.now().strftime('%Y-%m-%d')}.jsonl"
    assert out.exists(), f"expected JSONL at {out}"
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[-1])
    assert record["battleId"] == "battle-gen9ou-override-1"
    assert record["turn"] == 7
    assert record["kind"] == "turn"
    assert record["text"].startswith("engine recced")
    assert record["overrideTag"] == "item_assumption"


def test_annotation_without_override_tag_writes_null(client, monkeypatch, tmp_path):
    """Battle-level notes (and older extension clients) omit `overrideTag`.
    Pydantic's default of None must serialize to JSON `null` so downstream
    readers see a consistent schema across every line, regardless of source."""
    monkeypatch.setattr(proxy, "_NOTES_DIR", tmp_path)

    body = {
        "battleId": "battle-gen9ou-no-tag-2",
        "turn": 0,
        "kind": "battle",
        "text": "freeform reflection on the whole match",
    }
    r = client.post("/annotation", json=body)
    assert r.status_code == 200, r.text

    from datetime import datetime as _dt
    out = tmp_path / f"{_dt.now().strftime('%Y-%m-%d')}.jsonl"
    raw = out.read_text(encoding="utf-8").splitlines()[-1]
    # Empirically verified: pydantic v2 model_dump_json() emits null (not omits)
    # for Optional fields whose value is None. Lock that contract here.
    assert '"overrideTag":null' in raw, raw
    record = json.loads(raw)
    assert record["overrideTag"] is None
    assert record["kind"] == "battle"
