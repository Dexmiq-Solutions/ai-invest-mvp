"""Tests for LLM Client Hardening (Phase 1).

Covers:
- Default OPENROUTER_MAX_TOKENS = 2500
- Default OPENROUTER_TIMEOUT = 25s (converted to 25,000 ms for ChatOpenRouter)
- Default OPENROUTER_MAX_RETRIES = 2
- Environment variable overrides and fallbacks for invalid values
- Explicit arguments overriding environment variables
- Factory interface and backward compatibility
- Missing API key behavior (graceful None return)
- Failure logging and exception re-raising in invoke_llm
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from app.llm import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TIMEOUT_SECONDS,
    create_base_llm,
    get_llm_factory,
    invoke_llm,
)


def test_default_llm_configuration():
    """Verify default tokens (2500), timeout (25s -> 25000ms), and max_retries (2)."""
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key-123"}, clear=False):
        # Remove any override env vars if present in environment
        os.environ.pop("OPENROUTER_MAX_TOKENS", None)
        os.environ.pop("OPENROUTER_TIMEOUT", None)
        os.environ.pop("OPENROUTER_MAX_RETRIES", None)

        with patch("langchain_openrouter.ChatOpenRouter") as mock_chat:
            client = create_base_llm()
            assert client is not None
            mock_chat.assert_called_once()
            _, kwargs = mock_chat.call_args
            assert kwargs["max_tokens"] == 2500
            assert kwargs["timeout"] == 25000
            assert kwargs["max_retries"] == 2
            assert kwargs["api_key"] == "test-key-123"


def test_environment_variable_overrides():
    """Verify env vars override defaults."""
    env = {
        "OPENROUTER_API_KEY": "test-key-123",
        "OPENROUTER_MAX_TOKENS": "3500",
        "OPENROUTER_TIMEOUT": "45",
        "OPENROUTER_MAX_RETRIES": "1",
        "OPENROUTER_MODEL": "anthropic/claude-3-haiku",
    }
    with patch.dict(os.environ, env, clear=True):
        with patch("langchain_openrouter.ChatOpenRouter") as mock_chat:
            client = create_base_llm()
            assert client is not None
            _, kwargs = mock_chat.call_args
            assert kwargs["max_tokens"] == 3500
            assert kwargs["timeout"] == 45000
            assert kwargs["max_retries"] == 1
            assert kwargs["model"] == "anthropic/claude-3-haiku"


def test_invalid_env_vars_fallback_to_hardened_defaults():
    """Invalid or negative env vars fall back safely to hardened defaults with warnings."""
    env = {
        "OPENROUTER_API_KEY": "test-key-123",
        "OPENROUTER_MAX_TOKENS": "invalid_int",
        "OPENROUTER_TIMEOUT": "-10",
        "OPENROUTER_MAX_RETRIES": "xyz",
    }
    with patch.dict(os.environ, env, clear=True):
        with patch("langchain_openrouter.ChatOpenRouter") as mock_chat:
            client = create_base_llm()
            assert client is not None
            _, kwargs = mock_chat.call_args
            assert kwargs["max_tokens"] == DEFAULT_MAX_TOKENS
            assert kwargs["timeout"] == int(DEFAULT_TIMEOUT_SECONDS * 1000)
            assert kwargs["max_retries"] == DEFAULT_MAX_RETRIES


def test_explicit_arguments_override_env():
    """Explicit keyword arguments override environment variables and defaults."""
    env = {
        "OPENROUTER_API_KEY": "test-key-123",
        "OPENROUTER_MAX_TOKENS": "3000",
        "OPENROUTER_TIMEOUT": "50",
        "OPENROUTER_MAX_RETRIES": "3",
    }
    with patch.dict(os.environ, env, clear=True):
        with patch("langchain_openrouter.ChatOpenRouter") as mock_chat:
            # Explicit timeout in seconds
            create_base_llm(max_tokens=1000, timeout=10, max_retries=0)
            _, kwargs = mock_chat.call_args
            assert kwargs["max_tokens"] == 1000
            assert kwargs["timeout"] == 10000
            assert kwargs["max_retries"] == 0

        with patch("langchain_openrouter.ChatOpenRouter") as mock_chat_ms:
            # Explicit timeout already in milliseconds (>= 500)
            create_base_llm(timeout=15000)
            _, kwargs = mock_chat_ms.call_args
            assert kwargs["timeout"] == 15000


def test_missing_api_key_returns_none():
    """When OPENROUTER_API_KEY is missing/empty, returns None without raising."""
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}, clear=True):
        client = create_base_llm(api_key="")
        assert client is None


def test_client_init_exception_returns_none():
    """When ChatOpenRouter throws during init, returns None without raising."""
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "key"}, clear=False):
        with patch("langchain_openrouter.ChatOpenRouter", side_effect=RuntimeError("Provider init failed")):
            client = create_base_llm()
            assert client is None


def test_llm_factory_interface():
    """Verify get_llm_factory returns a callable producing configured clients."""
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}, clear=False):
        with patch("langchain_openrouter.ChatOpenRouter") as mock_chat:
            factory = get_llm_factory(model="custom-model", max_tokens=1800, timeout=12, max_retries=1)
            assert callable(factory)

            client = factory()
            assert client is not None
            _, kwargs = mock_chat.call_args
            assert kwargs["model"] == "custom-model"
            assert kwargs["max_tokens"] == 1800
            assert kwargs["timeout"] == 12000
            assert kwargs["max_retries"] == 1


def test_invoke_llm_success_and_error_handling():
    """Verify invoke_llm passes through success and re-raises on failure while logging."""
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = "response text"

    res = invoke_llm(mock_llm, "test prompt")
    assert res == "response text"

    mock_llm.invoke.side_effect = TimeoutError("Request timed out after 25s")
    with pytest.raises(TimeoutError, match="Request timed out after 25s"):
        invoke_llm(mock_llm, "test prompt")
