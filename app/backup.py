"""Bounded encrypted logical backups, authenticated validation and atomic restore.
Legacy plain gzip archives can be imported by an authenticated owner.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import secrets
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import delete, select, text

from . import db, security, service
from .config import settings, RUNTIME
from .db import BackupRecord, session_scope, utcnow

log = logging.getLogger("kanar.backup")

PART_SIZE = 18 * 1024 * 1024  # retrievable via getFile (20 MiB download cap)

# Tables (in FK-safe export order) included in every backup.
# Operational inbox and browser sessions are not historical business data.
# Keep queued receipts through restore; never resurrect old authenticated sessions.
EXCLUDED_TABLES = {"update_inbox", "web_sessions", "login_challenges"}
BACKUP_TABLES = [t.name for t in db.Base.metadata.sorted_tables if t.name not in EXCLUDED_TABLES]
MODEL_BY_TABLE = {t.name: t for t in db.Base.metadata.sorted_tables}


async def _export(session, compressed=False) -> bytes:
    """Stream rows to a bounded spool, no all-table Python object graph.

    Gzip output is generated incrementally; AES-GCM is bounded by backup_max_bytes.
    Export fails explicitly rather than OOMing or silently omitting any rows.
    """
    import tempfile
    def encode(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                          default=lambda x: x.isoformat()).encode()
    counts = {}
    for name in BACKUP_TABLES:
        counts[name] = (await session.execute(select(func_count(MODEL_BY_TABLE[name].c[next(iter(MODEL_BY_TABLE[name].primary_key.columns)).name])))).scalar_one()
    manifest = {"format":"kanar-db-backup", "version":1, "schema_version":"1.0.0",
        "created_at":utcnow().isoformat(), "table_order":BACKUP_TABLES, "row_counts":counts}
    with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as spool:
        stream = gzip.GzipFile(fileobj=spool, mode="wb", compresslevel=6) if compressed else spool
        expanded = 0
        def write(chunk):
            nonlocal expanded
            expanded += len(chunk)
            if expanded > settings.restore_expanded_max_bytes:
                raise ValueError("BACKUP_EXPANDED_LIMIT")
            stream.write(chunk)
            if spool.tell() > settings.backup_max_bytes:
                raise ValueError("BACKUP_SIZE_LIMIT")
        write(b'{"manifest":' + encode(manifest) + b',"data":{')
        for ti, name in enumerate(BACKUP_TABLES):
            write((b"," if ti else b"") + encode(name) + b":[")
            result = await session.stream(select(MODEL_BY_TABLE[name]).execution_options(yield_per=250))
            first = True
            try:
                async for rows in result.mappings().partitions(250):
                    # Only one batch of rows is live at once.
                    for row in rows:
                        write((b"" if first else b",") + encode(dict(row)))
                        first = False
            finally:
                await result.close()
            write(b"]")
        write(b"}}")
        if compressed:
            stream.close()
        if spool.tell() > settings.backup_max_bytes:
            raise ValueError("BACKUP_SIZE_LIMIT")
        spool.seek(0)
        return spool.read()


async def dump_database(session) -> bytes:
    return await _export(session)


def _coerce(table, row: dict) -> dict:
    """Parse ISO strings back into datetime columns."""
    import datetime as _dt
    out = {}
    for k, v in row.items():
        col = table.columns.get(k)
        if v is not None and col is not None and _is_dt(col.type):
            if isinstance(v, str):
                v = _dt.datetime.fromisoformat(v)
        out[k] = v
    return out


def _is_dt(type_) -> bool:
    import sqlalchemy as sa
    return isinstance(type_, (sa.DateTime, sa.types.DateTime))


def _selfref_columns(table) -> list[str]:
    """Columns whose FK points back at the SAME table (users.referred_by_id)."""
    return [fk.parent.name for fk in table.foreign_keys if fk.column.table is table]


async def _insert_rows(session, table, rows: list[dict]) -> None:
    """Insert rows with self-referential FK handling (v2.4.3).

    Real-Postgres incident 2026-11-15 05:27: users.referred_by_id -> users.id
    broke the bulk INSERT whenever a row referenced another row LATER in the
    file (FK checked immediately, no DEFERRABLE). Two-phase insert: first
    pass stores the self-ref columns as NULL, second pass UPDATEs the real
    values once every row of the table exists."""
    selfrefs = _selfref_columns(table)
    if not selfrefs or not rows:
        await session.execute(table.insert(), rows)
        return
    first = [{k: (None if k in selfrefs else v) for k, v in r.items()} for r in rows]
    await session.execute(table.insert(), first)
    from sqlalchemy import select as _sel
    pk = list(table.primary_key.columns)[0].name
    existing = {row[pk]: row for row in
                (await session.execute(_sel(table))).mappings().all()}
    for r in rows:
        key = r.get(pk)
        cur = existing.get(key)
        if cur is None or all(cur[c] == r.get(c) for c in selfrefs):
            continue
        await session.execute(
            table.update().where(table.c[pk] == key).values(
                **{c: r.get(c) for c in selfrefs}))


async def _restore_into(session, payload: dict) -> dict:
    """Wipe & load inside the caller's transaction; verify consistency.

    v2.4.3 fix: deletion MUST run in REVERSE dependency order (children
    before parents). The old manifest-order deletion started with `users`,
    so any current row still referencing a user (coin_transactions, chats,
    …) aborted the whole restore with a ForeignKeyViolationError on real
    Postgres (incident 2026-11-15 05:22) — the sqlite test suite never
    enforced FKs, which is why it slipped through. All metadata tables are
    wiped children-first (including tables absent from the archive), then
    rows load in manifest (parent-first) order."""
    order = payload.get("manifest", {}).get("table_order") or BACKUP_TABLES
    loaded: dict[str, int] = {}
    for table in reversed(db.Base.metadata.sorted_tables):
        if table.name == "update_inbox":
            continue
        await session.execute(delete(table))
    for tname in order:
        if tname in EXCLUDED_TABLES:
            continue
        table = MODEL_BY_TABLE.get(tname)
        if table is None:
            continue
        rows = payload.get("data", {}).get(tname, [])
        if rows:
            await _insert_rows(session, table, [_coerce(table, r) for r in rows])
        loaded[tname] = len(rows)
    # v2.4.3: rows are inserted with their EXPLICIT ids, which does NOT
    # advance the Postgres identity sequences — the first write after the
    # restore then collides with a restored id (UniqueViolationError on
    # security_events at the very next panel login, incident 05:36; and
    # processed_updates would make the dedup guard swallow EVERY update).
    # Re-sync every autoincrement sequence to MAX(id) after the load.
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        from sqlalchemy import text as _text
        for table in db.Base.metadata.sorted_tables:
            pk = list(table.primary_key.columns)[0].name
            seq = (await session.execute(_text(
                "SELECT pg_get_serial_sequence(:t, :c)"),
                {"t": table.name, "c": pk})).scalar()
            if not seq:
                continue
            mx = (await session.execute(_text(
                f"SELECT COALESCE(MAX({pk}), 0) FROM {table.name}"))).scalar() or 0
            if mx > 0:
                await session.execute(_text(f"SELECT setval('{seq}', {mx}, true)"))
            else:
                await session.execute(_text(f"SELECT setval('{seq}', 1, false)"))
    # ---- consistency checks ----
    problems = []
    users = {u["id"]: u for u in payload.get("data", {}).get("users", [])}
    for txn in payload.get("data", {}).get("coin_transactions", []):
        if txn.get("user_id") not in users:
            problems.append(f"orphan coin txn {txn.get('id')}")
    for chat in payload.get("data", {}).get("chats", []):
        if chat.get("user_a_id") not in users or chat.get("user_b_id") not in users:
            problems.append(f"orphan chat {chat.get('id')}")
    if problems:
        raise ValueError("CONSISTENCY_CHECK_FAILED: " + "; ".join(problems[:5]))
    return {"tables": len(loaded), "rows": sum(loaded.values())}


async def create_backup(session, trigger: str, created_by: str = "system") -> BackupRecord:
    if not settings.backup_password or len(settings.backup_password) < 16:
        raise ValueError("BACKUP_PASSWORD must contain at least 16 characters")
    stamp = utcnow().strftime("%Y%m%d_%H%M%S")
    rec = BackupRecord(id=f"BKP-{stamp}-{secrets.token_hex(3)}",
        filename=f"hamchat_db_{stamp}.json.gz.enc", trigger=trigger,
        schema_version="1.0.0", created_by=created_by)
    if session.bind.dialect.name == "postgresql" and not session.in_transaction():
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
    comp = await _export(session, compressed=True)
    blob, checksum = await asyncio.to_thread(security.encrypt_backup, comp, settings.backup_password)
    rec.encrypted, rec.cipher = True, "AES-256-GCM / scrypt"
    rec.size_bytes, rec.sha256 = len(blob), checksum
    session.add(rec)
    return rec, blob


async def deliver_backup(bot, rec: BackupRecord, blob: bytes, owner_id: int) -> bool:
    """Send the backup to the owner via the PRODUCT bot, chunked if needed.

    New files are AES-GCM encrypted. Keys are never placed in captions."""
    from .platform import PlatformError
    try:
        parts = (blob[i:i + PART_SIZE] for i in range(0, len(blob), PART_SIZE))
        sent_ids = []
        total = max(1, (len(blob) + PART_SIZE - 1) // PART_SIZE)
        for idx, part in enumerate(parts, 1):
            suffix = f".part{idx:02d}of{total:02d}" if total > 1 else ""
            fname = rec.filename + suffix
            msg = await bot.call("sendDocument", {
                "chat_id": owner_id, "caption": (
                    f"📦 پشتیبان پایگاه‌داده هم‌چت\nشناسه: <code>{rec.id}</code>\n"
                    f"حجم: {rec.size_bytes / 1e6:.2f} MB | SHA-256: <code>{rec.sha256[:16]}…</code>\n"
                    f"بخش {idx} از {total}\n"
                    "🔒 رمزگذاری‌شده با AES-256-GCM؛ کلید را جداگانه و امن نگهداری کنید."
                    + ("\n(برای بازیابی، همه بخش‌ها را به ترتیب فوروارد کن)" if total > 1 else "")
                    + "\n↩️ برای بازگردانی، همین فایل را با همین کپشن به ربات فوروارد کن."),
                "parse_mode": "HTML",
            }, files={"document": (fname, part)})
            sent_ids.append(msg.get("message_id"))
        rec.delivery_status = "delivered"
        rec.delivery_target = f"Telegram product bot -> {owner_id}"
        rec.parts = [{"index": i + 1, "message_id": mid} for i, mid in enumerate(sent_ids)]
        return True
    except PlatformError as exc:
        log.error("backup delivery failed: %s", exc)
        rec.delivery_status = "failed"
        rec.error_message = f"delivery failed: {exc}"
        return False


async def run_backup_job(bot, trigger: str, created_by: str = "system") -> Optional[BackupRecord]:
    if RUNTIME.get("backup_in_progress") or RUNTIME.get("restore_in_progress"):
        return None
    RUNTIME["backup_in_progress"] = True
    lock_conn = None
    try:
        if db.get_engine().dialect.name == "postgresql":
            lock_conn = await db.get_engine().connect()
            locked = (await lock_conn.execute(text("SELECT pg_try_advisory_lock(71389201)"))).scalar_one()
            if not locked:
                await lock_conn.close()
                lock_conn = None
                return None
        async with session_scope() as session:
            result = await create_backup(session, trigger, created_by)
            if isinstance(result, tuple):
                rec, blob = result
            else:
                return result
        ok = await deliver_backup(bot, rec, blob, settings.backup_owner_telegram_id)
        async with session_scope() as session:
            rec = await session.merge(rec)
            if not ok:
                await service.notify(session, "خطای ارسال پشتیبان",
                                     f"پشتیبان {rec.id} ساخته شد اما ارسال ناموفق بود.",
                                     "critical", "/backups")
            # retention of backup records (files live in Telegram chat)
            cfg = await service.get_setting(session, "backup")
            cutoff = utcnow() - timedelta(days=cfg.get("retentionDays", 90))
            old = await session.execute(select(BackupRecord).where(
                BackupRecord.created_at < cutoff, BackupRecord.id != rec.id))
            for r in old.scalars():
                await session.delete(r)
            today = service.tehran_day()
            cfg["lastRunDate"] = today
            await service.set_setting(session, "backup", cfg)
            return rec
    finally:
        RUNTIME["backup_in_progress"] = False
        if lock_conn is not None:
            try:
                await lock_conn.execute(text("SELECT pg_advisory_unlock(71389201)"))
            finally:
                await lock_conn.close()


# ---------------------------------------------------------------- restore ----
GZIP_MAGIC = b"\x1f\x8b"


def _decode_json(comp):
    import io
    with gzip.GzipFile(fileobj=io.BytesIO(comp)) as gz:
        raw = gz.read(settings.restore_expanded_max_bytes + 1)
    if len(raw) > settings.restore_expanded_max_bytes:
        raise ValueError("EXPANDED_SIZE_LIMIT")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise ValueError("BAD_MANIFEST")
    return payload


def _load_payload(blob: bytes, password: str) -> tuple[dict, bool]:
    """Auto-detect the archive format and return (payload, encrypted).

    Raises ValueError with a user-safe message on any failure."""
    if len(blob) > settings.restore_max_bytes:
        raise ValueError("ARCHIVE_SIZE_LIMIT")
    if blob[:5] == security.BACKUP_MAGIC:
        if not password:
            raise ValueError("ENCRYPTED_NO_PASSWORD")
        try:
            comp = security.decrypt_backup(blob, password)
        except ValueError as exc:
            raise ValueError(f"AUTH_FAILED: {exc}") from exc
        return _decode_json(comp), True
    if blob[:2] == GZIP_MAGIC:
        return _decode_json(blob), False
    raise ValueError("BAD_FORMAT: not a kanar backup archive")


def validate_blob(blob: bytes, expected_sha256: str = "", password: str = "") -> dict:
    """Manifest/format/checksum/encryption validation without touching the DB.

    Auto-detects AES-encrypted archives and legacy plaintext gzip."""
    result: dict[str, Any] = {"valid": False, "encrypted": True, "warnings": [],
                              "sha256": security.sha256_hex(blob), "checksumMatch": None,
                              "schemaVersion": "", "currentSchemaVersion": "1.0.0",
                              "compatible": False, "sizeMb": round(len(blob) / 1e6, 2)}
    if expected_sha256:
        result["checksumMatch"] = (expected_sha256 == result["sha256"])
    if blob[:5] == security.BACKUP_MAGIC:
        result["encrypted"] = True
        if not password:
            result["warnings"].append("این فایل رمزنگاری‌شده است؛ رمز بازیابی لازم است "
                                      "(کلید BACKUP_PASSWORD زمان تهیه پشتیبان لازم است).")
            return result
    elif blob[:2] == GZIP_MAGIC:
        result["encrypted"] = False
    else:
        result["warnings"].append("فرمت آرشیو ناشناخته است (نه gzip و نه KNRB1).")
        return result
    try:
        payload, _ = _load_payload(blob, password)
    except ValueError as exc:
        result["warnings"].append(f"باز کردن آرشیو ناموفق بود: {exc}")
        return result
    except Exception:
        result["warnings"].append("محتوای آرشیو معتبر نیست.")
        return result
    manifest = payload.get("manifest", {})
    if manifest.get("format") != "kanar-db-backup":
        result["warnings"].append("فرمت آرشیو ناشناخته است.")
        return result
    result["schemaVersion"] = str(manifest.get("schema_version", "?"))
    result["compatible"] = result["schemaVersion"] == result["currentSchemaVersion"]
    if not result["compatible"]:
        result["warnings"].append("نسخه اسکیمای آرشیو با نسخه فعلی متفاوت است.")
    result["valid"] = result["compatible"] and result["checksumMatch"] is not False
    result["warnings"].append("تمامی نشست‌های فعال در طول بازیابی قطع می‌شوند.")
    result["warnings"].append("پیش از شروع، یک اسنپ‌شات اضطراری برای بازگشت ایجاد می‌شود.")
    return result


async def execute_restore(blob: bytes, password: str = "") -> dict:
    if RUNTIME.get("restore_in_progress") or RUNTIME.get("backup_in_progress"):
        raise ValueError("RESTORE_BUSY")
    RUNTIME["restore_in_progress"] = True
    lock_conn = None
    t0 = asyncio.get_running_loop().time()
    try:
        if db.get_engine().dialect.name == "postgresql":
            lock_conn = await db.get_engine().connect()
            if not (await lock_conn.execute(text("SELECT pg_try_advisory_lock(71389201)"))).scalar_one():
                raise ValueError("RESTORE_BUSY")
        # Drain in-flight domain updates; the calling bot update is excluded.
        from .inbox import active_update
        own = int(active_update.get())
        for _ in range(100):
            if RUNTIME.get("inbox_active", 0) <= own and RUNTIME.get("admin_writes", 0) <= 1:
                break
            await asyncio.sleep(.1)
        else:
            raise ValueError("RESTORE_DRAIN_TIMEOUT")
        payload, _ = await asyncio.to_thread(_load_payload, blob, password)
        if payload.get("manifest", {}).get("schema_version") != "1.0.0":
            raise ValueError("INCOMPATIBLE_SCHEMA")
        async with session_scope() as s:
            rec, snapshot = await create_backup(s, "restore_rb", "system")
            emergency_id = rec.id
        bot = RUNTIME.get("adapter_telegram")
        if bot:
            if not await deliver_backup(bot, rec, snapshot, settings.backup_owner_telegram_id):
                raise ValueError("ROLLBACK_SNAPSHOT_DELIVERY_FAILED")
        elif settings.production:
            raise ValueError("ROLLBACK_SNAPSHOT_DELIVERY_UNAVAILABLE")
        snapshot_record = {column.name:getattr(rec,column.name) for column in db.BackupRecord.__table__.columns}
        del snapshot
        # Replacement is ONE transaction. Any failure rolls back the original DB.
        try:
            async with session_scope() as s:
                stats = await _restore_into(s, payload)
                cfg = await service.get_setting(s, "app")
                cfg["maintenanceMode"] = False
                await service.set_setting(s, "app", cfg)
                # No archived browser session/challenge is ever reactivated.
                await s.execute(delete(db.WebSession))
                await s.execute(delete(db.LoginChallenge))
                snapshot_record["delivery_status"] = rec.delivery_status if bot else "local"
                s.add(BackupRecord(**snapshot_record))
            service.invalidate_cache()
            return {"status":"succeeded", "rollbackSnapshotId":emergency_id,
                "maintenanceEnabled":False, "durationSec":round(asyncio.get_running_loop().time()-t0, 1),
                "message":f"بازیابی کامل شد: {stats['tables']} جدول، {stats['rows']} ردیف. برای امنیت دوباره وارد شوید."}
        except Exception:
            log.exception("restore transaction rolled back; original data retained")
            return {"status":"rolled_back", "rollbackSnapshotId":emergency_id,
                "maintenanceEnabled":False, "durationSec":round(asyncio.get_running_loop().time()-t0, 1),
                "message":"بازیابی ناموفق بود؛ تراکنش لغو شد و داده‌های قبلی حفظ شدند."}
    finally:
        RUNTIME["restore_in_progress"] = False
        if lock_conn is not None:
            try:
                await lock_conn.execute(text("SELECT pg_advisory_unlock(71389201)"))
            finally:
                await lock_conn.close()


def func_count(col):
    from sqlalchemy import func
    return func.count(col)


async def reassemble_parts(blobs: list[bytes], manifest_hint: str = "") -> bytes:
    if len(blobs) == 1:
        return blobs[0]
    return b"".join(blobs)
