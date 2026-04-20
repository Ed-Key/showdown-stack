"""Showdown Copilot CLI entry point."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys

from poke_env.ps_client import AccountConfiguration
from poke_env.ps_client.server_configuration import ShowdownServerConfiguration

from showdown_copilot.adapter_ext import SpectatorAdapter
from showdown_copilot.engine_client import EngineClient
from showdown_copilot.priors import PriorsSource
from showdown_copilot.spectator import CopilotSpectator
from showdown_copilot.tui import CopilotApp

logger = logging.getLogger(__name__)

ROOM_ID_RE = re.compile(r"battle-[a-z0-9]+-\d+")


def _format_from_room_id(room_id: str) -> str:
    # e.g., "battle-gen9monotype-2212345678" → "gen9monotype"
    parts = room_id.split("-")
    if len(parts) >= 3 and parts[0] == "battle":
        return parts[1]
    raise ValueError(f"cannot extract format from room_id: {room_id}")


def _prompt_multiline(prompt: str) -> str:
    print(prompt)
    print("(enter an empty line to finish)")
    lines: list[str] = []
    for line in sys.stdin:
        if line.strip() == "":
            break
        lines.append(line.rstrip("\n"))
    return "\n".join(lines)


def _prompt_room_id() -> str:
    raw = input("Battle URL or room ID: ").strip()
    m = ROOM_ID_RE.search(raw)
    if not m:
        raise SystemExit(f"cannot parse a battle room ID from: {raw!r}")
    return m.group(0)


async def _run_session(
    coaching_user: str,
    bot_username: str,
    bot_password: str,
    engine_url: str,
    team_type: str | None,
) -> None:
    # 1. Prompt for team (synchronous, at startup before the TUI opens)
    own_paste = _prompt_multiline("Paste your team (Pokepaste format):")
    if not own_paste.strip():
        raise SystemExit("no team pasted; aborting.")

    # 2. Prompt for battle room
    room_id = _prompt_room_id()
    fmt = _format_from_room_id(room_id)
    logger.info("session: room=%s format=%s coaching=%s", room_id, fmt, coaching_user)

    # 3. Build components
    priors = PriorsSource()
    adapter = SpectatorAdapter(
        own_paste=own_paste, format=fmt, team_type=team_type, priors=priors,
    )
    engine = EngineClient(base_url=engine_url)

    spec = CopilotSpectator(
        account_configuration=AccountConfiguration(bot_username, bot_password),
        battle_format=fmt,
        server_configuration=ShowdownServerConfiguration,
        coaching_user=coaching_user,
    )

    app = CopilotApp(bot_username=bot_username)

    async def _spectator_loop():
        """Runs alongside app.run_async() on the same event loop.

        MUST remain on the same loop — the TUI widgets are not thread-safe,
        so all app.push_* calls must happen from this coroutine, not from a
        thread executor.
        """
        await spec.join_battle(room_id)
        app.push_turn_event(f"joined {room_id} as spectator")
        last_turn_processed = -1
        while True:
            await asyncio.sleep(0.25)
            battle = next(iter(spec._battles.values()), None)
            if battle is None:
                continue
            if getattr(battle, "finished", False):
                app.push_turn_event(f"battle finished (winner={getattr(battle, 'won', '?')})")
                break
            cur_turn = getattr(battle, "turn", 0) or 0
            if cur_turn > last_turn_processed:
                app.push_turn_event(f"--- turn {cur_turn} ---")
                try:
                    state = adapter.to_engine_json(battle)
                    async for update in engine.stream_analyze(state, time_limit_ms=8000):
                        app.push_engine_update(update)
                        if update.is_final:
                            break
                    # Only mark turn processed on success — retry on next poll if anything raised
                    last_turn_processed = cur_turn
                except Exception as e:
                    app.push_turn_event(f"analysis error: {e}")
                    logger.exception("turn analysis failed (turn %s)", cur_turn)

    # Start TUI + spectator concurrently
    spec_task = asyncio.create_task(_spectator_loop())
    try:
        await app.run_async()
    finally:
        spec_task.cancel()
        try:
            await spec_task
        except asyncio.CancelledError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(prog="copilot")
    parser.add_argument("--coaching-user", required=True, help="Showdown username of the human player (e.g., Mariga)")
    parser.add_argument("--bot-username", default=os.environ.get("SC_BOT_USER", ""))
    parser.add_argument("--bot-password", default=os.environ.get("SC_BOT_PASS", ""))
    parser.add_argument("--engine-url", default="http://localhost:7267")
    parser.add_argument("--team-type", default=None, help="Only for Monotype (e.g., Fighting, Dark)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level.upper())
    if not args.bot_username or not args.bot_password:
        raise SystemExit("bot-username and bot-password required (or SC_BOT_USER / SC_BOT_PASS env vars)")

    asyncio.run(_run_session(
        coaching_user=args.coaching_user,
        bot_username=args.bot_username,
        bot_password=args.bot_password,
        engine_url=args.engine_url,
        team_type=args.team_type,
    ))


if __name__ == "__main__":
    main()
