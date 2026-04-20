"""CopilotSpectator — poke-env Player subclass forced into spectator mode."""
from __future__ import annotations

import logging

from poke_env.player import Player

logger = logging.getLogger(__name__)


class CopilotSpectator(Player):
    """Never submits moves. Joins battle rooms on demand and routes battle
    ownership to the user being coached rather than the bot's own account."""

    def __init__(self, *args, coaching_user: str, **kwargs):
        super().__init__(*args, **kwargs)
        self._coaching_user = coaching_user.lower()

    def choose_move(self, battle):
        """Spectators never submit moves. Returns a default order (no-op)."""
        return self.choose_default_move()

    async def _handle_battle_request(self, battle, **_):
        """Spectators never receive |request|. Defensive no-op in case we do."""
        return None

    def _patch_player_roles(self) -> None:
        """For each tracked battle, set _player_role so poke-env routes
        ownership correctly (p1/p2 points to the coached user)."""
        for battle_tag, battle in self._battles.items():
            if not battle._players:
                continue
            p1 = battle._players[0]["username"].lower()
            p2 = battle._players[1]["username"].lower() if len(battle._players) > 1 else ""
            if p1 == self._coaching_user:
                battle._player_role = "p1"
            elif p2 == self._coaching_user:
                battle._player_role = "p2"
            else:
                logger.warning(
                    "coaching_user=%r is neither player in %s (p1=%s, p2=%s)",
                    self._coaching_user, battle_tag, p1, p2,
                )

    def _handle_battle_message(self, split_messages):
        # Patch roles before poke-env dispatches any ownership-sensitive logic
        self._patch_player_roles()
        super()._handle_battle_message(split_messages)

    async def join_battle(self, room_id: str) -> None:
        """Ask Showdown to place us in a battle room as a spectator."""
        if not room_id.startswith("battle-"):
            raise ValueError(f"room_id must start with 'battle-', got {room_id!r}")
        await self.ps_client.send_message(f"/join {room_id}")
        logger.info("requested join to %s as spectator", room_id)
