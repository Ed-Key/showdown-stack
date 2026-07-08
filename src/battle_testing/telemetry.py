"""Telemetry data models for battle testing: BattleSummary and BatchResult."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from battle_testing.mcts_player import TurnRecord


@dataclass
class BattleSummary:
    """Summary of a single battle with per-player telemetry."""

    battle_id: str
    winner: str  # "p1", "p2", or "tie"
    total_turns: int
    p1_label: str
    p2_label: str
    avg_confidence_p1: float
    avg_confidence_p2: float
    avg_decision_time_p1: float
    avg_decision_time_p2: float
    move_frequency_p1: dict[str, int]
    move_frequency_p2: dict[str, int]
    low_confidence_turns_p1: int  # confidence < 0.3
    low_confidence_turns_p2: int
    turns_p1: list[TurnRecord]
    turns_p2: list[TurnRecord]

    @classmethod
    def from_turns(
        cls,
        battle_id: str,
        turns_p1: list[TurnRecord],
        turns_p2: list[TurnRecord],
        winner: str,
        total_turns: int,
        p1_label: str,
        p2_label: str,
    ) -> BattleSummary:
        """Compute a BattleSummary from raw turn records."""
        avg_conf_p1 = _avg([t.confidence for t in turns_p1])
        avg_conf_p2 = _avg([t.confidence for t in turns_p2])

        avg_time_p1 = _avg([t.decision_time_ms for t in turns_p1])
        avg_time_p2 = _avg([t.decision_time_ms for t in turns_p2])

        freq_p1 = dict(Counter(t.best_move for t in turns_p1))
        freq_p2 = dict(Counter(t.best_move for t in turns_p2))

        low_conf_p1 = sum(1 for t in turns_p1 if t.confidence < 0.3)
        low_conf_p2 = sum(1 for t in turns_p2 if t.confidence < 0.3)

        return cls(
            battle_id=battle_id,
            winner=winner,
            total_turns=total_turns,
            p1_label=p1_label,
            p2_label=p2_label,
            avg_confidence_p1=avg_conf_p1,
            avg_confidence_p2=avg_conf_p2,
            avg_decision_time_p1=avg_time_p1,
            avg_decision_time_p2=avg_time_p2,
            move_frequency_p1=freq_p1,
            move_frequency_p2=freq_p2,
            low_confidence_turns_p1=low_conf_p1,
            low_confidence_turns_p2=low_conf_p2,
            turns_p1=turns_p1,
            turns_p2=turns_p2,
        )


@dataclass
class BatchResult:
    """Aggregated results across a batch of battles."""

    total_battles: int
    p1_wins: int
    p2_wins: int
    ties: int
    win_rate_p1: float
    win_rate_p2: float
    ci_lower: float  # 95% CI lower bound for p1 win rate
    ci_upper: float  # 95% CI upper bound for p1 win rate
    avg_game_length: float
    avg_confidence_p1: float
    avg_confidence_p2: float
    avg_decision_time_p1: float
    avg_decision_time_p2: float
    low_confidence_rate_p1: float
    low_confidence_rate_p2: float
    p1_label: str
    p2_label: str
    summaries: list[BattleSummary]


def aggregate_batch(
    summaries: list[BattleSummary], p1_label: str, p2_label: str
) -> BatchResult:
    """Aggregate a list of BattleSummary into a BatchResult with 95% CI."""
    n = len(summaries)
    if n == 0:
        return BatchResult(
            total_battles=0,
            p1_wins=0,
            p2_wins=0,
            ties=0,
            win_rate_p1=0.0,
            win_rate_p2=0.0,
            ci_lower=0.0,
            ci_upper=0.0,
            avg_game_length=0.0,
            avg_confidence_p1=0.0,
            avg_confidence_p2=0.0,
            avg_decision_time_p1=0.0,
            avg_decision_time_p2=0.0,
            low_confidence_rate_p1=0.0,
            low_confidence_rate_p2=0.0,
            p1_label=p1_label,
            p2_label=p2_label,
            summaries=summaries,
        )

    p1_wins = sum(1 for s in summaries if s.winner == "p1")
    p2_wins = sum(1 for s in summaries if s.winner == "p2")
    ties = sum(1 for s in summaries if s.winner == "tie")

    # Win rate excludes ties: only count decisive games
    decisive = p1_wins + p2_wins
    p = p1_wins / decisive if decisive > 0 else 0.0

    # 95% CI using normal approximation: z=1.96, se=sqrt(p*(1-p)/n)
    # n for CI is decisive games only (ties excluded)
    se = math.sqrt(p * (1 - p) / decisive) if decisive > 0 else 0.0
    z = 1.96
    ci_lower = max(0.0, p - z * se)
    ci_upper = min(1.0, p + z * se)

    avg_game_length = _avg([s.total_turns for s in summaries])
    avg_conf_p1 = _avg([s.avg_confidence_p1 for s in summaries])
    avg_conf_p2 = _avg([s.avg_confidence_p2 for s in summaries])
    avg_time_p1 = _avg([s.avg_decision_time_p1 for s in summaries])
    avg_time_p2 = _avg([s.avg_decision_time_p2 for s in summaries])

    # Low confidence rate: total low-conf turns / total turns across all battles
    total_turns_p1 = sum(len(s.turns_p1) for s in summaries)
    total_turns_p2 = sum(len(s.turns_p2) for s in summaries)
    total_low_p1 = sum(s.low_confidence_turns_p1 for s in summaries)
    total_low_p2 = sum(s.low_confidence_turns_p2 for s in summaries)

    low_rate_p1 = total_low_p1 / total_turns_p1 if total_turns_p1 > 0 else 0.0
    low_rate_p2 = total_low_p2 / total_turns_p2 if total_turns_p2 > 0 else 0.0

    return BatchResult(
        total_battles=n,
        p1_wins=p1_wins,
        p2_wins=p2_wins,
        ties=ties,
        win_rate_p1=p,
        win_rate_p2=1.0 - p if decisive > 0 else 0.0,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        avg_game_length=avg_game_length,
        avg_confidence_p1=avg_conf_p1,
        avg_confidence_p2=avg_conf_p2,
        avg_decision_time_p1=avg_time_p1,
        avg_decision_time_p2=avg_time_p2,
        low_confidence_rate_p1=low_rate_p1,
        low_confidence_rate_p2=low_rate_p2,
        p1_label=p1_label,
        p2_label=p2_label,
        summaries=summaries,
    )


def _avg(values: list[float]) -> float:
    """Compute average, returning 0.0 for empty lists."""
    return sum(values) / len(values) if values else 0.0
