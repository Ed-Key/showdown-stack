"""Async HTTP client for poke-engine's /analyze/stream endpoint."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger(__name__)


@dataclass
class EngineUpdate:
    best_move: str
    confidence: float
    sims: int
    depth: int
    pv: list[str] = field(default_factory=list)
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    message: str | None = None
    pimc_breakdown: list[dict[str, Any]] = field(default_factory=list)
    pimc_consensus: dict[str, Any] | None = None
    is_final: bool = False
    error: str | None = None

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> "EngineUpdate":
        event = obj.get("event")
        # Terminal events: "final" (normal completion) or "error" (engine-reported failure).
        # Both stop the stream — callers check .error to distinguish success from failure.
        return cls(
            best_move=obj.get("bestMove", ""),
            confidence=float(obj.get("confidence", 0.0)),
            sims=int(obj.get("sims", 0)),
            depth=int(obj.get("depth", 0)),
            pv=list(obj.get("pv", [])),
            alternatives=list(obj.get("alternatives", [])),
            message=obj.get("message") if isinstance(obj.get("message"), str) else None,
            pimc_breakdown=list(obj.get("pimcBreakdown", []))
            if isinstance(obj.get("pimcBreakdown"), list)
            else [],
            pimc_consensus=obj.get("pimcConsensus")
            if isinstance(obj.get("pimcConsensus"), dict)
            else None,
            is_final=(event in ("final", "error")),
            error=obj.get("message") if event == "error" else None,
        )


class EngineClient:
    def __init__(self, base_url: str = "http://localhost:7267", timeout: float = 60.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def stream_analyze(
        self,
        state: dict[str, Any],
        time_limit_ms: int = 5000,
        update_interval_ms: int = 250,
    ) -> AsyncIterator[EngineUpdate]:
        payload = {**state, "timeLimitMs": time_limit_ms, "updateIntervalMs": update_interval_ms}
        url = f"{self._base_url}/analyze/stream"
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            async with c.stream("POST", url, json=payload) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("skipping non-JSON line: %r", line)
                        continue
                    yield EngineUpdate.from_json(obj)
