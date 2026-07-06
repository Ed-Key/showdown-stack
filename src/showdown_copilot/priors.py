"""Opponent set priors from Smogon chaos JSON."""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from showdown_copilot.models import Distributions, ModalSet
from showdown_copilot.stats import _NATURE_TO_SPE_MULT, compute_speed_stat

if TYPE_CHECKING:
    from showdown_copilot.belief import OpponentBelief


def _get_base_speeds() -> dict[str, int]:
    """Lazy accessor for belief._BASE_SPEEDS to avoid a hard import cycle.

    belief.py imports from stats and _ability_pools but NOT priors at
    runtime. We can't import _BASE_SPEEDS at module load because tests may
    set up sys.modules ordering; the lazy accessor sidesteps the issue.
    """
    from showdown_copilot.belief import _BASE_SPEEDS

    return _BASE_SPEEDS

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path.home() / ".showdown-copilot" / "cache"
SMOGON_STATS_BASE = "https://www.smogon.com/stats"
DEFAULT_RATING = 1630


def _normalize(name: str) -> str:
    return "".join(c.lower() for c in name if c.isalnum())


def _parse_spread(spread_key: str) -> tuple[str, dict[str, int]]:
    """Parse Smogon spread string 'Nature:hp/atk/def/spa/spd/spe' → (nature, evs dict)."""
    nature, evs_raw = spread_key.split(":", 1)
    parts = [int(x) for x in evs_raw.split("/")]
    if len(parts) != 6:
        raise ValueError(f"bad spread: {spread_key}")
    return nature, {
        "hp": parts[0], "atk": parts[1], "def": parts[2],
        "spa": parts[3], "spd": parts[4], "spe": parts[5],
    }


def _top_key(d: dict[str, float]) -> str | None:
    if not d:
        return None
    return max(d.items(), key=lambda kv: kv[1])[0]


def _normalize_dist(d: dict[str, float]) -> dict[str, float]:
    """Normalize a name → weight dict so values sum to 1.0. Empty dict
    returns empty dict. Non-positive total returns empty dict."""
    total = sum(v for v in d.values() if v > 0)
    if total <= 0:
        return {}
    return {k: v / total for k, v in d.items() if v > 0}


def _top_n_keys(d: dict[str, float], n: int) -> list[str]:
    return [k for k, _ in sorted(d.items(), key=lambda kv: -kv[1])[:n]]


def _weighted_pick(d: dict[str, float], rng: random.Random) -> str | None:
    """Weighted-random pick from a {key: weight} mapping. Returns None if empty."""
    if not d:
        return None
    keys = list(d.keys())
    weights = [d[k] for k in keys]
    total = sum(weights)
    if total <= 0:
        return None
    return rng.choices(keys, weights=weights, k=1)[0]


def _spread_consistent_with_speed(
    spread_key: str, base_speed: int, belief: "OpponentBelief"
) -> bool:
    """Return True iff the spread's computed Speed fits belief.speed_range,
    accounting for the Choice Scarf 1.5× bracket.

    Plan H Phase 2 spread filter. Called by _modal_set_from_consistent_candidates
    when belief.speed_range is set. Considers BOTH the unscarfed and scarfed
    brackets unless one is excluded by impossible_items / item_inferred_choicescarf.
    """
    nature, evs = _parse_spread(spread_key)
    nat_mult = _NATURE_TO_SPE_MULT.get(nature, 1.0)
    raw = compute_speed_stat(base_speed, evs.get("spe", 0), 31, nat_mult, 100)

    if belief.speed_range is None:
        return True
    lo, hi = belief.speed_range

    if belief.item_inferred_choicescarf:
        # Forced scarf bracket only.
        return lo <= int(raw * 1.5) <= hi

    # Non-scarf bracket (raw speed).
    if lo <= raw <= hi:
        return True
    # Scarf bracket if scarf still allowed by other rules.
    if "choicescarf" not in belief.impossible_items:
        if lo <= int(raw * 1.5) <= hi:
            return True
    return False


def _weighted_pick_n_distinct(d: dict[str, float], n: int, rng: random.Random) -> list[str]:
    """Pick up to n distinct keys, weighted by their values, without replacement."""
    if not d or n <= 0:
        return []
    pool = dict(d)
    out: list[str] = []
    for _ in range(n):
        if not pool:
            break
        keys = list(pool.keys())
        weights = [pool[k] for k in keys]
        total = sum(weights)
        if total <= 0:
            break
        chosen = rng.choices(keys, weights=weights, k=1)[0]
        out.append(chosen)
        del pool[chosen]
    return out


class PriorsSource:
    """Loads Smogon chaos JSON and produces ModalSets for opponent Pokémon."""

    def __init__(
        self,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        rating: int = DEFAULT_RATING,
        month: str | None = None,
    ):
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._rating = rating
        self._month = month or self._latest_month_str()
        self._loaded: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _latest_month_str() -> str:
        now = datetime.utcnow()
        # Smogon publishes around the 1st-5th; previous month is safest until mid-month
        if now.day < 10:
            year, month = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
        else:
            year, month = now.year, now.month
        return f"{year}-{month:02d}"

    def _chaos_path(self, fmt: str) -> Path:
        return self._cache_dir / f"{fmt}-{self._rating}.json"

    def _ensure_loaded(self, fmt: str) -> dict[str, Any]:
        if fmt in self._loaded:
            return self._loaded[fmt]
        path = self._chaos_path(fmt)
        if not path.exists():
            url = f"{SMOGON_STATS_BASE}/{self._month}/chaos/{fmt}-{self._rating}.json"
            logger.info("fetching chaos JSON %s", url)
            r = httpx.get(url, timeout=30.0)
            r.raise_for_status()
            path.write_text(r.text)
        data = json.loads(path.read_text())
        self._loaded[fmt] = data
        return data

    def _neutral_default(self, species: str) -> ModalSet:
        norm = _normalize(species)
        return ModalSet(
            species=norm,
            level=100,
            types=[],
            moves=[],
            item="none",
            ability="none",
            nature="Serious",
            evs={k: 0 for k in ("hp", "atk", "def", "spa", "spd", "spe")},
            ivs={k: 31 for k in ("hp", "atk", "def", "spa", "spd", "spe")},
            stats={k: 100 for k in ("hp", "atk", "def", "spa", "spd", "spe")},
            tera_type="",
            weight_kg=0.0,
        )

    def _lookup_entry(
        self, species: str, fmt: str, team_type: str | None = None
    ) -> dict | None:
        """Resolve a chaos entry for `species` in `fmt`. Tries typed key
        for monotype formats, then falls back to plain species. Returns
        None if no entry exists.
        """
        chaos = self._ensure_loaded(fmt)
        data = chaos.get("data", {})
        entry = None
        if team_type and fmt.startswith("gen9monotype"):
            entry = data.get(f"{species} ({team_type})")
        if entry is None:
            entry = data.get(species)
        return entry

    def usage_summary(self, species: str, format: str) -> dict[str, Any] | None:
        """Display-ready usage stats for the preview planner's grounding pack.

        Returns None when the species has no chaos entry (or loading fails);
        the caller omits that species rather than blocking plan generation.
        """
        try:
            entry = self._lookup_entry(species, format)
        except Exception:
            return None
        if entry is None:
            return None

        def top(dist: dict[str, float], n: int, floor_pct: int) -> list[dict[str, Any]]:
            normalized = _normalize_dist(dict(dist or {}))
            rows = sorted(normalized.items(), key=lambda kv: -kv[1])[:n]
            return [
                {"name": name, "pct": round(weight * 100)}
                for name, weight in rows
                if round(weight * 100) >= floor_pct
            ]

        items = _normalize_dist(dict(entry.get("Items", {}) or {}))
        scarf_pct = round(sum(
            weight for name, weight in items.items() if _normalize(name) == "choicescarf"
        ) * 100)
        return {
            "topMoves": top(entry.get("Moves", {}), 4, 20),
            "topItems": top(entry.get("Items", {}), 2, 20),
            "topAbilities": top(entry.get("Abilities", {}), 2, 20),
            "topTera": top(entry.get("Tera Types", {}), 2, 20),
            "scarfPct": scarf_pct,
        }

    def get_set(
        self,
        species: str,
        format: str,
        team_type: str | None = None,
        belief: "OpponentBelief | None" = None,
    ) -> ModalSet:
        entry = self._lookup_entry(species, format, team_type)
        if entry is None:
            logger.warning(
                "no chaos entry for %s (type=%s) in %s — returning neutral default",
                species, team_type, format,
            )
            return self._neutral_default(species)

        # Belief-aware path: filter candidate sets first, then modal-pick.
        if belief is not None:
            modal = self._modal_set_from_consistent_candidates(
                species=species, entry=entry, belief=belief,
            )
            if modal is not None:
                return modal
            # Filter empty (e.g., revealed move not in any chaos set) →
            # fall through to unfiltered modal so we always return SOMETHING.
            logger.warning(
                "belief filter empty for %s — falling back to unfiltered modal",
                species,
            )

        return self._modal_set_from_entry(species, entry)

    def get_distributions(
        self,
        species: str,
        fmt: str,
        belief: "OpponentBelief | None" = None,
        team_type: str | None = None,
    ) -> Distributions | None:
        """Return belief-filtered chaos distributions for a species.

        Mirrors the filtering logic of get_set() but returns the post-filter
        dicts (normalized to probabilities) instead of reducing to top-1.
        Used by the /belief endpoint to expose probabilities to the extension.

        Returns None if no chaos entry exists for `species` in `fmt`.
        """
        entry = self._lookup_entry(species, fmt, team_type)
        if entry is None:
            return None

        if belief is not None:
            items = self._filter_items(entry.get("Items", {}), belief)
            abilities = self._filter_abilities(entry.get("Abilities", {}), belief)
            spreads = self._filter_spreads(
                entry.get("Spreads", {}) or {}, species, belief,
            )
        else:
            items = dict(entry.get("Items", {}))
            abilities = dict(entry.get("Abilities", {}))
            spreads = dict(entry.get("Spreads", {}) or {})

        # Moves are not reduced — UI shows the full distribution.
        moves = dict(entry.get("Moves", {}))
        # Tera not belief-filtered per existing convention.
        tera = dict(entry.get("Tera Types", {}))

        return Distributions(
            moves=_normalize_dist(moves),
            items=_normalize_dist(items),
            abilities=_normalize_dist(abilities),
            spreads=_normalize_dist(spreads),
            tera_types=_normalize_dist(tera),
        )

    def _modal_set_from_entry(self, species: str, entry: dict) -> ModalSet:
        """Existing modal-pick logic, factored out so the belief-aware
        path can call it as a fallback when the candidate filter is empty."""
        moves = _top_n_keys(entry.get("Moves", {}), 4)
        item = _top_key(entry.get("Items", {})) or "none"
        ability = _top_key(entry.get("Abilities", {})) or "none"
        spread_key = _top_key(entry.get("Spreads", {}))
        if spread_key:
            nature, evs = _parse_spread(spread_key)
        else:
            nature = "Serious"
            evs = {k: 0 for k in ("hp", "atk", "def", "spa", "spd", "spe")}
        tera = _top_key(entry.get("Tera Types", {})) or ""

        return ModalSet(
            species=_normalize(species),
            level=100,
            types=[],  # filled by adapter using species base data
            moves=moves,
            item=_normalize(item),
            ability=_normalize(ability),
            nature=nature,
            evs=evs,
            ivs={k: 31 for k in ("hp", "atk", "def", "spa", "spd", "spe")},
            stats={k: 100 for k in ("hp", "atk", "def", "spa", "spd", "spe")},
            tera_type=tera,
            weight_kg=0.0,
        )

    def _filter_items(
        self, raw_items: dict[str, float], belief: "OpponentBelief"
    ) -> dict[str, float]:
        """Apply belief constraints to a chaos Items distribution.

        Honors revealed_item, impossible_items, and item_inferred_choicescarf.
        Returns the filtered raw-weight dict (callers normalize if needed).
        """
        out = {
            k: v for k, v in raw_items.items()
            if _normalize(k) not in belief.impossible_items
            and (belief.revealed_item is None or _normalize(k) == belief.revealed_item)
        }
        # Bracket math forced Choice Scarf (e.g. opp moved first against a
        # fully-modified speed too high for any non-scarf bracket). Hard-lock
        # so non-scarf items can't win the modal pick. infer_choicescarf
        # already poisons the Choice trio via impossible_items, but Life Orb /
        # Z-Crystal / Boots would still pass that filter without this lock.
        if belief.item_inferred_choicescarf and belief.revealed_item is None:
            out = {k: v for k, v in out.items() if _normalize(k) == "choicescarf"}
        return out

    def _filter_abilities(
        self, raw_abilities: dict[str, float], belief: "OpponentBelief"
    ) -> dict[str, float]:
        """Apply belief constraints to a chaos Abilities distribution.

        Honors revealed_ability and impossible_abilities. Returns the filtered
        raw-weight dict.
        """
        return {
            k: v for k, v in raw_abilities.items()
            if _normalize(k) not in belief.impossible_abilities
            and (belief.revealed_ability is None or _normalize(k) == belief.revealed_ability)
        }

    def _filter_spreads(
        self, raw_spreads: dict[str, float], species: str, belief: "OpponentBelief"
    ) -> dict[str, float]:
        """Apply belief.speed_range to a chaos Spreads distribution.

        When speed_range is None or base_speed unknown, returns the input
        unchanged. Returns the filtered raw-weight dict.
        """
        if belief.speed_range is None:
            return dict(raw_spreads)
        base_speed = _get_base_speeds().get(_normalize(species))
        if base_speed is None:
            return dict(raw_spreads)
        return {
            k: v for k, v in raw_spreads.items()
            if _spread_consistent_with_speed(k, base_speed, belief)
        }

    def _modal_set_from_consistent_candidates(
        self, species: str, entry: dict, belief: "OpponentBelief"
    ) -> ModalSet | None:
        """Build a ModalSet from chaos entry, filtered by belief constraints.

        Filters:
          - moves must be a superset of belief.revealed_moves
          - item must NOT be in belief.impossible_items
          - if belief.revealed_item is set, the item must equal it
          - ability must NOT be in belief.impossible_abilities
          - if belief.revealed_ability is set, the ability must equal it
          - the (item, ability, moves, spread) combination must pass
            `legality.set_makes_sense` (Pokemon-mechanics + belief
            reconciliation port from foul-play)

        Returns None if no candidates survive the filter (caller should
        fall back to unfiltered modal). Returns a ModalSet built from the
        modal pick over the FILTERED candidate distributions otherwise.
        """
        from showdown_copilot.legality import set_makes_sense

        items_dist = self._filter_items(entry.get("Items", {}), belief)
        if not items_dist:
            return None  # no consistent item

        abilities_dist = self._filter_abilities(entry.get("Abilities", {}), belief)
        if not abilities_dist:
            return None  # no consistent ability

        # Moves: chaos data lists per-move usage, but we need to enforce that
        # the chosen 4-move SET is a superset of revealed_moves. The simplest
        # approach: take chaos top-N (say top-12) candidate moves, then choose
        # 4 such that all revealed_moves are included. Falls back to top-4 if
        # revealed_moves is empty.
        moves_dist = entry.get("Moves", {})
        if not moves_dist:
            return None
        chosen_moves = self._select_modal_moves_with_revealed(
            moves_dist=moves_dist, revealed=belief.revealed_moves,
        )
        if chosen_moves is None:
            return None  # revealed move not in chaos data at all

        # Spreads: filter by belief.speed_range when narrowed (Phase 2).
        # When the filter empties the distribution, return None to trigger
        # fall-through to the unfiltered modal path (consistent with the
        # items / abilities / moves filter behavior above).
        spreads_dist = self._filter_spreads(entry.get("Spreads", {}) or {}, species, belief)
        if belief.speed_range is not None and not spreads_dist:
            return None
        spread_key = _top_key(spreads_dist)
        if spread_key:
            nature, evs = _parse_spread(spread_key)
        else:
            nature = "Serious"
            evs = {k: 0 for k in ("hp", "atk", "def", "spa", "spd", "spe")}

        # Tera: same — Phase 1 doesn't filter tera by belief
        tera = _top_key(entry.get("Tera Types", {})) or ""

        # Legality filter (port of foul-play smogon_set_makes_sense /
        # set_makes_sense). Walk item × ability candidates in popularity
        # order; first combination that passes wins. If NONE pass, fall
        # through to the next-best fallback (preserves the current default
        # behavior — never crash on an unsensible-but-popular combo).
        items_sorted = sorted(items_dist.items(), key=lambda kv: -kv[1])
        abilities_sorted = sorted(abilities_dist.items(), key=lambda kv: -kv[1])
        base_speed = _get_base_speeds().get(_normalize(species))

        chosen_item = None
        chosen_ability = None
        for item_key, _iw in items_sorted:
            for ability_key, _aw in abilities_sorted:
                if set_makes_sense(
                    item=_normalize(item_key),
                    ability=_normalize(ability_key),
                    moves=chosen_moves,
                    nature=nature,
                    evs=evs,
                    belief=belief,
                    species=_normalize(species),
                    base_speed=base_speed,
                ):
                    chosen_item = item_key
                    chosen_ability = ability_key
                    break
            if chosen_item is not None:
                break

        if chosen_item is None:
            # No (item, ability) combination passes the legality filter
            # against the modal moves+spread. Fall back to the unfiltered
            # top-of-distribution pick — current behavior, safer than
            # crashing or returning None.
            logger.debug(
                "legality: no consistent combination for %s; falling back "
                "to top-of-distribution modal", species,
            )
            chosen_item = _top_key(items_dist) or "none"
            chosen_ability = _top_key(abilities_dist) or "none"

        return ModalSet(
            species=_normalize(species),
            level=100,
            types=[],
            moves=chosen_moves,
            item=_normalize(chosen_item),
            ability=_normalize(chosen_ability),
            nature=nature,
            evs=evs,
            ivs={k: 31 for k in ("hp", "atk", "def", "spa", "spd", "spe")},
            stats={k: 100 for k in ("hp", "atk", "def", "spa", "spd", "spe")},
            tera_type=tera,
            weight_kg=0.0,
        )

    def _select_modal_moves_with_revealed(
        self, moves_dist: dict[str, float], revealed: set[str]
    ) -> list[str] | None:
        """Pick 4 distinct moves from `moves_dist` such that the result
        is a superset of `revealed`. Returns None if a revealed move is
        missing from the chaos distribution entirely.

        Hidden Power fuzzy match: Showdown's `|move|` event reports
        "Hidden Power" with no type, so revealed_moves typically contains
        bare `hiddenpower`. Chaos data only has typed variants
        (`hiddenpowerice`, `hiddenpowerfighting`, ...). We treat a
        revealed `hiddenpower<x>` (or bare `hiddenpower`) as satisfied by
        ANY chaos `hiddenpower<y>` entry, and surface the REVEALED type
        in the kept list so downstream damage calc uses the right type.

        Returned move ids are normalized (lowercase alphanumeric), matching
        the convention used by `_top_n_keys` consumers downstream and by
        `OpponentBelief.revealed_moves`.
        """
        norm_to_key: dict[str, tuple[str, float]] = {}
        for key, weight in moves_dist.items():
            norm_to_key[_normalize(key)] = (key, float(weight))

        chaos_has_hp = any(k.startswith("hiddenpower") for k in norm_to_key)

        for rev in revealed:
            if rev in norm_to_key:
                continue
            if rev.startswith("hiddenpower") and chaos_has_hp:
                continue  # fuzzy match: chaos has SOME hiddenpower variant
            return None

        revealed_has_hp = any(r.startswith("hiddenpower") for r in revealed)

        # Iterate revealed in sorted order for log/debug determinism (Python
        # set iteration order is implementation-dependent).
        kept: list[str] = []
        for rev in sorted(revealed):
            kept.append(rev)
            if len(kept) >= 4:
                break

        # Fill remaining slots with the top moves (excluding kept). When the
        # revealed set already contains a hiddenpower variant, suppress all
        # OTHER hiddenpower variants from chaos so we don't double-fill the
        # set with two HP moves of different types.
        remaining = []
        for norm, (_key, w) in norm_to_key.items():
            if norm in kept:
                continue
            if revealed_has_hp and norm.startswith("hiddenpower"):
                continue
            remaining.append((norm, w))
        remaining.sort(key=lambda kw: -kw[1])  # highest weight first
        for norm, _w in remaining:
            if len(kept) >= 4:
                break
            kept.append(norm)

        return kept[:4]

    def sample_set(
        self,
        species: str,
        format: str,
        team_type: str | None = None,
        rng: random.Random | None = None,
        belief: "OpponentBelief | None" = None,
    ) -> ModalSet:
        """Like get_set, but every field is weighted-random-drawn from the chaos
        distribution rather than picked modally. Same return shape.

        Used by PIMC outer loop to generate K diverse opponent-team hypotheses.
        """
        if rng is None:
            rng = random.Random()
        chaos = self._ensure_loaded(format)
        data = chaos.get("data", {})

        entry = None
        if team_type and format.startswith("gen9monotype"):
            entry = data.get(f"{species} ({team_type})")
        if entry is None:
            entry = data.get(species)
        if entry is None:
            logger.warning(
                "no chaos entry for %s (type=%s) in %s — neutral default (sample)",
                species, team_type, format,
            )
            return self._neutral_default(species)

        if belief is not None:
            sampled = self._sample_set_with_belief(
                species=species, entry=entry, belief=belief, rng=rng,
            )
            if sampled is not None:
                return sampled
            logger.warning(
                "belief filter empty for %s sample — falling back to unfiltered",
                species,
            )

        return self._sample_set_unfiltered(species, entry, rng)

    def _sample_set_unfiltered(
        self, species: str, entry: dict, rng: random.Random
    ) -> ModalSet:
        """Existing weighted-random sampling logic, factored out so the
        belief-aware sampling path can call it as a fallback when the
        candidate filter is empty."""
        moves = _weighted_pick_n_distinct(entry.get("Moves", {}), 4, rng)
        item = _weighted_pick(entry.get("Items", {}), rng) or "none"
        ability = _weighted_pick(entry.get("Abilities", {}), rng) or "none"
        spread_key = _weighted_pick(entry.get("Spreads", {}), rng)
        if spread_key:
            nature, evs = _parse_spread(spread_key)
        else:
            nature = "Serious"
            evs = {k: 0 for k in ("hp", "atk", "def", "spa", "spd", "spe")}
        tera = _weighted_pick(entry.get("Tera Types", {}), rng) or ""

        return ModalSet(
            species=_normalize(species),
            level=100,
            types=[],
            moves=moves,
            item=_normalize(item),
            ability=_normalize(ability),
            nature=nature,
            evs=evs,
            ivs={k: 31 for k in ("hp", "atk", "def", "spa", "spd", "spe")},
            stats={k: 100 for k in ("hp", "atk", "def", "spa", "spd", "spe")},
            tera_type=tera,
            weight_kg=0.0,
        )

    def _sample_set_with_belief(
        self,
        species: str,
        entry: dict,
        belief: "OpponentBelief",
        rng: random.Random,
    ) -> ModalSet | None:
        """Belief-filtered weighted-random sampling. Mirror of
        `_modal_set_from_consistent_candidates`, but uses `_weighted_pick`
        instead of `_top_key` over the filtered distributions. Returns None
        if any of the filtered distributions is empty (caller should fall
        back to unfiltered sampling).
        """
        items_dist = {
            k: v for k, v in entry.get("Items", {}).items()
            if _normalize(k) not in belief.impossible_items
            and (belief.revealed_item is None or _normalize(k) == belief.revealed_item)
        }
        if not items_dist:
            return None

        abilities_dist = {
            k: v for k, v in entry.get("Abilities", {}).items()
            if _normalize(k) not in belief.impossible_abilities
            and (belief.revealed_ability is None or _normalize(k) == belief.revealed_ability)
        }
        if not abilities_dist:
            return None

        moves_dist = entry.get("Moves", {})
        if not moves_dist:
            return None
        sampled_moves = self._sample_moves_with_revealed(
            moves_dist=moves_dist, revealed=belief.revealed_moves, rng=rng,
        )
        if sampled_moves is None:
            return None

        spread_key = _weighted_pick(entry.get("Spreads", {}), rng)
        if spread_key:
            nature, evs = _parse_spread(spread_key)
        else:
            nature = "Serious"
            evs = {k: 0 for k in ("hp", "atk", "def", "spa", "spd", "spe")}
        tera = _weighted_pick(entry.get("Tera Types", {}), rng) or ""

        return ModalSet(
            species=_normalize(species),
            level=100,
            types=[],
            moves=sampled_moves,
            item=_normalize(_weighted_pick(items_dist, rng) or "none"),
            ability=_normalize(_weighted_pick(abilities_dist, rng) or "none"),
            nature=nature,
            evs=evs,
            ivs={k: 31 for k in ("hp", "atk", "def", "spa", "spd", "spe")},
            stats={k: 100 for k in ("hp", "atk", "def", "spa", "spd", "spe")},
            tera_type=tera,
            weight_kg=0.0,
        )

    def _sample_moves_with_revealed(
        self,
        moves_dist: dict[str, float],
        revealed: set[str],
        rng: random.Random,
    ) -> list[str] | None:
        """Sample 4 distinct moves weighted by chaos usage, with the
        constraint that all moves in `revealed` MUST be in the result.
        Returns None if any revealed move is missing from `moves_dist`.

        Returned move ids are normalized.
        """
        # Map normalized → (display_key, weight)
        norm_to_key: dict[str, tuple[str, float]] = {}
        for key, weight in moves_dist.items():
            norm_to_key[_normalize(key)] = (key, float(weight))

        # Defensive: ALL revealed moves must exist in the chaos distribution.
        # Verify up front so a bogus 5th move fails loudly rather than being
        # silently dropped.
        if not revealed.issubset(norm_to_key.keys()):
            return None

        # Iterate revealed in sorted order for log/debug determinism.
        kept: list[str] = []
        for rev in sorted(revealed):
            kept.append(rev)
            if len(kept) >= 4:
                break

        # Build a normalized weight pool, exclude already-kept
        remaining_pool = {
            norm: w for norm, (_key, w) in norm_to_key.items()
            if norm not in kept
        }
        # Weighted-random fill of the remaining slots without replacement
        slots_left = 4 - len(kept)
        if slots_left > 0:
            extra = _weighted_pick_n_distinct(remaining_pool, slots_left, rng)
            kept.extend(extra)

        return kept[:4]

    def sample_k_sets(
        self,
        species: str,
        k: int,
        format: str,
        team_type: str | None = None,
        rng: random.Random | None = None,
        belief: "OpponentBelief | None" = None,
    ) -> list[ModalSet]:
        """Return K independent sample_set draws for one species.

        When `belief` is provided, the per-draw filter is applied so every
        sample HONORS revealed moves / item / ability / impossibility sets.
        This is the core PIMC v2 entry point — the proxy fan-out calls this
        once per opp Pokemon to materialize K plausible opponent teams.
        """
        if rng is None:
            rng = random.Random()
        return [
            self.sample_set(species, format, team_type, rng, belief=belief)
            for _ in range(k)
        ]
