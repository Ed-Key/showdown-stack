"""SimpleHeuristicsPlayer wrapper that logs every decision."""
import logging
from poke_env.player.baselines import SimpleHeuristicsPlayer
from poke_env.battle import Battle, Move, Pokemon
from poke_env.player.battle_order import SingleBattleOrder

logger = logging.getLogger("BattleLog")
logger.setLevel(logging.INFO)


class LoggingHeuristicsPlayer(SimpleHeuristicsPlayer):
    """Wraps SimpleHeuristicsPlayer to log every move decision."""

    def __init__(self, *args, log_file=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.battle_logs = []  # list of per-battle log entries
        self._current_battle_log = []

    def choose_move(self, battle):
        order = super().choose_move(battle)

        # Extract what was chosen
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        turn = battle.turn

        active_name = active.species if active else "?"
        active_hp = f"{active.current_hp_fraction*100:.0f}%" if active else "?"
        opp_name = opponent.species if opponent else "?"
        opp_hp = f"{opponent.current_hp_fraction*100:.0f}%" if opponent else "?"

        # What was the order?
        if hasattr(order, 'order'):
            if isinstance(order.order, Move):
                action = f"MOVE:{order.order.id}"
            elif isinstance(order.order, Pokemon):
                action = f"SWITCH:{order.order.species}"
            else:
                action = f"OTHER:{order.order}"
        else:
            action = "DEFAULT"

        # Available options
        moves = [f"{m.id}({m.base_power}bp)" for m in battle.available_moves] if battle.available_moves else []
        switches = [s.species for s in battle.available_switches] if battle.available_switches else []

        # Team state
        alive = [f"{m.species}({m.current_hp_fraction*100:.0f}%)" for m in battle.team.values() if not m.fainted]
        opp_alive = [f"{m.species}({m.current_hp_fraction*100:.0f}%)" for m in battle.opponent_team.values() if not m.fainted]
        opp_fainted = [m.species for m in battle.opponent_team.values() if m.fainted]

        entry = {
            "turn": turn,
            "active": active_name,
            "active_hp": active_hp,
            "opponent": opp_name,
            "opponent_hp": opp_hp,
            "action": action,
            "available_moves": moves,
            "available_switches": switches,
            "team_alive": alive,
            "opp_alive": opp_alive,
            "opp_fainted": opp_fainted,
        }
        self._current_battle_log.append(entry)

        return order

    def _battle_finished_callback(self, battle):
        """Called when a battle ends."""
        super()._battle_finished_callback(battle)
        if self._current_battle_log:
            won = battle.won if battle.won is not None else False
            self.battle_logs.append({
                "result": "WIN" if won else "LOSS",
                "turns": list(self._current_battle_log),
            })
            self._current_battle_log = []

    def get_loss_analysis(self, max_battles=20):
        """Analyze losses and return summary."""
        losses = [b for b in self.battle_logs if b["result"] == "LOSS"][:max_battles]
        wins = [b for b in self.battle_logs if b["result"] == "WIN"][:max_battles]

        analysis = []
        for b in losses:
            turns = b["turns"]
            if not turns:
                continue
            # What opponent Pokemon were still alive at end?
            last_turn = turns[-1]
            # What killed us? Track when our Pokemon died (HP went from >0 to not appearing)
            analysis.append({
                "final_turn": last_turn["turn"],
                "final_active": last_turn["active"],
                "final_opponent": last_turn["opponent"],
                "opp_remaining": last_turn["opp_alive"],
                "our_action_sequence": [t["action"] for t in turns[-5:]],  # last 5 moves
            })

        return {"total_losses": len([b for b in self.battle_logs if b["result"] == "LOSS"]),
                "total_wins": len([b for b in self.battle_logs if b["result"] == "WIN"]),
                "loss_samples": analysis}
