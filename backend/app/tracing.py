import logging
import os
from typing import Any, Dict, List

from app.config import settings

logger = logging.getLogger(__name__)


def is_tracing_enabled() -> bool:
    """Check if LangSmith tracing is enabled and configured."""
    return bool(settings.LANGCHAIN_TRACING_V2 and settings.LANGCHAIN_API_KEY)


def get_tracing_callbacks() -> List[Any]:
    """
    Safely obtain LangChain/LangSmith tracing callback handlers.
    Returns an empty list if tracing is disabled, unconfigured, or fails to initialize.
    Never logs, exposes, or persists API keys or secrets.
    """
    if not settings.LANGCHAIN_TRACING_V2:
        return []

    if not settings.LANGCHAIN_API_KEY:
        logger.warning(
            "LangSmith tracing is enabled (LANGCHAIN_TRACING_V2=true) but LANGCHAIN_API_KEY is missing. "
            "Tracing will remain inactive."
        )
        return []

    try:
        # Sync environment variables expected by LangChain internals
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = settings.LANGCHAIN_API_KEY
        project_name = settings.LANGCHAIN_PROJECT or "ai-software-investigation"
        os.environ["LANGCHAIN_PROJECT"] = project_name

        from langchain_core.tracers import LangChainTracer

        tracer = LangChainTracer(project_name=project_name)
        logger.info("LangSmith tracing initialized for project: %s", project_name)
        return [tracer]
    except Exception as exc:
        # Never log settings.LANGCHAIN_API_KEY
        logger.warning(
            "Failed to initialize LangSmith tracing (%s: %s). Investigation will continue without tracing.",
            type(exc).__name__,
            exc,
        )
        return []


def get_tracing_config() -> Dict[str, Any]:
    """
    Returns the LangGraph/LangChain execution config dictionary with callbacks,
    or an empty dictionary if tracing is inactive.
    """
    callbacks = get_tracing_callbacks()
    if callbacks:
        return {"callbacks": callbacks}
    return {}
