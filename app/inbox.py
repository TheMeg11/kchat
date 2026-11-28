"""Durable bounded update workers. ACK only after the inbox commit.

At-least-once delivery: domain idempotency protects money; remote Bot APIs do
not offer exactly-once send semantics. A crash after sending can repeat a reply.
"""
import asyncio
from contextvars import ContextVar
active_update = ContextVar("active_update", default=False)
import hashlib
import logging
from datetime import timedelta
from sqlalchemy import select, exists, or_
from sqlalchemy.orm import aliased
from sqlalchemy.exc import IntegrityError
from . import db
from .config import settings, RUNTIME

log = logging.getLogger("kanar.inbox")
_wakeups = {}

def wakeup(partition):
    return _wakeups.setdefault(partition, asyncio.Event())


def validate_update(payload):
    if not isinstance(payload, dict) or type(payload.get("update_id")) is not int:
        raise ValueError("invalid update_id")
    if not 0 <= payload["update_id"] < 2**63:
        raise ValueError("invalid update_id")
    for key in ("message", "edited_message", "callback_query", "pre_checkout_query"):
        if key in payload and not isinstance(payload[key], dict):
            raise ValueError("invalid update object")
        if key in payload:
            obj = payload[key]
            for field in ("from", "chat", "message", "successful_payment"):
                if field in obj and not isinstance(obj[field], dict):
                    raise ValueError("invalid nested object")
            for field, limit in (("text",4096),("caption",1024),("data",64)):
                if field in obj and (not isinstance(obj[field], str) or len(obj[field]) > limit):
                    raise ValueError("invalid text field")


async def enqueue(platform, payload):
    validate_update(payload)
    source = next((payload[k] for k in ("message", "edited_message", "callback_query", "pre_checkout_query") if payload.get(k)), {})
    actor = str((source.get("from") or {}).get("id") or (source.get("chat") or {}).get("id") or payload["update_id"])[:64]
    from .limits import message_limiter
    if not (source.get("successful_payment") or payload.get("pre_checkout_query")):
        if not message_limiter.allow(f"ingress:{platform}:{actor}", 120, 60):
            return
    partition = int.from_bytes(hashlib.sha256(f"{platform}:{actor}".encode()).digest()[:4], "big") % max(1, settings.update_workers)
    if payload.get("pre_checkout_query"):
        partition = max(1, settings.update_workers)  # dedicated fast checkout lane
    async with db.session_scope() as session:
        try:
            async with session.begin_nested():
                session.add(db.UpdateInbox(platform=platform, update_id=payload["update_id"],
                    actor=actor, partition=partition, payload=payload))
                await session.flush()
        except IntegrityError:
            existing = (await session.execute(select(db.UpdateInbox.id).where(
                db.UpdateInbox.platform == platform, db.UpdateInbox.update_id == payload["update_id"]))).scalar_one_or_none()
            if existing is None:
                raise
    wakeup(partition).set()


async def process_one(partition):
    from .bot import process_update
    if RUNTIME.get("restore_in_progress"):
        return False
    older = aliased(db.UpdateInbox)
    async with db.session_scope() as s:
        row = (await s.execute(select(db.UpdateInbox).where(
            db.UpdateInbox.partition == partition,
            db.UpdateInbox.status.in_(["queued", "processing"]),
            db.UpdateInbox.available_at <= db.utcnow(),
            ~exists(select(older.id).where(older.platform == db.UpdateInbox.platform,
                older.actor == db.UpdateInbox.actor, older.partition == db.UpdateInbox.partition, older.id < db.UpdateInbox.id,
                older.status.in_(["queued", "processing"]))))
            .order_by(db.UpdateInbox.id).with_for_update(skip_locked=True).limit(1))).scalar_one_or_none()
        if row is None:
            return False
        row.status = "processing"
        row.attempts += 1
        row.available_at = db.utcnow() + timedelta(seconds=120)
        rid, payload, platform, attempts = row.id, row.payload, row.platform, row.attempts
    RUNTIME["inbox_active"] = RUNTIME.get("inbox_active", 0) + 1
    context_token = active_update.set(True)
    try:
        await asyncio.wait_for(process_update(platform, payload), timeout=90)
        async with db.session_scope() as s:
            row = await s.get(db.UpdateInbox, rid)
            if row:
                row.status, row.payload, row.error = "done", {}, ""
    except asyncio.CancelledError:
        raise  # lease makes an unfinished item retryable after restart
    except Exception as exc:
        log.exception("update failed: inbox=%s attempt=%s", rid, attempts)
        async with db.session_scope() as s:
            row = await s.get(db.UpdateInbox, rid)
            if row:
                row.status = "failed" if attempts >= settings.update_max_attempts else "queued"
                row.error = type(exc).__name__[:64]  # no tokens/content in error field
                row.available_at = db.utcnow() + timedelta(seconds=min(2**attempts, 300))
                if row.status == "failed":
                    s.add(db.AdminNotification(title="خطای پردازش پیام", body=f"Inbox #{rid}: {row.error}", severity="critical", route="/"))
    finally:
        active_update.reset(context_token)
        RUNTIME["inbox_active"] -= 1
    return True


async def worker(partition):
    while True:
        event = wakeup(partition)
        event.clear()
        try:
            if await process_one(partition):
                continue
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("inbox worker unavailable")
        try:
            await asyncio.wait_for(event.wait(), 15)
        except asyncio.TimeoutError:
            pass
