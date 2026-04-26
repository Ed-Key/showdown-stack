import json
from unittest.mock import MagicMock

import pytest

from battle_testing.team_parser import PokemonSpec

from showdown_copilot.adapter_ext import SpectatorAdapter
from showdown_copilot.belief import BeliefTracker
from showdown_copilot.models import ModalSet
from showdown_copilot.priors import PriorsSource


class StubPriors:
    """Test double for PriorsSource."""
    def __init__(self, returns: dict[str, ModalSet]):
        self._returns = returns
        # Capture the most recent belief argument so tests can assert that
        # the adapter is plumbing belief through into get_set (Plan H Task 3).
        self.last_belief = None

    def get_set(self, species, format, team_type=None, belief=None):
        self.last_belief = belief
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


# ---------------- Plan H Task 3: BeliefTracker migration ----------------


def test_constructor_accepts_belief_tracker_kwarg():
    """Adapter accepts a pre-built BeliefTracker via the new kwarg, and uses it."""
    from showdown_copilot.belief import BeliefTracker

    priors = StubPriors({"garchomp": _modal("garchomp")})
    tracker = BeliefTracker()
    # Pre-seed the tracker so we can verify the adapter uses *this* instance,
    # not a fresh one of its own.
    tracker.on_reveal_move("Garchomp", "Earthquake")

    sa = SpectatorAdapter(
        own_paste=OWN_PASTE, format="gen9monotype",
        team_type="Ground", priors=priors,
        belief_tracker=tracker,
    )
    # The adapter's tracker IS the one we passed in.
    assert sa._belief is tracker
    # The pre-seeded reveal survives. Note: on_team_preview is NOT called here,
    # so the tracker is not reset — this is the documented "preserve preexisting
    # state across construction" path.
    assert "earthquake" in sa._belief.get("Garchomp").revealed_moves


def test_constructor_default_creates_fresh_belief_tracker():
    """Without belief_tracker kwarg, adapter constructs its own BeliefTracker."""
    from showdown_copilot.belief import BeliefTracker

    priors = StubPriors({"garchomp": _modal("garchomp")})
    sa = SpectatorAdapter(
        own_paste=OWN_PASTE, format="gen9monotype",
        team_type="Ground", priors=priors,
    )
    assert isinstance(sa._belief, BeliefTracker)


def test_on_reveal_delegates_to_belief_tracker():
    """on_reveal updates the BeliefTracker (revealed_moves/item/ability) in addition
    to mutating _opp_specs. Plan H Task 3 contract."""
    priors = StubPriors({"garchomp": _modal(
        "garchomp", moves=["earthquake", "dragontail", "stealthrock", "stoneedge"],
    )})
    sa = SpectatorAdapter(OWN_PASTE, "gen9monotype", "Ground", priors)
    sa.on_team_preview(["Garchomp"])

    sa.on_reveal(
        "Garchomp",
        revealed_move="Swords Dance",
        revealed_item="Choice Scarf",
        revealed_ability="Rough Skin",
    )

    belief = sa._belief.get("Garchomp")
    assert "swordsdance" in belief.revealed_moves
    assert belief.revealed_item == "choicescarf"
    assert belief.revealed_ability == "roughskin"


def test_on_team_preview_resets_belief_tracker():
    """on_team_preview must clear the belief tracker, not just _opp_specs."""
    priors = StubPriors({"garchomp": _modal("garchomp")})
    sa = SpectatorAdapter(OWN_PASTE, "gen9monotype", "Ground", priors)
    sa.on_team_preview(["Garchomp"])
    sa.on_reveal("Garchomp", revealed_move="Earthquake")
    assert "earthquake" in sa._belief.get("Garchomp").revealed_moves

    # New battle starts — preview must reset belief.
    sa.on_team_preview(["Garchomp"])
    assert "earthquake" not in sa._belief.get("Garchomp").revealed_moves


def test_on_team_preview_preserves_external_tracker_identity():
    """REGRESSION (Task 3 review): if a caller passes an external
    BeliefTracker (e.g., the harness in Task 9 holds a reference for its
    own pipeline state), on_team_preview must CLEAR it in place rather
    than REPLACING it. Otherwise the caller's reference is silently
    detached from the adapter's belief state.
    """
    priors = StubPriors({"garchomp": _modal("garchomp")})
    external_tracker = BeliefTracker()
    external_tracker.on_reveal_move("Garchomp", "Earthquake")  # pre-seeded
    sa = SpectatorAdapter(
        OWN_PASTE, "gen9monotype", "Ground", priors,
        belief_tracker=external_tracker,
    )
    assert sa._belief is external_tracker

    # Preview clears the tracker in place — but identity must be preserved.
    sa.on_team_preview(["Garchomp"])
    assert sa._belief is external_tracker, (
        "on_team_preview must clear in place, not replace the instance"
    )

    # And the clear actually happened.
    assert "earthquake" not in external_tracker.get("Garchomp").revealed_moves
    # Subsequent reveals through the adapter reach the external tracker.
    sa.on_reveal("Garchomp", revealed_move="Stone Edge")
    assert "stoneedge" in external_tracker.get("Garchomp").revealed_moves


def test_to_engine_json_passes_belief_to_get_set():
    """to_engine_json must plumb the per-species belief into priors.get_set
    so the chaos-set candidate filter (Plan H Task 2) is consulted."""
    priors = StubPriors({"garchomp": _modal("garchomp")})
    sa = SpectatorAdapter(OWN_PASTE, "gen9monotype", "Ground", priors)
    sa.on_team_preview(["Garchomp"])
    sa.on_reveal("Garchomp", revealed_move="Earthquake")

    fake_battle = _make_fake_battle()
    sa.to_engine_json(fake_battle)

    # StubPriors captured the last belief argument; it should be the
    # OpponentBelief that holds the revealed move.
    assert priors.last_belief is not None
    assert priors.last_belief.species == "garchomp"
    assert "earthquake" in priors.last_belief.revealed_moves


def test_to_engine_json_belief_aware_modal_matches_priors_direct():
    """to_engine_json should produce a payload that reflects the belief-aware
    modal — i.e., the same modal that priors.get_set(belief=...) returns.

    This uses the real PriorsSource (chaos JSON fixture from PIMC tests) so
    we exercise the actual filter logic (not just the StubPriors capture)."""
    from showdown_copilot.priors import PriorsSource as RealPriorsSource

    # Mirror the fake_priors_pimc fixture inline for this test
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        from pathlib import Path
        species_data = {
            "Corviknight": {
                "Moves": {"roost": 60, "bodypress": 40, "irondefense": 30, "uturn": 25, "defog": 20, "bravebird": 15},
                "Items": {"leftovers": 60, "rockyhelmet": 30, "heavydutyboots": 10},
                "Abilities": {"pressure": 50, "mirrorarmor": 40, "unnerve": 10},
                "Spreads": {"Impish:248/0/252/0/8/0": 70, "Careful:248/0/0/0/252/8": 30},
                "Tera Types": {"Dragon": 50, "Fairy": 30, "Steel": 20},
            },
        }
        fake = Path(td) / "gen9ou-1500.json"
        fake.write_text(json.dumps({"data": species_data}))
        priors = RealPriorsSource(cache_dir=Path(td), rating=1500, month="2026-04")

        sa = SpectatorAdapter(
            own_paste="""\
Iron Hands @ Choice Band
Ability: Quark Drive
Tera Type: Fighting
EVs: 252 HP / 252 Atk / 4 Def
Adamant Nature
- Drain Punch
- Wild Charge
- Heavy Slam
- Earthquake
""",
            format="gen9ou", team_type=None, priors=priors,
        )
        sa.on_team_preview(["Corviknight"])
        # Reveal a non-modal move; the belief-aware modal MUST include it.
        sa.on_reveal("Corviknight", revealed_move="bravebird")

        fake_battle = _make_fake_battle()
        out = sa.to_engine_json(fake_battle)

        # The independent reference: get_set with the same belief should
        # produce a modal whose moves include 'bravebird'.
        ref = priors.get_set(
            species="Corviknight", format="gen9ou", team_type=None,
            belief=sa._belief.get("Corviknight"),
        )
        assert "bravebird" in ref.moves, "sanity check: ref modal includes revealed move"
        # And the actual adapter output must contain it too (json contains it
        # because the inner BattleAdapter serializes the opp moves into sideTwo).
        assert "bravebird" in json.dumps(out).lower()


def test_revealed_dict_attribute_removed():
    """The Plan G' Task 4 _revealed dict has been replaced with _belief."""
    priors = StubPriors({"garchomp": _modal("garchomp")})
    sa = SpectatorAdapter(OWN_PASTE, "gen9monotype", "Ground", priors)
    assert not hasattr(sa, "_revealed"), (
        "_revealed dict should be gone — replaced by _belief BeliefTracker"
    )
    assert hasattr(sa, "_belief")


# ---------- Plan H Task 9 fix: to_engine_format for non-PIMC harness path ----------


def test_to_engine_format_returns_single_battlerequest_shape():
    """to_engine_format (used by MCTSPlayer non-PIMC code path) must return
    a single BattleRequest dict — NOT the {"hypotheses": [...]} envelope.
    """
    priors = StubPriors({"garchomp": _modal("garchomp")})
    sa = SpectatorAdapter(OWN_PASTE, "gen9monotype", "Ground", priors)
    sa.on_team_preview(["Garchomp"])

    fake_battle = _make_fake_battle()
    out = sa.to_engine_format(fake_battle)

    assert "hypotheses" not in out, (
        "to_engine_format must always return a single BattleRequest, "
        "never the PIMC fan-out envelope"
    )
    # BattleAdapter.to_engine_format contract — same top-level keys.
    assert "sideOne" in out
    assert "sideTwo" in out
    assert "weather" in out
    assert "terrain" in out
    assert "trickRoom" in out
    assert "timeLimit" in out


def test_to_engine_format_passes_belief_to_get_set():
    """to_engine_format must plumb belief through to priors.get_set the
    same way to_engine_json does."""
    priors = StubPriors({"garchomp": _modal("garchomp")})
    sa = SpectatorAdapter(OWN_PASTE, "gen9monotype", "Ground", priors)
    sa.on_team_preview(["Garchomp"])
    sa.on_reveal("Garchomp", revealed_move="Earthquake")

    fake_battle = _make_fake_battle()
    sa.to_engine_format(fake_battle)

    assert priors.last_belief is not None
    assert priors.last_belief.species == "garchomp"
    assert "earthquake" in priors.last_belief.revealed_moves


# ---------- Plan H Task 11 fix: known_opp_specs (real-team injection) ----------


def _real_spec(species, **overrides):
    """Build a PokemonSpec with realistic-shape values for tests."""
    base = dict(
        species=species,
        item="leftovers",
        ability="sturdy",
        nature="Adamant",
        level=100,
        evs={"hp": 252, "atk": 252, "def": 0, "spa": 0, "spd": 4, "spe": 0},
        ivs={k: 31 for k in ("hp", "atk", "def", "spa", "spd", "spe")},
        moves=["bodypress", "irondefense", "rapidspin", "spikes"],
        stats={"hp": 354, "atk": 200, "def": 300, "spa": 90, "spd": 130, "spe": 80},
        types=["Bug", "Steel"],
        weight_kg=125.8,
        tera_type="Steel",
    )
    base.update(overrides)
    return PokemonSpec(**base)


def test_known_opp_specs_used_when_provided():
    """When known_opp_specs is passed, _opp_specs is populated from those
    specs after on_team_preview — no chaos lookup."""
    priors = StubPriors({})  # empty — must NOT be consulted
    forretress = _real_spec("forretress")
    lopunny = _real_spec(
        "lopunnymega",
        item="lopunnite",
        types=["Normal", "Fighting"],
        weight_kg=33.0,
    )
    sa = SpectatorAdapter(
        own_paste=OWN_PASTE, format="gen9nationaldexag",
        team_type=None, priors=priors,
        known_opp_specs=[forretress, lopunny],
    )
    sa.on_team_preview(["Forretress", "Lopunny-Mega"])
    assert "forretress" in sa._opp_specs
    assert "lopunnymega" in sa._opp_specs
    assert sa._opp_specs["forretress"] is forretress
    assert sa._opp_specs["lopunnymega"] is lopunny


def test_known_opp_specs_preserves_real_stats():
    """Known spec has HP=354, weight=125.8 — these must survive into
    to_engine_format output (not be replaced with HP=100, weight=0)."""
    from showdown_copilot.adapter_ext import SpectatorAdapter

    priors = StubPriors({})
    forretress = _real_spec("forretress")  # HP=354, weight=125.8
    sa = SpectatorAdapter(
        own_paste=OWN_PASTE, format="gen9nationaldexag",
        team_type=None, priors=priors,
        known_opp_specs=[forretress],
    )
    sa.on_team_preview(["Forretress"])

    fake_battle = _make_fake_battle()
    out = sa.to_engine_format(fake_battle)

    # The opp side is sideTwo. Find the forretress entry.
    forr_pkm = None
    for p in out["sideTwo"]["pokemon"]:
        if p["species"] == "forretress":
            forr_pkm = p
            break
    assert forr_pkm is not None, "forretress missing from sideTwo"
    assert forr_pkm["maxhp"] == 354, f"expected HP=354, got {forr_pkm['maxhp']}"
    assert forr_pkm["weightKg"] == 125.8, f"expected weight=125.8, got {forr_pkm['weightKg']}"
    assert "Bug" in forr_pkm["types"], f"expected Bug type, got {forr_pkm['types']}"
    assert "Steel" in forr_pkm["types"], f"expected Steel type, got {forr_pkm['types']}"


def test_known_opp_specs_belief_overlays_revealed_item():
    """Known spec has item='leftovers', reveal 'Rocky Helmet' via on_reveal —
    engine output must contain 'rockyhelmet' not 'leftovers'."""
    priors = StubPriors({})
    forretress = _real_spec("forretress", item="leftovers")
    sa = SpectatorAdapter(
        own_paste=OWN_PASTE, format="gen9nationaldexag",
        team_type=None, priors=priors,
        known_opp_specs=[forretress],
    )
    sa.on_team_preview(["Forretress"])
    sa.on_reveal("forretress", revealed_item="Rocky Helmet")

    fake_battle = _make_fake_battle()
    out = sa.to_engine_format(fake_battle)

    forr_pkm = None
    for p in out["sideTwo"]["pokemon"]:
        if p["species"] == "forretress":
            forr_pkm = p
            break
    assert forr_pkm is not None
    assert forr_pkm["item"] == "rockyhelmet", (
        f"expected item=rockyhelmet (overlaid from belief), got {forr_pkm['item']}"
    )


def test_known_opp_specs_skips_priors_lookup_in_team_preview():
    """When known_opp_specs is set, on_team_preview must NOT call
    priors.get_set — that's the artifact we're fixing."""
    class TrackingPriors(StubPriors):
        def __init__(self):
            super().__init__({})
            self.get_set_calls = 0
        def get_set(self, species, format, team_type=None, belief=None):
            self.get_set_calls += 1
            # Don't have a real modal to return — would raise KeyError.
            raise AssertionError("get_set should not be called in known-specs path")

    priors = TrackingPriors()
    forretress = _real_spec("forretress")
    sa = SpectatorAdapter(
        own_paste=OWN_PASTE, format="gen9nationaldexag",
        team_type=None, priors=priors,
        known_opp_specs=[forretress],
    )
    # Must complete without raising.
    sa.on_team_preview(["Forretress"])
    assert priors.get_set_calls == 0


def test_known_opp_specs_none_falls_back_to_chaos_path():
    """known_opp_specs=None (default) — existing behavior unchanged."""
    priors = StubPriors({"garchomp": _modal("garchomp")})
    sa = SpectatorAdapter(
        own_paste=OWN_PASTE, format="gen9monotype",
        team_type="Ground", priors=priors,
        # known_opp_specs not passed
    )
    sa.on_team_preview(["Garchomp"])
    # Chaos path WAS taken — _opp_specs still populated.
    assert "garchomp" in sa._opp_specs
