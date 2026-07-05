#!/usr/bin/env python3
"""Replay dashboard engine-eval cases against a running engine server."""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from showdown_copilot.dashboard import _engine_eval_case_archive  # noqa: E402
from showdown_copilot.engine_context import find_replay_record_for_turn, load_engine_replay_records  # noqa: E402


def _engine_action_name(action: Any) -> str:
    text = str(action or "")
    if ":" in text:
        return text.split(":", 1)[1].strip()
    return text.strip()


def _terminal_from_stream(text: str) -> dict[str, Any] | None:
    terminal = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            terminal = event
    return terminal


def _post_json(url: str, payload: dict[str, Any], timeout_s: float) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return response.status, response.read().decode("utf-8", errors="replace")


def _case_record(case: dict[str, Any]) -> dict[str, Any] | None:
    source = case.get("source") if isinstance(case.get("source"), dict) else {}
    position = case.get("positionSummary") if isinstance(case.get("positionSummary"), dict) else {}
    replay = case.get("replay") if isinstance(case.get("replay"), dict) else {}
    record_path = Path(str(replay.get("recordPath") or ""))
    battle_id = str(source.get("battleId") or "")
    if not battle_id or not record_path.exists():
        return None
    records = load_engine_replay_records(battle_id, record_path.parent)
    return find_replay_record_for_turn(
        records,
        source.get("turn"),
        _engine_action_name(position.get("engineAction")),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-url", default="http://127.0.0.1:7270")
    parser.add_argument("--min-schema-version", type=int, default=11)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--timeout-s", type=float, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    archive = _engine_eval_case_archive(min_schema_version=args.min_schema_version)
    cases = [case for case in archive["cases"] if (case.get("replay") or {}).get("available")]
    cases = cases[: max(0, args.limit)]
    print(f"cases={len(cases)} total={archive['summary']['totalCases']} engine={args.engine_url}")

    endpoint = args.engine_url.rstrip("/") + "/analyze/stream"
    failures = 0
    for idx, case in enumerate(cases, 1):
        source = case.get("source") or {}
        position = case.get("positionSummary") or {}
        priority = case.get("priority") or {}
        replay = case.get("replay") or {}
        captured = (replay.get("terminal") or {}).get("bestMove")
        record = _case_record(case)
        label = (
            f"#{idx} score={priority.get('score')} "
            f"{source.get('result')} vs {source.get('opponent')} T{source.get('turn')} "
            f"{position.get('engineAction')} -> {position.get('actualAction')}"
        )
        if args.dry_run:
            print(f"{label} | captured={captured}")
            continue
        if not record or not isinstance(record.get("engine_request"), dict):
            print(f"{label} | missing replay request")
            failures += 1
            continue
        started = time.time()
        try:
            status, text = _post_json(endpoint, record["engine_request"], args.timeout_s)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"{label} | ERROR {exc}")
            failures += 1
            continue
        terminal = _terminal_from_stream(text)
        elapsed_ms = round((time.time() - started) * 1000)
        new_best = terminal.get("bestMove") if isinstance(terminal, dict) else None
        new_conf = terminal.get("confidence") if isinstance(terminal, dict) else None
        changed = "changed" if new_best != captured else "same"
        print(
            f"{label} | http={status} {elapsed_ms}ms | "
            f"captured={captured} new={new_best} conf={new_conf} {changed}"
        )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
