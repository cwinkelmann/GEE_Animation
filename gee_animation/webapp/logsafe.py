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
    """Attach a redacting filter to ``logger`` and to every handler it has now.

    Logger filters run only for records logged directly on ``logger``; records
    from child loggers reach the handlers without passing through it. Handler
    filters run for every record that reaches the handler, so they catch the
    propagated ones. Handlers added after this call are not covered.
    """
    f = RedactingFilter(secrets)
    logger.addFilter(f)
    for handler in logger.handlers:
        handler.addFilter(f)
    return f
