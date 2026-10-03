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
            "problem": "Test investigation",
        }
    )

    logger.info("Investigation initialized: %s", result)


if __name__ == "__main__":
    main()