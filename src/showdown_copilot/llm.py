# src/showdown_copilot/llm.py

import os
import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    async def complete(self, system: str, user: str, max_tokens: int = 400) -> str: ...


class GroqClient:
    """LLM client backed by Groq. Free tier: 14,400 req/day for llama-3.3-70b-versatile.

    Personal use at ~300 calls/day uses ~2% of the free limit. If quality
    proves insufficient at Checkpoint 3.3a, swap this class for a different
    single-provider client — do not add a fallback layer.
    """

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile"):
        from groq import AsyncGroq
        self._client = AsyncGroq(api_key=api_key)
        self._model = model

    async def complete(self, system: str, user: str, max_tokens: int = 400) -> str:
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.3,
        )
        return resp.choices[0].message.content or ""


def build_default_llm() -> LLMClient | None:
    """Construct the default LLM from env vars."""
    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        logger.warning("GROQ_API_KEY not set; /explain endpoint will return 503")
        return None
    return GroqClient(groq_key)
