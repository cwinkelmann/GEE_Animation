"""A logging filter that cannot let a live credential reach a log record."""
from __future__ import annotations

import logging


def _redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        text = text.replace(secret, "***")
    return text


def _withhold_traceback(record: logging.LogRecord) -> None:
    had_traceback = bool(record.exc_info or record.exc_text)
    record.exc_info = None
    record.exc_text = "[traceback withheld]" if had_traceback else None
    record.stack_info = None


class RedactingFilter(logging.Filter):
    def __init__(self, secrets):
        super().__init__()
        self._secrets = secrets          # callable -> iterable of live secrets

    def filter(self, record: logging.LogRecord) -> bool:
        # A logging filter must never break the code that logged. Any failure
        # inside it falls back to a safe rendering and still lets the record through.
        try:
            secrets = [s for s in self._secrets() if s]
        except Exception:
            # Without the secret list nothing can be verified, so withhold the record.
            record.msg, record.args = "[log message withheld: redaction unavailable]", ()
            _withhold_traceback(record)
            return True
        try:
            self._redact_record(record, secrets)
        except Exception:
            record.msg, record.args = _redact(str(record.msg), secrets), ()
            _withhold_traceback(record)
        return True

    @staticmethod
    def _redact_record(record: logging.LogRecord, secrets: list[str]) -> None:
        text = record.getMessage()
        redacted = _redact(text, secrets)
        if redacted != text:
            record.msg, record.args = redacted, ()
        # Formatter.format() appends the traceback and stack after filters run,
        # so they are rendered here and redacted. exc_text is reused by later formatters.
        if record.exc_info:
            record.exc_text = _redact(
                logging.Formatter().formatException(record.exc_info), secrets
            )
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = _redact(record.exc_text, secrets)
        if record.stack_info:
            record.stack_info = _redact(record.stack_info, secrets)



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
