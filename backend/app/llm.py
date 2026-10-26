
# backend/app/llm.py
"""Centralized LLM Factory and Client Manager."""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Optional, Union

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 2500
DEFAULT_TIMEOUT_SECONDS = 25.0
DEFAULT_MAX_RETRIES = 2


def create_base_llm(
    *,
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    api_key: Optional[str] = None,
    timeout: Optional[Union[int, float]] = None,
    max_retries: Optional[int] = None,
) -> Any:
    """Create a ChatOpenRouter client, or return None if initialization fails."""

    key = api_key or os.getenv("OPENROUTER_API_KEY")

    if not key:
        logger.error(
            "LLM unavailable: OPENROUTER_API_KEY is not configured."
        )
        return None

    model_name = model or os.getenv(
        "OPENROUTER_MODEL", "openai/gpt-4o-mini"
    )

    try:
        effective_tokens = (
            max_tokens
            if max_tokens is not None
            else int(os.getenv("OPENROUTER_MAX_TOKENS", str(DEFAULT_MAX_TOKENS)))
        )
        if effective_tokens <= 0:
            logger.warning(
                "OPENROUTER_MAX_TOKENS must be positive; using %d.", DEFAULT_MAX_TOKENS
            )
            effective_tokens = DEFAULT_MAX_TOKENS
    except (ValueError, TypeError):
        logger.warning(
            "Invalid OPENROUTER_MAX_TOKENS; using %d.", DEFAULT_MAX_TOKENS
        )
        effective_tokens = DEFAULT_MAX_TOKENS

    try:
        raw_timeout = (
            timeout
            if timeout is not None
            else float(os.getenv("OPENROUTER_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS)))
        )
        timeout_val = float(raw_timeout)
        if timeout_val <= 0:
            logger.warning(
                "OPENROUTER_TIMEOUT must be positive; using %s seconds.",
                DEFAULT_TIMEOUT_SECONDS,
            )
            effective_timeout_ms = int(DEFAULT_TIMEOUT_SECONDS * 1000)
        elif timeout_val >= 500:
            # Value already expressed in milliseconds
            effective_timeout_ms = int(timeout_val)
        else:
            # Value expressed in seconds; convert to milliseconds for ChatOpenRouter
            effective_timeout_ms = int(timeout_val * 1000)
    except (ValueError, TypeError):
        logger.warning(
            "Invalid OPENROUTER_TIMEOUT; using %s seconds.",
            DEFAULT_TIMEOUT_SECONDS,
        )
        effective_timeout_ms = int(DEFAULT_TIMEOUT_SECONDS * 1000)

    try:
        effective_retries = (
            max_retries
            if max_retries is not None
            else int(os.getenv("OPENROUTER_MAX_RETRIES", str(DEFAULT_MAX_RETRIES)))
        )
        if effective_retries < 0:
            logger.warning(
                "OPENROUTER_MAX_RETRIES cannot be negative; using %d.",
                DEFAULT_MAX_RETRIES,
            )
            effective_retries = DEFAULT_MAX_RETRIES
    except (ValueError, TypeError):
        logger.warning(
            "Invalid OPENROUTER_MAX_RETRIES; using %d.", DEFAULT_MAX_RETRIES
        )
        effective_retries = DEFAULT_MAX_RETRIES

    try:
        from langchain_openrouter import ChatOpenRouter

        client = ChatOpenRouter(
            model=model_name,
            temperature=temperature,
            api_key=key,
            max_tokens=effective_tokens,
            timeout=effective_timeout_ms,
            max_retries=effective_retries,
        )

        logger.info(
            "LLM client initialized. Model: %s, max_tokens: %d, timeout_ms: %d, max_retries: %d",
            model_name,
            effective_tokens,
            effective_timeout_ms,
            effective_retries,
        )
        return client

    except Exception:
        logger.exception(
            "LLM client initialization failed. Model: %s", model_name
        )
        return None


def invoke_llm(llm: Any, prompt: Any) -> Any:
    """Invoke the LLM and log the real request error before re-raising."""

    try:
        return llm.invoke(prompt)

    except Exception as exc:
        # Do not log credentials or request headers.
        logger.error(
            "LLM request failed. Error type: %s | Details: %s",
            type(exc).__name__,
            str(exc),
            exc_info=True,
        )
        raise


def get_llm_factory(
    *,
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    timeout: Optional[Union[int, float]] = None,
    max_retries: Optional[int] = None,
) -> Callable[[], Any]:
    """Return a callable that creates a configured LLM client."""

    def _factory() -> Any:
        return create_base_llm(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            max_retries=max_retries,
        )

    return _factory

