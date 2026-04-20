import json

import httpx
import pytest
from pytest_httpx import HTTPXMock

from showdown_copilot.engine_client import EngineClient, EngineUpdate


async def test_engine_client_yields_parsed_updates(httpx_mock: HTTPXMock):
    # Simulate a 3-line NDJSON stream from the engine
    ndjson_body = (
        json.dumps({"event": "update", "bestMove": "tackle", "confidence": 0.5, "sims": 100, "depth": 3, "pv": ["tackle"], "alternatives": []}) + "\n"
        + json.dumps({"event": "update", "bestMove": "tackle", "confidence": 0.7, "sims": 1000, "depth": 5, "pv": ["tackle", "switch:bulbasaur"], "alternatives": [{"move": "ember", "score": 0.3}]}) + "\n"
        + json.dumps({"event": "final",  "bestMove": "tackle", "confidence": 0.8, "sims": 5000, "depth": 8, "pv": ["tackle", "switch:bulbasaur", "ember"], "alternatives": []}) + "\n"
    )
    httpx_mock.add_response(
        url="http://localhost:7267/analyze/stream",
        content=ndjson_body.encode(),
    )

    client = EngineClient(base_url="http://localhost:7267")
    state = {"sideOne": {}, "sideTwo": {}}
    updates: list[EngineUpdate] = []
    async for u in client.stream_analyze(state, time_limit_ms=5000, update_interval_ms=250):
        updates.append(u)

    assert len(updates) == 3
    assert updates[0].best_move == "tackle"
    assert updates[0].confidence == 0.5
    assert updates[0].is_final is False
    assert updates[1].is_final is False
    assert updates[-1].is_final
    assert updates[-1].error is None
    assert updates[-1].sims == 5000


async def test_engine_client_handles_error_event_as_terminal(httpx_mock: HTTPXMock):
    body = (
        json.dumps({"event": "error", "message": "failed to parse battle state"}) + "\n"
    )
    httpx_mock.add_response(
        url="http://localhost:7267/analyze/stream",
        content=body.encode(),
    )
    client = EngineClient(base_url="http://localhost:7267")
    updates: list[EngineUpdate] = []
    async for u in client.stream_analyze({}, time_limit_ms=1000):
        updates.append(u)
    assert len(updates) == 1
    assert updates[0].is_final
    assert updates[0].error == "failed to parse battle state"


async def test_engine_client_skips_malformed_json_line(httpx_mock: HTTPXMock):
    body = (
        "not valid json\n"
        + json.dumps({"event": "final", "bestMove": "ember", "confidence": 0.6, "sims": 100, "depth": 4}) + "\n"
    )
    httpx_mock.add_response(
        url="http://localhost:7267/analyze/stream",
        content=body.encode(),
    )
    client = EngineClient(base_url="http://localhost:7267")
    updates: list[EngineUpdate] = []
    async for u in client.stream_analyze({}, time_limit_ms=1000):
        updates.append(u)
    # Bad line skipped, good line yielded
    assert len(updates) == 1
    assert updates[0].best_move == "ember"
    assert updates[0].is_final
