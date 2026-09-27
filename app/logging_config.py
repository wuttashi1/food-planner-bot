import logging
from logging.handlers import RotatingFileHandler
import structlog


def configure(level="INFO"):
    handler = RotatingFileHandler("logs/app.jsonl", maxBytes=5_000_000, backupCount=3)
    logging.basicConfig(level=level, handlers=[logging.StreamHandler(), handler], format="%(message)s", force=True)
    # Never log HTTP request URLs, exceptions with token-bearing Bot API URLs, or update payloads.
    for name in ("aiohttp", "aiogram", "sqlalchemy.engine"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
