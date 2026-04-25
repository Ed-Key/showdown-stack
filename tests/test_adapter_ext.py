import json
from unittest.mock import MagicMock

import pytest

from showdown_copilot.adapter_ext import SpectatorAdapter
from showdown_copilot.models import ModalSet
from showdown_copilot.priors import PriorsSource


class StubPriors:
    """Test double for PriorsSource."""
    def __init__(self, returns: dict[str, ModalSet]):
        self._returns = returns

    def get_set(self, species, format, team_type=None):
        return self._returns[species.lower()]


def _modal(species: str, **overrides) -> ModalSet:
    base = dict(
        species=species, level=100, types=[], moves=["tackle"],
        item="leftovers", ability="none", nature="Serious",
        evs={k: 0 for k in ("hp", "atk", "def", "spa", "spd", "spe")},
        ivs={k: 31 for k in ("hp", "atk", "def", "spa", "spd", "spe")},
        stats={k: 100 for k in ("hp", "atk", "def", "spa", "spd", "spe")},
        tera_type="", weight_kg=0.0,
    )
    base.update(overrides)
    return ModalSet(**base)


OWN_PASTE = """\
Breloom @ Focus Sash
Ability: Technician
Level: 100
EVs: 252 Atk / 4 HP / 252 Spe
Jolly Nature
- Spore
- Mach Punch
- Bullet Seed
- Swords Dance
"""


def test_on_team_preview_builds_opponent_specs_from_priors():
    priors = StubPriors({
        "garchomp": _modal("garchomp", moves=["earthquake", "dragontail", "stealthrock", "stoneedge"]),
        "excadrill": _modal("excadrill", moves=["earthquake", "ironhead", "rapidspin", "swordsdance"]),
    })
    sa = SpectatorAdapter(
        own_paste=OWN_PASTE, format="gen9monotype",
        team_type="Ground", priors=priors,
    )
    sa.on_team_preview(["Garchomp", "Excadrill"])
    assert len(sa._opp_specs) == 2
    assert "garchomp" in sa._opp_specs
    assert sa._opp_specs["garchomp"].moves == ["earthquake", "dragontail", "stealthrock", "stoneedge"]


def test_to_engine_json_returns_dict_with_expected_top_keys():
    priors = StubPriors({"garchomp": _modal("garchomp")})
    sa = SpectatorAdapter(
        own_paste=OWN_PASTE, format="gen9monotype",
        team_type="Ground", priors=priors,
    )
    sa.on_team_preview(["Garchomp"])

    # Build a minimal mock poke-env battle
    battle = MagicMock()
    battle.team = {}
    battle.opponent_team = {}
    battle.active_pokemon = None
    battle.opponent_active_pokemon = None
    battle.side_conditions = {}
    battle.opponent_side_conditions = {}
    battle.weather = {}
    battle.fields = {}

    result = sa.to_engine_json(battle)
    assert "sideOne" in result
    assert "sideTwo" in result
    assert "weather" in result
    assert "terrain" in result


def test_on_reveal_replaces_last_move_when_new_one_seen():
    priors = StubPriors({"garchomp": _modal(
        "garchomp", moves=["earthquake", "dragontail", "stealthrock", "stoneedge"]
    )})
    sa = SpectatorAdapter(OWN_PASTE, "gen9monotype", "Ground", priors)
    sa.on_team_preview(["Garchomp"])
    sa.on_reveal("Garchomp", revealed_move="Swords Dance")
    assert "swordsdance" in sa._opp_specs["garchomp"].moves
    assert "stoneedge" not in sa._opp_specs["garchomp"].moves


def test_on_reveal_ignores_already_known_move():
    priors = StubPriors({"garchomp": _modal(
        "garchomp", moves=["earthquake", "dragontail", "stealthrock", "stoneedge"]
    )})
    sa = SpectatorAdapter(OWN_PASTE, "gen9monotype", "Ground", priors)
    sa.on_team_preview(["Garchomp"])
    before = list(sa._opp_specs["garchomp"].moves)
    sa.on_reveal("Garchomp", revealed_move="Earthquake")
    assert sa._opp_specs["garchomp"].moves == before


def test_on_reveal_updates_item_and_ability():
    priors = StubPriors({"garchomp": _modal("garchomp", item="leftovers", ability="sandveil")})
    sa = SpectatorAdapter(OWN_PASTE, "gen9monotype", "Ground", priors)
    sa.on_team_preview(["Garchomp"])
    sa.on_reveal("Garchomp", revealed_item="Choice Scarf", revealed_ability="Rough Skin")
    assert sa._opp_specs["garchomp"].item == "choicescarf"
    assert sa._opp_specs["garchomp"].ability == "roughskin"


def test_on_reveal_unknown_species_is_noop():
    priors = StubPriors({"garchomp": _modal("garchomp")})
    sa = SpectatorAdapter(OWN_PASTE, "gen9monotype", "Ground", priors)
    sa.on_team_preview(["Garchomp"])
    # Not in _opp_specs — must not raise
    sa.on_reveal("Kingambit", revealed_move="Sucker Punch")
    assert "kingambit" not in sa._opp_specs


# ---------------- PIMC path tests (Plan G' Task 4) ----------------


@pytest.fixture
def fake_priors_pimc(tmp_path):
    """A PriorsSource backed by a tiny in-tmp chaos JSON for two species.

    Keys are display-cased to match real Smogon chaos JSON shape. The
    SpectatorAdapter is responsible for preserving display casing through
    to the sample_set call site.
    """
    species_data = {
        "Garchomp": {
            "Moves": {"earthquake": 50, "stealthrock": 40, "spikes": 30, "dragontail": 20, "swordsdance": 15, "scaleshot": 10},
            "Items": {"rockyhelmet": 50, "lifeorb": 40, "leftovers": 20},
            "Abilities": {"roughskin": 90, "sandveil": 10},
            "Spreads": {"Jolly:0/252/0/0/4/252": 80, "Adamant:0/252/0/0/4/252": 20},
            "Tera Types": {"Steel": 50, "Fire": 30, "Ground": 20},
        },
        "Corviknight": {
            "Moves": {"roost": 60, "bodypress": 40, "irondefense": 30, "uturn": 25, "defog": 20, "bravebird": 15},
            "Items": {"leftovers": 60, "rockyhelmet": 30, "heavydutyboots": 10},
            "Abilities": {"pressure": 50, "mirrorarmor": 40, "unnerve": 10},
            "Spreads": {"Impish:248/0/252/0/8/0": 70, "Careful:248/0/0/0/252/8": 30},
            "Tera Types": {"Dragon": 50, "Fairy": 30, "Steel": 20},
        },
    }
    fake = tmp_path / "gen9ou-1500.json"
    fake.write_text(json.dumps({"data": species_data}))
    return PriorsSource(cache_dir=tmp_path, rating=1500, month="2026-04")


@pytest.fixture
def own_paste_pimc():
    """Minimal team paste — content doesn't matter for these tests, the BattleAdapter parses it."""
    return """\
Iron Hands @ Choice Band
Ability: Quark Drive
Tera Type: Fighting
EVs: 252 HP / 252 Atk / 4 Def
Adamant Nature
- Drain Punch
- Wild Charge
- Heavy Slam
- Earthquake
"""


def _setup_adapter_pimc(fake_priors, own_paste, **kwargs):
    a = SpectatorAdapter(
        own_paste=own_paste,
        format="gen9ou",
        team_type=None,
        priors=fake_priors,
        **kwargs,
    )
    a.on_team_preview(["Garchomp", "Corviknight"])
    return a


def _make_fake_battle():
    battle = MagicMock()
    battle.team = {}
    battle.opponent_team = {}
    battle.active_pokemon = None
    battle.opponent_active_pokemon = None
    battle.side_conditions = {}
    battle.opponent_side_conditions = {}
    battle.weather = {}
    battle.fields = {}
    return battle


def test_adapter_pimc_off_payload_unchanged(fake_priors_pimc, own_paste_pimc):
    """When use_pimc=False, to_engine_json returns the same shape as today."""
    a = _setup_adapter_pimc(fake_priors_pimc, own_paste_pimc, use_pimc=False)
    fake_battle = _make_fake_battle()
    out = a.to_engine_json(fake_battle)
    assert "hypotheses" not in out


def test_adapter_pimc_on_emits_k_hypotheses(fake_priors_pimc, own_paste_pimc):
    """When use_pimc=True, output is {'hypotheses': [...]} of length pimc_k."""
    a = _setup_adapter_pimc(fake_priors_pimc, own_paste_pimc, use_pimc=True, pimc_k=4)
    fake_battle = _make_fake_battle()
    out = a.to_engine_json(fake_battle)
    assert "hypotheses" in out
    assert isinstance(out["hypotheses"], list)
    assert len(out["hypotheses"]) == 4


def test_adapter_revealed_move_in_all_hypotheses(fake_priors_pimc, own_paste_pimc):
    """After on_reveal, every PIMC hypothesis must contain the revealed move."""
    a = _setup_adapter_pimc(fake_priors_pimc, own_paste_pimc, use_pimc=True, pimc_k=8)
    a.on_reveal("Corviknight", revealed_move="irondefense")
    fake_battle = _make_fake_battle()
    out = a.to_engine_json(fake_battle)
    found_count = 0
    for h in out["hypotheses"]:
        h_str = json.dumps(h).lower()
        if "irondefense" in h_str:
            found_count += 1
    assert found_count == 8, f"only {found_count}/8 hypotheses contain revealed move 'irondefense'"


def test_adapter_revealed_item_in_all_hypotheses(fake_priors_pimc, own_paste_pimc):
    """After on_reveal with revealed_item, every PIMC hypothesis must contain that item."""
    a = _setup_adapter_pimc(fake_priors_pimc, own_paste_pimc, use_pimc=True, pimc_k=8)
    a.on_reveal("Corviknight", revealed_item="rockyhelmet")
    fake_battle = _make_fake_battle()
    out = a.to_engine_json(fake_battle)
    found_count = 0
    for h in out["hypotheses"]:
        h_str = json.dumps(h).lower()
        if "rockyhelmet" in h_str:
            found_count += 1
    assert found_count == 8, f"only {found_count}/8 hypotheses contain revealed item 'rockyhelmet'"


def test_adapter_revealed_ability_in_all_hypotheses(fake_priors_pimc, own_paste_pimc):
    """After on_reveal with revealed_ability, every PIMC hypothesis must contain that ability."""
    a = _setup_adapter_pimc(fake_priors_pimc, own_paste_pimc, use_pimc=True, pimc_k=8)
    a.on_reveal("Corviknight", revealed_ability="mirrorarmor")
    fake_battle = _make_fake_battle()
    out = a.to_engine_json(fake_battle)
    found_count = 0
    for h in out["hypotheses"]:
        h_str = json.dumps(h).lower()
        if "mirrorarmor" in h_str:
            found_count += 1
    assert found_count == 8, f"only {found_count}/8 hypotheses contain revealed ability 'mirrorarmor'"


def test_adapter_pimc_seed_yields_reproducible_hypotheses(fake_priors_pimc, own_paste_pimc):
    """Two adapters with the same pimc_seed produce identical hypotheses lists."""
    a1 = _setup_adapter_pimc(fake_priors_pimc, own_paste_pimc, use_pimc=True, pimc_k=4, pimc_seed=42)
    a2 = _setup_adapter_pimc(fake_priors_pimc, own_paste_pimc, use_pimc=True, pimc_k=4, pimc_seed=42)
    fake_battle = _make_fake_battle()
    out1 = a1.to_engine_json(fake_battle)
    out2 = a2.to_engine_json(fake_battle)
    # Compare the JSON serialization of the two outputs — should be byte-identical.
    assert json.dumps(out1, sort_keys=True) == json.dumps(out2, sort_keys=True)


def test_adapter_pimc_no_seed_yields_different_hypotheses(fake_priors_pimc, own_paste_pimc):
    """Without a seed, two consecutive calls produce different hypotheses (non-zero probability)."""
    a = _setup_adapter_pimc(fake_priors_pimc, own_paste_pimc, use_pimc=True, pimc_k=4)
    fake_battle = _make_fake_battle()
    out1 = a.to_engine_json(fake_battle)
    out2 = a.to_engine_json(fake_battle)
    # Across multiple draws, at least one of K should differ. Allow rare flake by
    # comparing serialized strings — they're nearly certainly different.
    s1 = json.dumps(out1, sort_keys=True)
    s2 = json.dumps(out2, sort_keys=True)
    # If both are identical that's a 1-in-(billions) chance with diverse priors;
    # treat it as a sampler-narrowness signal, not a flake. But assert anyway.
    assert s1 != s2, "two unseeded calls produced identical output — sampler may be too narrow"
