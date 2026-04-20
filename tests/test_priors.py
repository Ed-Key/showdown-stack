import json
from pathlib import Path

import pytest

from showdown_copilot.priors import PriorsSource


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
