"""LLM explanation rendering — pure prompt-assembly helpers extracted from
proxy.py during Phase 4 polish.

This module contains the deterministic parts of the /explain pipeline:
* The system prompt that grounds the coach persona.
* The ExplainRequest schema.
* All `_fmt_*` / `_render_*` block helpers (one per prompt section).
* `build_explain_prompt`, which composes the sections into a single string.

Stateful pieces stay in proxy.py — the FastAPI route, the LRU cache, the
LLM client handle, and `_gather_belief` (which reads proxy globals). Those
need access to proxy module state and don't extract cleanly.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from showdown_copilot.belief import _normalize


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


def build_explain_prompt(req: ExplainRequest, belief_map: dict, fmt: str) -> str:
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
