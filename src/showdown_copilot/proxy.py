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

import json
import logging
from collections import OrderedDict
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from showdown_copilot.belief import BeliefTracker
from showdown_copilot.priors import PriorsSource

logger = logging.getLogger(__name__)

# --- Config ---
ENGINE_URL = "http://localhost:7267"
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
# Hardcoded fallback chain: when the requested format has no cache locally
# and no chaos available on Smogon, try these in order. Strict-superset
# relationships per `project_chaos_cache_hack.md`. Keys are the format
# coming in from `b.tier`; values are formats to attempt instead.
_FORMAT_ALIASES: dict[str, list[str]] = {
    "gen9nationaldex": ["gen9nationaldexag"],
    "gen9nationaldexubers": ["gen9nationaldexag"],
    "gen9ag": ["gen9nationaldexag"],
}


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
    raw_fmt = meta.get("format") or DEFAULT_FORMAT
    # Showdown's `b.tier` is the display string ("[Gen 9] National Dex"), but
    # Smogon chaos URLs and our cache filenames use normalized form
    # ("gen9nationaldex"). Normalize via the same `_normalize` we use for
    # species; the rule (lowercase + alnum-only) is identical.
    fmt = _normalize(raw_fmt) or DEFAULT_FORMAT
    raw_reveals = meta.get("oppRevealedMoves") or {}
    # Re-normalize keys defensively in case the extension sent display-form names.
    revealed_moves = {_normalize(k): list(v) for k, v in raw_reveals.items()}

    if not battle_id:
        logger.warning("_planH missing battleId; skipping belief overlay")
        return req

    # Resolve format ONCE per request — fall back through aliases if the
    # requested format has no chaos cache, and remember a global failure
    # so we don't retry per-Pokemon.
    resolved_fmt = _resolve_format(fmt)

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


@app.post("/analyze/stream")
async def analyze_stream(req: Request) -> StreamingResponse:
    """Proxy endpoint: ingests BattleRequest, overlays belief, forwards to engine."""
    body = await req.json()
    body = apply_belief(body)
    assert _engine_client is not None

    async def relay():
        try:
            async with _engine_client.stream(
                "POST",
                f"{ENGINE_URL}/analyze/stream",
                json=body,
            ) as r:
                async for chunk in r.aiter_raw():
                    yield chunk
        except httpx.HTTPError as exc:
            logger.error("engine forward failed: %s", exc)
            err = {"event": "error", "message": f"proxy: engine unreachable ({exc})"}
            yield (json.dumps(err) + "\n").encode()

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


def main() -> None:
    """Entry point: configure logging, init globals, run uvicorn."""
    global _priors, _engine_client

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    _priors = PriorsSource()
    _engine_client = httpx.AsyncClient(timeout=120.0)

    logger.info("Plan H proxy starting on http://%s:%d", PROXY_HOST, PROXY_PORT)
    logger.info("forwarding to engine at %s", ENGINE_URL)
    logger.info("default format: %s   tracker LRU cap: %d", DEFAULT_FORMAT, MAX_TRACKERS)

    import uvicorn

    uvicorn.run(app, host=PROXY_HOST, port=PROXY_PORT, log_level="warning")


if __name__ == "__main__":
    main()
