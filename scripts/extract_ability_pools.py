"""One-time data extraction: build frozensets of species whose gen-9
ability pool includes Levitate or Magic Guard. Output is a Python
module consumed by belief.py.

Sourcing strategy (avoiding the Task 6 false-data trap — see
docs/superpowers/specs/2026-04-26-plan-h-posterior-tracking-design.md
section 4.6):

1. Candidate species are seeded from chaos cache (species seen on
   ladder) + a hand-curated fallback (niche species missing from cache).
2. Each candidate is then VERIFIED against poke-env's gen-9 pokedex —
   only species whose current gen-9 ability pool actually contains the
   target ability survive. Chaos cache historically conflates ability
   data across generations (e.g., gen-9 Gengar lost Levitate in gen 7;
   gen-9 Drifblim never had it post-gen-5 reshuffle), so the chaos data
   alone is not authoritative.
3. The generated module is consumed by belief.py for R3 (Magic Guard
   exemption from LO) and R4 (Levitate / Magic Guard hazard-immunity
   carve-outs).

Run from showdown-copilot repo root:
    python scripts/extract_ability_pools.py
"""
from __future__ import annotations

import json
from pathlib import Path

CACHE_PATH = Path.home() / ".showdown-copilot" / "cache" / "gen9nationaldexag-1630.json"
OUT_PATH = Path(__file__).parent.parent / "src" / "showdown_copilot" / "_ability_pools.py"

# Hand-curated fallback for species that may be missing from chaos cache
# (e.g., niche / never-seen-in-meta species). These are merged with the
# chaos-derived sets to produce the candidate pool, which is then
# filtered against poke-env's authoritative gen-9 pokedex.
LEVITATE_FALLBACK: set[str] = {
    "rotom", "rotomwash", "rotomheat", "rotomfan", "rotommow", "rotomfrost",
    "weezing", "weezinggalar",
    "gengar", "haunter", "gastly",
    "claydol", "baltoy",
    "flygon", "vibrava", "trapinch",
    "duskull", "dusknoir", "dusclops",
    "cresselia",
    "eelektross", "eelektrik", "tynamo",
    "mismagius", "misdreavus",
    "drifblim", "drifloon",
    "chandelure", "lampent", "litwick",
    "hydreigon", "zweilous", "deino",
    "latios", "latias",
    "uxie", "mesprit", "azelf",
    "carbink",
    "vikavolt", "charjabug", "grubbin",
}

MAGIC_GUARD_FALLBACK: set[str] = {
    "sigilyph",
    "alakazam", "alakazamega",
    "clefable", "clefairy", "cleffa",
    "reuniclus", "duosion", "solosis",
}


def normalize(name: str) -> str:
    return "".join(c.lower() for c in name if c.isalnum())


def extract_from_chaos(target_ability: str) -> set[str]:
    """Read chaos cache, return set of normalized species names whose
    Abilities distribution includes target_ability (case-insensitive,
    space/hyphen-stripped).
    """
    target_norm = normalize(target_ability)
    with CACHE_PATH.open() as f:
        data = json.load(f)["data"]
    out = set()
    for species_name, entry in data.items():
        abilities = entry.get("Abilities", {})
        for ab in abilities:
            if normalize(ab) == target_norm:
                out.add(normalize(species_name))
                break
    return out


def filter_by_pokedex(
    candidates: set[str], target_ability: str
) -> tuple[set[str], set[str]]:
    """Filter `candidates` to species that ACTUALLY have `target_ability`
    in poke-env's gen-9 pokedex. Returns (verified, dropped).

    This is the critical guard against the Task-6 false-positive trap —
    chaos data is gen-agnostic, but ability pools rebalance across gens.
    """
    from poke_env.data import GenData

    pokedex = GenData.from_gen(9).pokedex
    target_norm = normalize(target_ability)

    verified: set[str] = set()
    dropped: set[str] = set()
    for species_id in candidates:
        if species_id not in pokedex:
            dropped.add(species_id)
            continue
        abilities = pokedex[species_id].get("abilities", {})
        normalized = {normalize(name) for name in abilities.values()}
        if target_norm in normalized:
            verified.add(species_id)
        else:
            dropped.add(species_id)
    return verified, dropped


def main() -> None:
    chaos_levitate = extract_from_chaos("Levitate")
    chaos_magicguard = extract_from_chaos("Magic Guard")

    candidate_levitate = chaos_levitate | LEVITATE_FALLBACK
    candidate_magicguard = chaos_magicguard | MAGIC_GUARD_FALLBACK

    final_levitate, dropped_lev = filter_by_pokedex(candidate_levitate, "Levitate")
    final_magicguard, dropped_mg = filter_by_pokedex(candidate_magicguard, "Magic Guard")

    print(f"Chaos-derived Levitate candidates: {len(chaos_levitate)}")
    print(f"Total Levitate candidates (chaos | fallback): {len(candidate_levitate)}")
    print(f"Verified against poke-env gen-9 pokedex: {len(final_levitate)}")
    print(f"  Dropped (no Levitate in gen-9 pool): {sorted(dropped_lev)}")
    print()
    print(f"Chaos-derived Magic Guard candidates: {len(chaos_magicguard)}")
    print(f"Total Magic Guard candidates (chaos | fallback): {len(candidate_magicguard)}")
    print(f"Verified against poke-env gen-9 pokedex: {len(final_magicguard)}")
    print(f"  Dropped (no Magic Guard in gen-9 pool): {sorted(dropped_mg)}")

    OUT_PATH.write_text(
        '"""Generated by scripts/extract_ability_pools.py — do not edit by hand.\n\n'
        "Source: chaos cache (gen9nationaldexag) | hand-curated fallback,\n"
        "filtered against poke-env's gen-9 pokedex for authoritative ability\n"
        "pool data. See scripts/extract_ability_pools.py for the pipeline.\n"
        '"""\n'
        "from __future__ import annotations\n\n"
        f"_LEVITATE_SPECIES: frozenset[str] = frozenset({sorted(final_levitate)!r})\n\n"
        f"_MAGICGUARD_SPECIES: frozenset[str] = frozenset({sorted(final_magicguard)!r})\n"
    )
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()
