"""BattleRunner: orchestrates N battles between two poke-env Players with side-swapping."""

from __future__ import annotations

from poke_env.player import Player

from battle_testing.telemetry import BattleSummary, BatchResult, aggregate_batch


class BattleRunner:
    """Run a batch of battles between two players, alternating sides for fairness."""

    def __init__(self, p1: Player, p2: Player, n_battles: int = 200):
        self.p1 = p1
        self.p2 = p2
        self.n_battles = n_battles

    async def run_batch(self, p1_label: str = "p1", p2_label: str = "p2") -> BatchResult:
        """Run n_battles, alternating who is challenger, and return aggregated results."""
        summaries: list[BattleSummary] = []
        errors = 0

        for i in range(self.n_battles):
            print(f"\n{'='*50}", flush=True)
            print(f"BATTLE {i+1}/{self.n_battles}", flush=True)
            print(f"{'='*50}", flush=True)
            try:
                # Swap sides every other battle to eliminate P1 advantage
                if i % 2 == 0:
                    await self.p1.battle_against(self.p2, n_battles=1)
                else:
                    await self.p2.battle_against(self.p1, n_battles=1)

                # Determine winner from the most recent finished battle
                winner = self._detect_winner()
                print(f"[Battle {i+1}] Winner: {winner}", flush=True)

                # Collect turn records from players (if they support telemetry)
                turns_p1 = list(getattr(self.p1, "turn_records", []))
                turns_p2 = list(getattr(self.p2, "turn_records", []))

                # Determine total turns: max turn number from either player's records
                total_turns = 0
                if turns_p1:
                    total_turns = max(t.turn for t in turns_p1)
                elif turns_p2:
                    total_turns = max(t.turn for t in turns_p2)

                battle_id = f"battle-{i + 1:04d}"
                summary = BattleSummary.from_turns(
                    battle_id=battle_id,
                    turns_p1=turns_p1,
                    turns_p2=turns_p2,
                    winner=winner,
                    total_turns=total_turns,
                    p1_label=p1_label,
                    p2_label=p2_label,
                )
                summaries.append(summary)

                # Reset telemetry for next battle
                self._reset_telemetry()

            except Exception as e:
                errors += 1
                print(f"[BattleRunner] Battle {i + 1} error: {e}")
                self._reset_telemetry()
                continue

            # Progress report every 25 battles
            if (i + 1) % 25 == 0:
                wins = sum(1 for s in summaries if s.winner == "p1")
                ties = sum(1 for s in summaries if s.winner == "tie")
                total = len(summaries)
                decisive = total - ties
                rate = wins / decisive * 100 if decisive > 0 else 0.0
                print(
                    f"[{i + 1}/{self.n_battles}] P1 win rate: "
                    f"{wins}/{decisive} ({rate:.1f}%) | "
                    f"Ties: {ties} | Errors: {errors}"
                )

        return aggregate_batch(summaries, p1_label, p2_label)

    def _detect_winner(self) -> str:
        """Determine who won the most recent battle by checking finished battles.

        Returns "p1", "p2", or "tie".
        Raises RuntimeError if no finished battle is found.
        """
        # Check p1's most recent battle (sort numerically by battle tag suffix)
        if self.p1.battles:
            last_battle_tag = max(
                self.p1.battles.keys(),
                key=lambda t: int(t.rsplit("-", 1)[-1]),
            )
            battle = self.p1.battles[last_battle_tag]
            if battle.won is True:
                return "p1"
            elif battle.won is False:
                return "p2"
            elif battle.finished:
                # finished but won is None => tie
                return "tie"

        # Fallback: check p2's perspective
        if self.p2.battles:
            last_battle_tag = max(
                self.p2.battles.keys(),
                key=lambda t: int(t.rsplit("-", 1)[-1]),
            )
            battle = self.p2.battles[last_battle_tag]
            if battle.won is True:
                return "p2"
            elif battle.won is False:
                return "p1"
            elif battle.finished:
                return "tie"

        raise RuntimeError("No finished battle found to determine winner")

    def _reset_telemetry(self) -> None:
        """Reset telemetry on both players if they support it."""
        if hasattr(self.p1, "reset_telemetry"):
            self.p1.reset_telemetry()
        if hasattr(self.p2, "reset_telemetry"):
            self.p2.reset_telemetry()

    async def run_batch_concurrent(
        self, p1_label: str = "p1", p2_label: str = "p2"
    ) -> BatchResult:
        """Run n_battles concurrently. Requires max_concurrent_battles>1 on both players.

        Fires half with p1 challenging, half with p2 challenging, for fairness.
        Skips per-turn telemetry — only winners are recorded. Intended for fast
        multi-core sims where the cost of telemetry demux isn't worth it.
        """
        summaries: list[BattleSummary] = []
        half_a = self.n_battles // 2
        half_b = self.n_battles - half_a

        print(f"[concurrent] firing {half_a} p1→p2 + {half_b} p2→p1", flush=True)
        if half_a > 0:
            await self.p1.battle_against(self.p2, n_battles=half_a)
        if half_b > 0:
            await self.p2.battle_against(self.p1, n_battles=half_b)

        finished = [
            (tag, b) for tag, b in self.p1.battles.items() if b.finished
        ]
        # Sort deterministically
        finished.sort(key=lambda tb: int(tb[0].rsplit("-", 1)[-1]))

        for tag, battle in finished:
            if battle.won is True:
                winner = "p1"
            elif battle.won is False:
                winner = "p2"
            else:
                winner = "tie"
            summaries.append(
                BattleSummary.from_turns(
                    battle_id=tag,
                    turns_p1=[],
                    turns_p2=[],
                    winner=winner,
                    total_turns=0,
                    p1_label=p1_label,
                    p2_label=p2_label,
                )
            )

        print(f"[concurrent] collected {len(summaries)} finished battles", flush=True)
        return aggregate_batch(summaries, p1_label, p2_label)
