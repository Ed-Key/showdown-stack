"""Proxy-side grounding enrichment for live preview plans.

Assembles the facts the planner prompt cites: opponent likely sets from
Smogon usage priors and a base-speed ordering. Every function degrades to
an empty result instead of raising — grounding must never block a plan.
"""
from __future__ import annotations

import logging
from typing import Any

from .mechanics_facts import get_hidden_formes, get_pokemon_facts
from .priors import PriorsSource

logger = logging.getLogger(__name__)

SCARF_PLAUSIBLE_MIN_PCT = 15

_priors: PriorsSource | None = None
_priors_failed = False


def _priors_source() -> PriorsSource | None:
    global _priors, _priors_failed
    if _priors is None and not _priors_failed:
        try:
            _priors = PriorsSource()
        except Exception:  # noqa: BLE001 - cache dir/env issues must not break planning
            logger.warning("preview grounding: priors source unavailable", exc_info=True)
            _priors_failed = True
    return _priors


def build_opponent_likely_sets(opponent_team: list[str], fmt: str) -> list[dict[str, Any]]:
    source = _priors_source()
    if source is None:
        return []
    rows: list[dict[str, Any]] = []
    for species in opponent_team:
        if not species:
            continue
        try:
            summary = source.usage_summary(species, fmt)
        except Exception:  # noqa: BLE001 - a fetch/parse failure skips the species only
            logger.warning("preview grounding: usage_summary failed for %s", species, exc_info=True)
            continue
        if not summary:
            continue
        rows.append({"species": species, "basis": "usage-statistics", **summary})
    return rows


def build_possible_formes(opponent_team: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for species in opponent_team:
        if not species:
            continue
        try:
            formes = get_hidden_formes(species)
        except Exception:  # noqa: BLE001 - a dex hiccup skips the species only
            logger.warning("preview grounding: get_hidden_formes failed for %s", species, exc_info=True)
            continue
        if not formes:
            continue
        base = get_pokemon_facts(species)
        rows.append({
            "species": species,
            "baseTypes": base.get("types") or [],
            "baseSpeed": int((base.get("baseStats") or {}).get("spe") or 0),
            "formes": formes,
        })
    return rows


def build_speed_context(
    my_species: list[str],
    opponent_species: list[str],
    likely_sets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for side, names in (("mine", my_species), ("opp", opponent_species)):
        for name in names:
            facts = get_pokemon_facts(name)
            if not facts.get("found"):
                continue
            base_speed = int((facts.get("baseStats") or {}).get("spe") or 0)
            rows.append({"species": str(facts.get("name") or name), "side": side, "baseSpeed": base_speed})
            if side == "opp":
                try:
                    hidden = get_hidden_formes(name)
                except Exception:  # noqa: BLE001 - never block speed context on a forme lookup
                    hidden = []
                for forme in hidden:
                    rows.append({
                        "species": forme["name"],
                        "side": "opp",
                        "baseSpeed": int(forme.get("spe") or 0),
                        "forme": forme.get("formeKind"),
                        "guaranteed": False,
                    })
    if not rows:
        return {}
    rows.sort(key=lambda row: -row["baseSpeed"])
    context: dict[str, Any] = {"baseSpeedOrder": rows}
    scarf = [
        str(item.get("species"))
        for item in (likely_sets or [])
        if int(item.get("scarfPct") or 0) >= SCARF_PLAUSIBLE_MIN_PCT
    ]
    if scarf:
        context["scarfPlausible"] = scarf
    return context
