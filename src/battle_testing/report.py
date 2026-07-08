"""Report generation: write battle results to timestamped output directory."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from battle_testing.telemetry import BatchResult


def write_results(
    result: BatchResult,
    base_dir: str,
    team_a_path: str,
    team_b_path: str,
) -> str:
    """Write batch results to a timestamped output directory.

    Creates:
        <base_dir>/<timestamp>/
            summary.json   — batch-level aggregate (no per-turn data)
            report.txt     — human-readable report
            battles/       — per-battle JSON files with full turn logs
            teams/         — copies of team paste files

    Returns the path to the created output directory.
    """
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    output_dir = Path(base_dir) / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- summary.json (Layer 3: batch aggregate, no per-turn data) ---
    summary_data = _build_summary_dict(result)
    (output_dir / "summary.json").write_text(
        json.dumps(summary_data, indent=2) + "\n"
    )

    # --- report.txt ---
    report_text = _format_report(result)
    (output_dir / "report.txt").write_text(report_text)

    # --- battles/ directory with per-battle JSON ---
    battles_dir = output_dir / "battles"
    battles_dir.mkdir(exist_ok=True)
    for summary in result.summaries:
        battle_data = _battle_summary_to_dict(summary)
        filename = f"{summary.battle_id}.json"
        (battles_dir / filename).write_text(
            json.dumps(battle_data, indent=2) + "\n"
        )

    # --- teams/ directory ---
    teams_dir = output_dir / "teams"
    teams_dir.mkdir(exist_ok=True)
    _copy_team_file(team_a_path, teams_dir / "team_a.txt")
    _copy_team_file(team_b_path, teams_dir / "team_b.txt")

    return str(output_dir)


def _build_summary_dict(result: BatchResult) -> dict:
    """Build the summary.json dict (batch aggregate without per-turn data)."""
    return {
        "total_battles": result.total_battles,
        "p1_wins": result.p1_wins,
        "p2_wins": result.p2_wins,
        "ties": result.ties,
        "win_rate_p1": round(result.win_rate_p1, 4),
        "win_rate_p2": round(result.win_rate_p2, 4),
        "ci_lower": round(result.ci_lower, 4),
        "ci_upper": round(result.ci_upper, 4),
        "avg_game_length": round(result.avg_game_length, 2),
        "avg_confidence_p1": round(result.avg_confidence_p1, 4),
        "avg_confidence_p2": round(result.avg_confidence_p2, 4),
        "avg_decision_time_p1": round(result.avg_decision_time_p1, 2),
        "avg_decision_time_p2": round(result.avg_decision_time_p2, 2),
        "low_confidence_rate_p1": round(result.low_confidence_rate_p1, 4),
        "low_confidence_rate_p2": round(result.low_confidence_rate_p2, 4),
        "p1_label": result.p1_label,
        "p2_label": result.p2_label,
    }


def _format_report(result: BatchResult) -> str:
    """Generate human-readable report text."""
    sep = "=" * 55
    thin_sep = "-" * 55
    lines = [
        sep,
        "  MCTS Battle Test Results",
        f"  Format: Gen 9 AG | Battles: {result.total_battles}",
        sep,
        f"  {result.p1_label:<24s} {result.p1_wins:>4d} wins ({result.win_rate_p1 * 100:.1f}%)",
        f"  {result.p2_label:<24s} {result.p2_wins:>4d} wins ({result.win_rate_p2 * 100:.1f}%)",
    ]
    if result.ties > 0:
        lines.append(f"  {'Ties':<24s} {result.ties:>4d}")
    lines += [
        thin_sep,
        f"  Avg turns/game:          {result.avg_game_length:.1f}",
        f"  Avg confidence (P1):     {result.avg_confidence_p1:.3f}",
        f"  Avg confidence (P2):     {result.avg_confidence_p2:.3f}",
        f"  Avg decision time (P1):  {result.avg_decision_time_p1:.1f} ms",
        f"  Avg decision time (P2):  {result.avg_decision_time_p2:.1f} ms",
        f"  Low-conf rate (P1):      {result.low_confidence_rate_p1 * 100:.1f}%",
        f"  Low-conf rate (P2):      {result.low_confidence_rate_p2 * 100:.1f}%",
        f"  95% CI for P1 win rate:  [{result.ci_lower * 100:.1f}%, {result.ci_upper * 100:.1f}%]",
        sep,
        "",
    ]
    return "\n".join(lines)


def _battle_summary_to_dict(summary) -> dict:
    """Convert a BattleSummary to a JSON-serializable dict with full turn data."""
    turns_p1 = [_turn_record_to_dict(t) for t in summary.turns_p1]
    turns_p2 = [_turn_record_to_dict(t) for t in summary.turns_p2]
    return {
        "battle_id": summary.battle_id,
        "winner": summary.winner,
        "total_turns": summary.total_turns,
        "p1_label": summary.p1_label,
        "p2_label": summary.p2_label,
        "avg_confidence_p1": summary.avg_confidence_p1,
        "avg_confidence_p2": summary.avg_confidence_p2,
        "avg_decision_time_p1": summary.avg_decision_time_p1,
        "avg_decision_time_p2": summary.avg_decision_time_p2,
        "move_frequency_p1": summary.move_frequency_p1,
        "move_frequency_p2": summary.move_frequency_p2,
        "low_confidence_turns_p1": summary.low_confidence_turns_p1,
        "low_confidence_turns_p2": summary.low_confidence_turns_p2,
        "turns_p1": turns_p1,
        "turns_p2": turns_p2,
    }


def _turn_record_to_dict(t) -> dict:
    """Convert a TurnRecord dataclass to a JSON-serializable dict."""
    return {
        "turn": t.turn,
        "best_move": t.best_move,
        "confidence": t.confidence,
        "simulations": t.simulations,
        "depth": t.depth,
        "time_ms": t.time_ms,
        "decision_time_ms": t.decision_time_ms,
        "reasoning": t.reasoning,
        "alternatives": t.alternatives,
        "active_pokemon": t.active_pokemon,
        "opponent_active_pokemon": t.opponent_active_pokemon,
        "active_hp_fraction": t.active_hp_fraction,
        "opponent_hp_fraction": t.opponent_hp_fraction,
    }


def _copy_team_file(src_path: str, dest_path: Path) -> None:
    """Copy a team file if it exists, otherwise write a placeholder."""
    src = Path(src_path)
    if src.exists():
        shutil.copy2(src, dest_path)
    else:
        dest_path.write_text(f"# Team file not found: {src_path}\n")
