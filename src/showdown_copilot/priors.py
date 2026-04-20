"""Opponent set priors from Smogon chaos JSON."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from showdown_copilot.models import ModalSet

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


def _top_n_keys(d: dict[str, float], n: int) -> list[str]:
    return [k for k, _ in sorted(d.items(), key=lambda kv: -kv[1])[:n]]


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

    def get_set(
        self, species: str, format: str, team_type: str | None = None
    ) -> ModalSet:
        chaos = self._ensure_loaded(format)
        data = chaos.get("data", {})
        entry = data.get(species)
        if entry is None:
            logger.warning("no chaos entry for %s in %s — returning neutral default", species, format)
            return self._neutral_default(species)

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
