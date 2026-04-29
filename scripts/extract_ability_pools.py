"""One-time data extraction: build frozensets of species whose gen-9
ability pool includes specific abilities. Output is a Python module
consumed by belief.py.

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
3. The generated module is consumed by belief.py:
   - Phase 1: R3 (Magic Guard exemption from LO) and R4 (Levitate /
     Magic Guard hazard-immunity carve-outs).
   - Phase 2: can_have_speed_modified helper (Swift Swim, Chlorophyll,
     Sand Rush, Slush Rush, Surge Surfer, Quick Feet, Unburden,
     Protosynthesis, Quark Drive).

Run from showdown-copilot repo root:
    python scripts/extract_ability_pools.py
"""
from __future__ import annotations

import json
from pathlib import Path

CACHE_PATH = Path.home() / ".showdown-copilot" / "cache" / "gen9nationaldexag-1630.json"
OUT_PATH = Path(__file__).parent.parent / "src" / "showdown_copilot" / "_ability_pools.py"

# Hand-curated fallbacks per ability — species likely absent from chaos
# cache (niche / never-seen-in-meta). Merged with chaos-derived set,
# then filtered against poke-env's authoritative gen-9 pokedex.
FALLBACKS: dict[str, set[str]] = {
    "Levitate": {
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
    },
    "Magic Guard": {
        "sigilyph",
        "alakazam", "alakazamega",
        "clefable", "clefairy", "cleffa",
        "reuniclus", "duosion", "solosis",
    },
    # Phase 2 additions:
    "Swift Swim": {
        "kingdra", "kabutops", "omastar", "ludicolo", "lumineon",
        "mantine", "seismitoad", "barbaracle", "dracovish", "floatzel",
        "beartic", "huntail", "gorebyss", "armaldo", "qwilfish",
        "qwilfishhisui", "swampert", "swampertmega", "ludicolo",
        "lotad", "lombre", "horsea", "seadra", "feebas", "carvanha",
    },
    "Chlorophyll": {
        "venusaur", "venusaurmega", "bellossom", "tangrowth",
        "whimsicott", "lilligant", "sawsbuck", "leafeon",
        "tropius", "vileplume", "victreebel", "exeggutor",
        "jumpluff", "skiploom", "hoppip",
        "cherrim", "cherubi", "sunflora", "sunkern",
        "shiftry", "rowlet",
    },
    "Sand Rush": {
        "excadrill", "stoutland", "sandaconda",
    },
    "Slush Rush": {
        "beartic", "sandshrewalola", "sandslashalola",
        "vanilluxe", "vanillish", "vanillite",
        "arctovish", "arctozolt", "cetitan",
    },
    "Surge Surfer": {
        "raichualola",
    },
    "Quick Feet": {
        "linoone", "linoonegalar", "obstagoon",
        "ursaring", "granbull", "jolteon",
        "rapidash", "rapidashgalar",
        "shiftry",
    },
    "Unburden": {
        "hawlucha", "hitmonlee", "drifblim", "drifloon",
        "sceptilemega",
        "treecko", "grovyle",  # may or may not have; pokedex filter resolves
    },
    "Protosynthesis": {
        "greattusk", "sandyshocks", "brutebonnet", "fluttermane",
        "slitherwing", "roaringmoon", "walkingwake",
        "ragingbolt", "gougingfire",
        "scream tail", "screamtail",  # alternate spellings
    },
    "Quark Drive": {
        "irontreads", "ironbundle", "ironhands", "ironjugulis",
        "ironmoth", "ironthorns", "ironvaliant", "ironleaves",
        "ironboulder", "ironcrown",
    },
}


def normalize(name: str) -> str:
    return "".join(c.lower() for c in name if c.isalnum())


def extract_from_chaos(target_ability: str) -> set[str]:
    """Read chaos cache, return set of normalized species names whose
    Abilities distribution includes target_ability.
    """
    target_norm = normalize(target_ability)
    if not CACHE_PATH.exists():
        return set()
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

    Critical guard against the Task-6 false-positive trap — chaos data
    is gen-agnostic, ability pools rebalance across gens.
    """
    from poke_env.data import GenData

    pokedex = GenData.from_gen(9).pokedex
    target_norm = normalize(target_ability)

    verified: set[str] = set()
    dropped: set[str] = set()
    for species_id in candidates:
        norm_id = normalize(species_id)
        if norm_id not in pokedex:
            dropped.add(species_id)
            continue
        abilities = pokedex[norm_id].get("abilities", {})
        normalized = {normalize(name) for name in abilities.values()}
        if target_norm in normalized:
            verified.add(norm_id)
        else:
            dropped.add(species_id)
    return verified, dropped


def build_pool(target_ability: str) -> set[str]:
    """One-shot: chaos + fallback → poke-env filter → verified set."""
    chaos = extract_from_chaos(target_ability)
    fallback = FALLBACKS.get(target_ability, set())
    candidates = chaos | fallback
    verified, dropped = filter_by_pokedex(candidates, target_ability)
    print(
        f"{target_ability}: chaos={len(chaos)} fallback={len(fallback)} "
        f"candidates={len(candidates)} verified={len(verified)} "
        f"dropped={len(dropped)}"
    )
    return verified


def main() -> None:
    pools = {
        "LEVITATE": build_pool("Levitate"),
        "MAGICGUARD": build_pool("Magic Guard"),
        "SWIFTSWIM": build_pool("Swift Swim"),
        "CHLOROPHYLL": build_pool("Chlorophyll"),
        "SANDRUSH": build_pool("Sand Rush"),
        "SLUSHRUSH": build_pool("Slush Rush"),
        "SURGESURFER": build_pool("Surge Surfer"),
        "QUICKFEET": build_pool("Quick Feet"),
        "UNBURDEN": build_pool("Unburden"),
        "PROTOSYNTHESIS": build_pool("Protosynthesis"),
        "QUARKDRIVE": build_pool("Quark Drive"),
    }

    lines = [
        '"""Generated by scripts/extract_ability_pools.py — do not edit by hand.',
        "",
        "Source: chaos cache (gen9nationaldexag) | hand-curated fallback,",
        "filtered against poke-env's gen-9 pokedex for authoritative ability",
        "pool data. See scripts/extract_ability_pools.py for the pipeline.",
        '"""',
        "from __future__ import annotations",
        "",
    ]
    for name, species in pools.items():
        lines.append(
            f"_{name}_SPECIES: frozenset[str] = frozenset({sorted(species)!r})"
        )
        lines.append("")

    OUT_PATH.write_text("\n".join(lines))
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()
