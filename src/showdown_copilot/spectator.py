"""CopilotSpectator — poke-env Player subclass forced into spectator mode."""
from __future__ import annotations

import logging
from typing import Callable

from poke_env.player import Player

from showdown_copilot.speed_inference_hooks import (
    derive_opp_moved_first,
    sniff_for_speed,
)

logger = logging.getLogger(__name__)


class CopilotSpectator(Player):
    """Never submits moves. Joins battle rooms on demand and routes battle
    ownership to the user being coached rather than the bot's own account."""

    def __init__(
        self,
        *args,
        coaching_user: str,
        speed_observer: Callable[[int, list[tuple[str, str, str, int]], list[str]], None] | None = None,
        **kwargs,
    ):
        """
        Args:
          coaching_user: Showdown username being coached (drives player_role
            patching).
          speed_observer: Phase 2 — optional callback invoked at each
            |turn|N+1| boundary with (just_finished_turn, move_log,
            skip_flags). The TUI host (copilot.py) wires this to its
            BeliefTracker via on_turn_boundary_speed. None = speed
            inference disabled.
        """
        super().__init__(*args, **kwargs)
        self._coaching_user = coaching_user.lower()
        # Phase 2 — per-turn speed buffers
        self._speed_observer = speed_observer
        self._turn_move_log: list[tuple[str, str, str, int]] = []
        self._turn_skip_flags: list[str] = []

    def choose_move(self, battle):  # required: Player declares this abstract
        raise NotImplementedError(
            "CopilotSpectator never submits moves; "
            "_handle_battle_request no-op should prevent this path"
        )

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

    async def _handle_battle_message(self, split_messages):
        # Patch before + after super — "before" covers the common case where
        # _players is already populated from earlier messages; "after" covers
        # the first batch of messages that introduces the `|player|` lines.
        self._patch_player_roles()

        # Phase 2 — sniff move-order + skip flags BEFORE super() mutates state.
        # On |turn|N+1|, fire the speed observer for turn N before delegating.
        # getattr defaults guard against tests that bypass __init__ via __new__.
        observer = getattr(self, "_speed_observer", None)
        if observer is not None:
            move_log = getattr(self, "_turn_move_log", None)
            skip_flags = getattr(self, "_turn_skip_flags", None)
            if move_log is None:
                move_log = []
                self._turn_move_log = move_log
            if skip_flags is None:
                skip_flags = []
                self._turn_skip_flags = skip_flags
            for split_message in split_messages:
                if len(split_message) < 2:
                    continue
                sniff_for_speed(split_message, move_log, skip_flags)
            for split_message in split_messages:
                if (
                    len(split_message) >= 3
                    and split_message[1] == "turn"
                ):
                    try:
                        new_turn = int(split_message[2])
                    except (ValueError, TypeError):
                        continue
                    observer(
                        new_turn - 1,
                        list(move_log),
                        list(skip_flags),
                    )
                    self._turn_move_log = []
                    self._turn_skip_flags = []
                    break

        await super()._handle_battle_message(split_messages)
        self._patch_player_roles()

    async def join_battle(self, room_id: str) -> None:
        """Ask Showdown to place us in a battle room as a spectator."""
        if not room_id.startswith("battle-"):
            raise ValueError(f"room_id must start with 'battle-', got {room_id!r}")
        await self.ps_client.send_message(f"/join {room_id}")
        logger.info("requested join to %s as spectator", room_id)
