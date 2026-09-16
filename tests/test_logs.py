"""WEBHOOK_SECRET never reaches a log line."""

import io
import logging

from app.logs import secret_redactor

SECRET = "19c85d22acb863e3e66940117720806a1236afa4c82e853d"
URL = f"https://bot.example.com/webhooks/telegram/{SECRET}"


def logger_writing_to(buffer: io.StringIO, name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def test_secret_in_an_argument_is_hidden() -> None:
    out = io.StringIO()
    logger = logger_writing_to(out, "test.redact.args")
    logger.addFilter(secret_redactor(SECRET))

    logger.info("telegram webhook set to %s", URL)

    assert SECRET not in out.getvalue()
    assert (
        out.getvalue()
        == "telegram webhook set to https://bot.example.com/webhooks/telegram/19c8…\n"
    )


def test_secret_in_the_message_itself_is_hidden() -> None:
    out = io.StringIO()
    logger = logger_writing_to(out, "test.redact.msg")
    logger.addFilter(secret_redactor(SECRET))

    logger.info(f"Habitica webhook w1 created for {URL}")

    assert SECRET not in out.getvalue()
    assert "19c8…" in out.getvalue()


def test_uvicorn_access_line_is_hidden_via_the_handler() -> None:
    # uvicorn logs the path as one of several %s arguments, through a handler
    # of its own; the filter sits on the handler there.
    out = io.StringIO()
    logger = logger_writing_to(out, "test.redact.access")
    logger.handlers[0].addFilter(secret_redactor(SECRET))

    path = f"/webhooks/habitica/{SECRET}"
    logger.info('%s - "%s %s HTTP/%s" %d', "172.18.0.1:44782", "POST", path, "1.1", 200)

    assert out.getvalue() == '172.18.0.1:44782 - "POST /webhooks/habitica/19c8… HTTP/1.1" 200\n'


def test_lines_without_the_secret_are_untouched() -> None:
    out = io.StringIO()
    logger = logger_writing_to(out, "test.redact.other")
    logger.addFilter(secret_redactor(SECRET))

    logger.info("habitica webhook %s: %d events", "taskActivity", 2)

    assert out.getvalue() == "habitica webhook taskActivity: 2 events\n"
