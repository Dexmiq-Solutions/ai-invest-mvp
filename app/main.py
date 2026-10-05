import logging

from app.graph.graph import build_graph
from app.logging_config import setup_logging


def main() -> None:
    setup_logging()

    logger = logging.getLogger(__name__)
    logger.info("AI Software Investigation System starting")

    graph = build_graph()

    result = graph.invoke(
        {
            "problem": "My profile page returns 401 after login.",
            "project_path": ".",
            "problem_context": {
                "expected_behavior": "Profile page should open after login.",
                "actual_behavior": "The profile page returns HTTP 401.",
            },
        }
    )

    logger.info("Investigation result: %s", result)


if __name__ == "__main__":
    main()