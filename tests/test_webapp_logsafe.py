# tests/test_webapp_logsafe.py
import io
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


def test_a_secret_logged_on_a_child_logger_is_redacted_before_the_parents_handler():
    # Own StringIO handler on the parent, not caplog: the assertion then reads
    # exactly what the parent's handler wrote, via the propagation path.
    parent = logging.getLogger("test.propagate")
    parent.setLevel(logging.INFO)
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    parent.addHandler(handler)
    try:
        install(parent, lambda: ["child-SECRET-VALUE"])
        logging.getLogger("test.propagate.child").info(
            "token is child-SECRET-VALUE"
        )
    finally:
        parent.removeHandler(handler)
    written = buffer.getvalue()
    assert "child-SECRET-VALUE" not in written
    assert "token is ***" in written


TRACEBACK_TOKEN = "exc-SECRET-VALUE"


def test_a_secret_inside_an_exception_traceback_is_redacted():
    # The secret is only in the exception message, never in log arguments, so
    # only the rendered traceback can leak it. The source line of the raise
    # shows no literal, so the output can only contain it via the message.
    # Assert on handler output.
    parent = logging.getLogger("test.traceback")
    parent.setLevel(logging.INFO)
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    parent.addHandler(handler)
    try:
        install(parent, lambda: [TRACEBACK_TOKEN])
        try:
            raise RuntimeError(f"EE refused token {TRACEBACK_TOKEN}")
        except RuntimeError:
            logging.getLogger("test.traceback.child").exception("render failed")
    finally:
        parent.removeHandler(handler)
    written = buffer.getvalue()
    assert "exc-SECRET-VALUE" not in written
    assert "RuntimeError: EE refused token ***" in written


def test_a_secret_in_the_stack_info_source_line_is_redacted():
    # stack_info prints the source line of the calling frame, so the secret
    # literal must be written on the call-site line to reach the stack text.
    # Locals are not printed, so the source line is the only path to test.
    parent = logging.getLogger("test.stack")
    parent.setLevel(logging.INFO)
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    parent.addHandler(handler)
    try:
        install(parent, lambda: ["stk-SECRET-VALUE"])
        logging.getLogger("test.stack.child").info("checking", stack_info=True); _ = "stk-SECRET-VALUE"
    finally:
        parent.removeHandler(handler)
    written = buffer.getvalue()
    assert "Stack (most recent call last)" in written
    assert "stk-SECRET-VALUE" not in written
    assert "***" in written


def test_messages_without_secrets_are_untouched(caplog):
    log = logging.getLogger("test.redact3")
    install(log, lambda: ["tok-ABC"])
    with caplog.at_level(logging.INFO, logger="test.redact3"):
        log.info("rendered 12 frames")
    assert "rendered 12 frames" in "\n".join(r.getMessage() for r in caplog.records)
