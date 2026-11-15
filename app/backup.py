"""Compressed database backup, Telegram delivery, restore & rollback engine.

v2.4.3 product change: the super-admin backup is COMPRESSED (gzip) but NOT
ENCRYPTED — restore needs zero credentials. The old encrypted flow failed in
practice twice (BACKUP_PASSWORD rotated between the backup and the restore,
and caption-password parsing is fragile), so a plain .json.gz is delivered.
Legacy AES-encrypted .enc archives remain fully restorable: validate/execute
auto-detect the format by magic bytes (KNRB1 = encrypted, gzip = plain)."""
from __future__ import annotations

import asyncio
import gzip
import io
import json
import logging
import secrets
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import delete, select, text

from . import db, security, service
from .db import BackupRecord, session_scope, utcnow

log = logging.getLogger("kanar.backup")

PART_SIZE = 40 * 1024 * 1024  # stay under Telegram's 50MB multipart cap

# Tables (in FK-safe export order) included in every backup.
BACKUP_TABLES = [
    "users", "queue_entries", "chats", "chat_messages", "blocks", "recent_pairs",
    "reports", "bans", "appeals", "coin_transactions", "payments", "store_packages",
    "referrals", "daily_claims", "app_settings", "media_types", "role_permissions",
    "bot_admins", "web_admins", "web_sessions", "login_challenges", "recovery_codes",
    "audit_logs", "security_events", "admin_notifications", "broadcasts",
    "support_tickets", "support_messages", "backups", "processed_updates",
    "violations", "moderation_dictionary", "moderation_allowlist",
]

MODEL_BY_TABLE = {t.name: t for t in db.Base.metadata.sorted_tables}


async def dump_database(session) -> bytes:
    """Full logical dump (all tables incl. chat text + financial records) as JSON."""
    data: dict[str, Any] = {}
    for tname in BACKUP_TABLES:
        table = MODEL_BY_TABLE.get(tname)
        if table is None:
            data[tname] = []
            continue
        rows = (await session.execute(select(table))).mappings().all()
        out = []
        for r in rows:
            row = {}
            for k, v in r.items():
                if isinstance(v, (db.datetime,)):
                    row[k] = v.isoformat()
                else:
                    row[k] = v
            out.append(row)
        data[tname] = out
    manifest = {
        "format": "kanar-db-backup", "version": 1,
        "schema_version": service.__dict__.get("SCHEMA_VERSION", "1.0.0")
        if hasattr(service, "SCHEMA_VERSION") else "1.0.0",
        "app": "kanar-anonymous-chat", "created_at": utcnow().isoformat(),
        "table_order": BACKUP_TABLES, "row_counts": {k: len(v) for k, v in data.items()},
    }
    blob = json.dumps({"manifest": manifest, "data": data}, ensure_ascii=False,
                      separators=(",", ":")).encode()
    return blob


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


async def _restore_into(session, payload: dict) -> dict:
    """Wipe & load inside the caller's transaction; verify consistency."""
    order = payload.get("manifest", {}).get("table_order") or BACKUP_TABLES
    loaded: dict[str, int] = {}
    for tname in order:
        table = MODEL_BY_TABLE.get(tname)
        if table is None:
            continue
        await session.execute(delete(table))
        rows = payload.get("data", {}).get(tname, [])
        if rows:
            await session.execute(table.insert(), [_coerce(table, r) for r in rows])
        loaded[tname] = len(rows)
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
    """Dump -> gzip (v2.4.3: no encryption) -> SHA-256 -> record."""
    import gzip
    stamp = utcnow().strftime("%Y%m%d_%H%M%S")
    bid = f"BKP-{stamp}-{secrets.token_hex(2)}"
    rec = BackupRecord(id=bid, filename=f"kanar_db_{stamp}.json.gz",
                       trigger=trigger, schema_version="1.0.0", created_by=created_by)
    raw = await dump_database(session)
    blob = gzip.compress(raw, compresslevel=6)
    rec.encrypted = False
    rec.cipher = "gzip only (no encryption)"
    rec.size_bytes = len(blob)
    rec.sha256 = security.sha256_hex(blob)
    session.add(rec)
    return rec, blob  # type: ignore[return-value]


async def deliver_backup(bot, rec: BackupRecord, blob: bytes, owner_id: int) -> bool:
    """Send the backup to the owner via the PRODUCT bot, chunked if needed.

    v2.4.3: the file is plain gzip — no restore password anymore. Forwarding
    the file back to the bot (caption intact) is all it takes to restore."""
    from .platform import PlatformError
    try:
        parts = [blob[i:i + PART_SIZE] for i in range(0, len(blob), PART_SIZE)] or [b""]
        sent_ids = []
        total = len(parts)
        for idx, part in enumerate(parts, 1):
            suffix = f".part{idx:02d}of{total:02d}" if total > 1 else ""
            fname = rec.filename + suffix
            msg = await bot.call("sendDocument", {
                "chat_id": owner_id, "caption": (
                    f"📦 پشتیبان پایگاه‌داده هم‌چت\nشناسه: <code>{rec.id}</code>\n"
                    f"حجم: {rec.size_bytes / 1e6:.2f} MB | SHA-256: <code>{rec.sha256[:16]}…</code>\n"
                    f"بخش {idx} از {total}\n"
                    "🔒 gzip بدون رمزنگاری — نیازی به رمز بازیابی نیست."
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
    """DB-locked backup job: prevents duplicates, safe across restarts."""
    async with session_scope() as session:
        lock = await session.execute(
            text("SELECT pg_try_advisory_lock(hashtext('kanar_backup'))") if False else
            select(db.AppSetting).where(db.AppSetting.key == "_backup_running"))
        flag = lock.scalar_one_or_none()
        if flag is not None and flag.value:  # stale lock older than 1h is cleared below
            started = flag.value.get("started_at")
            import datetime as _dt
            try:
                started_dt = _dt.datetime.fromisoformat(started)
            except Exception:
                started_dt = None
            if started_dt and utcnow() - started_dt < timedelta(hours=1):
                return None
        await service.set_setting(session, "_backup_running",
                                  {"started_at": utcnow().isoformat()})
        await session.commit()
    try:
        async with session_scope() as session:
            result = await create_backup(session, trigger, created_by)
            if isinstance(result, tuple):
                rec, blob = result
            else:
                return result
            from .config import settings
            ok = await deliver_backup(bot, rec, blob, settings.backup_owner_telegram_id)
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
        async with session_scope() as session:
            await service.set_setting(session, "_backup_running", None)


# ---------------------------------------------------------------- restore ----
GZIP_MAGIC = b"\x1f\x8b"


def _load_payload(blob: bytes, password: str) -> tuple[dict, bool]:
    """Auto-detect the archive format and return (payload, encrypted).

    Raises ValueError with a user-safe message on any failure."""
    if blob[:5] == security.BACKUP_MAGIC:
        if not password:
            raise ValueError("ENCRYPTED_NO_PASSWORD")
        try:
            comp = security.decrypt_backup(blob, password)
        except ValueError as exc:
            raise ValueError(f"AUTH_FAILED: {exc}") from exc
        return json.loads(gzip.decompress(comp)), True
    if blob[:2] == GZIP_MAGIC:
        return json.loads(gzip.decompress(blob)), False
    raise ValueError("BAD_FORMAT: not a kanar backup archive")


def validate_blob(blob: bytes, expected_sha256: str = "", password: str = "") -> dict:
    """Manifest/format/checksum/encryption validation without touching the DB.

    v2.4.3: auto-detects plain gzip (new) vs AES-encrypted (legacy)."""
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
                                      "(فایل‌های جدید gzip بدون رمز هستند).")
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
    result["valid"] = True
    result["warnings"].append("تمامی نشست‌های فعال در طول بازیابی قطع می‌شوند.")
    result["warnings"].append("پیش از شروع، یک اسنپ‌شات اضطراری برای بازگشت ایجاد می‌شود.")
    return result


async def execute_restore(blob: bytes, password: str = "") -> dict:
    """Emergency backup -> restore -> verify -> auto-rollback on failure.

    v2.4.3 fixes applied here:
    - accepts BOTH plain .json.gz (new) and legacy .enc archives;
    - the emergency rollback snapshot is plain gzip (no BACKUP_PASSWORD needed);
    - the snapshot record used trigger='restore_rollback_snapshot' (25 chars)
      which OVERFLOWED backups.trigger varchar(16) — every restore died with
      StringDataRightTruncationError before touching any data (incident
      2026-11-14 22:58 & 23:02). Short trigger + column widened to 32 at boot."""
    import gzip
    t0 = asyncio.get_event_loop().time()
    emergency_id = None
    try:
        payload, _enc = _load_payload(blob, password)
    except ValueError as exc:
        raise ValueError(f"AUTH_FAILED: {exc}") from exc

    # 1) emergency snapshot of the CURRENT database (plain gzip, not delivered)
    async with session_scope() as session:
        raw = await dump_database(session)
        import gzip as gz
        em_blob = gz.compress(raw)
        em_sha = security.sha256_hex(em_blob)
        emergency_id = f"RBK-{utcnow().strftime('%Y%m%d%H%M%S')}"
        session.add(BackupRecord(id=emergency_id, filename=f"emergency_{emergency_id}.json.gz",
                                 trigger="restore_rb", status="success",
                                 encrypted=False, cipher="gzip only (no encryption)",
                                 size_bytes=len(em_blob), sha256=em_sha,
                                 delivery_status="local", delivery_target="rollback snapshot"))
        # 2) maintenance ON before destructive replacement
        app_cfg = await service.get_setting(session, "app")
        app_cfg["maintenanceMode"] = True
        await service.set_setting(session, "app", app_cfg)

    try:
        async with session_scope() as session:
            stats = await _restore_into(session, payload)
        # 3) post-restore verification query
        async with session_scope() as session:
            n_users = (await session.execute(select(func_count(db.User.id)))).scalar()
        # 3.5) v2.1 FIX: turn maintenance back OFF on success — previously it stayed
        # ON forever ("until owner confirms") which froze the bot for everyone.
        async with session_scope() as session:
            app_cfg = await service.get_setting(session, "app")
            app_cfg["maintenanceMode"] = False
            await service.set_setting(session, "app", app_cfg)
        result = {"status": "succeeded", "rollbackSnapshotId": emergency_id,
                  "maintenanceEnabled": False,
                  "durationSec": round(asyncio.get_event_loop().time() - t0, 1),
                  "message": (f"بازیابی کامل شد: {stats['tables']} جدول، {stats['rows']} ردیف، "
                              f"{n_users} کاربر. اسنپ‌شات بازگشت: {emergency_id}. "
                              "حالت تعمیر پس از بازیابی موفق، خودکار خاموش شد.")}
        return result
    except Exception as exc:
        log.exception("restore failed, rolling back")
        # 4) rollback to emergency snapshot
        async with session_scope() as session:
            payload_back = {"manifest": {"table_order": BACKUP_TABLES, "schema_version": "1.0.0"},
                            "data": json.loads(gzip.decompress(em_blob))["data"]}
            await _restore_into(session, payload_back)
        return {"status": "rolled_back", "rollbackSnapshotId": emergency_id,
                "maintenanceEnabled": True,
                "durationSec": round(asyncio.get_event_loop().time() - t0, 1),
                "message": f"بازیابی ناموفق بود ({exc}) و به اسنپ‌شات اضطراری بازگشت شد."}


def func_count(col):
    from sqlalchemy import func
    return func.count(col)


async def reassemble_parts(blobs: list[bytes], manifest_hint: str = "") -> bytes:
    if len(blobs) == 1:
        return blobs[0]
    return b"".join(blobs)
