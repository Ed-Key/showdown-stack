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
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from showdown_copilot.belief import BeliefTracker, _BASE_SPEEDS, _normalize
from showdown_copilot.llm import LLMClient, build_default_llm
from showdown_copilot.models import Distributions
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

    # Race-condition guard (2026-05-08): soft-persist fires per turn as
    # fetch() promises. Network jitter can make an EARLIER persist's POST
    # land at the proxy AFTER a later persist's POST, overwriting more
    # complete data with less complete data. Skip overwrite if the
    # incoming pm has STRICTLY FEWER turn diffs than what's on disk.
    if existing_fname and out.exists():
        try:
            existing_pm = json.loads(out.read_text(encoding="utf-8"))
            existing_turns = len(existing_pm.get("turns") or [])
            new_turns = len(body.get("turns") or [])
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

# System prompt is intentionally strict about hallucination guards. The Groq
# llama-3.3-70b model will happily invent a 5th opp move if it thinks one is
# missing; the GROUNDING RULES below are the load-bearing safeguard. Do not
# soften these without re-running the manual smoke test in the plan.
SYSTEM_PROMPT = """You are a competitive Pokemon Showdown coach explaining engine recommendations to a 1500+ ELO player.

GROUNDING RULES (non-negotiable):
- Cite ONLY facts from the provided context. Never invent species, moves, items, or abilities not listed in the context.
- If a fact is missing or unknown, write "unknown" — DO NOT guess.
- Use exact species names from the "My Team" / "Opp Team" sections.
- Use exact move names from "revealed moves" / "modal moves" / engine PV.
- HP percentages must come from the context; do not estimate.

OUTPUT:
- 4-6 sentences in competitive vocabulary (e.g., "soft check", "force-out", "pivot", "Choice-locked").
- Walk through the engine's PV in plain English.
- If the engine's bestMove and the damage matrix disagree on safety (e.g., engine picks switch X but matrix shows X gets OHKO'd), explicitly flag the conflict.
- If the conflict is SEVERE (OHKO mismatch on the picked switch, immunity violation, or a revealed-move OHKO the engine seems unaware of) AND one of the listed "Engine alternatives" addresses the conflict, you MAY name that alternative and explain in one sentence why it's safer. Only point to moves explicitly listed in "Engine alternatives" — never invent moves.
- Default to describing the engine's bestMove. Do NOT second-guess the engine on close % differences (e.g., 38/36/34% alts) or marginal damage; only override on severe matrix-grounded conflicts.
- No filler ("careful play required", "the matchup is tricky") — be specific.
- No hedging ("in general", "depending on")."""


class ExplainRequest(BaseModel):
    """Schema for POST /explain. Extension assembles snapshot + engine_result
    + matrix_summary client-side; the proxy fills in belief + format from its
    own state. `last_steps` is a tail of stepQueue lines, ~6-12 entries."""

    battle_id: str
    turn: int
    rqid: int
    snapshot: dict
    engine_result: dict
    last_steps: list[str] = Field(default_factory=list)
    matrix_summary: dict | None = None


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


def _fmt_pct(value: Any, decimals: int = 0) -> str:
    """Render a numeric percentage as 'NN%' or '?' when missing/non-numeric.
    The snapshot may carry HP as either 0-100 or 0-1 depending on the source;
    we trust the extension's contract (0-100) and only sanity-check the type.
    """
    if isinstance(value, (int, float)):
        return f"{round(float(value), decimals):g}%" if decimals else f"{int(round(float(value)))}%"
    return "?"


def _fmt_boosts(boosts: dict[str, int] | None) -> str:
    """Render non-zero stat boosts as '+1 Atk, -2 SpD'. Returns '+0 all' when
    boosts is empty or all-zero — keeps the section visually consistent."""
    if not isinstance(boosts, dict):
        return "+0 all"
    nonzero = [(k, v) for k, v in boosts.items() if isinstance(v, int) and v != 0]
    if not nonzero:
        return "+0 all"
    label = {
        "atk": "Atk", "def": "Def", "spa": "SpA", "spd": "SpD",
        "spe": "Spe", "accuracy": "Acc", "evasion": "Eva",
    }
    parts = [f"{'+' if v > 0 else ''}{v} {label.get(k, k)}" for k, v in nonzero]
    return ", ".join(parts)


def _fmt_modal_list(items: list[dict] | None, limit: int = 4) -> str:
    """'Earthquake 98%, Swords Dance 61%, Scale Shot 45%'."""
    if not items:
        return "(none)"
    return ", ".join(
        f"{it.get('name', '?')} {it.get('pct', 0)}%"
        for it in items[:limit]
    )


def _render_field(snap: dict) -> str:
    """Weather + terrain + trick room one-liner."""
    weather = snap.get("weather") or {}
    terrain = snap.get("terrain") or {}
    if isinstance(weather, dict):
        wname = weather.get("weatherType", "none")
        wturns = weather.get("turnsRemaining", -1)
        w_str = f"{wname}" + (f" ({wturns} turns left)" if isinstance(wturns, int) and wturns > 0 else "")
    else:
        w_str = str(weather)
    if isinstance(terrain, dict):
        tname = terrain.get("terrainType", "none")
        tturns = terrain.get("turnsRemaining", -1)
        t_str = f"{tname}" + (f" ({tturns} turns left)" if isinstance(tturns, int) and tturns > 0 else "")
    else:
        t_str = str(terrain)
    tr = bool(snap.get("trickRoom") or snap.get("inTrickRoom"))
    return f"weather={w_str}, terrain={t_str}, trick_room={'true' if tr else 'false'}"


def _render_side_conditions(snap: dict) -> str:
    """Render side conditions for both sides as 'my: stealthrock, spikes(1)'."""
    def _one(side: dict | None, label: str) -> str | None:
        if not isinstance(side, dict):
            return None
        cond = side.get("sideConditions") or {}
        if not cond:
            return f"  {label}: (none)"
        parts = []
        for name, val in cond.items():
            if isinstance(val, dict):
                layers = val.get("layers")
                parts.append(f"{name}({layers})" if layers else name)
            elif isinstance(val, int) and val > 1:
                parts.append(f"{name}({val})")
            else:
                parts.append(name)
        return f"  {label}: {', '.join(parts)}"

    lines = []
    my_line = _one(snap.get("mine") or snap.get("my"), "my")
    opp_line = _one(snap.get("opp") or snap.get("opponent"), "opp")
    if my_line:
        lines.append(my_line)
    if opp_line:
        lines.append(opp_line)
    return "\n".join(lines) if lines else "  (none)"


def _render_active(snap_side: dict | None, label: str, belief: dict | None = None) -> str:
    """One block describing an active Pokemon. Pulls from snapshot first;
    backfills opponent unknowns from the belief dict (chaos modal)."""
    if not isinstance(snap_side, dict):
        return f"=== {label} ===\n(no data)"
    species = snap_side.get("activeSpecies") or snap_side.get("species") or "?"
    hp_pct = snap_side.get("activeHp") if "activeHp" in snap_side else snap_side.get("hp")
    level = snap_side.get("activeLevel") or snap_side.get("level") or "?"
    ability = snap_side.get("activeAbility") or snap_side.get("ability") or "?"
    item = snap_side.get("activeItem") or snap_side.get("item") or "?"
    boosts = snap_side.get("activeBoosts") or snap_side.get("boosts") or {}
    moves = snap_side.get("activeMoves") or snap_side.get("moves") or []
    speed = snap_side.get("activeSpeed") or snap_side.get("speed")

    lines = [f"=== {label} ==="]
    lines.append(f"{species} {_fmt_pct(hp_pct)} HP")
    lines.append(f"  level {level} · ability {ability} · item {item}")
    lines.append(f"  boosts: {_fmt_boosts(boosts) if isinstance(boosts, dict) else '+0 all'}")

    move_names = []
    if isinstance(moves, list):
        for m in moves:
            if isinstance(m, dict):
                name = m.get("name") or m.get("id")
                if name and name != "none":
                    move_names.append(name)
            elif isinstance(m, str) and m and m != "none":
                move_names.append(m)
    if move_names:
        lines.append(f"  moves: {', '.join(move_names)}")
    else:
        lines.append("  moves: unknown")

    # Belief overlay for opponent active: when revealed list is incomplete,
    # surface modal moves/item/ability so the LLM has chaos-grounded context.
    if belief:
        revealed = belief.get("revealed", {})
        modal = belief.get("modal", {})
        rev_moves = revealed.get("moves") or []
        if rev_moves:
            lines.append(f"  revealed moves: {', '.join(rev_moves)}")
        modal_moves = modal.get("moves") or []
        if modal_moves:
            lines.append(f"  modal moves (chaos): {_fmt_modal_list(modal_moves, limit=4)}")
        modal_items = modal.get("items") or []
        if modal_items and (item == "?" or item == "none" or item is None):
            lines.append(f"  modal item (chaos): {_fmt_modal_list(modal_items, limit=3)}")
        modal_abilities = modal.get("abilities") or []
        if modal_abilities and (ability == "?" or ability == "none" or ability is None):
            lines.append(f"  modal ability (chaos): {_fmt_modal_list(modal_abilities, limit=2)}")

    if isinstance(speed, (int, float)) and speed:
        lines.append(f"  speed: {int(speed)}")
    return "\n".join(lines)


def _render_team(snap_side: dict | None, label: str, opp_belief: dict | None = None) -> str:
    """Render a team list. Each entry: 'Species (active|bench, 80%, ability/item)'."""
    if not isinstance(snap_side, dict):
        return f"=== {label} ===\n(no data)"
    team = snap_side.get("team") or snap_side.get("pokemon") or []
    active_idx = snap_side.get("activeIndex")

    if not isinstance(team, list) or not team:
        return f"=== {label} ===\n(no data)"

    lines = [f"=== {label} ==="]
    for i, mon in enumerate(team):
        if not isinstance(mon, dict):
            continue
        species = mon.get("species") or mon.get("name") or "?"
        if species == "none" or not species:
            continue
        hp = mon.get("hp")
        maxhp = mon.get("maxhp") or 100
        if isinstance(hp, (int, float)) and isinstance(maxhp, (int, float)) and maxhp > 0:
            hp_pct = round(100 * hp / maxhp)
        else:
            hp_pct = mon.get("hpPct")
        fainted = bool(mon.get("fainted")) or hp_pct == 0
        slot = "active" if active_idx == i else ("FAINTED" if fainted else "bench")
        ability = mon.get("ability")
        item = mon.get("item")
        extras = []
        if ability and ability not in ("none", "?"):
            extras.append(str(ability))
        if item and item not in ("none", "?"):
            extras.append(str(item))
        extras_str = f", {'/'.join(extras)}" if extras else ""

        if fainted:
            lines.append(f"{species} (FAINTED)")
        elif isinstance(hp_pct, (int, float)):
            lines.append(f"{species} ({slot}, {int(hp_pct)}%{extras_str})")
        else:
            lines.append(f"{species} ({slot}{extras_str})")
    return "\n".join(lines)


def _render_matrix(matrix_summary: dict | None) -> str | None:
    """Render the damage matrix's top-K cells, both directions. Returns None
    when the extension didn't send a matrix (first-turn or feature off)."""
    if not isinstance(matrix_summary, dict):
        return None
    opp_attacks = matrix_summary.get("opp_attacks_me") or []
    me_attacks = matrix_summary.get("me_attacks_opp") or []
    if not opp_attacks and not me_attacks:
        return None

    lines = ["=== Damage Matrix (top threats) ==="]

    def _row(cell: dict, direction: str) -> str:
        attacker_key = "opp" if direction == "opp" else "me"
        attacker = cell.get(attacker_key, "?")
        move = cell.get("move", "?")
        target = cell.get("target", "?")
        dmg = cell.get("dmg_pct_max")
        source = cell.get("source", "?")
        ohko = cell.get("ohko")
        two_hko = cell.get("two_hko")
        tags = []
        if source:
            tags.append(source)
        if ohko:
            tags.append("OHKO")
        elif two_hko:
            tags.append("2HKO")
        tag_str = f" ({', '.join(tags)})" if tags else ""
        dmg_str = f"{dmg}%" if isinstance(dmg, (int, float)) else "?%"
        return f"  - {attacker} {move} → {dmg_str} on {target}{tag_str}"

    if opp_attacks:
        lines.append("Opp attacks me:")
        for cell in opp_attacks:
            if isinstance(cell, dict):
                lines.append(_row(cell, "opp"))
    if me_attacks:
        lines.append("Me attacks opp:")
        for cell in me_attacks:
            if isinstance(cell, dict):
                lines.append(_row(cell, "me"))
    return "\n".join(lines)


def _render_engine(er: dict) -> str:
    """Engine output section: bestMove + confidence + sims + depth + PV + alts."""
    best = er.get("bestMove") or er.get("best_move") or "?"
    conf = er.get("confidence")
    conf_str = f"{round(conf * 100)}%" if isinstance(conf, (int, float)) else "?"
    sims = er.get("sims") or er.get("simulations") or 0
    depth = er.get("depth", 0)
    pv = er.get("pv") or []
    if isinstance(pv, list):
        pv_str = " → ".join(str(x) for x in pv)[:600] or "(no PV)"
    else:
        pv_str = str(pv)[:600] or "(no PV)"
    alts = er.get("alternatives") or er.get("alts") or []
    alts_str = " | ".join(
        f"{a.get('move', '?')} {round(a.get('confidence', 0) * 100)}%"
        for a in alts[:3]
        if isinstance(a, dict)
    ) or "(none)"

    lines = [
        "=== Engine Output ===",
        f"bestMove: {best} ({conf_str} confidence, {sims} sims, depth {depth})",
        f"PV: {pv_str}",
        f"alts: {alts_str}",
    ]
    return "\n".join(lines)


def _render_log(steps: list[str]) -> str | None:
    """Render the recent battle-log tail. Caps at last 12 lines to bound prompt
    size; the extension should already be sending ~6-12."""
    if not steps:
        return None
    tail = [s for s in steps if isinstance(s, str)][-12:]
    if not tail:
        return None
    body = "\n".join(tail)
    return f"=== Recent Battle Log (last {len(tail)} lines) ===\n{body}"


def _build_explain_prompt(req: ExplainRequest, belief_map: dict, fmt: str) -> str:
    """Assemble the multi-section LLM prompt. Each section is rendered
    defensively from `req.snapshot` + augmented belief + matrix_summary; empty
    sections drop out entirely so we never emit '(no data)' lines wholesale.

    Section order is: context → field → side conditions → my active → opp
    active → my team → opp team → matrix → engine → log → task. The LLM gets
    facts before the question.
    """
    snap = req.snapshot or {}
    er = req.engine_result or {}

    sections: list[str] = []

    # --- Context header ---
    sections.append(
        "=== Battle Context ===\n"
        f"Format: {fmt or 'unknown'}\n"
        f"Turn: {req.turn}\n"
        f"Field: {_render_field(snap)}"
    )

    # Side conditions, only if at least one side has any.
    side_cond_block = _render_side_conditions(snap)
    if "(none)" not in side_cond_block or "my:" in side_cond_block:
        sections.append(f"Side conditions:\n{side_cond_block}")

    # --- Actives ---
    my_side = snap.get("mine") or snap.get("my") or {}
    opp_side = snap.get("opp") or snap.get("opponent") or {}
    opp_species_norm = _normalize(opp_side.get("activeSpecies") or opp_side.get("species") or "")
    opp_belief = belief_map.get(opp_species_norm) if opp_species_norm else None

    sections.append(_render_active(my_side, "My Active"))
    sections.append(_render_active(opp_side, "Opp Active", belief=opp_belief))

    # --- Teams ---
    sections.append(_render_team(my_side, "My Team"))
    sections.append(_render_team(opp_side, "Opp Team (visible from preview)"))

    # --- Matrix (skip when missing) ---
    matrix_block = _render_matrix(req.matrix_summary)
    if matrix_block:
        sections.append(matrix_block)

    # --- Engine ---
    sections.append(_render_engine(er))

    # --- Log (skip when missing) ---
    log_block = _render_log(req.last_steps)
    if log_block:
        sections.append(log_block)

    sections.append(
        "=== Task ===\n"
        "Explain why this turn matters in 4-6 sentences. Walk through the engine's "
        "PV in plain English. Flag any disagreement between the engine's pick and "
        "what the damage matrix shows."
    )

    return "\n\n".join(sections)


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
    user_prompt = _build_explain_prompt(req, belief_map, fmt)

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

    _priors = PriorsSource()
    _engine_client = httpx.AsyncClient(timeout=120.0)

    logger.info("Plan H proxy starting on http://%s:%d", PROXY_HOST, PROXY_PORT)
    logger.info("forwarding to engine at %s", ENGINE_URL)
    logger.info("default format: %s   tracker LRU cap: %d", DEFAULT_FORMAT, MAX_TRACKERS)

    import uvicorn

    uvicorn.run(app, host=PROXY_HOST, port=PROXY_PORT, log_level="warning")


if __name__ == "__main__":
    main()
