import asyncio
import logging
import time
from datetime import datetime, timedelta
from .db import domain_lock
from .retention import run_retention
from .events import backfill
from .operations import process_operations
from .controllers import adapter_for, ControllerReadUnavailable
from .common import now, get_setting, set_setting, iso
from .events import store_real_event
from .models import Controller

logger = logging.getLogger(__name__)


def close_real(database, settings, adapter):
    adapter.session.close()
    live = list(adapter.session.live)
    with database.session() as db:
        domain_lock(db)
        for event in live:
            store_real_event(db, event, settings.hikvision_controller_id)
        set_setting(
            db,
            "hikvision:" + settings.hikvision_controller_id,
            adapter.session.metadata,
        )
        db.commit()
    adapter.session.acknowledge_live(len(live))


def collect_real(database, settings, adapter, collect=False, at=None):
    at = at or now()
    session = adapter.session
    controller_id = settings.hikvision_controller_id
    key = "backfill:HIKVISION_READ_ONLY:" + controller_id
    # Read checkpoint without keeping a DB/global workflow lock over SDK calls.
    with database.session() as db:
        if not db.get(Controller, controller_id):
            return
        checkpoint = get_setting(db, key)
    rows, complete, through, failure = [], False, None, None
    try:
        session.poll_live()
        if collect or session.reconnected:
            start = (
                datetime.fromisoformat(checkpoint["at"])
                if checkpoint.get("at")
                else at - timedelta(minutes=settings.hikvision_initial_minutes)
            )
            through = min(
                at, start + timedelta(minutes=settings.hikvision_history_window_minutes)
            )
            rows, complete = session.read_history(
                start - timedelta(minutes=2), through, settings.hikvision_event_limit
            )
            session.reconnected = False
    except ControllerReadUnavailable as exc:
        failure = str(exc)
        if failure not in {
            "AUTHENTICATION_STOPPED",
            "RECONNECT_WAIT",
            "CREDENTIALS_MISSING",
            "NATIVE_BRIDGE_MISSING",
            "SDK_PACKAGE_MISSING",
        }:
            session.close()
            session.next_attempt = time.monotonic() + session.backoff
            session.backoff = min(300, session.backoff * 2)
    live = list(session.live)
    with database.session() as db:
        domain_lock(db)
        for event in rows + live:
            store_real_event(db, event, controller_id)
        if complete and through:
            # Same transaction as all corresponding events. Failed/partial reads
            # never advance; live delivery never advances the history watermark.
            set_setting(db, key, {"at": iso(through)})
        state = {**session.metadata, "attemptedAt": iso(at)}
        if failure:
            state.update(
                state="BLOCKED"
                if failure
                in {
                    "CREDENTIALS_MISSING",
                    "NATIVE_BRIDGE_MISSING",
                    "SDK_PACKAGE_MISSING",
                }
                else "FAILED",
                code=failure,
            )
        set_setting(db, "hikvision:" + controller_id, state)
        db.commit()
    session.acknowledge_live(len(live))


def tick(database, settings, collect=False, at=None, adapter=None):
    adapter = adapter or adapter_for(settings)
    if settings.controller_mode == "HIKVISION_READ_ONLY":
        collect_real(database, settings, adapter, collect, at)
    with database.session() as db:
        domain_lock(db)
        run_retention(db, settings, at)
        if collect and settings.controller_mode == "SIMULATED":
            try:
                backfill(db, at, adapter)
            except ControllerReadUnavailable:
                # Retention continues; no fake events or checkpoint on failure.
                logger.warning(
                    "Controller event collection blocked; SDK transport unavailable."
                )
        db.commit()
        process_operations(db, settings, at)
        # Requests resumed by confirmed old removals produce new outbox rows.
        process_operations(db, settings, at)


async def worker(database, settings):
    adapter = adapter_for(settings)
    next_history = 0.0
    running = None
    try:
        while True:
            try:
                collect = time.monotonic() >= next_history
                if collect:
                    next_history = time.monotonic() + 120
                running = asyncio.create_task(
                    asyncio.to_thread(
                        tick, database, settings, collect, adapter=adapter
                    )
                )
                await asyncio.shield(running)
            except Exception:
                logger.error(
                    "Background job failed; durable pending records remain for retry."
                )
            await asyncio.sleep(2)
    finally:
        # Cancellation does not stop a native/DB thread. Finish its bounded work
        # before closing the SDK session or disposing the application database.
        if running and not running.done():
            try:
                await running
            except Exception:
                logger.error("Background job stopped with pending records retained.")
        if settings.controller_mode == "HIKVISION_READ_ONLY":
            await asyncio.to_thread(close_real, database, settings, adapter)
