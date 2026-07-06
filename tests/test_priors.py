import json
import random
from collections import Counter
from pathlib import Path

import pytest

from showdown_copilot.belief import BeliefTracker, OpponentBelief
from showdown_copilot.models import Distributions
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


# ---------------- Belief-aware tests (Plan H Phase 1 Task 2) ----------------


@pytest.fixture
def fake_priors_with_garchomp(tmp_path):
    """A PriorsSource backed by a tiny in-tmp chaos JSON with one species
    that has multiple plausible items, abilities, and moves.

    Note: chaos keys are intentionally lowercase-alphanumeric here to match
    how Smogon writes move/item/ability ids in the JSON. The belief-filter
    code normalizes both sides before comparing, so display-cased keys
    would also work — see test_get_set_belief_normalizes_display_cased_moves.
    """
    data = {
        "data": {
            "Garchomp": {
                "Moves": {
                    "earthquake": 50, "stealthrock": 40, "spikes": 30,
                    "dragontail": 20, "swordsdance": 15, "scaleshot": 10,
                    "stoneedge": 8,
                },
                "Items": {"rockyhelmet": 50, "lifeorb": 40, "leftovers": 20},
                "Abilities": {"roughskin": 90, "sandveil": 10},
                "Spreads": {"Jolly:0/252/0/0/4/252": 80},
                "Tera Types": {"Steel": 50},
            }
        }
    }
    fake = tmp_path / "gen9ou-1500.json"
    fake.write_text(json.dumps(data))
    return PriorsSource(cache_dir=tmp_path, rating=1500, month="2026-04")


def test_get_set_with_no_belief_matches_existing_modal(fake_priors_with_garchomp):
    """Without belief, get_set should produce the same modal as before."""
    out = fake_priors_with_garchomp.get_set("Garchomp", format="gen9ou")
    # Earthquake is highest usage so it should be in the top-4
    assert "earthquake" in out.moves
    assert out.item == "rockyhelmet"  # top item
    assert out.ability == "roughskin"


def test_get_set_with_revealed_move_filters_to_consistent_set(fake_priors_with_garchomp):
    """If revealed_moves contains 'stoneedge', the returned moveset must
    include stoneedge even though it's not the top-4."""
    belief = OpponentBelief(species="garchomp", revealed_moves={"stoneedge"})
    out = fake_priors_with_garchomp.get_set("Garchomp", format="gen9ou", belief=belief)
    assert "stoneedge" in out.moves


def test_get_set_with_impossible_item_excludes_it(fake_priors_with_garchomp):
    """If impossible_items includes 'rockyhelmet' (the modal), get_set
    should pick the next-best item (lifeorb)."""
    belief = OpponentBelief(species="garchomp", impossible_items={"rockyhelmet"})
    out = fake_priors_with_garchomp.get_set("Garchomp", format="gen9ou", belief=belief)
    assert out.item != "rockyhelmet"
    assert out.item == "lifeorb"  # next highest


def test_get_set_with_impossible_ability_excludes_it(fake_priors_with_garchomp):
    """If impossible_abilities includes 'roughskin', get_set picks sandveil."""
    belief = OpponentBelief(species="garchomp", impossible_abilities={"roughskin"})
    out = fake_priors_with_garchomp.get_set("Garchomp", format="gen9ou", belief=belief)
    assert out.ability == "sandveil"


def test_get_set_with_revealed_item_forces_match(fake_priors_with_garchomp):
    """If revealed_item is 'lifeorb' (not the modal), get_set returns it."""
    belief = OpponentBelief(species="garchomp", revealed_item="lifeorb")
    out = fake_priors_with_garchomp.get_set("Garchomp", format="gen9ou", belief=belief)
    assert out.item == "lifeorb"


def test_get_set_filter_empty_falls_back_to_unfiltered_modal(fake_priors_with_garchomp):
    """If revealed_moves contains an unknown move, filter empty → fall back."""
    belief = OpponentBelief(species="garchomp", revealed_moves={"jankymovenoone_uses"})
    out = fake_priors_with_garchomp.get_set("Garchomp", format="gen9ou", belief=belief)
    # Falls back: no jankymove in result, but we still got SOMETHING valid
    assert "jankymovenoone_uses" not in out.moves
    assert out.item in {"rockyhelmet", "lifeorb", "leftovers"}
    assert out.ability in {"roughskin", "sandveil"}


def test_get_set_belief_normalizes_display_cased_moves(tmp_path):
    """If chaos keys are display-cased (e.g. 'Stealth Rock'), the belief
    filter MUST normalize before comparing — `revealed_moves` always
    contains normalized ids ('stealthrock')."""
    data = {
        "data": {
            "Landorus-Therian": {
                "Moves": {
                    "Earthquake": 50, "Stealth Rock": 40, "U-turn": 30,
                    "Stone Edge": 20, "Knock Off": 15,
                },
                "Items": {"Rocky Helmet": 50, "Leftovers": 30},
                "Abilities": {"Intimidate": 100},
                "Spreads": {"Impish:252/0/216/0/40/0": 60},
                "Tera Types": {"Steel": 50},
            }
        }
    }
    fake = tmp_path / "gen9ou-1500.json"
    fake.write_text(json.dumps(data))
    src = PriorsSource(cache_dir=tmp_path, rating=1500, month="2026-04")
    # 'stealthrock' is the normalized form of 'Stealth Rock'
    belief = OpponentBelief(
        species="landorustherian", revealed_moves={"stealthrock"}
    )
    out = src.get_set("Landorus-Therian", format="gen9ou", belief=belief)
    # Result moves are normalized; the revealed move must be in there
    assert "stealthrock" in out.moves


def test_sample_set_with_belief_respects_revealed_move(tmp_path):
    """sample_set with belief must include all revealed_moves in output."""
    data = {
        "data": {
            "Garchomp": {
                "Moves": {
                    "earthquake": 50, "stealthrock": 40, "spikes": 30,
                    "dragontail": 20, "swordsdance": 15, "scaleshot": 10,
                    "stoneedge": 8,
                },
                "Items": {"rockyhelmet": 50, "lifeorb": 40, "leftovers": 20},
                "Abilities": {"roughskin": 90, "sandveil": 10},
                "Spreads": {"Jolly:0/252/0/0/4/252": 80},
                "Tera Types": {"Steel": 50},
            }
        }
    }
    fake = tmp_path / "gen9ou-1500.json"
    fake.write_text(json.dumps(data))
    src = PriorsSource(cache_dir=tmp_path, rating=1500, month="2026-04")
    belief = OpponentBelief(species="garchomp", revealed_moves={"stoneedge"})
    rng = random.Random(7)
    # Run a few samples — each must include stoneedge
    for _ in range(5):
        out = src.sample_set("Garchomp", format="gen9ou", rng=rng, belief=belief)
        assert "stoneedge" in out.moves


def test_sample_set_with_belief_falls_back_when_filter_empty(tmp_path):
    """sample_set falls back to unfiltered sampling when belief filter empty."""
    data = {
        "data": {
            "Garchomp": {
                "Moves": {"earthquake": 50, "stealthrock": 40},
                "Items": {"rockyhelmet": 50, "lifeorb": 40},
                "Abilities": {"roughskin": 90},
                "Spreads": {"Jolly:0/252/0/0/4/252": 80},
                "Tera Types": {"Steel": 50},
            }
        }
    }
    fake = tmp_path / "gen9ou-1500.json"
    fake.write_text(json.dumps(data))
    src = PriorsSource(cache_dir=tmp_path, rating=1500, month="2026-04")
    belief = OpponentBelief(species="garchomp", revealed_moves={"jankyunknown"})
    rng = random.Random(0)
    out = src.sample_set("Garchomp", format="gen9ou", rng=rng, belief=belief)
    # Falls back to unfiltered: still produces a valid set
    assert out.item in {"rockyhelmet", "lifeorb"}
    assert out.ability == "roughskin"
    assert "jankyunknown" not in out.moves


def test_get_set_revealed_ability_overrides_R5_impossible_abilities(tmp_path):
    """REGRESSION (Plan H Task 4 review): contract test that proves the
    priors filter prefers `revealed_ability` over `impossible_abilities`.

    The R5 rule (Task 4) eagerly adds Intimidate to `impossible_abilities`
    on every opp switch-in. If the protocol then fires the `-ability:
    Intimidate` event (i.e., Intimidate WAS in fact present), revealed_ability
    is set positively. The priors filter must select chaos sets matching
    the revealed ability — not exclude them based on the now-stale
    impossible_abilities entry.

    This test pins the contract that `BeliefTracker.eagerly_rule_out_*`
    relies on: a positive reveal SUPERSEDES any prior false impossibility.
    Without this, R5 would actively HARM modal selection in cases where
    its inference was disproved.
    """
    data = {
        "data": {
            "Landorus-Therian": {
                "Moves": {"earthquake": 80, "uturn": 70, "stoneedge": 50},
                "Items": {"rockyhelmet": 50, "leftovers": 30},
                # Two abilities — Intimidate is what we'll reveal positively
                # despite R5 putting it in impossible_abilities.
                "Abilities": {"intimidate": 70, "sandforce": 30},
                "Spreads": {"Jolly:0/252/0/0/4/252": 80},
                "Tera Types": {"Steel": 50},
            }
        }
    }
    fake = tmp_path / "gen9ou-1500.json"
    fake.write_text(json.dumps(data))
    src = PriorsSource(cache_dir=tmp_path, rating=1500, month="2026-04")

    # Drive R5 through the real BeliefTracker: switch-in adds intimidate
    # (and 6 other auto-trigger abilities) to impossible_abilities.
    tracker = BeliefTracker()
    tracker.on_switch_in("Landorus-Therian")
    belief_after_r5 = tracker.get("Landorus-Therian")
    assert "intimidate" in belief_after_r5.impossible_abilities

    # Now the protocol fires `-ability: Intimidate` — Intimidate WAS in
    # fact present despite R5's eager rule-out. on_reveal_ability sets
    # revealed_ability AND discards intimidate from impossible_abilities
    # (Plan H Task 8 review fix: positive reveal supersedes the eager
    # rule-out — the priors filter is a conjunction, not an override).
    tracker.on_reveal_ability("Landorus-Therian", "Intimidate")
    belief = tracker.get("Landorus-Therian")
    assert belief.revealed_ability == "intimidate"
    assert "intimidate" not in belief.impossible_abilities, (
        "on_reveal_ability must discard the revealed ability from "
        "impossible_abilities — keeping it would cause priors filter to "
        "exclude every candidate (revealed_ability check + impossible "
        "check are AND-ed)"
    )

    # The priors filter selects chaos sets matching revealed_ability.
    # With intimidate in impossible_abilities AND revealed_ability ==
    # intimidate, the filter would have been empty (silent fallback to
    # unfiltered modal). With the discard fix, the filter correctly
    # picks intimidate.
    out = src.get_set("Landorus-Therian", format="gen9ou", belief=belief)
    assert out.ability == "intimidate", (
        "priors filter must select revealed_ability — without the "
        "discard fix, R5's eager rule-out would harm modal selection "
        "on every Pokemon whose ability gets revealed"
    )


@pytest.fixture
def mini_chaos_natdex_priors(tmp_path):
    """A PriorsSource backed by a tiny gen9nationaldexag chaos JSON
    containing Garchomp with multiple plausible items/abilities/moves.

    Values are already probabilities (decimal weights), matching how
    the real natdex chaos cache hack stores them after synthesis.
    """
    data = {
        "data": {
            "Garchomp": {
                "Moves": {
                    "earthquake": 0.50,
                    "stoneedge": 0.30,
                    "swordsdance": 0.20,
                    "scaleshot": 0.18,
                    "stealthrock": 0.15,
                },
                "Items": {
                    "rockyhelmet": 0.40,
                    "choiceband": 0.25,
                    "lifeorb": 0.20,
                    "leftovers": 0.15,
                },
                "Abilities": {"roughskin": 0.90, "sandveil": 0.10},
                "Spreads": {"Adamant:0/252/0/0/4/252": 0.80},
                "Tera Types": {"Steel": 0.50, "Fire": 0.30, "Ground": 0.20},
            }
        }
    }
    fake = tmp_path / "gen9nationaldexag-1500.json"
    fake.write_text(json.dumps(data))
    return PriorsSource(cache_dir=tmp_path, rating=1500, month="2026-04")


def test_get_distributions_filters_by_belief_and_returns_full_dists(mini_chaos_natdex_priors):
    src = mini_chaos_natdex_priors
    belief = OpponentBelief(species="garchomp")
    belief.revealed_item = "choiceband"
    dists = src.get_distributions("Garchomp", "gen9nationaldexag", belief=belief)
    assert dists is not None
    # revealed_item locks items distribution to {choiceband: 1.0}
    assert dists.items == {"choiceband": 1.0}
    # moves dist still shows all chaos options (none impossible)
    assert "earthquake" in dists.moves
    assert dists.moves["earthquake"] > 0
    # tera not belief-filtered
    assert len(dists.tera_types) > 0


def test_usage_summary_formats_and_filters(monkeypatch):
    from showdown_copilot.priors import PriorsSource

    entry = {
        "Moves": {"toxic": 780, "protect": 710, "earthquake": 400, "spikes": 90, "uturn": 20},
        "Items": {"toxicorb": 940, "choicescarf": 60},
        "Abilities": {"poisonheal": 970, "hypercutter": 30},
        "Tera Types": {"Water": 500, "Normal": 300, "Ghost": 100},
    }
    source = PriorsSource.__new__(PriorsSource)  # skip __init__ (no network/cache)
    monkeypatch.setattr(source, "_lookup_entry", lambda species, fmt, team_type=None: entry)

    summary = source.usage_summary("Gliscor", "gen9nationaldex")

    move_names = [row["name"] for row in summary["topMoves"]]
    assert move_names[:2] == ["toxic", "protect"]
    assert all(row["pct"] >= 20 for row in summary["topMoves"])
    assert "spikes" not in move_names  # below the 20% floor
    assert summary["topItems"][0]["name"] == "toxicorb"
    assert summary["scarfPct"] == 6
    assert summary["topAbilities"][0] == {"name": "poisonheal", "pct": 97}


def test_usage_summary_returns_none_for_unknown_species(monkeypatch):
    from showdown_copilot.priors import PriorsSource

    source = PriorsSource.__new__(PriorsSource)
    monkeypatch.setattr(source, "_lookup_entry", lambda species, fmt, team_type=None: None)
    assert source.usage_summary("Missingno", "gen9nationaldex") is None
