import json
import random
from collections import Counter
from pathlib import Path

import pytest

from showdown_copilot.priors import (
    PriorsSource,
    _weighted_pick,
    _weighted_pick_n_distinct,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def natdex_chaos_file(tmp_path):
    src = (FIXTURE_DIR / "mini_chaos_natdex.json").read_text()
    dst = tmp_path / "gen9nationaldex-1630.json"
    dst.write_text(src)
    return dst


def test_priors_source_reads_cached_chaos_file(natdex_chaos_file, tmp_path):
    src = PriorsSource(cache_dir=tmp_path)
    ms = src.get_set(species="Dragapult", format="gen9nationaldex")
    assert ms.species == "dragapult"
    # top-4 moves, ordered by frequency
    assert ms.moves[:4] == ["dracometeor", "shadowball", "uturn", "flamethrower"]
    assert ms.item == "choicespecs"
    assert ms.ability == "infiltrator"
    assert ms.nature == "Timid"
    # Spreads format: "Nature:hp/atk/def/spa/spd/spe"
    assert ms.evs == {"hp": 0, "atk": 0, "def": 4, "spa": 252, "spd": 0, "spe": 252}
    assert ms.tera_type == "Ghost"


def test_priors_source_unknown_species_returns_neutral_default(natdex_chaos_file, tmp_path):
    src = PriorsSource(cache_dir=tmp_path)
    ms = src.get_set(species="Mewtwo", format="gen9nationaldex")
    # neutral fallback when species isn't in chaos data
    assert ms.species == "mewtwo"
    assert ms.nature == "Serious"
    assert ms.moves == []
    assert ms.item == "none"


@pytest.fixture
def monotype_chaos_file(tmp_path):
    src = (FIXTURE_DIR / "mini_chaos_monotype.json").read_text()
    dst = tmp_path / "gen9monotype-1630.json"
    dst.write_text(src)
    return dst


def test_monotype_uses_per_type_breakdown(monotype_chaos_file, tmp_path):
    src = PriorsSource(cache_dir=tmp_path)
    dark = src.get_set("Kingambit", format="gen9monotype", team_type="Dark")
    steel = src.get_set("Kingambit", format="gen9monotype", team_type="Steel")
    # Dark team: Black Glasses is dominant
    assert dark.item == "blackglasses"
    # Steel team: Rocky Helmet is dominant
    assert steel.item == "rockyhelmet"
    # Different move distributions
    assert "kowtowcleave" in dark.moves
    assert "knockoff" in steel.moves


def test_monotype_falls_back_to_plain_species_when_no_type_match(tmp_path):
    # fixture with only "Dragapult" (no type variants)
    (tmp_path / "gen9monotype-1630.json").write_text(
        (FIXTURE_DIR / "mini_chaos_natdex.json").read_text()
    )
    src = PriorsSource(cache_dir=tmp_path)
    # Asking for "Dragapult (Ghost)" on a Ghost team — variant doesn't exist
    ms = src.get_set("Dragapult", format="gen9monotype", team_type="Ghost")
    # Falls back to plain "Dragapult" entry
    assert ms.item == "choicespecs"


# ---------------------------------------------------------------------------
# Sampling helpers + PriorsSource.sample_set / sample_k_sets (Plan G' Task 3)
# ---------------------------------------------------------------------------


def test_weighted_pick_empty_returns_none():
    rng = random.Random(42)
    assert _weighted_pick({}, rng) is None


def test_weighted_pick_zero_total_returns_none():
    rng = random.Random(42)
    assert _weighted_pick({"a": 0.0, "b": 0.0}, rng) is None


def test_weighted_pick_dominant_almost_always_wins():
    rng = random.Random(42)
    d = {"common": 99.0, "rare": 1.0}
    counts = Counter(_weighted_pick(d, rng) for _ in range(1000))
    # ~990 common, ~10 rare. Allow ±3σ slack.
    assert counts["common"] > 950
    assert counts["rare"] < 50


def test_weighted_pick_n_distinct_respects_n():
    rng = random.Random(42)
    d = {"a": 1.0, "b": 1.0, "c": 1.0, "d": 1.0}
    picked = _weighted_pick_n_distinct(d, 3, rng)
    assert len(picked) == 3
    assert len(set(picked)) == 3  # distinct


def test_weighted_pick_n_distinct_caps_at_pool_size():
    rng = random.Random(42)
    d = {"a": 1.0, "b": 1.0}
    picked = _weighted_pick_n_distinct(d, 5, rng)
    assert len(picked) == 2


def test_sample_set_falls_back_to_neutral_default_on_unknown_species(tmp_path):
    """If chaos JSON doesn't have an entry for the species, return a neutral default."""
    fake = tmp_path / "gen9ou-1500.json"
    fake.write_text('{"data":{}}')
    src = PriorsSource(cache_dir=tmp_path, rating=1500, month="2026-04")
    rng = random.Random(0)
    s = src.sample_set("UnknownMon", format="gen9ou", rng=rng)
    assert s.moves == []
    assert s.item == "none"
    assert s.ability == "none"


def test_sample_k_sets_diversity_typical_species(tmp_path):
    """K=4 samples for a species with diverse chaos data should produce >= 2 unique tuples."""
    data = {
        "data": {
            "Garchomp": {
                "Moves": {"earthquake": 50, "stealthrock": 40, "spikes": 30, "dragontail": 20, "swordsdance": 15, "scaleshot": 10},
                "Items": {"rockyhelmet": 50, "lifeorb": 40, "leftovers": 20},
                "Abilities": {"roughskin": 90, "sandveil": 10},
                "Spreads": {"Jolly:0/252/0/0/4/252": 80, "Adamant:0/252/0/0/4/252": 20},
                "Tera Types": {"Steel": 50, "Fire": 30, "Ground": 20},
            }
        }
    }
    fake = tmp_path / "gen9ou-1500.json"
    fake.write_text(json.dumps(data))
    src = PriorsSource(cache_dir=tmp_path, rating=1500, month="2026-04")
    rng = random.Random(42)
    sets = src.sample_k_sets("Garchomp", k=4, format="gen9ou", rng=rng)
    tuples = {(s.item, s.ability, s.nature, tuple(sorted(s.moves))) for s in sets}
    assert len(tuples) >= 2, f"K=4 produced only {len(tuples)} unique tuples; sampler too narrow"
