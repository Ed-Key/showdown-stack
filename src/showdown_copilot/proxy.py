"""Path B-lite proxy: wraps Plan H belief tracking around the extension's engine call.

Sits between extension (port 7271) and engine (port 7267). For each request:

1. Extract `_planH` metadata (battleId, format, oppRevealedMoves) from BattleRequest.
2. Get / create BeliefTracker for this battle (keyed by battleId, LRU-capped).
3. Feed reveals (moves / item / ability / tera) into the tracker.
4. For each opp Pokemon: compute belief-aware modal via priors.get_set, overlay
   on the request (replace moves; backfill item/ability/teraType when unrevealed).
5. Forward modified request to engine, stream response back unchanged.

Run: `python -m showdown_copilot.proxy` (requires `pip install -e .[proxy]`).
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import random
import subprocess
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from showdown_copilot.belief import BeliefTracker, _BASE_SPEEDS, _normalize
from showdown_copilot.dashboard import router as dashboard_router
from showdown_copilot.explainer import ExplainRequest, SYSTEM_PROMPT, build_explain_prompt
from showdown_copilot.llm import LLMClient, build_default_llm
from showdown_copilot.models import Distributions
from showdown_copilot.preview_plan import PreviewPlanRequest, build_preview_plan
from showdown_copilot.priors import PriorsSource
from showdown_copilot.stats import _NATURE_TO_SPE_MULT, compute_speed_stat

logger = logging.getLogger(__name__)

# --- Config ---
ENGINE_URL = "http://localhost:7270"  # Plan I live testing 2026-04-30; revert to 7267 for baseline
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 7271
DEFAULT_FORMAT = "gen9ou"
MAX_TRACKERS = 10  # LRU cap

# --- Globals (lazy-initialized in `main` to avoid import-time chaos fetches) ---
_priors: PriorsSource | None = None
_trackers: "OrderedDict[str, BeliefTracker]" = OrderedDict()
_engine_client: httpx.AsyncClient | None = None
# Per-format normalized→display map. Built lazily on first lookup; the chaos
# cache stores entries under display form ("Dragapult", "Iron-Hands"), but the
# extension sends `species` already normalized via `norm()` ("dragapult",
# "ironhands"). Without this map, every priors.get_set call would miss and
# fall back to neutral default.
_display_cache: dict[str, dict[str, str]] = {}
# Per-format resolution cache. Maps the format string the extension sends
# (e.g. "gen9nationaldex") to the format we actually use for chaos lookups
# (e.g. "gen9nationaldexag" via alias fallback) — or None when no chaos is
# available and overlay should be skipped. Without this cache, the alias
# resolution path retries the failing original format on EVERY request,
# adding 100ms-3s of Smogon 404 latency per turn.
_format_resolution: dict[str, str | None] = {}
# Phase 2 — track the highest turn number for which we already fired
# `on_turn_boundary_speed` per battle. The extension may send multiple
# requests per turn (mid-turn force-switch, polling latency); we want
# speed inference to fire AT MOST ONCE per actual game turn boundary.
_last_speed_turn: dict[str, int] = {}
# Per-battle resolved chaos format. Populated from `apply_belief` once the
# format alias chain resolves, then read by GET /belief/{battle_id} so the
# endpoint can call `_priors.get_distributions` with the right format
# without having to re-derive it from the request stream.
_format_by_battle: dict[str, str] = {}
# Lazy-built LLM client for the /explain endpoint. Constructed via
# `build_default_llm()` which reads GROQ_API_KEY from env. When unset, this
# stays None and /explain returns 503 — no fallback layer (per Task 3.2).
_llm: LLMClient | None = build_default_llm()
# LRU explanation cache keyed by (battle_id, turn, rqid). Same key always
# returns the same text; bounded at 500 entries (FIFO eviction). Live as a
# module-level dict so tests can clear it between cases.
_explain_cache: "OrderedDict[tuple[str, int, int], str]" = OrderedDict()
_EXPLAIN_CACHE_MAX = 500
# Hardcoded fallback chain: when the requested format has no cache locally
# and no chaos available on Smogon, try these in order. Strict-superset
# relationships per `project_chaos_cache_hack.md`. Keys are the format
# coming in from `b.tier`; values are formats to attempt instead.
_FORMAT_ALIASES: dict[str, list[str]] = {
    "gen9nationaldex": ["gen9nationaldexag"],
    "gen9nationaldexubers": ["gen9nationaldexag"],
    "gen9ag": ["gen9nationaldexag"],
}

# Formats that ban Terastallization (Smogon OU + NatDex OU as of late 2025).
# Compared against the *original* normalized format, not the chaos-fallback alias —
# AG-aliased lookups still inherit the original format's ban rules.
_TERA_BANNED_FORMATS: frozenset[str] = frozenset({
    "gen9ou",
    "gen9nationaldex",
})

# --- PIMC v2 proxy fan-out -------------------------------------------------
# Default K=4 = PIMC ON. Explicit `POKE_PROXY_PIMC_K=0` disables (single-modal
# POST), and K=1 also routes through the single-modal path. K in 2..8 enables
# PIMC fan-out: K plausible opponent teams sampled per opp mon, K full
# hypotheses combined into a `{"hypotheses":[...]}` request the engine can
# dispatch.
#
# Read at request time (not import time) so a manual env-flip + uvicorn
# autoreload picks it up without code edits. Override via:
#   `POKE_PROXY_PIMC_K=8 sc-proxy`   (max hypotheses)
#   `POKE_PROXY_PIMC_K=0 sc-proxy`   (disable PIMC explicitly)
# Bound is enforced (clamp to [0, 8]) — anything outside is ignored to
# protect the engine from runaway K values eating the per-hypothesis budget.
# Invalid input (negative, non-numeric) falls back to 0 rather than the
# default 4: a misconfigured env var should not silently elevate to PIMC.
_DEFAULT_PIMC_K = 4


def _read_pimc_k_env() -> int:
    """Parse `POKE_PROXY_PIMC_K`; clamp to [0, 8]; return 0 on parse error.
    Returns `_DEFAULT_PIMC_K` (4) when the env var is unset."""
    raw = os.environ.get("POKE_PROXY_PIMC_K")
    if raw is None:
        return _DEFAULT_PIMC_K
    try:
        k = int(raw)
    except (TypeError, ValueError):
        return 0
    if k < 0:
        return 0
    if k > 8:
        return 8
    return k


def _choose_pimc_k(
    requested_k: int, opp_pokemon: list[dict[str, Any]]
) -> int:
    """Return the env-requested K.

    Auto-tune previously scaled K down by reveal phase, but live play showed
    it dropped too aggressively. Keep this helper as the no-belief entry point
    in case callers need the old signature; it intentionally performs no
    phase-based scaling.
    """
    if requested_k <= 0:
        return 0
    if requested_k == 1:
        return 1
    return requested_k


def _choose_pimc_k_from_belief(
    requested_k: int,
    tracker: BeliefTracker,
    opp_pokemon: list[dict[str, Any]],
) -> int:
    """Return the env-requested K using the belief-aware call signature.

    The tracker and opponent list remain parameters so callers do not need
    to change, but K is no longer scaled by reveal phase.
    """
    if requested_k <= 0:
        return 0
    if requested_k == 1:
        return 1
    return requested_k


def _normalize(name: str) -> str:
    """Match the normalization the extension uses (`norm()` in content.ts)."""
    return "".join(c.lower() for c in (name or "") if c.isalnum())


def _resolve_format(fmt: str) -> str | None:
    """Pick a usable chaos format for `fmt`, with full memoization. Tries the
    requested format first, then aliases from `_FORMAT_ALIASES`. Returns the
    resolved format string, or None when nothing is loadable. The result is
    cached in `_format_resolution` so subsequent calls (every turn, every
    Pokemon) skip ALL retry attempts and ALL Smogon hits — the previous
    implementation re-tried the failing original format every request.
    """
    assert _priors is not None

    if fmt in _format_resolution:
        return _format_resolution[fmt]

    candidates = [fmt] + _FORMAT_ALIASES.get(fmt, [])
    for candidate in candidates:
        if candidate in _priors._loaded:
            _format_resolution[fmt] = candidate
            return candidate
        try:
            _priors._ensure_loaded(candidate)
            if candidate != fmt:
                logger.info("chaos resolved: %s → %s", fmt, candidate)
            _format_resolution[fmt] = candidate
            return candidate
        except Exception as exc:  # noqa: BLE001 — 404 / connection / parse all OK to swallow
            logger.warning("chaos load failed for %s: %s", candidate, exc)

    _format_resolution[fmt] = None
    logger.error(
        "no chaos available for %s (tried %s); overlay disabled for this format",
        fmt, candidates,
    )
    return None


def _resolve_display_species(species: str, fmt: str) -> str:
    """Map normalized species → display form using the loaded chaos cache.
    Falls back to the input string when no match (lets PriorsSource emit its
    neutral default and warn, rather than silently substituting)."""
    assert _priors is not None
    if fmt not in _display_cache:
        try:
            chaos = _priors._ensure_loaded(fmt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not load chaos for %s: %s", fmt, exc)
            _display_cache[fmt] = {}
            return species
        data = chaos.get("data", {})
        _display_cache[fmt] = {_normalize(d): d for d in data}
    return _display_cache[fmt].get(species, species)


def _get_tracker(battle_id: str) -> BeliefTracker:
    """LRU lookup; create on miss; evict oldest at cap."""
    if battle_id in _trackers:
        _trackers.move_to_end(battle_id)
        return _trackers[battle_id]
    tracker = BeliefTracker()
    _trackers[battle_id] = tracker
    if len(_trackers) > MAX_TRACKERS:
        evicted_id, _ = _trackers.popitem(last=False)
        logger.info("evicted tracker for battle %s (cap %d)", evicted_id, MAX_TRACKERS)
    return tracker


def _ingest_reveals(
    tracker: BeliefTracker,
    opp_pokemon: list[dict[str, Any]],
    revealed_moves_by_species: dict[str, list[str]],
) -> None:
    """Push per-Pokemon reveals from this turn's BattleRequest into the tracker.

    Idempotent: re-feeding the same reveal is a no-op (sets / equality writes).
    """
    for pkmn in opp_pokemon:
        species = pkmn.get("species", "")
        if not species:
            continue

        # Item: extension uses 'none' as the unrevealed sentinel.
        item = pkmn.get("item") or "none"
        if item != "none":
            tracker.on_reveal_item(species, item)

        # Ability: same convention.
        ability = pkmn.get("ability") or "none"
        if ability != "none":
            tracker.on_reveal_ability(species, ability)

        # Moves: from raw `moveTrack` (extension forwards via _planH).
        # NOT from pkmn["moves"], which is already padded with chaos priors.
        for move_id in revealed_moves_by_species.get(species, []):
            if move_id and move_id != "none":
                tracker.on_reveal_move(species, move_id)

        # Tera: terastallized + teraType arrives ground-truth from Showdown DOM.
        if pkmn.get("terastallized") and pkmn.get("teraType"):
            tracker.on_terastallize(species, pkmn["teraType"])


def _apply_transform_if_present(pkmn: dict[str, Any]) -> bool:
    """Apply a poke-env-style Transform overlay if the extension forwarded
    `transformedInto` for this Pokemon. Returns True if applied (caller skips
    chaos-modal padding), False otherwise.

    Reference: github.com/hsahovic/poke-env Pokemon.transform(). The Showdown
    `|-transform|USER|SPECIES` event provides only the species; the receiving
    client must derive everything else from its memory of the target. Fields
    copied: types, moves (PP=5 in gen5+), stats (NOT HP/maxhp), ability,
    boosts. Fields NOT copied: HP, status, item, level, tera state — Ditto
    Teras to its OWN preview Tera type, not the target's.
    """
    xform = pkmn.get("transformedInto")
    if not xform:
        return False

    # Types: explicit list overwrites base types. Caller must pass the
    # target's base dex types (Soak'd target uses base, not effective).
    types = xform.get("types")
    if isinstance(types, list) and types:
        pkmn["types"] = list(types)

    # Ability: copy target's active ability.
    if xform.get("ability"):
        pkmn["ability"] = xform["ability"]

    # Stats: copy target's stats EXCEPT hp/maxhp (Ditto keeps own HP).
    for k in ("attack", "defense", "specialAttack", "specialDefense", "speed"):
        if k in xform:
            pkmn[k] = xform[k]

    # Moves: copy target's revealed moveset, PP=5 each (gen5+ Transform rule).
    # Pad to 4 slots with the engine's "none" sentinel.
    moves_in = xform.get("moves") or []
    move_objs: list[dict[str, Any]] = [
        {"id": m, "pp": 5, "disabled": False} for m in moves_in[:4] if m
    ]
    while len(move_objs) < 4:
        move_objs.append({"id": "none", "pp": 0, "disabled": False})
    pkmn["moves"] = move_objs

    return True


def _apply_modal(pkmn: dict[str, Any], tracker: BeliefTracker, fmt: str) -> None:
    """Overlay belief-aware modal on a single opp Pokemon dict in-place.

    - moves: ALWAYS replaced (modal guarantees superset of revealed_moves).
    - item: backfilled only when extension sent 'none' (unrevealed).
    - ability: backfilled only when extension sent 'none'.
    - teraType: backfilled only when extension sent '' (extension hardcodes
      empty for opp at content.ts:215).

    Stats / hp / status / level / types are left untouched — they're either
    runtime state (hp, status) or already accurate (types via Dex lookup),
    or they need Phase 2 speed-range narrowing before they're trustworthy.
    """
    species = pkmn.get("species", "")
    # 'none' is the engine's empty-slot sentinel (see content.ts `emptyPokemon`).
    # Without this skip the proxy fires get_set('none', fmt) for every empty
    # reserve, which causes the chaos 404 retry storm on unsupported formats.
    if not species or species == "none":
        return False

    # Transform / Imposter short-circuit. When the extension forwards
    # `transformedInto` (computed from Showdown DOM's pkmn.volatiles.transform),
    # apply poke-env-style Transform: copy target's types/moves/stats/ability
    # and the boosts that were live at transform time. HP/item/status/tera
    # stay native to the transforming Pokemon. Bypasses chaos-modal because
    # the post-Transform state is fully known — sampling would introduce noise.
    if _apply_transform_if_present(pkmn):
        return True

    assert _priors is not None

    belief = tracker.get(species)
    display_name = _resolve_display_species(species, fmt)
    try:
        modal = _priors.get_set(display_name, fmt, belief=belief)
    except Exception as exc:  # noqa: BLE001 — engine call must never crash on get_set
        logger.warning("get_set(%s, %s) failed: %s", display_name, fmt, exc)
        return False

    # Moves: replace with modal's top-4, pad to engine's expected length of 4.
    move_objs: list[dict[str, Any]] = [{"id": m, "pp": 8} for m in modal.moves[:4]]
    while len(move_objs) < 4:
        move_objs.append({"id": "none", "pp": 0})
    pkmn["moves"] = move_objs

    if pkmn.get("item") == "none" and modal.item and modal.item != "none":
        pkmn["item"] = modal.item

    if pkmn.get("ability") == "none" and modal.ability and modal.ability != "none":
        pkmn["ability"] = modal.ability

    if pkmn.get("teraType") == "" and modal.tera_type:
        pkmn["teraType"] = modal.tera_type

    # Speed plumbing fix (2026-05-08): when the belief tracker has narrowed
    # opp's speed range from observed turn order, OR has inferred a Choice
    # Scarf from a forced-upgrade, recompute the modal's actual speed stat
    # and overwrite the extension's "max-EV-Jolly" assumption. Without this
    # the engine plays as if every opp Pokemon is 252-Spe Jolly even after
    # we've observed they're slower (or Scarf'd faster) — causing speed
    # bug deaths (e.g. Recover-while-OHKO'd-by-faster-opp).
    if belief.speed_range is not None or belief.item_inferred_choicescarf:
        base_spe = _BASE_SPEEDS.get(_normalize(species))
        if base_spe:
            nat_mult = _NATURE_TO_SPE_MULT.get(modal.nature, 1.0)
            ev_spe = modal.evs.get("spe", 0)
            spe = compute_speed_stat(base_spe, ev_spe, 31, nat_mult, 100)
            # Choice Scarf 1.5x. Apply when belief has inferred it OR when
            # the modal happened to land on a scarf set (item filter forced
            # this when speed_range[0] > non-scarf max).
            if (
                belief.item_inferred_choicescarf
                or _normalize(modal.item) == "choicescarf"
            ):
                spe = int(spe * 1.5)
            pkmn["speed"] = spe

    return True


def _maybe_fire_speed_narrowing(
    meta: dict[str, Any],
    tracker: BeliefTracker,
    battle_id: str,
    opp_pokemon: list[dict[str, Any]],
) -> None:
    """Phase 2 — Path C: read move-order metadata from the extension's
    `_planH.oppMoveOrderThisTurn` block and fire on_turn_boundary_speed
    for the just-finished turn. NO-OP if the metadata is absent (extension
    is on Phase 1 schema only).

    Dedupes by `(battle_id, turn)` — the extension may send multiple
    requests per game turn (mid-turn force-switch, polling latency) and
    we want speed inference to fire AT MOST ONCE per actual turn.
    """
    info = meta.get("oppMoveOrderThisTurn")
    if not info:
        return  # extension didn't capture move-order (Phase 1 client)

    turn = info.get("turn")
    if not isinstance(turn, int) or turn < 1:
        return  # invalid or pre-turn-1 (no observation possible)

    # Dedupe: only fire if this is a NEW turn for this battle.
    last_fired = _last_speed_turn.get(battle_id, 0)
    if turn <= last_fired:
        return
    _last_speed_turn[battle_id] = turn

    # Identify active opp species (the one whose move-order we're scoring).
    side_two = info.get("activeOppSpecies") or _active_opp_species(opp_pokemon)
    if not side_two:
        return

    move_log = info.get("moveLog") or []
    skip_flags = list(info.get("skipFlags") or [])
    my_role = info.get("myRole")  # "p1" or "p2"

    # Determine opp_moved_first from the move-log.
    opp_moved_first = _derive_opp_moved_first_from_log(move_log, my_role)

    my_speed = int(info.get("myActiveSpeedPostModifiers") or 0)
    if my_speed <= 0:
        # Without our own speed we can't compute the threshold.
        skip_flags.append("no_my_speed")

    weather = meta.get("weather")
    terrain = meta.get("terrain")
    in_trick_room = bool(meta.get("inTrickRoom"))

    tracker.on_turn_boundary_speed(
        species=side_two,
        turn=turn,
        my_active_speed_post_modifiers=my_speed,
        opp_moved_first=opp_moved_first,
        skip_reasons=skip_flags,
        in_trick_room=in_trick_room,
        weather=weather,
        terrain=terrain,
    )

    logger.info(
        "[%s][speed-narrowed] turn=%d opp=%s my_speed=%d opp_first=%s "
        "skips=%s tr=%s → range=%s scarf=%s",
        battle_id, turn, side_two, my_speed, opp_moved_first,
        skip_flags, in_trick_room,
        tracker.get(side_two).speed_range,
        tracker.get(side_two).item_inferred_choicescarf,
    )


def _active_opp_species(opp_pokemon: list[dict[str, Any]]) -> str:
    """Pick the active opp species from the BattleRequest sideTwo.pokemon list.
    Falls back to the first non-'none' Pokemon if no explicit activeIndex.
    """
    for p in opp_pokemon:
        sp = p.get("species") or ""
        if sp and sp != "none":
            return _normalize(sp)
    return ""


def _derive_opp_moved_first_from_log(
    move_log: list[dict[str, Any]], my_role: str | None
) -> bool | None:
    """Return True iff opp's |move| event preceded ours, False if ours first,
    None if uninformative (priority mismatch, single move, no role).
    """
    if len(move_log) < 2 or my_role is None:
        return None
    first, second = move_log[0], move_log[1]
    prio_first = int(first.get("priority", 0))
    prio_second = int(second.get("priority", 0))
    if prio_first != prio_second:
        return None  # priority mismatch — uninformative
    side_first = (first.get("side") or "").lower()
    return side_first != my_role.lower()


def apply_belief(req: dict[str, Any]) -> dict[str, Any]:
    """Mutate `req` in-place with belief-aware overlays for opp Pokemon.

    No-op (and returns `req` unchanged) when `_planH` metadata is absent —
    the proxy is back-compat with the pre-Plan-H extension.
    """
    meta = req.pop("_planH", None)  # strip before forwarding to engine
    if not meta:
        return req

    battle_id = meta.get("battleId")
    turn_num = meta.get("turn")
    # Engine-log correlation keys: forward both as top-level fields on `req`
    # so the engine can stamp them on the [ENGINE-INSTRUMENT] line. The
    # engine's serde_json::Value reader tolerates unknown top-level keys.
    if battle_id:
        req["battleId"] = battle_id
    if isinstance(turn_num, int):
        req["turn"] = turn_num
    raw_fmt = meta.get("format") or DEFAULT_FORMAT
    # Showdown's `b.tier` is the display string ("[Gen 9] National Dex"), but
    # Smogon chaos URLs and our cache filenames use normalized form
    # ("gen9nationaldex"). Normalize via the same `_normalize` we use for
    # species; the rule (lowercase + alnum-only) is identical.
    fmt = _normalize(raw_fmt) or DEFAULT_FORMAT
    raw_reveals = meta.get("oppRevealedMoves") or {}
    # Re-normalize keys defensively in case the extension sent display-form names.
    revealed_moves = {_normalize(k): list(v) for k, v in raw_reveals.items()}

    # Format-level Tera ban: signal to the engine so it stops searching
    # MoveTera options (~15-20% of root branching factor wasted otherwise).
    # Allow the extension to override via `_planH.teraBanned`; otherwise
    # derive from the format itself.
    extension_override = meta.get("teraBanned")
    if extension_override is not None:
        req["teraBanned"] = bool(extension_override)
    else:
        req["teraBanned"] = fmt in _TERA_BANNED_FORMATS

    if not battle_id:
        logger.warning("_planH missing battleId; skipping belief overlay")
        return req

    # Resolve format ONCE per request — fall back through aliases if the
    # requested format has no chaos cache, and remember a global failure
    # so we don't retry per-Pokemon.
    resolved_fmt = _resolve_format(fmt)
    if resolved_fmt is not None:
        _format_by_battle[battle_id] = resolved_fmt

    tracker = _get_tracker(battle_id)
    opp_pokemon = (req.get("sideTwo") or {}).get("pokemon") or []

    _ingest_reveals(tracker, opp_pokemon, revealed_moves)

    # Phase 2 — fire speed-range narrowing for the just-finished turn,
    # BEFORE applying the modal overlay (so the spread filter sees the
    # newly-narrowed range when it picks).
    _maybe_fire_speed_narrowing(meta, tracker, battle_id, opp_pokemon)

    overlay_results: dict[str, bool] = {}
    if resolved_fmt is not None:
        for pkmn in opp_pokemon:
            sp = pkmn.get("species", "?")
            overlay_results[sp] = bool(_apply_modal(pkmn, tracker, resolved_fmt))

    # Live-debug visibility: one-line summary + per-mon detail when there's
    # a reveal worth reporting. `overlay=ok` means the proxy's modal replaced
    # the extension's padding; `overlay=skip` means we fell through (chaos
    # 404 / unknown species) and the engine sees the extension's original.
    overlay_ok = sum(1 for v in overlay_results.values() if v)
    fmt_tag = fmt if resolved_fmt is None else (
        fmt if resolved_fmt == fmt else f"{fmt}→{resolved_fmt}"
    )
    logger.info(
        "[%s][turn-applied] fmt=%s opp_mons=%d overlay=%d/%d trackers=%d",
        battle_id, fmt_tag, len(opp_pokemon), overlay_ok, len(opp_pokemon), len(_trackers),
    )
    for pkmn in opp_pokemon:
        sp = pkmn.get("species", "?")
        belief = tracker.get(sp)
        if belief.revealed_moves or belief.impossible_items or belief.impossible_abilities:
            tag = "ok" if overlay_results.get(sp) else "skip"
            logger.info(
                "  %s: overlay=%s rev_moves=%s rev_item=%s rev_abil=%s "
                "imposs_items=%d imposs_abil=%d moves_sent=%s",
                sp, tag,
                sorted(belief.revealed_moves),
                belief.revealed_item,
                belief.revealed_ability,
                len(belief.impossible_items),
                len(belief.impossible_abilities),
                [m["id"] for m in pkmn.get("moves", [])],
            )

    return req


# --- PIMC v2 proxy fan-out -------------------------------------------------
def _sample_one_set_for_pkmn(
    pkmn: dict[str, Any],
    tracker: BeliefTracker,
    resolved_fmt: str,
    rng: random.Random,
) -> dict[str, Any] | None:
    """Sample ONE plausible set for one opp Pokemon and return an overlaid
    copy of `pkmn`. Mirrors `_apply_modal` but uses a sampled (not modal)
    set so the K hypotheses diverge from each other.

    Returns None on failure (chaos miss / unknown species / sample raised);
    caller should fall back to the original `pkmn` dict in that case.
    """
    assert _priors is not None
    species = pkmn.get("species", "")
    if not species or species == "none":
        return None

    # Transform short-circuit (mirrors _apply_modal). Each PIMC hypothesis
    # gets the same Transform overlay because the post-Transform state is
    # fully known — only the chaos-sampled fields should vary across K.
    if pkmn.get("transformedInto"):
        out = copy.deepcopy(pkmn)
        _apply_transform_if_present(out)
        return out

    belief = tracker.get(species)
    display_name = _resolve_display_species(species, resolved_fmt)
    try:
        sampled = _priors.sample_set(
            display_name, resolved_fmt, rng=rng, belief=belief
        )
    except Exception as exc:  # noqa: BLE001 — engine call must never crash
        logger.warning(
            "sample_set(%s, %s) failed: %s", display_name, resolved_fmt, exc
        )
        return None

    out = copy.deepcopy(pkmn)
    move_objs: list[dict[str, Any]] = [{"id": m, "pp": 8} for m in sampled.moves[:4]]
    while len(move_objs) < 4:
        move_objs.append({"id": "none", "pp": 0})
    out["moves"] = move_objs
    if out.get("item") == "none" and sampled.item and sampled.item != "none":
        out["item"] = sampled.item
    if out.get("ability") == "none" and sampled.ability and sampled.ability != "none":
        out["ability"] = sampled.ability
    if out.get("teraType") == "" and sampled.tera_type:
        out["teraType"] = sampled.tera_type
    return out


def apply_belief_pimc(req: dict[str, Any], k: int) -> dict[str, Any]:
    """PIMC v2 fan-out: build K plausible-team hypotheses from belief.

    Returns a `{"hypotheses":[state1, state2, ..., stateK]}` request shape.
    Each state is a full BattleRequest with the SAME player-side data and
    DIFFERENT sampled opp sets. The K=`k` here is the final env-requested K.

    When K <= 1, falls back to `apply_belief` (single modal). When the
    request lacks `_planH` metadata or the chaos format is unresolvable,
    also falls back to `apply_belief` so the engine still gets a valid
    single-state POST instead of a malformed multi-hypothesis blob.

    Reveal honoring: each sampled set respects `belief.revealed_moves` /
    `revealed_item` / `revealed_ability` because we pass `belief=` through
    to `priors.sample_set`. So if EQ is revealed for Garchomp, all K of
    Garchomp's sampled sets include EQ.

    """
    if k <= 1:
        return apply_belief(req)

    # Peek at metadata WITHOUT consuming it — we need apply_belief's
    # ingest-and-overlay logic to fire ONCE (on the modal pass below)
    # so the tracker is updated. We then re-derive K hypotheses on top
    # of the same tracker state.
    meta = req.get("_planH")
    if not meta:
        # No belief metadata → no point fanning out.
        return apply_belief(req)

    battle_id = meta.get("battleId")
    if not battle_id:
        return apply_belief(req)

    raw_fmt = meta.get("format") or DEFAULT_FORMAT
    fmt = _normalize(raw_fmt) or DEFAULT_FORMAT
    assert _priors is not None

    # Resolve format. If unavailable, no PIMC — fall through to apply_belief
    # which will log the same error and return the un-overlaid request.
    resolved_fmt = _resolve_format(fmt)
    if resolved_fmt is None:
        return apply_belief(req)

    # Run the standard belief pipeline ONCE on the original request:
    #  - ingests reveals into the tracker
    #  - fires speed inference for the just-finished turn
    #  - resolves format (cached for subsequent _resolve_format calls)
    #  - mutates `req` in-place to strip _planH and add top-level fields
    apply_belief(req)

    # Now build K hypothesis-states from the snapshot, each with sampled
    # opp Pokemon. The player-side (sideOne) is identical across all K.
    tracker = _trackers.get(battle_id)
    if tracker is None:
        # Defensive: should never happen since apply_belief just made it.
        return req

    rng = random.Random()
    hypotheses: list[dict[str, Any]] = []
    for i in range(k):
        # Start from the post-apply_belief request (which already has the
        # _planH stripped + top-level battleId/turn/teraBanned set), then
        # replace the sideTwo.pokemon list with sampled-set overlays.
        hyp = copy.deepcopy(req)
        opp_list = (hyp.get("sideTwo") or {}).get("pokemon") or []
        new_opp_list: list[dict[str, Any]] = []
        for pkmn in opp_list:
            sampled_pkmn = _sample_one_set_for_pkmn(
                pkmn, tracker, resolved_fmt, rng
            )
            new_opp_list.append(sampled_pkmn if sampled_pkmn is not None else pkmn)
        if hyp.get("sideTwo"):
            hyp["sideTwo"]["pokemon"] = new_opp_list
        hypotheses.append(hyp)

    # Wrap in the engine-expected envelope. Top-level battleId / turn are
    # forwarded outside the hypotheses array so the engine's instrument
    # log line can stamp them once (server.rs:1060-1062 reads them from the
    # outer body). Keep them on `req` even though they're also present
    # inside each hypothesis.
    pimc_req: dict[str, Any] = {"hypotheses": hypotheses}
    if "battleId" in req:
        pimc_req["battleId"] = req["battleId"]
    if "turn" in req:
        pimc_req["turn"] = req["turn"]
    if "teraBanned" in req:
        pimc_req["teraBanned"] = req["teraBanned"]
    # Forward time / interval budgets at top level too — the engine divides
    # `time_limit_ms` by K internally to compute per-hypothesis budget
    # (server.rs:1004), so it MUST see the original full budget here.
    if "timeLimitMs" in req:
        pimc_req["timeLimitMs"] = req["timeLimitMs"]
    if "updateIntervalMs" in req:
        pimc_req["updateIntervalMs"] = req["updateIntervalMs"]

    logger.info(
        "[%s][pimc-fanout] k=%d opp_mons=%d format=%s",
        battle_id, k, len(opp_list), resolved_fmt,
    )
    return pimc_req


# --- FastAPI app ---

app = FastAPI(title="showdown-copilot Plan H proxy")

# Allow the Showdown page to POST cross-origin. The original engine accepts
# cross-origin requests; FastAPI / Starlette returns 405 on the preflight
# OPTIONS by default, so without this middleware the browser blocks every
# request before it reaches `analyze_stream`. Bound to localhost so the open
# allow-origins is fine.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(dashboard_router)


# --- engine-replay logging ---
# JSONL per battle, append-only. One line per /analyze/stream request.
# Captures the post-belief-overlay request body + raw engine response so a
# replay tool can re-POST identical inputs to a different engine version.
# Fire-and-forget after the stream completes — never blocks the live response.
_REPLAY_DIR = Path(
    "/Users/edkiboma/Projects/pokemon-ai/workspace/analysis/engine-replay"
)
_PROXY_GIT_SHA: str | None = None


def _get_proxy_git_sha() -> str:
    """Cached git sha of the showdown-stack repo. 'unknown' if git unavailable."""
    global _PROXY_GIT_SHA
    if _PROXY_GIT_SHA is None:
        try:
            _PROXY_GIT_SHA = subprocess.check_output(
                ["git", "-C", "/Users/edkiboma/Projects/pokemon-ai/showdown-stack",
                 "rev-parse", "HEAD"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2,
            ).strip()[:12]
        except Exception:  # noqa: BLE001
            _PROXY_GIT_SHA = "unknown"
    return _PROXY_GIT_SHA


async def _log_replay_record(req_body: dict, response_chunks: list[bytes]) -> None:
    """Write one JSONL line capturing the engine input/output for later replay.
    Best-effort; never raises into the caller."""
    try:
        battle_id = req_body.get("battleId") or "unknown"
        # rqid + turn live in the body; force_switch may be top-level or in _planH.
        ph = req_body.get("_planH") or {}
        turn = req_body.get("turn") or ph.get("turn") or 0
        rqid = req_body.get("rqid") or ph.get("rqid") or 0
        force_switch = bool(req_body.get("forceSwitch") or ph.get("forceSwitch"))

        response_text = b"".join(response_chunks).decode("utf-8", errors="replace")
        # Last non-empty line of NDJSON is the terminal "final" event from engine.
        terminal_event: dict | None = None
        for line in reversed(response_text.strip().splitlines()):
            line = line.strip()
            if line:
                try:
                    terminal_event = json.loads(line)
                except json.JSONDecodeError:
                    pass
                break

        record = {
            "schema": 1,
            "captured_at_ms": int(time.time() * 1000),
            "battle_id": battle_id,
            "turn": turn,
            "rqid": rqid,
            "force_switch": force_switch,
            "engine_url": ENGINE_URL,
            "proxy_git_sha": _get_proxy_git_sha(),
            "engine_request": req_body,
            "engine_response_terminal": terminal_event,
            "engine_response_raw": response_text,
        }

        _REPLAY_DIR.mkdir(parents=True, exist_ok=True)
        out_file = _REPLAY_DIR / f"{battle_id}.jsonl"
        # Append synchronously — JSONL appends are atomic at line granularity.
        with open(out_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning("engine-replay log failed for battle=%s: %s",
                       req_body.get("battleId", "?"), exc)


@app.post("/analyze/stream")
async def analyze_stream(req: Request) -> StreamingResponse:
    """Proxy endpoint: ingests BattleRequest, overlays belief, forwards to engine.

    When `POKE_PROXY_PIMC_K >= 2`, the request is fanned out into K
    hypothesis-states using `apply_belief_pimc`. Default 0/1 =
    single-modal path.
    """
    body = await req.json()
    pimc_k_env = _read_pimc_k_env()
    if pimc_k_env >= 2:
        # Preserve the helper call so the first request for a battle and later
        # requests follow the same K-selection path. The helpers currently
        # return the env-requested K without phase-based scaling.
        meta = (body or {}).get("_planH") or {}
        battle_id = meta.get("battleId")
        opp_pokemon = ((body or {}).get("sideTwo") or {}).get("pokemon") or []
        tracker = _trackers.get(battle_id) if battle_id else None
        if tracker is not None:
            effective_k = _choose_pimc_k_from_belief(pimc_k_env, tracker, opp_pokemon)
        else:
            effective_k = min(8, pimc_k_env)
        body = apply_belief_pimc(body, effective_k)
    else:
        body = apply_belief(body)
    assert _engine_client is not None

    # Captured for the engine-replay logger (fire-and-forget after stream ends).
    response_chunks: list[bytes] = []

    async def relay():
        try:
            async with _engine_client.stream(
                "POST",
                f"{ENGINE_URL}/analyze/stream",
                json=body,
            ) as r:
                async for chunk in r.aiter_raw():
                    yield chunk
                    response_chunks.append(chunk)
        except httpx.HTTPError as exc:
            logger.error("engine forward failed: %s", exc)
            err = {"event": "error", "message": f"proxy: engine unreachable ({exc})"}
            err_bytes = (json.dumps(err) + "\n").encode()
            yield err_bytes
            response_chunks.append(err_bytes)
        finally:
            # Stream complete (or errored). Schedule the replay log; don't await.
            asyncio.create_task(_log_replay_record(body, response_chunks))

    return StreamingResponse(relay(), media_type="application/x-ndjson")


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "trackers": list(_trackers.keys()),
            "engine_url": ENGINE_URL,
        }
    )


@app.post("/preview-plan")
async def preview_plan(req: PreviewPlanRequest) -> JSONResponse:
    """Create the structured live matchup plan shown at team preview."""
    started = time.perf_counter()
    logger.info(
        "/preview-plan: start battle=%s fmt=%s mode=%s preset=%s my=%s opp=%s",
        req.battleId,
        req.format,
        req.runMode,
        req.presetId,
        [mon.species for mon in req.myTeam],
        req.opponentTeam,
    )
    response = await build_preview_plan(req)
    logger.info(
        "/preview-plan: done battle=%s source=%s provider=%s model=%s latency_ms=%s route_ms=%s fallback=%s",
        response.battleId,
        response.source,
        response.provider,
        response.model,
        response.latencyMs,
        int((time.perf_counter() - started) * 1000),
        response.fallbackReason,
    )
    return JSONResponse(response.model_dump())


def _serialize_distributions(d: Distributions) -> dict:
    """Convert dist dicts to sorted [{name, pct}] lists for the extension UI."""
    def _to_list(dist: dict[str, float], top_n: int) -> list[dict]:
        items = sorted(dist.items(), key=lambda kv: -kv[1])[:top_n]
        return [{"name": name, "pct": round(pct * 100, 1)} for name, pct in items]
    return {
        "moves": _to_list(d.moves, top_n=8),
        "items": _to_list(d.items, top_n=5),
        "abilities": _to_list(d.abilities, top_n=3),
        "spreads": _to_list(d.spreads, top_n=3),
        "tera_types": _to_list(d.tera_types, top_n=5),
    }


def _empty_dists() -> dict:
    return {"moves": [], "items": [], "abilities": [], "spreads": [], "tera_types": []}


@app.get("/belief/{battle_id}")
async def get_belief(battle_id: str) -> JSONResponse:
    """Expose per-opponent belief + chaos modal probabilities for the extension."""
    tracker = _trackers.get(battle_id)
    if tracker is None:
        raise HTTPException(status_code=404, detail=f"no tracker for battle_id={battle_id}")

    fmt = _format_by_battle.get(battle_id)
    if fmt is None:
        raise HTTPException(status_code=409, detail=f"no format known for battle_id={battle_id}")

    assert _priors is not None
    out: dict[str, dict] = {}
    for species, belief in tracker.all_beliefs().items():
        display = _resolve_display_species(species, fmt)
        dists = _priors.get_distributions(display, fmt, belief=belief)
        out[species] = {
            "revealed": {
                "moves": sorted(belief.revealed_moves),
                "item": belief.revealed_item,
                "ability": belief.revealed_ability,
                "tera_type": belief.tera_type if belief.terastallized else None,
            },
            "modal": _serialize_distributions(dists) if dists else _empty_dists(),
            "speed_range": belief.speed_range,
            "item_inferred_choicescarf": belief.item_inferred_choicescarf,
        }
    return JSONResponse({
        "battle_id": battle_id,
        "format": fmt,
        "opponents": out,
    })


# Where note-disk syncs land. Path is relative to the workspace repo so notes
# live alongside battle postmortems and other analysis artifacts. The dir is
# created on first write.
_NOTES_DIR = Path(
    "/Users/edkiboma/Projects/pokemon-ai/workspace/analysis/play-notes"
)

# Where /explain JSONL syncs land. One file per UTC date, append-only. Mirrors
# the /annotation pattern so post-hoc analysis can correlate engine recs +
# LLM explanations + user notes from the same on-disk corpus.
_EXPLANATIONS_DIR = Path(
    "/Users/edkiboma/Projects/pokemon-ai/workspace/analysis/explanations"
)


def _llm_model_name() -> str:
    """Best-effort model name for the explanation log. Returns 'unknown' if
    the client doesn't expose it."""
    if _llm is None:
        return "none"
    return getattr(_llm, "_model", "unknown")


class AnnotationRequest(BaseModel):
    """Schema for POST /annotation. Extension fires this when a user saves a
    per-turn ('N' modal) or per-battle freeform note. `overrideTag` is set
    only when the user picked a tag from the dropdown in the per-turn modal;
    older clients and battle-level notes leave it None. Persisted verbatim
    to JSONL — downstream (engine-debug corpus) reads `overrideTag` to
    aggregate which engine error categories triggered overrides."""

    battleId: str
    turn: int
    kind: str  # "turn" | "battle"
    text: str
    overrideTag: str | None = None
    timestampMs: int | None = None


@app.post("/annotation")
async def save_annotation(req: AnnotationRequest) -> JSONResponse:
    """Append a user annotation to today's JSONL file.

    File: {NOTES_DIR}/{YYYY-MM-DD}.jsonl, one note per line. Append-only;
    UTF-8; never overwrites existing entries. Fire-and-forget from the
    extension side — failures here don't affect localStorage capture.
    `overrideTag` is null for battle-level notes / older clients.
    """
    if not req.battleId:
        return JSONResponse({"ok": False, "error": "missing battleId"}, status_code=400)
    if req.kind not in ("turn", "battle"):
        return JSONResponse({"ok": False, "error": "bad kind"}, status_code=400)

    date = datetime.now().strftime("%Y-%m-%d")
    out = _NOTES_DIR / f"{date}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    line = req.model_dump_json()
    with out.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    return JSONResponse({"ok": True})


# --- /postmortem endpoint --------------------------------------------------
# Auto-persist battle postmortems to disk so future Claude analysis sessions
# can read them. The extension fires this fire-and-forget after writing to
# localStorage (which remains the canonical client-side store). If the proxy
# is down, localStorage still has it and the user can recover later.
_POSTMORTEM_DIR = Path(
    "/Users/edkiboma/Projects/pokemon-ai/workspace/analysis/battle-postmortems"
)
_MANIFEST_PATH = _POSTMORTEM_DIR / "manifest.jsonl"


def _format_shortcode(fmt: str) -> str:
    """Map full format string to short slug for filenames.

    Examples:
        "[Gen 9] National Dex" -> "natdex"
        "gen9nationaldexag" -> "natdexag"
        "gen9ou" -> "ou"
        "gen9ubers" -> "ubers"
    """
    s = (fmt or "unknown").lower()
    s = s.removeprefix("[gen 9] ").removeprefix("[gen 8] ")
    s = s.removeprefix("gen9").removeprefix("gen8")
    # Common name swaps — apply before alnum-strip so multi-word forms resolve.
    s = s.replace("national dex", "natdex").replace("nationaldex", "natdex")
    # Strip non-alphanum (spaces, brackets, punctuation).
    return "".join(c for c in s if c.isalnum()) or "unknown"


def _safe_slug(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum()) or "unknown"


def _build_postmortem_filename(pm: dict) -> str:
    """Build a smart filename from postmortem metadata.

    Pattern: ``YYYY-MM-DD-HHMM-<opponent>-<formatShort>-<battleIdSuffix>.json``

    Sorts chronologically with ``ls -1``, opponent visible at glance, no
    overwrites on rematch. Falls back to current time when ``endedAtMs`` is
    missing/invalid (fresh-from-the-browser quirks).
    """
    ended_ms = pm.get("endedAtMs") or pm.get("endedAt") or 0
    if not isinstance(ended_ms, (int, float)) or ended_ms <= 0:
        ended_ms = int(datetime.now().timestamp() * 1000)
    dt = datetime.fromtimestamp(ended_ms / 1000)
    date_part = dt.strftime("%Y-%m-%d-%H%M")
    opp = _safe_slug(pm.get("opponent", "unknown"))
    fmt = _format_shortcode(pm.get("format", ""))
    battle_id = pm.get("battleId", "")
    suffix = "".join(c for c in battle_id[-8:] if c.isalnum()) or "x"
    return f"{date_part}-{opp}-{fmt}-{suffix}.json"


def _load_replay_finals_by_turn(battle_id: str) -> dict[int, dict[str, Any]]:
    """Return latest terminal engine final per turn for a battle.

    The browser can abort an old response stream when a newer decision point
    arrives, leaving scHistory with a decision row but no final. The proxy's
    engine-replay archive still has the completed terminal event, so disk
    postmortems can be repaired for analytics without changing live UI flow.
    """
    replay_path = _REPLAY_DIR / f"{battle_id}.jsonl"
    if not replay_path.exists():
        return {}

    finals: dict[int, dict[str, Any]] = {}
    try:
        with replay_path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                turn = row.get("turn")
                terminal = row.get("engine_response_terminal")
                if not isinstance(turn, int):
                    continue
                if not isinstance(terminal, dict) or not terminal.get("bestMove"):
                    continue
                finals[turn] = terminal
    except Exception as exc:  # noqa: BLE001
        logger.warning("/postmortem: could not read replay finals for %s: %s", battle_id, exc)
        return {}
    return finals


def _enrich_missing_postmortem_picks_from_replay(pm: dict) -> int:
    """Fill missing regular-row recommendations from engine replay JSONL.

    This targets rows like: actual switch captured, but myPick.name is null
    because the client-side fetch stream was aborted before scHistory got the
    final event. We only patch regular rows and only when the pick is absent.
    """
    battle_id = pm.get("battleId")
    turns = pm.get("turns")
    if not isinstance(battle_id, str) or not battle_id or not isinstance(turns, list):
        return 0

    finals_by_turn = _load_replay_finals_by_turn(battle_id)
    if not finals_by_turn:
        return 0

    patched = 0
    for turn_row in turns:
        if not isinstance(turn_row, dict) or turn_row.get("forceSwitch"):
            continue
        my_pick = turn_row.get("myPick")
        if not isinstance(my_pick, dict) or my_pick.get("name"):
            continue
        turn = turn_row.get("turn")
        if not isinstance(turn, int):
            continue
        final = finals_by_turn.get(turn)
        if not final:
            continue
        my_pick["name"] = final.get("bestMove")
        my_pick["confidence"] = final.get("confidence")
        my_pick["sims"] = final.get("sims")
        my_pick["depth"] = final.get("depth")
        my_pick["pv"] = final.get("pv") if isinstance(final.get("pv"), list) else []
        my_pick["message"] = final.get("message")
        my_pick["pimcConsensus"] = final.get("pimcConsensus") if isinstance(final.get("pimcConsensus"), dict) else None
        my_pick["pimcBreakdown"] = final.get("pimcBreakdown") if isinstance(final.get("pimcBreakdown"), list) else []
        patched += 1
    return patched


def _normalize_postmortem_token(value: Any) -> str:
    return "".join(c for c in str(value or "").lower() if c.isalnum())


def _reclassify_postmortem_pick_kinds(pm: dict) -> int:
    """Classify regular recommendations as moves or switches from team preview.

    Older browser builds wrote species-name recommendations like "GARCHOMP" as
    ``kind: "move"`` because the engine response only has a bestMove string.
    The postmortem already stores our team preview, so repair the serialized
    analytics shape here instead of requiring a browser-side reparse.
    """
    team_preview = pm.get("teamPreview")
    turns = pm.get("turns")
    if not isinstance(team_preview, dict) or not isinstance(turns, list):
        return 0

    mine = team_preview.get("mine")
    if not isinstance(mine, list):
        return 0

    my_species = {
        normalized
        for species in mine
        if (normalized := _normalize_postmortem_token(species))
    }
    if not my_species:
        return 0

    patched = 0
    for turn_row in turns:
        if not isinstance(turn_row, dict) or turn_row.get("forceSwitch"):
            continue
        my_pick = turn_row.get("myPick")
        if not isinstance(my_pick, dict):
            continue
        name = my_pick.get("name")
        if not name:
            continue
        desired_kind = (
            "switch" if _normalize_postmortem_token(name) in my_species else "move"
        )
        if my_pick.get("kind") != desired_kind:
            my_pick["kind"] = desired_kind
            patched += 1
    return patched


@app.post("/postmortem")
async def postmortem(req: Request) -> JSONResponse:
    """Accept and persist a battle postmortem JSON. The extension fires this
    fire-and-forget after writing to localStorage; if we're down, localStorage
    is the source of truth and the user can recover later.

    Uses ``Request`` + ``await req.json()`` rather than a Pydantic model so the
    proxy is forward-compatible with future TypeScript schema evolutions —
    we store JSON verbatim and downstream tools parse with the canonical schema.
    """
    try:
        body = await req.json()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"bad json: {exc}"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "body must be object"}, status_code=400)
    if not isinstance(body.get("battleId"), str) or not body["battleId"]:
        return JSONResponse({"ok": False, "error": "missing battleId"}, status_code=400)

    _POSTMORTEM_DIR.mkdir(parents=True, exist_ok=True)

    # Fix B: if a postmortem for this battleId already exists on disk, REUSE
    # the existing filename and overwrite it. Lets the extension safely fire
    # incremental disk POSTs every turn — they all converge to one file per
    # battle. Without this, mid-battle posts (endedAtMs=0) would each get a
    # fresh datetime-based filename, polluting the archive with duplicates.
    battle_id = body["battleId"]
    existing_fname: str | None = None
    if _MANIFEST_PATH.exists():
        with _MANIFEST_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("battleId") == battle_id:
                    existing_fname = entry.get("file")
                    # don't break — last entry wins (latest filename)

    fname = existing_fname or _build_postmortem_filename(body)
    out = _POSTMORTEM_DIR / fname
    enriched_missing_picks = _enrich_missing_postmortem_picks_from_replay(body)
    if enriched_missing_picks:
        logger.info(
            "/postmortem: enriched %d missing pick(s) from engine replay for %s",
            enriched_missing_picks,
            battle_id,
        )
    reclassified_pick_kinds = _reclassify_postmortem_pick_kinds(body)
    if reclassified_pick_kinds:
        logger.info(
            "/postmortem: reclassified %d regular pick kind(s) for %s",
            reclassified_pick_kinds,
            battle_id,
        )

    # Race-condition guard (2026-05-08): soft-persist fires per turn as
    # fetch() promises. Network jitter can make an EARLIER persist's POST
    # land at the proxy AFTER a later persist's POST, overwriting more
    # complete data with less complete data. Skip overwrite if the
    # incoming pm has STRICTLY FEWER turn diffs than what's on disk.
    #
    # Exception: a final battle-end post can legitimately have fewer decision
    # rows if Showdown ends before another engine request is recorded. In that
    # case, preserve the richer existing turns and merge final metadata like
    # winner/endedAtMs/totalTurns onto the existing file.
    if existing_fname and out.exists():
        try:
            existing_pm = json.loads(out.read_text(encoding="utf-8"))
            existing_turns = len(existing_pm.get("turns") or [])
            new_turns = len(body.get("turns") or [])
            if new_turns < existing_turns:
                ended_ms = body.get("endedAtMs")
                new_is_final = bool(body.get("winner")) or (
                    isinstance(ended_ms, (int, float)) and ended_ms > 0
                )
                if new_is_final:
                    merged = dict(existing_pm)
                    for key in (
                        "winner",
                        "endedAtMs",
                        "totalTurns",
                        "battleNote",
                        "replayUrl",
                        "schemaVersion",
                    ):
                        if body.get(key) is not None:
                            merged[key] = body.get(key)
                    if isinstance(existing_pm.get("totalTurns"), int) and isinstance(body.get("totalTurns"), int):
                        merged["totalTurns"] = max(existing_pm["totalTurns"], body["totalTurns"])
                    body = merged
                    enriched_missing_picks = _enrich_missing_postmortem_picks_from_replay(body)
                    if enriched_missing_picks:
                        logger.info(
                            "/postmortem: enriched %d missing pick(s) after final merge for %s",
                            enriched_missing_picks,
                            battle_id,
                        )
                    reclassified_pick_kinds = _reclassify_postmortem_pick_kinds(body)
                    if reclassified_pick_kinds:
                        logger.info(
                            "/postmortem: reclassified %d regular pick kind(s) after final merge for %s",
                            reclassified_pick_kinds,
                            battle_id,
                        )
                    new_turns = existing_turns
                else:
                    logger.info(
                        "/postmortem: skipping overwrite for %s — new pm has %d turn diffs, existing has %d",
                        battle_id, new_turns, existing_turns,
                    )
                    return JSONResponse({
                        "ok": True, "file": fname, "overwrote": False,
                        "skipped_stale": True, "existing_turns": existing_turns, "new_turns": new_turns,
                    })
            if new_turns < existing_turns:
                logger.info(
                    "/postmortem: skipping overwrite for %s — new pm has %d turn diffs, existing has %d",
                    battle_id, new_turns, existing_turns,
                )
                return JSONResponse({
                    "ok": True, "file": fname, "overwrote": False,
                    "skipped_stale": True, "existing_turns": existing_turns, "new_turns": new_turns,
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning("/postmortem: could not read existing file %s: %s", fname, exc)

    out.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")

    manifest_entry = {
        "battleId": battle_id,
        "file": fname,
        "opponent": body.get("opponent"),
        "format": body.get("format"),
        "endedAtMs": body.get("endedAtMs"),
        "totalTurns": body.get("totalTurns"),
        "winner": body.get("winner"),
        "schemaVersion": body.get("schemaVersion"),
    }
    # Manifest is append-only; readers dedupe by battleId taking the latest.
    # We append even on overwrite so endedAtMs / totalTurns / winner reflect
    # the most-recent post, while still preserving incremental history if
    # someone wants to see it.
    with _MANIFEST_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(manifest_entry, ensure_ascii=False) + "\n")

    return JSONResponse({"ok": True, "file": fname, "overwrote": existing_fname is not None})


# --- /explain endpoint -----------------------------------------------------
# SYSTEM_PROMPT, ExplainRequest, build_explain_prompt, and the per-section
# render helpers all live in showdown_copilot.explainer. This module keeps
# only the FastAPI route + the stateful pieces (LRU cache, JSONL persist,
# belief gather that reads proxy globals).


def _gather_belief(battle_id: str) -> dict[str, dict]:
    """Derive per-opp belief from `_trackers` + `_priors`.

    Same shape as the GET /belief endpoint (revealed + modal). Returns {} when
    there's no tracker for this battle yet (first-turn case) — the prompt
    builder degrades gracefully with empty belief.
    """
    tracker = _trackers.get(battle_id)
    fmt = _format_by_battle.get(battle_id)
    if tracker is None or fmt is None or _priors is None:
        return {}
    out: dict[str, dict] = {}
    for species, belief in tracker.all_beliefs().items():
        display = _resolve_display_species(species, fmt)
        try:
            dists = _priors.get_distributions(display, fmt, belief=belief)
        except Exception as exc:  # noqa: BLE001 — never crash /explain on chaos lookup
            logger.warning("get_distributions(%s, %s) failed: %s", display, fmt, exc)
            dists = None
        out[species] = {
            "revealed": {
                "moves": sorted(belief.revealed_moves),
                "item": belief.revealed_item,
                "ability": belief.revealed_ability,
                "tera_type": belief.tera_type if belief.terastallized else None,
            },
            "modal": _serialize_distributions(dists) if dists else _empty_dists(),
        }
    return out


@app.post("/explain")
async def explain(req: ExplainRequest) -> dict:
    """Generate a coach-style explanation for the current engine recommendation.

    Augments the extension's payload with proxy-side belief (chaos modal +
    revealed reveals from BeliefTracker) so the LLM sees the same data the
    rest of the panel renders. Cached by (battle_id, turn, rqid); same key
    always returns the same text within the LRU window.
    """
    if _llm is None:
        raise HTTPException(
            status_code=503,
            detail="No LLM configured (set GROQ_API_KEY)",
        )

    key = (req.battle_id, req.turn, req.rqid)
    if key in _explain_cache:
        # Touch for LRU semantics, then return.
        _explain_cache.move_to_end(key)
        return {"explanation": _explain_cache[key], "cached": True}

    belief_map = _gather_belief(req.battle_id)
    fmt = _format_by_battle.get(req.battle_id, "unknown")
    user_prompt = build_explain_prompt(req, belief_map, fmt)

    try:
        text = await _llm.complete(SYSTEM_PROMPT, user_prompt, max_tokens=400)
    except Exception as exc:  # noqa: BLE001 — surface as 502, never crash proxy
        logger.error("/explain failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))

    _explain_cache[key] = text
    if len(_explain_cache) > _EXPLAIN_CACHE_MAX:
        _explain_cache.popitem(last=False)

    # Persist to JSONL (debug-corpus). Wrapped in try/except so a disk hiccup
    # doesn't fail the response — localStorage / cache still has the text.
    try:
        date = datetime.now().strftime("%Y-%m-%d")
        out = _EXPLANATIONS_DIR / f"{date}.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        log_line = {
            "battleId": req.battle_id,
            "turn": req.turn,
            "rqid": req.rqid,
            "text": text,
            "model": _llm_model_name(),
            "timestampMs": int(datetime.now().timestamp() * 1000),
        }
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(log_line, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — never fail /explain on disk write
        logger.warning("/explain JSONL write failed: %s", exc)

    return {"explanation": text, "cached": False}


def main() -> None:
    """Entry point: configure logging, init globals, run uvicorn."""
    global _priors, _engine_client

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    # Enable DEBUG specifically for the legality module so its `_reject` lines
    # land in /tmp/proxy.log without flooding everything else with DEBUG noise.
    # Cheap audit channel: counts + per-rule reasons for filtered-out modal sets.
    logging.getLogger("showdown_copilot.legality").setLevel(logging.DEBUG)

    _priors = PriorsSource()
    _engine_client = httpx.AsyncClient(timeout=120.0)

    logger.info("Plan H proxy starting on http://%s:%d", PROXY_HOST, PROXY_PORT)
    logger.info("forwarding to engine at %s", ENGINE_URL)
    logger.info("default format: %s   tracker LRU cap: %d", DEFAULT_FORMAT, MAX_TRACKERS)

    import uvicorn

    uvicorn.run(app, host=PROXY_HOST, port=PROXY_PORT, log_level="warning")


if __name__ == "__main__":
    main()
