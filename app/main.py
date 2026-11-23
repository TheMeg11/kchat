"""Application entrypoint: FastAPI app, webhooks, schedulers, SPA hosting."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from sqlalchemy import select
from datetime import timedelta
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from . import backup as backup_mod
from . import db, security, service
from .config import RUNTIME, settings
from .db import (BroadcastCampaign, Chat, QueueEntry, User, WebAdmin,
                 session_scope, utcnow)
from .platform import BaleAdapter, PlatformError, TelegramAdapter

log = logging.getLogger("kanar.main")

# v2.4.3: asyncio only keeps WEAK references to tasks — a fire-and-forget
# create_task() result can be garbage-collected mid-execution (documented
# asyncio pitfall: "save a reference to the result of this function"). All
# background work goes through spawn(): the strong reference set keeps the
# task alive until it finishes and any uncaught crash is logged instead of
# disappearing silently.
_BG_TASKS: set[asyncio.Task] = set()


def _bg_done(task: asyncio.Task) -> None:
    _BG_TASKS.discard(task)
    if not task.cancelled() and task.exception() is not None:
        log.error("background task crashed: %r", task.exception())


def spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_bg_done)
    return task

WEB_DIR = Path(__file__).resolve().parent.parent / "webadmin"
SESSION_COOKIE = "kanar_admin_session"


# ------------------------------------------------------------- startup ----
def ensure_persistent_database() -> None:
    """v2.2 (item 8): protect production data across Railway deploys.

    Railway recycles the container filesystem on every deploy, so a local
    SQLite file would be silently WIPED each time. When a Railway environment
    is detected and no Postgres DATABASE_URL is configured, boot must FAIL
    LOUDLY instead of starting with a throwaway database.
    """
    on_railway = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"))
    if on_railway and not settings.database_url.startswith("postgres"):
        raise RuntimeError(
            "DATA SAFETY: running on Railway without a PostgreSQL DATABASE_URL. "
            "A local SQLite file would be erased on every deploy. Attach the "
            "Railway Postgres service (DATABASE_URL is injected automatically) "
            "and redeploy. Set ALLOW_SQLITE_ON_RAILWAY=1 only for throwaway "
            "test deployments.")
    if on_railway and os.getenv("ALLOW_SQLITE_ON_RAILWAY") == "1" \
            and not settings.database_url.startswith("postgres"):
        log.warning("ALLOW_SQLITE_ON_RAILWAY=1 — ephemeral SQLite in use; "
                    "ALL DATA WILL BE LOST ON THE NEXT DEPLOY.")


async def init_app() -> None:
    ensure_persistent_database()
    db.init_engine(settings.database_url)
    engine = db.get_engine()
    await db.create_all(engine)
    added = await db.ensure_added_columns(engine)
    if added:
        log.info("v2.4 column migration applied: %s", ", ".join(added))
    widened = await db.ensure_widened_columns(engine)
    if widened:
        log.info("v2.4.1 column widening applied: %s", ", ".join(widened))
    await _one_shot_boot_restore()
    async with session_scope() as s:
        await service.seed(s)
    # STABLE webhook path secret: derived from WEBHOOK_SECRET so redeploys never
    # invalidate the URLs registered with Telegram/Bale (v3 fix).
    import hashlib
    RUNTIME["path_secret"] = hashlib.sha256(
        ("path:" + settings.webhook_secret).encode()).hexdigest()[:22]
    if settings.telegram_bot_token:
        RUNTIME["adapter_telegram"] = TelegramAdapter(
            settings.telegram_bot_token, settings.webhook_secret)
    if settings.bale_bot_token:
        RUNTIME["adapter_bale"] = BaleAdapter(settings.bale_bot_token,
                                              settings.bale_provider_token)
    await ensure_webadmin()
    await setup_commands()
    await setup_webhooks()
    await log_database_summary()


async def _one_shot_boot_restore() -> None:
    """v2.4.3 one-shot data-recovery gate (incident 2026-11-14).

    The runtime database was reset during a deploy; the owner supplied the
    backup taken minutes earlier (BKP-20261114_225200-02dd). Ship the archive
    with the deploy and set RESTORE_ON_BOOT_FILE=<path inside the container>
    (plus RESTORE_ON_BOOT_PASSWORD=<key> for legacy encrypted archives). At
    boot — before any traffic is served and before seeding — the archive is
    restored via the regular execute_restore flow (emergency snapshot, full
    replace, verify, auto-rollback on failure) and the file is renamed to
    *.done so it can never run twice. Remove the env + file after recovery."""
    path = os.getenv("RESTORE_ON_BOOT_FILE", "")
    if not path:
        return
    if not os.path.exists(path):
        log.warning("RESTORE_ON_BOOT_FILE=%s not found — boot restore skipped", path)
        return
    password = os.getenv("RESTORE_ON_BOOT_PASSWORD", "")
    blob = Path(path).read_bytes()
    log.warning("BOOT RESTORE starting from %s (%d bytes) — replacing ALL data",
                path, len(blob))
    try:
        result = await backup_mod.execute_restore(blob, password)
    except Exception:
        log.exception("BOOT RESTORE failed hard — continuing with current data")
        return
    log.warning("BOOT RESTORE result: %s — %s", result.get("status"),
                result.get("message"))
    try:
        Path(path).rename(path + ".done")
    except OSError:
        pass


async def log_database_summary() -> None:
    """v2.2 (item 8): startup visibility — prove the persistent DB was kept."""
    from sqlalchemy import func
    try:
        async with session_scope() as s:
            users = (await s.execute(select(func.count(User.id)))).scalar() or 0
            msgs = (await s.execute(select(func.count(Chat.id)))).scalar() or 0
        log.info("database OK (%s) — users: %d, chat rows: %d — data persisted "
                 "across deploys", "Postgres" if db.get_engine().dialect.name ==
                 "postgresql" else db.get_engine().url.drivername, users, msgs)
    except Exception:
        log.exception("database summary failed")


async def setup_commands() -> None:
    import asyncio
    commands = [
        {"command": "start", "description": "شروع / منوی اصلی"},
        {"command": "search", "description": "جستجوی هم‌چت ناشناس"},
        {"command": "next", "description": "کاربر بعدی"},
        {"command": "stop", "description": "توقف جستجو/گفتگو"},
        {"command": "profile", "description": "پروفایل و تنظیمات"},
        {"command": "coins", "description": "موجودی سکه"},
        {"command": "store", "description": "فروشگاه سکه"},
        {"command": "bonus", "description": "پاداش روزانه"},
        {"command": "referral", "description": "دعوت دوستان"},
        {"command": "link", "description": "لینک ناشناس من"},
        {"command": "msg", "description": "پیام ناشناس به کاربر"},
        {"command": "gift", "description": "هدیه به هم‌چت"},
        {"command": "language", "description": "تغییر زبان"},
        {"command": "rules", "description": "قوانین"},
        {"command": "privacy", "description": "حریم خصوصی"},
        {"command": "paysupport", "description": "پشتیبانی پرداخت"},
        {"command": "help", "description": "راهنمای کامل"},
    ]
    for plat in ("telegram", "bale"):
        adp = RUNTIME.get(f"adapter_{plat}")
        if adp:
            try:
                await asyncio.wait_for(adp.set_my_commands(commands), timeout=20)
            except (PlatformError, asyncio.TimeoutError) as exc:
                log.warning("setMyCommands failed on %s: %s", plat, exc)


async def setup_webhooks() -> None:
    # v2.4.2 (incident 2026-11-14): Bale webhook DELIVERY never reaches Railway.
    # setWebhook answers 200 and getWebhookInfo confirms the URL, yet not a
    # single update POST arrives from Bale's servers — while the very same bot
    # answered instantly in long-polling mode (proven again during the incident
    # recovery). Outbound Railway -> tapi.bale.ai is reliable, so Bale now
    # ALWAYS runs on long polling; the stale webhook registration is deleted
    # first (getUpdates returns 409 while a webhook is still active).
    # Telegram keeps its webhook — delivery there is reliable.
    import asyncio as _a
    adp_t = RUNTIME.get("adapter_telegram")
    if not settings.public_url:
        log.info("PUBLIC_URL not set — bots will use long polling fallback")
    elif adp_t is not None:
        url = f"{settings.public_url.rstrip('/')}/webhook/telegram/{RUNTIME['path_secret']}"
        try:
            await _a.wait_for(adp_t.set_webhook(url), timeout=20)
            RUNTIME["webhook_set_telegram"] = True
            log.info("webhook registered for telegram -> %s", url)
        except (PlatformError, _a.TimeoutError) as exc:
            log.warning("setWebhook failed for telegram: %s (falling back to polling)", exc)
    adp_b = RUNTIME.get("adapter_bale")
    if adp_b is not None:
        try:
            await _a.wait_for(
                adp_b.call("deleteWebhook", {"drop_pending_updates": False}), timeout=20)
            log.info("bale webhook registration cleared — bale switches to long polling")
        except (PlatformError, _a.TimeoutError) as exc:
            log.warning("deleteWebhook failed on bale (continuing with polling): %s", exc)
        RUNTIME["bale_polling"] = True


async def ensure_webadmin() -> None:
    """Bootstrap the Owner web-admin.

    v2.3.1: credentials can be pinned via the ADMIN_PANEL_USERNAME /
    ADMIN_PANEL_PASSWORD variables (Railway) — every startup reconciles the
    admin account to them (create / rename / password reset). Without them
    the legacy flow runs once: random credentials delivered by the delivery
    bot; the plaintext password is never stored or logged."""
    from sqlalchemy import select as _sel
    async with session_scope() as s:
        admins = list((await s.execute(
            _sel(WebAdmin).order_by(WebAdmin.id))).scalars())
    env_user = (settings.admin_panel_username or "").strip().lower()
    env_pass = (settings.admin_panel_password or "").strip()
    if env_user:
        if env_pass and len(env_pass) < 10:
            log.error("ADMIN_PANEL_PASSWORD is shorter than 10 chars — ignored")
            env_pass = ""
        first_admin_id = admins[0].id if admins else None
        async with session_scope() as s:
            admin = (await s.execute(
                _sel(WebAdmin).where(WebAdmin.username == env_user))).scalar_one_or_none()
            if admin is None:
                admin = await s.get(WebAdmin, first_admin_id) if first_admin_id else None
                if admin is None:
                    s.add(WebAdmin(username=env_user, full_name="مالک هم‌چت", email="",
                                   password_hash=security.hash_password(env_pass or "!unset!"),
                                   role="owner", must_change_password=False, status="active"))
                    log.info("web admin created from ADMIN_PANEL_USERNAME: %s", env_user)
                else:
                    log.info("web admin renamed %s -> %s", admin.username, env_user)
                    admin.username = env_user
            if not admin:
                return
            if env_pass:
                admin.password_hash = security.hash_password(env_pass)
                admin.must_change_password = False
                log.info("web admin password reconciled from ADMIN_PANEL_PASSWORD: %s", env_user)
            else:
                log.warning("ADMIN_PANEL_PASSWORD not set — password for %s unchanged", env_user)
        return
    if admins:
        return
    username = security.random_username()
    password = security.random_password()
    async with session_scope() as s:
        s.add(WebAdmin(username=username, full_name="مالک هم‌چت", email="",
                       password_hash=security.hash_password(password),
                       role="owner", must_change_password=True, status="active"))
    log.info("initial web admin created: %s", username)
    if settings.delivery_bot_token:
        panel = settings.public_url or "(PUBLIC_URL تنظیم نشده)"
        text = ("🔐 پنل مدیریت وب هم‌چت — اطلاعات ورود اولیه\n\n"
                f"آدرس: {panel}\nنام کاربری: <code>{username}</code>\n"
                f"رمز عبور: <code>{password}</code>\n\n"
                "⚠️ این رمز فقط همین یک‌بار نمایش داده می‌شود؛ در اولین ورود باید آن را "
                "تغییر دهید.")
        try:
            async with httpx.AsyncClient(timeout=30) as cli:
                await cli.post(
                    f"https://api.telegram.org/bot{settings.delivery_bot_token}/sendMessage",
                    json={"chat_id": settings.backup_owner_telegram_id, "text": text,
                          "parse_mode": "HTML"})
        except httpx.HTTPError as exc:
            log.error("failed to deliver web admin credentials: %s", exc)


# ---------------------------------------------------------- schedulers ----
async def scheduler_loop() -> None:
    last_purge = 0.0
    last_daily = ""
    tick = 0
    while True:
        try:
            tick += 1
            now = utcnow().timestamp()
            if now - last_purge > 3600:
                last_purge = now
                async with session_scope() as s:
                    stats = await service.purge_expired(s)
                    if any(stats.values()):
                        log.info("hourly purge: %s", stats)
            await expire_bans()
            await enforce_search_timeout()
            await enforce_idle_timeout()
            await run_match_tick()
            await process_broadcasts()
            await maybe_daily_backup()
            # v2.4 growth engine: queue-wait nudge every tick (cheap), signup
            # rescue + reliability alerts once a minute (every 3rd tick).
            from . import growth
            await growth.notify_stale_queue_waiters()
            if tick % 3 == 0:
                await growth.rescue_incomplete_signups()
                await growth.check_alerts()
            # daily jobs: dynamic ages + birthday greetings (Tehran day)
            today = service.tehran_day()
            if last_daily != today:
                last_daily = today
                await daily_maintenance_jobs()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("scheduler tick failed")
            try:
                from . import growth
                await growth.report_scheduler_crash(exc)
            except Exception:
                pass
        await asyncio.sleep(20)


async def daily_maintenance_jobs() -> None:
    """Once per Tehran day: refresh ages from birthdays + greetings + 50-coin gift."""
    from . import bot as bot_mod
    from .i18n import tr
    try:
        async with session_scope() as s:
            changed = await service.update_dynamic_ages(s)
            if changed:
                log.info("dynamic age refresh: %d users", changed)
        async with session_scope() as s:
            for u in await service.today_birthdays(s):
                gifted = 0
                try:
                    # v2.1: 50-coin birthday gift (idempotent per user per year)
                    gifted = await service.grant_birthday_gift(s, u)
                except Exception:
                    log.exception("birthday gift failed for %s", u.public_id)
                try:
                    adp = bot_mod.adapter(u.platform)
                    if adp:
                        text = tr(u.language, "birthday_greet", name=u.display_name)
                        if gifted:
                            text += "\n\n" + tr(u.language, "birthday_gift_coins", coins=gifted)
                        await adp.send_message(u.platform_user_id, text)
                except PlatformError as exc:
                    # v2.4.4: 403 "bot was blocked by the user" used to dump a
                    # full traceback on EVERY maintenance run — flag the user
                    # into the bot-blocked isolation list (same mechanism as
                    # the growth reminders) and move on quietly.
                    from . import growth as _growth
                    if not await _growth.mark_bot_blocked(u.id, u.platform, str(exc)):
                        log.error("birthday greet failed for %s: %s", u.public_id, exc)
                except Exception:
                    log.exception("birthday greet failed for %s", u.public_id)
    except Exception:
        log.exception("daily maintenance jobs failed")


async def expire_bans() -> None:
    from sqlalchemy import select
    from .db import Ban
    async with session_scope() as s:
        expired = (await s.execute(select(Ban).where(
            Ban.status == "active", Ban.type == "temporary",
            Ban.expires_at < utcnow()))).scalars()
        for b in expired:
            b.status = "expired"
            u = await s.get(User, b.user_id)
            if u and u.status == "banned":
                remaining = await service.active_ban(s, u)
                if not remaining:
                    u.status = "active"


async def enforce_search_timeout() -> None:
    from . import bot as bot_mod
    async with session_scope() as s:
        cfg = await service.get_setting(s, "matching")
        cutoff = utcnow() - timedelta(seconds=cfg.get("searchTimeoutSec", 300))
        stale = (await s.execute(select(QueueEntry).where(
            QueueEntry.created_at < cutoff))).scalars()
        for e in stale:
            u = await s.get(User, e.user_id)
            await s.delete(e)
            if u:
                await bot_mod.send(u, "search_timeout")
                await bot_mod.main_menu(u)


async def enforce_idle_timeout() -> None:
    from . import bot as bot_mod
    async with session_scope() as s:
        cfg = await service.get_setting(s, "matching")
        cutoff = utcnow() - timedelta(seconds=cfg.get("idleTimeoutSec", 900))
        stale = (await s.execute(select(Chat).where(
            Chat.status == "active", Chat.last_activity_at < cutoff))).scalars()
        for c in stale:
            cid = c.id
            await service.end_chat(s, c, "idle_timeout")
            for uid in (c.user_a_id, c.user_b_id):
                u = await s.get(User, uid)
                if u:
                    await bot_mod.send(u, "chat_idle_timeout")
                    await bot_mod.main_menu(u)
                    # v2.5: the chat's control panel must not keep offering
                    # next/end/report after the timeout ended the chat
                    await bot_mod.retire_controls(u, cid)


async def run_match_tick() -> None:
    from . import bot as bot_mod
    await bot_mod.run_match_cycle()


async def promote_scheduled_broadcasts() -> list[int]:
    """v2.5: campaigns created with scheduled_for were stored as
    status="scheduled" and NEVER queued, so they never ran at all. Promote the
    ones whose time has come."""
    try:
        async with session_scope() as s:
            due = (await s.execute(select(BroadcastCampaign).where(
                BroadcastCampaign.status == "scheduled",
                BroadcastCampaign.scheduled_for.isnot(None),
                BroadcastCampaign.scheduled_for <= utcnow()))).scalars()
            due_ids = [c.id for c in due]
            for cid in due_ids:
                camp = await s.get(BroadcastCampaign, cid)
                if camp:
                    camp.status = "sending"
            if due_ids:
                RUNTIME.setdefault("broadcast_queue", []).extend(due_ids)
            return due_ids
    except Exception:
        log.exception("scheduled broadcast promotion failed")
        return []


async def process_broadcasts() -> None:
    await promote_scheduled_broadcasts()
    queue: list = RUNTIME.get("broadcast_queue") or []
    if not queue:
        return
    bid = queue.pop(0)
    active = RUNTIME.setdefault("broadcast_active", set())
    if bid in active:
        queue.append(bid)
        return
    active.add(bid)
    spawn(_run_broadcast(bid, active))


async def _run_broadcast(bid: int, active: set) -> None:
    from .platform import PlatformError as PE
    # v2.4.5 FIX: this function referenced bot_mod without importing it —
    # every broadcast died with NameError on the FIRST target (campaign stuck
    # "sending", delivered 0), while the owner thought the bot ignored them.
    from . import bot as bot_mod
    try:
        async with session_scope() as s:
            camp = await s.get(BroadcastCampaign, bid)
            if not camp or camp.status not in ("sending",):
                return
            targets = await service.broadcast_targets(s, camp.audience)
            camp_id, rate = camp.id, max(1, min(camp.rate_limit_per_sec, 25))
        delivered = failed = 0
        failures: dict[str, int] = {}
        # v2.5 PERF: the old loop opened a DB session (and wrote two counters)
        # for EVERY recipient — thousands of transactions for a big campaign.
        # Progress is now flushed every CHECK_EVERY targets and on exit.
        CHECK_EVERY = 25
        since_check = 0

        async def _flush(final: bool = False) -> bool:
            nonlocal since_check
            async with session_scope() as s:
                camp = await s.get(BroadcastCampaign, camp_id)
                if not camp:
                    return False
                if not final and camp.status != "sending":
                    return False       # paused / cancelled mid-run
                camp.delivered_count = delivered
                camp.failed_count = failed
            since_check = 0
            return True

        for u in targets:
            if since_check >= CHECK_EVERY:
                if not await _flush():
                    return
            since_check += 1
            adp = bot_mod.adapter(u.platform)
            if not adp:
                failed += 1
                failures["adapter_missing"] = failures.get("adapter_missing", 0) + 1
                continue
            try:
                await adp.send_message(u.platform_user_id, camp.message)
                delivered += 1
            except PE as exc:
                failed += 1
                # v2.4: users who blocked the bot are isolated automatically so
                # future broadcasts skip them (excluded from broadcast_targets).
                if exc.error_code in (400, 403):
                    from . import growth
                    await growth.mark_bot_blocked(u.id, u.platform, str(exc))
                key = str(exc)[:60]
                failures[key] = failures.get(key, 0) + 1
            await asyncio.sleep(1.0 / rate)
        async with session_scope() as s:
            camp = await s.get(BroadcastCampaign, camp_id)
            camp.delivered_count = delivered
            camp.failed_count = failed
            camp.status = "completed"
            camp.failure_summary = [{"reason": k, "count": v} for k, v in failures.items()]
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("broadcast %s failed", bid)
        async with session_scope() as s:
            camp = await s.get(BroadcastCampaign, bid)
            if camp:
                camp.status = "failed"
    finally:
        active.discard(bid)


async def maybe_daily_backup() -> None:
    """Daily encrypted backup at 03:00 Asia/Tehran, delivered via product bot."""
    now = service.tehran_now()
    async with session_scope() as s:
        cfg = await service.get_setting(s, "backup")
        if not cfg.get("autoBackupEnabled", True):
            return
        last = cfg.get("lastRunDate") or ""
        hh, mm = (cfg.get("scheduleTimeTehran") or "03:00").split(":")
        if (now.hour, now.minute) < (int(hh), int(mm)) or last == service.tehran_day(now):
            return
    bot = RUNTIME.get("adapter_telegram")
    if not bot:
        return
    log.info("starting scheduled daily backup")
    rec = await backup_mod.run_backup_job(bot, "scheduled_daily")
    log.info("scheduled backup finished: %s", rec.id if rec else "skipped (locked)")
    # v2.4: the owner gets a PV alert when the nightly backup failed
    if rec is not None and rec.status == "failed":
        from . import growth
        await growth.report_backup_failure(rec.error_message or "نامشخص")
    elif rec is not None and rec.delivery_status != "delivered":
        from . import growth
        await growth.report_backup_failure(f"ارسال پشتیبان {rec.id} ناموفق بود")


async def polling_task(platform: str) -> None:
    from . import bot as bot_mod
    adp = RUNTIME.get(f"adapter_{platform}")
    if not adp:
        return
    stop = asyncio.Event()
    log.info("starting long-polling fallback for %s", platform)
    # v2.4.3 backlog flush: after an outage the platform queue can hold
    # thousands of stale updates (incident 2026-11-14: 1512 pending on Bale
    # after the poller died for ~6h). Replaying them spams users with
    # hours-old state machines, so a huge backlog is dropped instead.
    start_offset = 0
    try:
        info = await adp.get_webhook_info()
        pending = int(info.get("pending_update_count") or 0)
        if pending > 200:
            # offset=-1 returns the most recent update (Telegram Bot API
            # semantics; Bale mirrors it). max() keeps it correct even if a
            # platform ignores -1 and returns the whole queue.
            last = await adp.call("getUpdates", {"offset": -1, "limit": 1}) or []
            if last:
                start_offset = max(int(u.get("update_id", 0)) for u in last) + 1
                log.warning("%s: dropping stale backlog of %d pending updates "
                            "(resume from update_id=%d)", platform, pending,
                            start_offset)
    except Exception:
        log.exception("backlog check failed for %s (continuing from oldest)",
                      platform)
    # Supervisor: poll_updates returns only when `stop` is set (shutdown). If
    # it ever exits or raises for any other reason, restart it — the poller
    # must never go dark permanently.
    while not stop.is_set():
        try:
            await adp.poll_updates(lambda upd: bot_mod.process_update(platform, upd),
                                   stop, start_offset=start_offset)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("poller for %s exited unexpectedly — restarting in 5s",
                          platform)
            await asyncio.sleep(5)
        start_offset = 0


# ------------------------------------------------------------------ app ----
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_app()
    tasks = [asyncio.create_task(scheduler_loop())]
    for plat in ("telegram", "bale"):
        if RUNTIME.get(f"adapter_{plat}") and not RUNTIME.get(f"webhook_set_{plat}"):
            tasks.append(asyncio.create_task(polling_task(plat)))
    log.info("kanar app started (webhooks: tg=%s bale=%s)",
             RUNTIME.get("webhook_set_telegram"), RUNTIME.get("webhook_set_bale"))
    yield
    for t in tasks:
        t.cancel()
    for plat in ("telegram", "bale"):
        adp = RUNTIME.get(f"adapter_{plat}")
        if adp:
            await adp.close()
    await db.dispose_engine()


app = FastAPI(title="HamChat — Anonymous Chat", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    path = request.url.path
    if (path.startswith("/api/admin/v1") and
            request.method in ("POST", "PUT", "PATCH", "DELETE") and
            not path.endswith(("/auth/login",
                               "/auth/change-initial-password",
                               "/auth/logout"))):
        sid = request.cookies.get(SESSION_COOKIE)
        token = request.headers.get("X-CSRF-Token")
        # v2.4: stateless CSRF derived from the session id — admin sessions now
        # survive deploys/restarts (the old in-memory map died on every boot).
        expected = security.csrf_token(sid or "", settings.webhook_secret) if sid else ""
        if not sid or not expected or token != expected:
            return JSONResponse({"detail": "توکن CSRF نامعتبر است. دوباره وارد شوید.",
                                 "code": "CSRF"}, status_code=403)
    return await call_next(request)


@app.get("/health")
async def health():
    maintenance = False
    try:
        async with session_scope() as s:
            await s.execute(__import__("sqlalchemy").text("SELECT 1"))
            cfg = await service.get_setting(s, "app")
            maintenance = bool(cfg.get("maintenanceMode", False))
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "db": db_ok,
            "webhooks": {"telegram": bool(RUNTIME.get("webhook_set_telegram")),
                         "bale": bool(RUNTIME.get("webhook_set_bale"))},
            "modes": {"telegram": "webhook" if RUNTIME.get("webhook_set_telegram")
                      else "polling" if RUNTIME.get("adapter_telegram") else "off",
                      "bale": "polling" if RUNTIME.get("adapter_bale") else "off"},
            "maintenance": maintenance}


async def _handle_webhook(platform: str, secret: str, request: Request) -> JSONResponse:
    if secret != RUNTIME.get("path_secret"):
        return JSONResponse({"ok": True}, status_code=404)
    adp = RUNTIME.get(f"adapter_{platform}")
    if adp is None:
        return JSONResponse({"ok": True})
    if platform == "telegram":
        if not adp.verify_webhook_secret(
                request.headers.get("X-Telegram-Bot-Api-Secret-Token")):
            return JSONResponse({"ok": True}, status_code=403)
    try:
        upd = await request.json()
    except Exception:
        return JSONResponse({"ok": True})
    from . import bot as bot_mod
    spawn(bot_mod.process_update(platform, upd))
    return JSONResponse({"ok": True})


@app.post("/webhook/telegram/{secret}")
async def tg_webhook(secret: str, request: Request):
    return await _handle_webhook("telegram", secret, request)


@app.post("/webhook/bale/{secret}")
async def bale_webhook(secret: str, request: Request):
    return await _handle_webhook("bale", secret, request)


# admin API router
from .webadmin import router as admin_router, ApiErr  # noqa: E402
app.include_router(admin_router)


@app.exception_handler(ApiErr)
async def apierr_flat_handler(request: Request, exc: ApiErr):
    """v2.3.1: emit the error contract the panel actually reads —
    `detail` (Persian message) and `code` at the TOP level of the body.
    The old nested shape made the panel show its generic connection-error
    toast for every structured server error."""
    d = exc.detail
    msg, code = (d.get("detail"), d.get("code")) if isinstance(d, dict) else (d, "")
    return JSONResponse(status_code=exc.status_code,
                        content={"detail": msg, "code": code})


@app.get("/")
async def index():
    f = WEB_DIR / "index.html"
    if f.exists():
        return FileResponse(f)
    return JSONResponse({"app": "kanar", "panel": "webadmin bundle missing"})


@app.get("/{full_path:path}")
async def spa(full_path: str):
    if full_path.startswith(("api/", "webhook/", "health")):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    f = (WEB_DIR / full_path).resolve()
    if str(f).startswith(str(WEB_DIR.resolve())) and f.is_file():
        return FileResponse(f)
    f = WEB_DIR / "index.html"
    if f.exists():
        return FileResponse(f)
    return JSONResponse({"detail": "Not Found"}, status_code=404)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx logs every request URL at INFO, and Bot-API request URLs carry the
    # full bot token (/bot<token>/method) — redact it so tokens never reach the
    # platform log storage (Railway keeps deployment logs indefinitely).
    import re as _re

    class _RedactBotTokens(logging.Filter):
        _rx = _re.compile(r"/bot[^/\s]+/")

        def filter(self, record: logging.LogRecord) -> bool:
            try:
                text = record.getMessage()
            except Exception:
                return True
            if "/bot" in text:
                record.msg = self._rx.sub("/bot***:REDACTED/", text)
                record.args = ()
            return True

    for _name in ("httpx", "httpcore"):
        logging.getLogger(_name).addFilter(_RedactBotTokens())
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port,
                log_level="info", access_log=False)


if __name__ == "__main__":
    main()
