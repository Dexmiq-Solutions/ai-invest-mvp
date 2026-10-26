import logging
import time

from app.graph.graph import build_graph
from app.logging_config import setup_logging, sanitize_log_text
from app.tracing import get_tracing_config


def main() -> None:
    setup_logging()

    logger = logging.getLogger("app.main")
    logger.info("AI Software Investigation System starting up")

    start_time = time.time()
    graph = build_graph()

    input_payload = {
        "problem": "My profile page returns 401 after login.",
        "project_path": r"C:\Users\Dellj'\OneDrive\STUDY\DevTender.zip",
        "problem_context": {
            "expected_behavior": "Profile page should open after login.",
            "actual_behavior": "The profile page returns HTTP 401.",
        },
    }
    logger.info("Invoking investigation workflow for problem: %s", sanitize_log_text(input_payload["problem"]))

    try:
        tracing_config = get_tracing_config()
        if tracing_config:
            result = graph.invoke(input_payload, config=tracing_config)
        else:
            result = graph.invoke(input_payload)
        elapsed = time.time() - start_time
        status = result.get("execution_status") or result.get("current_step") or "unknown"
        findings_count = len(result.get("findings", []))
        logger.info(
            "Investigation workflow completed in %.2fs with status: %s (findings: %d)",
            elapsed,
            status,
            findings_count,
        )
    except Exception as exc:
        elapsed = time.time() - start_time
        logger.exception("Investigation workflow failed after %.2fs: %s", elapsed, exc)


if __name__ == "__main__":
    main()