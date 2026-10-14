"""A logging filter that cannot let a live credential reach a log record."""
from __future__ import annotations

import logging


class RedactingFilter(logging.Filter):
    def __init__(self, secrets):
        super().__init__()
        self._secrets = secrets          # callable -> iterable of live secrets

    def filter(self, record: logging.LogRecord) -> bool:
        text = record.getMessage()
        redacted = text
        for secret in self._secrets():
            if secret and secret in redacted:
                redacted = redacted.replace(secret, "***")
        if redacted != text:
            record.msg, record.args = redacted, ()
        return True


def install(logger: logging.Logger, secrets) -> RedactingFilter:
    f = RedactingFilter(secrets)
    logger.addFilter(f)
    return f
