"""Take one collection round by hand.

    uv run python scripts/snapshot_now.py [--backfill] [--close-day] [--cron]

Mind the Habitica day: any call after `dayStart` triggers cron, exactly like
opening the app would. `--cron` triggers it on purpose.
"""

import argparse
import asyncio
import logging
from datetime import UTC, datetime

from app.collector.backfill import backfill
from app.collector.day_close import close_day, run_cron
from app.collector.snapshots import collect
from app.config import get_settings
from app.db.engine import session_scope
from app.habitica.client import client_from_settings


async def main(args: argparse.Namespace) -> None:
    logging.basicConfig(level="INFO", format="%(levelname)s %(name)s %(message)s")
    user_id = get_settings().habitica_user_id
    async with client_from_settings() as client, session_scope() as session:
        now = datetime.now(UTC)
        if args.close_day:
            await close_day(client, session, user_id, now)
        elif args.cron:
            await run_cron(client, session, user_id, now)
        else:
            outcome = await collect(client, session, user_id, now)
            print(
                f"hp={outcome.user.hp} mp={outcome.user.mp} gp={outcome.user.gp} "
                f"lvl={outcome.user.lvl}; {outcome.tasks.seen} tasks, "
                f"{outcome.tasks.changed} changed, {len(outcome.tasks.events)} events"
            )
        if args.backfill:
            result = await backfill(client, session, user_id)
            print(f"backfill: {len(result.records)} daily records, {len(result.events)} events")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true", help="import task history too")
    parser.add_argument("--close-day", action="store_true", help="run close_day instead")
    parser.add_argument("--cron", action="store_true", help="run cron on purpose instead")
    asyncio.run(main(parser.parse_args()))
