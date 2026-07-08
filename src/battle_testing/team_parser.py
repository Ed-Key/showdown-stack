from dataclasses import dataclass, field
from poke_env.data import GenData


STAT_NAMES = ["hp", "atk", "def", "spa", "spd", "spe"]

NATURE_MODIFIERS: dict[str, dict[str, float]] = {
    "Adamant": {"atk": 1.1, "spa": 0.9},
    "Bold": {"def": 1.1, "atk": 0.9},
    "Brave": {"atk": 1.1, "spe": 0.9},
    "Calm": {"spd": 1.1, "atk": 0.9},
    "Careful": {"spd": 1.1, "spa": 0.9},
    "Gentle": {"spd": 1.1, "def": 0.9},
    "Hasty": {"spe": 1.1, "def": 0.9},
    "Impish": {"def": 1.1, "spa": 0.9},
    "Jolly": {"spe": 1.1, "spa": 0.9},
    "Lax": {"def": 1.1, "spd": 0.9},
    "Lonely": {"atk": 1.1, "def": 0.9},
    "Mild": {"spa": 1.1, "def": 0.9},
    "Modest": {"spa": 1.1, "atk": 0.9},
    "Naive": {"spe": 1.1, "spd": 0.9},
    "Naughty": {"atk": 1.1, "spd": 0.9},
    "Quiet": {"spa": 1.1, "spe": 0.9},
    "Rash": {"spa": 1.1, "spd": 0.9},
    "Relaxed": {"def": 1.1, "spe": 0.9},
    "Sassy": {"spd": 1.1, "spe": 0.9},
    "Timid": {"spe": 1.1, "atk": 0.9},
    "Hardy": {},
    "Docile": {},
    "Serious": {},
    "Bashful": {},
    "Quirky": {},
}


@dataclass
class PokemonSpec:
    species: str
    item: str
    ability: str
    nature: str
    level: int
    evs: dict[str, int]
    ivs: dict[str, int]
    moves: list[str]
    stats: dict[str, int] = field(default_factory=dict)
    types: list[str] = field(default_factory=list)
    weight_kg: float = 0.0
    tera_type: str | None = None


def _normalize_id(name: str) -> str:
    """Convert display name to ID format: 'Life Orb' -> 'lifeorb', 'Tapu Lele' -> 'tapulele'"""
    return "".join(c.lower() for c in name if c.isalnum())


def _compute_stat(
    base: int, ev: int, iv: int, level: int, nature_mod: float, is_hp: bool
) -> int:
    if is_hp:
        if base == 1:  # Shedinja
            return 1
        return ((2 * base + iv + ev // 4) * level // 100) + level + 10
    return int(((2 * base + iv + ev // 4) * level // 100 + 5) * nature_mod)


def _compute_all_stats(
    base_stats: dict, evs: dict, ivs: dict, level: int, nature: str
) -> dict[str, int]:
    mods = NATURE_MODIFIERS.get(nature, {})
    stats = {}
    for stat in STAT_NAMES:
        base = base_stats.get(stat, 80)
        mod = mods.get(stat, 1.0)
        stats[stat] = _compute_stat(
            base, evs[stat], ivs[stat], level, mod, is_hp=(stat == "hp")
        )
    return stats


EV_NAME_MAP = {
    "HP": "hp",
    "Atk": "atk",
    "Def": "def",
    "SpA": "spa",
    "SpD": "spd",
    "Spe": "spe",
}


def parse_team_file(paste: str) -> list[PokemonSpec]:
    """Parse Showdown paste format into PokemonSpec list with computed stats."""
    gen_data = GenData.from_gen(9)
    blocks = paste.strip().split("\n\n")
    team = []

    for block in blocks:
        lines = [line.strip() for line in block.strip().split("\n") if line.strip()]
        if not lines:
            continue

        # Line 1: "Nickname (Species) @ Item" or "Species @ Item"
        first_line = lines[0]
        item = ""
        if " @ " in first_line:
            name_part, item = first_line.split(" @ ", 1)
        else:
            name_part = first_line

        # Handle nickname: "Nickname (Species)" or just "Species"
        if "(" in name_part and ")" in name_part:
            species_display = name_part[name_part.index("(") + 1 : name_part.index(")")]
        else:
            species_display = name_part.strip()

        species_id = _normalize_id(species_display)
        item_id = _normalize_id(item) if item else "none"

        ability = ""
        nature = "Serious"
        level = 100
        evs = {s: 0 for s in STAT_NAMES}
        ivs = {s: 31 for s in STAT_NAMES}
        moves: list[str] = []
        tera_type = None

        for line in lines[1:]:
            if line.startswith("Ability:"):
                ability = _normalize_id(line.split(":", 1)[1].strip())
            elif line.startswith("Level:"):
                level = int(line.split(":", 1)[1].strip())
            elif line.startswith("EVs:"):
                for part in line.split(":", 1)[1].split("/"):
                    part = part.strip()
                    val, stat_name = part.rsplit(" ", 1)
                    evs[EV_NAME_MAP[stat_name]] = int(val)
            elif line.startswith("IVs:"):
                for part in line.split(":", 1)[1].split("/"):
                    part = part.strip()
                    val, stat_name = part.rsplit(" ", 1)
                    ivs[EV_NAME_MAP[stat_name]] = int(val)
            elif line.endswith("Nature"):
                nature = line.replace("Nature", "").strip()
            elif line.startswith("Tera Type:"):
                tera_type = line.split(":", 1)[1].strip()
            elif line.startswith("- "):
                moves.append(_normalize_id(line[2:]))

        # Look up base stats from poke-env pokedex
        pokedex_entry = gen_data.pokedex.get(species_id, {})
        base_stats_raw = pokedex_entry.get("baseStats", {})
        base_stats = {s: base_stats_raw.get(s, 80) for s in STAT_NAMES}
        types = pokedex_entry.get("types", [])
        weight_kg = pokedex_entry.get("weightkg", 0.0)

        computed_stats = _compute_all_stats(base_stats, evs, ivs, level, nature)

        team.append(
            PokemonSpec(
                species=species_id,
                item=item_id,
                ability=ability,
                nature=nature,
                level=level,
                evs=evs,
                ivs=ivs,
                moves=moves,
                stats=computed_stats,
                types=types,
                weight_kg=weight_kg,
                tera_type=tera_type,
            )
        )

    return team
