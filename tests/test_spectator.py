from unittest.mock import MagicMock, AsyncMock

import pytest

from showdown_copilot.spectator import CopilotSpectator


def make_mock_battle(p1_name: str, p2_name: str) -> MagicMock:
    """Build a minimal mock battle matching poke-env's internal shape."""
    b = MagicMock()
    b._players = [{"username": p1_name}, {"username": p2_name}]
    b._player_role = None
    return b


def test_player_role_override_when_coaching_p1():
    # Minimal instance — we only test the _patch_roles method, not full poke-env init
    spec = CopilotSpectator.__new__(CopilotSpectator)
    spec._coaching_user = "mariga"
    spec._battles = {"battle-gen9monotype-1": make_mock_battle("Mariga", "igboC")}

    spec._patch_player_roles()

    assert spec._battles["battle-gen9monotype-1"]._player_role == "p1"


def test_player_role_override_when_coaching_p2():
    spec = CopilotSpectator.__new__(CopilotSpectator)
    spec._coaching_user = "mariga"
    spec._battles = {"battle-1": make_mock_battle("SomeoneElse", "Mariga")}

    spec._patch_player_roles()

    assert spec._battles["battle-1"]._player_role == "p2"


def test_coaching_user_not_in_room_logs_warning(caplog):
    spec = CopilotSpectator.__new__(CopilotSpectator)
    spec._coaching_user = "mariga"
    spec._battles = {"battle-1": make_mock_battle("FooBar", "BarBaz")}

    with caplog.at_level("WARNING"):
        spec._patch_player_roles()

    assert any("Mariga" in rec.message or "mariga" in rec.message.lower() for rec in caplog.records)
