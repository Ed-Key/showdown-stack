from unittest.mock import MagicMock

from showdown_copilot.adapter_ext import SpectatorAdapter
from showdown_copilot.models import ModalSet


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
