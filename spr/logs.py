"""Logging setup. Modules log through logging.getLogger(__name__)."""
import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO):
    logging.basicConfig(level=level, format=LOG_FORMAT, stream=sys.stdout)
    # Telethon logs every connection step at INFO
    logging.getLogger("telethon").setLevel(logging.WARNING)
    # Library warnings (e.g. Telethon's session warnings) go to the log too
    logging.captureWarnings(True)
