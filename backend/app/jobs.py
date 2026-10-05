import asyncio
import logging
from .db import domain_lock
from .retention import run_retention
from .events import backfill
from .operations import process_operations

logger = logging.getLogger(__name__)


def tick(database, settings, collect=False, at=None):
    with database.session() as db:
        domain_lock(db)
        run_retention(db, settings, at)
        if collect:
            backfill(db, at)
        db.commit()
        process_operations(db, settings, at)
        # Requests resumed by confirmed old removals produce new outbox rows.
        process_operations(db, settings, at)


async def worker(database, settings):
    count = 0
    while True:
        try:
            await asyncio.to_thread(tick, database, settings, count % 60 == 0)
        except Exception:
            # Do not print SQL parameters, credentials or upload content.
            logger.error(
                "Background job failed; durable pending records will remain for retry."
            )
        count += 1
        await asyncio.sleep(2)
