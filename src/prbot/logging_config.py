import logging
import re
from logging.handlers import RotatingFileHandler

from prbot.paths import log_dir


class Redactor(logging.Formatter):
    def format(self, record):
        text = super().format(record)
        return re.sub(r"[0-9]{5,20}:[A-Za-z0-9_-]{20,}", "[REDACTED]", text)


def configure(level):
    handler = RotatingFileHandler(
        log_dir() / "prbot.log", maxBytes=2_000_000, backupCount=4, encoding="utf-8"
    )
    handler.setFormatter(Redactor("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # HTTP request URLs contain credentials; never emit third-party request details.
    for name in ("httpx", "httpcore", "telegram", "keyring"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
