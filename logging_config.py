import logging
import os

from dotenv import load_dotenv
from uvicorn.logging import AccessFormatter, DefaultFormatter

load_dotenv()


class _ColoredTimestamp:
    """Mixin: color %(asctime)s with the same ANSI color as the level prefix."""

    def formatMessage(self, record):
        if self.use_colors and hasattr(record, "asctime"):
            record.asctime = self.color_level_name(record.asctime, record.levelno)
        return super().formatMessage(record)


class ColoredTimestampFormatter(_ColoredTimestamp, DefaultFormatter):
    pass


class ColoredTimestampAccessFormatter(_ColoredTimestamp, AccessFormatter):
    pass


_configured = False


def get_logger(name: str = "CDS-NLP") -> logging.Logger:
    """Logger that renders with uvicorn's colourised level prefix.

    Named as a child of "uvicorn" so under uvicorn it propagates to uvicorn's
    colourised handler automatically. Outside uvicorn (pytest, scripts) we attach
    our own colourised StreamHandler once so lines still appear.

    Set LOG_LEVEL=DEBUG in .env to enable debug-level timing output.
    """
    global _configured
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    logger = logging.getLogger(f"uvicorn.{name}")
    logger.setLevel(level)
    if not _configured and not logging.getLogger("uvicorn").handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(DefaultFormatter("%(levelprefix)s %(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
        _configured = True
    return logger
