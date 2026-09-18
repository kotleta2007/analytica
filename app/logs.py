"""Logging setup: the standard module, plus one filter that keeps
WEBHOOK_SECRET out of the log.

Both webhook URLs end in the secret, and it would otherwise appear in our
own lines (webhook set, webhook moved) and in uvicorn's access log for every
delivery. Logs get pasted into reviews and chats; the secret must not travel
with them.
"""

import logging
from collections.abc import Callable

from app.config import Settings

SHOWN_CHARS = 4


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not settings.webhook_secret:
        return
    redact = secret_redactor(settings.webhook_secret)
    for handler in logging.getLogger().handlers:
        handler.addFilter(redact)
    # uvicorn gives its access log its own handler and does not propagate.
    logging.getLogger("uvicorn.access").addFilter(redact)


def secret_redactor(secret: str) -> Callable[[logging.LogRecord], bool]:
    """A logging filter: the secret becomes its first few characters and an ellipsis."""
    shown = secret[:SHOWN_CHARS] + "…"

    def hide(value: object) -> object:
        return value.replace(secret, shown) if isinstance(value, str) else value

    def redact(record: logging.LogRecord) -> bool:
        record.msg = hide(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(hide(arg) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: hide(arg) for key, arg in record.args.items()}
        return True

    return redact
