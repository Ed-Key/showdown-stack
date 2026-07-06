#!/usr/bin/env python3
"""Run the preview-plan agent against saved postmortem team previews.

Examples:
  python scripts/evaluate-preview-plans.py --limit 5 --run-mode fake
  python scripts/evaluate-preview-plans.py --run-mode auto --preset anthropic-haiku-45-balanced
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from showdown_copilot.preview_plan import (
    PreviewPlanRequest,
    PreviewPokemon,
    build_preview_plan,
    preview_plan_quality_checks,
)

DEFAULT_POSTMORTEM_DIR = Path(
    "/Users/edkiboma/Projects/pokemon-ai/workspace/analysis/battle-postmortems"
)


def _load_recent_postmortems(directory: Path, limit: int, min_schema: int) -> list[dict[str, Any]]:
    rows: list[tuple[float, dict[str, Any]]] = []
    for path in directory.glob("*.json"):
        try:
            stat = path.stat()
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if int(data.get("schemaVersion") or 0) < min_schema:
            continue
        preview = data.get("teamPreview")
        if not isinstance(preview, dict) or not preview.get("mine") or not preview.get("opp"):
            continue
        rows.append((stat.st_mtime, data))
    rows.sort(key=lambda item: item[0], reverse=True)
    return [data for _, data in rows[:limit]]


def _request_from_postmortem(pm: dict[str, Any], preset: str, run_mode: str) -> PreviewPlanRequest:
    preview = pm.get("teamPreview") or {}
    mine = [
        PreviewPokemon(species=str(species))
        for species in (preview.get("mine") or [])
        if species
    ]
    return PreviewPlanRequest(
        battleId=str(pm.get("battleId") or "preview"),
        format=str(pm.get("format") or "gen9nationaldex"),
        myTeam=mine,
        opponentTeam=[str(species) for species in (preview.get("opp") or []) if species],
        teamStats={
            "opponent": pm.get("opponent"),
            "winner": pm.get("winner"),
            "totalTurns": pm.get("totalTurns"),
            "note": "Offline evaluator uses preview only; exact own moves are omitted unless present in live data.",
        },
        presetId=preset,
        runMode=run_mode,  # type: ignore[arg-type]
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postmortem-dir", default=str(DEFAULT_POSTMORTEM_DIR))
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--min-schema", type=int, default=7)
    parser.add_argument("--preset", default="openai-gpt-54-mini-balanced")
    parser.add_argument("--run-mode", choices=["fake", "auto", "real"], default="fake")
    parser.add_argument("--battle", default=None,
                        help="Path to a single postmortem JSON to replay (overrides --limit scanning)")
    parser.add_argument("--grounding", choices=["on", "off"], default="on",
                        help="off sets SHOWDOWN_PREVIEW_DISABLE_GROUNDING=1 for this run")
    args = parser.parse_args()

    if args.grounding == "off":
        os.environ["SHOWDOWN_PREVIEW_DISABLE_GROUNDING"] = "1"
    else:
        os.environ.pop("SHOWDOWN_PREVIEW_DISABLE_GROUNDING", None)

    if args.battle:
        data = json.loads(Path(args.battle).read_text(encoding="utf-8"))
        postmortems = [data]
    else:
        postmortems = _load_recent_postmortems(Path(args.postmortem_dir), args.limit, args.min_schema)
    if not postmortems:
        print("No matching postmortems found.")
        return

    for pm in postmortems:
        req = _request_from_postmortem(pm, args.preset, args.run_mode)
        response = await build_preview_plan(req)
        checks = preview_plan_quality_checks(response.plan, req.opponentTeam)
        passed = sum(1 for item in checks if item.get("passed"))
        print("=" * 88)
        print(f"{pm.get('opponent') or 'unknown'} | {pm.get('winner') or 'unknown'} | {pm.get('battleId')}")
        print("Opponent:", " / ".join(req.opponentTeam))
        print(f"Source: {response.source} provider={response.provider} model={response.model or 'n/a'} latency={response.latencyMs}ms")
        print(f"Plan: {response.plan.archetype} ({response.plan.confidence})")
        print("Win path:", response.plan.winPath)
        sanitized = list(getattr(response, "sanitizedClaims", None) or [])
        if sanitized:
            print(f"Sanitized claims ({len(sanitized)}):")
            for message in sanitized:
                print(f"  - {message}")
        print("Lead:", response.plan.recommendedLead.pokemon, "-", response.plan.recommendedLead.reason)
        if response.plan.preserveTargets:
            print("Preserve:", "; ".join(f"{p.pokemon}: {p.reason}" for p in response.plan.preserveTargets))
        if response.plan.dangerRules:
            print("Danger:", "; ".join(rule.rule for rule in response.plan.dangerRules[:3]))
        if checks:
            print(f"Rubric: {passed}/{len(checks)} passed")
            for item in checks:
                marker = "PASS" if item.get("passed") else "MISS"
                print(f"  [{marker}] {item.get('name')}: {item.get('expected')}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
