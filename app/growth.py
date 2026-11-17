"""v2.4 growth & reliability engine.

Three responsibilities, all driven from the main scheduler loop:

1. rescue_incomplete_signups() — nudges users who abandoned the signup FSM.
   Cadence is configurable (growth.reminderHours = [6, 24, 72]); after the last
   stage the user is left alone. Rate limiting: at most growth.rescueMaxPerDay
   reminders per Tehran day, at most a small batch per scheduler tick, and the
   adapter-level throttling (25 msg/s + per-chat spacing) keeps us far below
   the official Telegram/Bale broadcast limits (~30 msg/s global).

2. Users who BLOCKED the bot (delivery fails with 403) go into an isolated
   list (users.bot_blocked = 1). They are excluded from broadcasts, stats
   targeting and rescue reminders; the moment they send the bot any message
   again, the flag clears and they return to the active population
   (bot.restore_from_blocklist on every incoming update).

3. check_alerts() — auto alerts to the owner's Telegram PV: polling down,
   webhook errors/pending backlog, scheduler crashes and failed backups.
   Each alert key is rate-limited to once per hour.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta

from sqlalchemy import func, select

from . import db, service
from .config import RUNTIME, settings
from .db import QueueEntry, User, utcnow
from .platform import PlatformError

log = logging.getLogger("kanar.growth")

WARN_AFTER_MIN = 15        # polling considered down after this many minutes
WEBHOOK_CHECK_SEC = 600    # webhook info probe interval
ALERT_COOLDOWN_SEC = 3600  # one alert per key per hour


# --------------------------------------------------------------- alerts ----
def _alert_ready(key: str) -> bool:
    last = (RUNTIME.setdefault("alert_last", {}) or {}).get(key, 0)
    return time.time() - last > ALERT_COOLDOWN_SEC


def _alert_sent(key: str) -> None:
    RUNTIME.setdefault("alert_last", {})[key] = time.time()


async def alert_owner(text: str, key: str = "") -> bool:
    """Send a reliability alert to the owner's Telegram PV (product bot)."""
    if key:
        if not _alert_ready(key):
            return False
        _alert_sent(key)
    bot = RUNTIME.get("adapter_telegram")
    if bot is None:
        log.warning("alert (no telegram adapter): %s", text)
        return False
    try:
        await bot.send_message(settings.backup_owner_telegram_id, text[:3800])
        return True
    except PlatformError as exc:
        log.error("alert delivery failed: %s", exc)
        return False


async def check_alerts() -> None:
    """Scheduler-driven reliability watchdog (runs every tick, cheap)."""
    from .i18n import tr
    now = time.time()
    health = RUNTIME.get("poll_health") or {}
    for plat in ("telegram", "bale"):
        adp = RUNTIME.get(f"adapter_{plat}")
        if not adp:
            continue
        if RUNTIME.get(f"webhook_set_{plat}"):
            # webhook mode — probe getWebhookInfo occasionally
            last = RUNTIME.get(f"webhook_checked_{plat}", 0)
            if now - last < WEBHOOK_CHECK_SEC:
                continue
            RUNTIME[f"webhook_checked_{plat}"] = now
            try:
                wh = await adp.get_webhook_info()
                pending = int(wh.get("pending_update_count") or 0)
                err = wh.get("last_error_message") or ""
                if pending > 50 or (err and pending > 0):
                    await alert_owner(tr("fa", "alert_webhook", platform=plat,
                                         detail=(err or "صف آپدیت بزرگ")[:200],
                                         pending=pending),
                                      key=f"webhook_{plat}")
            except Exception:
                pass  # probing failures themselves are not worth alerting
        else:
            # polling mode — the poll loop records its health
            h = health.get(plat) or {}
            last_ok = h.get("last_ok") or 0
            started = h.get("started_at") or now
            down_min = (now - max(last_ok, started)) / 60.0
            if down_min > WARN_AFTER_MIN:
                await alert_owner(tr("fa", "alert_poll_down", platform=plat,
                                     minutes=int(down_min),
                                     error=(h.get("last_error") or "—")[:200]),
                                  key=f"poll_{plat}")


async def report_scheduler_crash(exc: Exception) -> None:
    from .i18n import tr
    await alert_owner(tr("fa", "alert_scheduler", error=str(exc)[:300]),
                      key="scheduler_crash")


async def report_backup_failure(detail: str) -> None:
    from .i18n import tr
    await alert_owner(tr("fa", "alert_backup_fail", detail=detail[:300]),
                      key="backup_fail")


# ------------------------------------------------- bot-blocked isolation ----
BLOCK_MARKERS = ("blocked", "kicked", "deactivated", "chat not found", "forbidden")


async def mark_bot_blocked(user_id: int, platform: str, error: str) -> bool:
    """Isolate a user whose delivery failed with 'bot blocked' (403-class).

    Their row stays for bookkeeping but they are excluded from broadcasts,
    rescue reminders and active-user targeting until they message again."""
    if not any(m in error.lower() for m in BLOCK_MARKERS):
        return False
    async with db.session_scope() as s:
        u = await s.get(User, user_id)
        if u is None or u.bot_blocked:
            return False
        u.bot_blocked = True
    log.info("user %s isolated into bot-blocked list (%s): %s", user_id, platform, error[:120])
    return True


async def restore_from_blocklist(user_id: int) -> None:
    """User messaged the bot again — return them to the active population."""
    async with db.session_scope() as s:
        u = await s.get(User, user_id)
        if u is not None and u.bot_blocked:
            u.bot_blocked = False


# ------------------------------------------- signup rescue reminders ----
def _due(last: object, stage_hours: int) -> bool:
    if last is None:
        return True
    try:
        return utcnow() - last >= timedelta(hours=stage_hours)
    except TypeError:
        return True


async def rescue_incomplete_signups() -> dict:
    """Nudge incomplete signups on a 3-stage cadence, then leave them alone.

    - skips bot-blocked users (isolated list)
    - hard cap growth.rescueMaxPerDay per Tehran day (counter in settings)
    - small batch per call so the scheduler stays snappy
    """
    from . import bot as bot_mod
    from .i18n import tr
    sent = skipped = 0
    async with db.session_scope() as s:
        cfg = await service.get_setting(s, "growth")
        if not cfg.get("rescueEnabled", True):
            return {"sent": 0, "skipped": 0}
        stages = [int(h) for h in cfg.get("reminderHours", [6, 24, 72])]
        max_day = int(cfg.get("rescueMaxPerDay", 200))
        day = service.tehran_day()
        counter = await s.get(db.AppSetting, "_rescue_counter")
        if counter is None or (counter.value or {}).get("day") != day:
            used = 0
        else:
            used = int((counter.value or {}).get("count", 0))
        if used >= max_day:
            return {"sent": 0, "skipped": 0}
        budget = min(15, max_day - used)
        rows = (await s.execute(
            select(User).where(
                User.status == "active", User.age == 0,
                User.bot_blocked == False,                      # noqa: E712
                User.reminder_stage < len(stages),
                User.reminder_stage < 3)
            .order_by(User.id).limit(200))).scalars()
        targets = []
        for u in rows:
            hours = stages[u.reminder_stage]
            anchor = u.last_reminder_at or u.registered_at
            if _due(anchor, hours):
                targets.append(u)
            if len(targets) >= budget:
                break
        for u in targets:
            key = f"rescue_reminder_{min(u.reminder_stage + 1, 3)}"
            eco = await service.get_setting(s, "economy")
            text = tr(u.language, key)
            s.add(db.AdminNotification(  # panel trace of rescue activity
                title=f"یادآوری ثبت‌نام ← {u.public_id}",
                body=f"مرحله {u.reminder_stage + 1} ارسال شد.", severity="info",
                route="/users"))
            u.reminder_stage += 1
            u.last_reminder_at = utcnow()
            used += 1
            sent += 1
            adp = bot_mod.adapter(u.platform)
            if adp:
                try:
                    await adp.send_message(
                        u.platform_user_id, text,
                        keyboard=[[{"text": tr(u.language, "btn_search"),
                                    "callback_data": "resume_signup"}]])
                except PlatformError as exc:
                    if await mark_bot_blocked(u.id, u.platform, str(exc)):
                        skipped += 1
                    log.warning("rescue send failed %s: %s", u.public_id, exc)
        await service.set_setting(s, "_rescue_counter", {"day": day, "count": used})
    if sent:
        log.info("rescue reminders sent: %d (skipped %d)", sent, skipped)
    return {"sent": sent, "skipped": skipped}


# ------------------------------------------ first-match waiting nudge ----
async def notify_stale_queue_waiters() -> int:
    """Users waiting in the queue for 60+ seconds without a match get ONE
    encouraging message (with the referral pitch); they STAY in the queue."""
    from . import bot as bot_mod
    from .i18n import tr
    n = 0
    cutoff = utcnow() - timedelta(seconds=60)
    async with db.session_scope() as s:
        eco = await service.get_setting(s, "economy")
        rows = (await s.execute(
            select(QueueEntry).where(
                QueueEntry.created_at <= cutoff, QueueEntry.notified_at.is_(None))
            .limit(20))).scalars()
        for e in rows:
            u = await s.get(User, e.user_id)
            e.notified_at = utcnow()
            if not u or u.status != "active":
                continue
            adp = bot_mod.adapter(u.platform)
            if not adp:
                continue
            try:
                await adp.send_message(
                    u.platform_user_id,
                    tr(u.language, "search_still_waiting",
                       coins=eco["referralRewardPerParty"]),
                    keyboard=[[{"text": tr(u.language, "btn_stop_search"),
                                "callback_data": "stop_search"}]])
                n += 1
            except PlatformError as exc:
                log.warning("queue-wait notify failed %s: %s", u.public_id, exc)
    if n:
        log.info("queue-wait nudges sent: %d", n)
    return n
