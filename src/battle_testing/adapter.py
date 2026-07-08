"""BattleAdapter: translates between poke-env Battle objects and poke-engine BattleRequest JSON."""

from __future__ import annotations

import logging

from poke_env.battle import Effect, Status, Weather, SideCondition, Field
from poke_env.player.battle_order import SingleBattleOrder

from battle_testing.team_parser import PokemonSpec

logger = logging.getLogger(__name__)


STATUS_MAP = {
    Status.BRN: "Burn",
    Status.FRZ: "Freeze",
    Status.PAR: "Paralyze",
    Status.PSN: "Poison",
    Status.SLP: "Sleep",
    Status.TOX: "Toxic",
    None: "None",
}

WEATHER_MAP = {
    Weather.SUNNYDAY: "sun",
    Weather.DESOLATELAND: "sun",
    Weather.RAINDANCE: "rain",
    Weather.PRIMORDIALSEA: "rain",
    Weather.SANDSTORM: "sandstorm",
    Weather.HAIL: "hail",
    Weather.SNOWSCAPE: "hail",
    Weather.DELTASTREAM: "none",
}

SIDE_CONDITION_MAP = {
    SideCondition.AURORA_VEIL: "auroraVeil",
    SideCondition.LIGHT_SCREEN: "lightScreen",
    SideCondition.REFLECT: "reflect",
    SideCondition.SAFEGUARD: "safeguard",
    SideCondition.SPIKES: "spikes",
    SideCondition.STEALTH_ROCK: "stealthRock",
    SideCondition.STICKY_WEB: "stickyWeb",
    SideCondition.TAILWIND: "tailwind",
    SideCondition.TOXIC_SPIKES: "toxicSpikes",
    SideCondition.MIST: "mist",
}

TERRAIN_MAP = {
    Field.ELECTRIC_TERRAIN: "electricterrain",
    Field.GRASSY_TERRAIN: "grassyterrain",
    Field.MISTY_TERRAIN: "mistyterrain",
    Field.PSYCHIC_TERRAIN: "psychicterrain",
}

DEFAULT_SIDE_CONDITIONS = {
    "auroraVeil": 0,
    "craftyShield": 0,
    "healingWish": 0,
    "lightScreen": 0,
    "luckyChant": 0,
    "lunarDance": 0,
    "matBlock": 0,
    "mist": 0,
    "protect": 0,
    "quickGuard": 0,
    "reflect": 0,
    "safeguard": 0,
    "spikes": 0,
    "stealthRock": 0,
    "stickyWeb": 0,
    "tailwind": 0,
    "toxicCount": 0,
    "toxicSpikes": 0,
    "wideGuard": 0,
}

# Maps short stat names from poke-env boosts to long names for poke-engine
BOOST_NAME_MAP = {
    "atk": "attack",
    "def": "defense",
    "spa": "specialAttack",
    "spd": "specialDefense",
    "spe": "speed",
}

# Maps poke-env Effect enum values to poke-engine volatile status strings.
# Built dynamically to handle different poke-env versions gracefully.
_VOLATILE_STATUS_CANDIDATES = {
    "CONFUSION": "CONFUSION",
    "SUBSTITUTE": "SUBSTITUTE",
    "TAUNT": "TAUNT",
    "ENCORE": "ENCORE",
    "DISABLE": "DISABLE",
    "LEECH_SEED": "LEECHSEED",
    "YAWN": "YAWN",
    "TORMENT": "TORMENT",
    "PARTIALLY_TRAPPED": "PARTIALLYTRAPPED",
    "MUST_RECHARGE": "MUSTRECHARGE",
    "INGRAIN": "INGRAIN",
    "AQUA_RING": "AQUARING",
    "ATTRACT": "ATTRACT",
    "CHARGE": "CHARGE",
    "CURSE": "CURSE",
    "DESTINY_BOND": "DESTINYBOND",
    "EMBARGO": "EMBARGO",
    "ENDURE": "ENDURE",
    "FLASH_FIRE": "FLASHFIRE",
    "FLINCH": "FLINCH",
    "FOCUS_ENERGY": "FOCUSENERGY",
    "FORESIGHT": "FORESIGHT",
    "GASTRO_ACID": "GASTROACID",
    "HEAL_BLOCK": "HEALBLOCK",
    "HELPING_HAND": "HELPINGHAND",
    "IMPRISON": "IMPRISON",
    "LOCKED_MOVE": "LOCKEDMOVE",
    "MAGNET_RISE": "MAGNETRISE",
    "MINIMIZE": "MINIMIZE",
    "NO_RETREAT": "NORETREAT",
    "OCTOLOCK": "OCTOLOCK",
    "PROTECT": "PROTECT",
    "ROOST": "ROOST",
    "SALT_CURE": "SALTCURE",
    "SLOW_START": "SLOWSTART",
    "SMACK_DOWN": "SMACKDOWN",
    "TAR_SHOT": "TARSHOT",
    "THROAT_CHOP": "THROATCHOP",
}

VOLATILE_STATUS_MAP: dict[Effect, str] = {}
for _name, _engine_name in _VOLATILE_STATUS_CANDIDATES.items():
    if hasattr(Effect, _name):
        VOLATILE_STATUS_MAP[getattr(Effect, _name)] = _engine_name


def _normalize_species(species: str) -> str:
    """Normalize species name for matching: lowercase, strip non-alnum."""
    return "".join(c.lower() for c in species if c.isalnum())


class BattleAdapter:
    """Translates between poke-env's Battle object and poke-engine's BattleRequest JSON format."""

    def __init__(self, own_team: list[PokemonSpec], opponent_team: list[PokemonSpec]):
        # Index team specs by normalized species for lookup
        self._own_specs: dict[str, PokemonSpec] = {
            _normalize_species(s.species): s for s in own_team
        }
        self._opp_specs: dict[str, PokemonSpec] = {
            _normalize_species(s.species): s for s in opponent_team
        }
        # Keep ordered lists for unrevealed Pokemon
        self._own_team = own_team
        self._opp_team = opponent_team

    def to_engine_format(self, battle) -> dict:
        """Convert a poke-env Battle object to poke-engine BattleRequest JSON."""
        side_one = self._build_side(
            team=battle.team,
            active=battle.active_pokemon,
            side_conditions=battle.side_conditions,
            specs=self._own_specs,
            spec_list=self._own_team,
            is_own_side=True,
        )
        side_two = self._build_side(
            team=battle.opponent_team,
            active=battle.opponent_active_pokemon,
            side_conditions=battle.opponent_side_conditions,
            specs=self._opp_specs,
            spec_list=self._opp_team,
            is_own_side=False,
        )

        return {
            "sideOne": side_one,
            "sideTwo": side_two,
            "weather": self._build_weather(battle.weather),
            "terrain": self._build_terrain(battle.fields),
            "trickRoom": Field.TRICK_ROOM in battle.fields,
            "timeLimit": 300,
        }

    def to_battle_order(self, response: dict, battle) -> SingleBattleOrder:
        """Convert poke-engine response to poke-env SingleBattleOrder."""
        best_move = response.get("bestMove", "")

        # Handle explicit switch orders: "SWITCH SPECIES"
        if best_move.upper().startswith("SWITCH "):
            target_species = _normalize_species(best_move[7:])
            for mon in battle.available_switches:
                if _normalize_species(mon.species) == target_species:
                    return SingleBattleOrder(order=mon)
            logger.warning(
                "Could not find switch target '%s', falling back", best_move
            )
            return self._fallback_order(battle)

        # Handle move orders: match by move ID
        move_id = best_move.lower().replace(" ", "")
        for move in battle.available_moves:
            if move.id == move_id:
                return SingleBattleOrder(order=move)

        # Strip form suffix (e.g. "icepunch-mega", "zenheadbutt-megax") — poke-engine
        # labels moves on mega/alt forms with a trailing suffix that poke-env doesn't use.
        if "-" in move_id:
            stripped = move_id.rsplit("-", 1)[0]
            for move in battle.available_moves:
                if move.id == stripped:
                    return SingleBattleOrder(order=move)

        # Form-signature move aliases: engine uses base name, poke-env uses signature.
        # Zamazenta-Crowned: Iron Head → Behemoth Bash
        # Zacian-Crowned: Iron Head → Behemoth Blade
        MOVE_ALIASES = {
            "ironhead": ["behemothbash", "behemothblade"],
        }
        for alias in MOVE_ALIASES.get(move_id, []):
            for move in battle.available_moves:
                if move.id == alias:
                    return SingleBattleOrder(order=move)

        # poke-engine returns switches as just the species name (no "SWITCH " prefix)
        # If no move matched, check if it's a switch target
        normalized = _normalize_species(best_move)
        for mon in battle.available_switches:
            if _normalize_species(mon.species) == normalized:
                return SingleBattleOrder(order=mon)

        logger.warning(
            "Could not find move or switch '%s'. Available moves: %s. Available switches: %s. Falling back.",
            best_move,
            [m.id for m in battle.available_moves],
            [_normalize_species(m.species) for m in battle.available_switches],
        )
        return self._fallback_order(battle)

    def _fallback_order(self, battle) -> SingleBattleOrder:
        """Return first available move or switch as fallback."""
        if battle.available_moves:
            return SingleBattleOrder(order=battle.available_moves[0])
        if battle.available_switches:
            return SingleBattleOrder(order=battle.available_switches[0])
        from poke_env.player.battle_order import DefaultBattleOrder
        return DefaultBattleOrder()

    def _build_side(
        self,
        team: dict,
        active,
        side_conditions: dict,
        specs: dict[str, PokemonSpec],
        spec_list: list[PokemonSpec],
        is_own_side: bool,
    ) -> dict:
        """Build one side of the BattleRequest."""
        pokemon_list = []
        seen_species = set()
        active_index = 0

        # Build Pokemon from revealed team members
        for i, (key, mon) in enumerate(team.items()):
            species_norm = _normalize_species(mon.species)
            seen_species.add(species_norm)
            spec = specs.get(species_norm)

            if mon == active:
                active_index = len(pokemon_list)

            pokemon_list.append(
                self._build_pokemon(mon, spec, is_own_side)
            )

        # Fill unrevealed opponent Pokemon from spec list
        if not is_own_side:
            for spec in spec_list:
                species_norm = _normalize_species(spec.species)
                if species_norm not in seen_species:
                    seen_species.add(species_norm)
                    pokemon_list.append(self._build_pokemon_from_spec(spec))

        # Pad to 6 with empty/fainted placeholders
        while len(pokemon_list) < 6:
            pokemon_list.append(self._empty_pokemon())

        # Build boosts from active Pokemon
        boosts = self._build_boosts(active)

        # Extract volatile statuses from active Pokemon
        volatile_statuses: list[str] = []
        if active and hasattr(active, "effects"):
            for effect, counter in active.effects.items():
                engine_name = VOLATILE_STATUS_MAP.get(effect)
                if engine_name:
                    volatile_statuses.append(engine_name)

        return {
            "pokemon": pokemon_list,
            "activeIndex": active_index,
            "sideConditions": self._build_side_conditions(side_conditions),
            "volatileStatuses": volatile_statuses,
            "boosts": boosts,
            "forceTrapped": False,
        }

    def _build_pokemon(self, mon, spec: PokemonSpec | None, is_own_side: bool) -> dict:
        """Build a Pokemon dict from a poke-env Pokemon and optional spec."""
        species_norm = _normalize_species(mon.species)

        # Determine HP values
        if is_own_side:
            hp = mon.current_hp or 0
            maxhp = mon.max_hp or (spec.stats["hp"] if spec else 1)
        else:
            # Opponent: use hp_fraction * spec HP
            if spec:
                maxhp = spec.stats["hp"]
                hp = int(mon.current_hp_fraction * maxhp)
            else:
                maxhp = 100
                hp = int(mon.current_hp_fraction * 100)

        # Get stats from spec or use defaults
        if spec:
            stats = spec.stats
            evs = spec.evs
            ivs = spec.ivs
            nature = spec.nature
            types = spec.types
            weight_kg = spec.weight_kg
            tera_type = spec.tera_type or ""
        else:
            stats = {"hp": maxhp, "atk": 100, "def": 100, "spa": 100, "spd": 100, "spe": 100}
            evs = {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}
            ivs = {"hp": 31, "atk": 31, "def": 31, "spa": 31, "spd": 31, "spe": 31}
            nature = "Serious"
            types = [str(t).split(".")[-1].capitalize() for t in mon.types if t is not None]
            weight_kg = 0.0
            tera_type = ""

        # Build moves list
        moves = []
        move_ids = spec.moves if spec else []
        if mon.moves:
            for move_id, move_obj in mon.moves.items():
                pp = move_obj.current_pp if hasattr(move_obj, "current_pp") else 8
                moves.append({"id": move_id, "pp": pp})
        elif move_ids:
            for mid in move_ids:
                moves.append({"id": mid, "pp": 8})

        # Pad moves to 4
        while len(moves) < 4:
            moves.append({"id": "none", "pp": 0})

        item = mon.item if mon.item else (spec.item if spec else "none")
        ability = mon.ability if mon.ability else (spec.ability if spec else "none")

        return {
            "species": species_norm,
            "level": mon.level,
            "types": types,
            "hp": hp,
            "maxhp": maxhp,
            "ability": ability,
            "item": item,
            "nature": nature,
            "evs": evs,
            "attack": stats.get("atk", 100),
            "defense": stats.get("def", 100),
            "specialAttack": stats.get("spa", 100),
            "specialDefense": stats.get("spd", 100),
            "speed": stats.get("spe", 100),
            "status": STATUS_MAP.get(mon.status, "None"),
            "restTurns": 0,
            "sleepTurns": 0,
            "weightKg": weight_kg,
            "moves": moves,
            "terastallized": getattr(mon, "terastallized", False) or False,
            "teraType": tera_type,
        }

    def _build_pokemon_from_spec(self, spec: PokemonSpec) -> dict:
        """Build a Pokemon dict from a PokemonSpec alone (unrevealed opponent)."""
        moves = [{"id": mid, "pp": 8} for mid in spec.moves]
        while len(moves) < 4:
            moves.append({"id": "none", "pp": 0})

        return {
            "species": spec.species,
            "level": spec.level,
            "types": spec.types,
            "hp": spec.stats["hp"],
            "maxhp": spec.stats["hp"],
            "ability": spec.ability,
            "item": spec.item,
            "nature": spec.nature,
            "evs": spec.evs,
            "attack": spec.stats.get("atk", 100),
            "defense": spec.stats.get("def", 100),
            "specialAttack": spec.stats.get("spa", 100),
            "specialDefense": spec.stats.get("spd", 100),
            "speed": spec.stats.get("spe", 100),
            "status": "None",
            "restTurns": 0,
            "sleepTurns": 0,
            "weightKg": spec.weight_kg,
            "moves": moves,
            "terastallized": False,
            "teraType": spec.tera_type or "",
        }

    def _empty_pokemon(self) -> dict:
        """Create an empty/fainted placeholder Pokemon."""
        return {
            "species": "none",
            "level": 1,
            "types": [],
            "hp": 0,
            "maxhp": 0,
            "ability": "none",
            "item": "none",
            "nature": "Serious",
            "evs": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
            "attack": 0,
            "defense": 0,
            "specialAttack": 0,
            "specialDefense": 0,
            "speed": 0,
            "status": "None",
            "restTurns": 0,
            "sleepTurns": 0,
            "weightKg": 0.0,
            "moves": [{"id": "none", "pp": 0}] * 4,
            "terastallized": False,
            "teraType": "",
        }

    def _build_weather(self, weather: dict) -> dict:
        """Convert poke-env weather dict to poke-engine weather format."""
        if not weather:
            return {"weatherType": "none", "turnsRemaining": -1}

        for weather_type, turns in weather.items():
            mapped = WEATHER_MAP.get(weather_type, "none")
            return {"weatherType": mapped, "turnsRemaining": turns}

        return {"weatherType": "none", "turnsRemaining": -1}

    def _build_terrain(self, fields: dict) -> dict:
        """Convert poke-env fields dict to poke-engine terrain format."""
        for field_type, turns in fields.items():
            if field_type in TERRAIN_MAP:
                return {"terrainType": TERRAIN_MAP[field_type], "turnsRemaining": turns}

        return {"terrainType": "none", "turnsRemaining": -1}

    def _build_side_conditions(self, conditions: dict) -> dict:
        """Convert poke-env side conditions dict to poke-engine format."""
        result = dict(DEFAULT_SIDE_CONDITIONS)
        for condition, stacks in conditions.items():
            field_name = SIDE_CONDITION_MAP.get(condition)
            if field_name and field_name in result:
                result[field_name] = stacks
        return result

    def _build_boosts(self, active) -> dict:
        """Extract boosts from active Pokemon, mapping short names to long names."""
        boosts = {
            "attack": 0,
            "defense": 0,
            "specialAttack": 0,
            "specialDefense": 0,
            "speed": 0,
        }
        if active and hasattr(active, "boosts"):
            for short, long in BOOST_NAME_MAP.items():
                boosts[long] = active.boosts.get(short, 0)
        return boosts
