"""Messages the bot sends on its own: reminders, the new-day line, reports.

The running adapter registers a hook at startup; scheduled jobs call
`send_to_user` and never learn which platform is on the other side. With no
adapter registered (the headless collector, a script) the message is logged
and dropped, and the caller is told.
"""

import logging
from collections.abc import Awaitable, Callable

from app.core.harness import log_message, utcnow
from app.core.replies import Reply, User
from app.db.engine import session_scope

logger = logging.getLogger(__name__)

SendHook = Callable[[User, Reply], Awaitable[None]]

_hook: SendHook | None = None


def register(hook: SendHook) -> None:
    global _hook
    _hook = hook


def unregister() -> None:
    global _hook
    _hook = None


async def send_to_user(user: User, reply: Reply) -> bool:
    """Deliver `reply` through the registered adapter; False if there is none."""
    if _hook is None:
        logger.warning("no adapter registered; dropping message to %s: %.80s", user.id, reply.text)
        return False
    await _hook(user, reply)
    async with session_scope() as session:
        await log_message(session, user.id, utcnow(), "out", "text", reply.text)
    return True
