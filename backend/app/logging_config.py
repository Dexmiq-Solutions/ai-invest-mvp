import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

from app.config import settings

# Regular expressions to detect and redact sensitive credentials
SENSITIVE_PATTERNS = [
    # Bearer tokens and JWTs
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.]{15,}\b"), "Bearer [REDACTED]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}(?:\.[A-Za-z0-9_\-]*)?\b"), "[REDACTED_JWT]"),
    # API keys (e.g. OpenAI, Anthropic, OpenRouter, AWS, Stripe)
    (re.compile(r"\bsk[_-][A-Za-z0-9_\-]{8,}\b"), "sk-[REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIA[REDACTED]"),
    # Key-value secret patterns: password=..., api_key=..., secret=...
    (
        re.compile(
            r"(?i)\b(password|passwd|secret|api[_-]?key|token|auth[_-]?token|access[_-]?token)"
            r"\s*([:=])\s*['\"]?[^\s'\",&]+['\"]?"
        ),
        r"\1\2[REDACTED]",
    ),
    # Authorization header lines
    (re.compile(r"(?i)(authorization\s*:\s*)[^\r\n]+"), r"\1[REDACTED]"),
]


def sanitize_log_text(text: Any, max_len: int = 500) -> str:
    """
    Sanitize text for safe logging by redacting secrets, API keys, tokens,
    and bounding maximum output length to avoid dumping entire files into logs.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)

    for pattern, replacement in SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)

    # Normalize excessive internal whitespace/newlines
    text = " ".join(text.split())

    if len(text) > max_len:
        return text[:max_len] + f"... [truncated, total {len(text)} chars]"
    return text


class SensitiveDataFilter(logging.Filter):
    """
    Logging filter that intercepts log records and redacts any sensitive
    tokens, authorization headers, or secrets before output formatting.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for pattern, replacement in SENSITIVE_PATTERNS:
                record.msg = pattern.sub(replacement, record.msg)

        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    sanitize_log_text(arg, max_len=1000) if isinstance(arg, str) else arg
                    for arg in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: (sanitize_log_text(v, max_len=1000) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }

        return True


def setup_logging(
    log_level: str | None = None,
    log_file: str | None = None,
    enable_file_logging: bool | None = None,
) -> None:
    """
    Centrally configure logging for the application.
    Avoids duplicate handlers and adds sensitive data redaction.
    """
    level_name = (log_level or settings.LOG_LEVEL or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Formatter with timestamp, level, logger name, and message
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    sensitive_filter = SensitiveDataFilter()

    # Clear existing handlers to prevent duplicate lines on multiple setups
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # Console Handler (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(sensitive_filter)
    root_logger.addHandler(console_handler)

    # File Handler (if configured or requested)
    should_log_file = (
        enable_file_logging
        if enable_file_logging is not None
        else settings.LOG_TO_FILE
    )
    target_file = log_file or settings.LOG_FILE

    if should_log_file and target_file:
        try:
            file_path = Path(target_file).resolve()
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(file_path, encoding="utf-8")
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            file_handler.addFilter(sensitive_filter)
            root_logger.addHandler(file_handler)
        except Exception as exc:
            root_logger.warning("Failed to initialize file logger at %s: %s", target_file, exc)