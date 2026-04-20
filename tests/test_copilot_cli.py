"""Smoke tests for CLI room-ID parsing helpers."""
import pytest

from showdown_copilot.copilot import _format_from_room_id, ROOM_ID_RE


def test_format_from_room_id_monotype():
    assert _format_from_room_id("battle-gen9monotype-2212345678") == "gen9monotype"


def test_format_from_room_id_natdex_ou():
    assert _format_from_room_id("battle-gen9nationaldex-1234567") == "gen9nationaldex"


def test_format_from_room_id_raises_on_bad_shape():
    with pytest.raises(ValueError):
        _format_from_room_id("gen9monotype-2212345678")


def test_room_id_regex_matches_full_url():
    m = ROOM_ID_RE.search("https://play.pokemonshowdown.com/battle-gen9ou-1234567890")
    assert m is not None
    assert m.group(0) == "battle-gen9ou-1234567890"


def test_room_id_regex_matches_bare_id():
    m = ROOM_ID_RE.search("battle-gen9monotype-999")
    assert m is not None
    assert m.group(0) == "battle-gen9monotype-999"


def test_room_id_regex_rejects_non_battle_prefix():
    assert ROOM_ID_RE.search("lobby-gen9monotype-999") is None
