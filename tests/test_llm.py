# tests/test_llm.py

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from showdown_copilot.llm import GroqClient, build_default_llm


@pytest.mark.asyncio
async def test_groq_client_calls_chat_completions():
    with patch("groq.AsyncGroq") as mock_groq_cls:
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="hello world"))]
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
        mock_groq_cls.return_value = mock_client

        c = GroqClient(api_key="fake-key")
        result = await c.complete("sys", "usr", max_tokens=100)
        assert result == "hello world"
        mock_client.chat.completions.create.assert_awaited_once()


def test_build_default_llm_returns_none_without_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert build_default_llm() is None


def test_build_default_llm_returns_groq_with_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    llm = build_default_llm()
    assert isinstance(llm, GroqClient)
