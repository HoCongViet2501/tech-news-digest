"""Logging setup. httpx logs full request URLs at INFO, and Telegram URLs contain
the bot token, so HTTP client loggers are capped at WARNING."""

import logging


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for name in ("httpx", "httpcore", "httpx2", "httpcore2", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)
