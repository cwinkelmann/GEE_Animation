# tests/test_webapp_logsafe.py
import logging

from gee_animation.webapp.logsafe import RedactingFilter, install


def test_a_refresh_token_never_reaches_a_log_record(caplog):
    log = logging.getLogger("test.redact")
    install(log, lambda: ["refresh-SECRET-VALUE"])
    with caplog.at_level(logging.INFO, logger="test.redact"):
        log.info("initialising EE with %s for project p", "refresh-SECRET-VALUE")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "refresh-SECRET-VALUE" not in text
    assert "***" in text


def test_redaction_covers_the_exception_text_too(caplog):
    log = logging.getLogger("test.redact2")
    install(log, lambda: ["tok-ABC"])
    with caplog.at_level(logging.ERROR, logger="test.redact2"):
        log.error("failed: %s", RuntimeError("bad token tok-ABC"))
    assert "tok-ABC" not in "\n".join(r.getMessage() for r in caplog.records)


def test_messages_without_secrets_are_untouched(caplog):
    log = logging.getLogger("test.redact3")
    install(log, lambda: ["tok-ABC"])
    with caplog.at_level(logging.INFO, logger="test.redact3"):
        log.info("rendered 12 frames")
    assert "rendered 12 frames" in "\n".join(r.getMessage() for r in caplog.records)
