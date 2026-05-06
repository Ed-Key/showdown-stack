"""Data models for Showdown Copilot."""
from __future__ import annotations

from dataclasses import dataclass, field

from battle_testing.team_parser import PokemonSpec


@dataclass
class ModalSet:
    """Most-likely opponent Pokémon set, derived from Smogon usage stats or
    per-friend priors. Produced by PriorsSource; consumed by SpectatorAdapter."""

    species: str
    level: int
    types: list[str]
    moves: list[str]
    item: str
    ability: str
    nature: str
    evs: dict[str, int]
    ivs: dict[str, int]
    stats: dict[str, int]
    tera_type: str = ""
    weight_kg: float = 0.0

    def to_pokemon_spec(self) -> PokemonSpec:
        return PokemonSpec(
            species=self.species,
            level=self.level,
            types=self.types,
            moves=list(self.moves),
            item=self.item,
            ability=self.ability,
            nature=self.nature,
            evs=dict(self.evs),
            ivs=dict(self.ivs),
            stats=dict(self.stats),
            tera_type=self.tera_type,
            weight_kg=self.weight_kg,
        )


@dataclass
class Distributions:
    """Filtered chaos distributions per category. Each is name → probability."""
    moves: dict[str, float] = field(default_factory=dict)
    items: dict[str, float] = field(default_factory=dict)
    abilities: dict[str, float] = field(default_factory=dict)
    spreads: dict[str, float] = field(default_factory=dict)
    tera_types: dict[str, float] = field(default_factory=dict)
