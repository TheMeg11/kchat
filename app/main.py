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
            "and redeploy.")


async def init_app() -> None:
    settings.validate_production()
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
    from .migrations import migrate
    await migrate(engine)
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
    await _one_shot_boot_restore()
    async with session_scope() as s:
        await service.seed(s)
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

    Environment credentials bootstrap once, never reset a changed password.
    Without them, generate one-time credentials for private delivery."""
    from sqlalchemy import select as _sel
    async with session_scope() as s:
        admins = list((await s.execute(
            _sel(WebAdmin).order_by(WebAdmin.id))).scalars())
    env_user = (settings.admin_panel_username or "").strip().lower()
    env_pass = (settings.admin_panel_password or "").strip()
    # Quarantine accounts created by the old public fallback password.
    for previous in admins:
        if await security.password_work(security.verify_password,previous.password_hash,"!unset!"):
            async with session_scope() as s:
                old = await s.get(WebAdmin,previous.id)
                from sqlalchemy import update
                from .db import WebSession
                await s.execute(update(WebSession).where(WebSession.admin_id==old.id).values(revoked=True))
                if env_user and len(env_pass)>=10 and env_pass!="!unset!":
                    old.password_hash=await security.password_work(security.hash_password,env_pass)
                    old.must_change_password=True
                else:
                    old.status="suspended"
                    log.critical("unsafe legacy owner account suspended; operator recovery required")
    if env_user:
        if not env_pass or len(env_pass) < 10 or env_pass == "!unset!":
            raise RuntimeError("ADMIN_PANEL_PASSWORD (10+ characters) is required with ADMIN_PANEL_USERNAME")
        async with session_scope() as s:
            admin = (await s.execute(_sel(WebAdmin).where(WebAdmin.role == "owner"))).scalars().first()
            if admin is None:
                s.add(WebAdmin(username=env_user, full_name="مالک هم‌چت", email="",
                    password_hash=security.hash_password(env_pass), role="owner",
                    must_change_password=True, status="active"))
        # Environment credentials bootstrap ONCE. Never undo a password change on deploy.
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
    """Independent jobs: one slow or broken task must not skip the rest."""
    last_purge, last_daily, tick = 0.0, "", 0
    async def run(job):
        if RUNTIME.get("restore_in_progress"):
            return
        try:
            await asyncio.wait_for(job(), timeout=90)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scheduler job failed: %s", job.__name__)
            try:
                from .growth import report_scheduler_crash
                await asyncio.wait_for(report_scheduler_crash(RuntimeError(job.__name__)),timeout=5)
            except Exception:
                pass
    while True:
        if RUNTIME.get("restore_in_progress"):
            await asyncio.sleep(1)
            continue
        tick += 1
        now = asyncio.get_running_loop().time()
        if now - last_purge > 3600:
            async def purge():
                async with session_scope() as s:
                    await service.purge_expired(s)
            await run(purge)
            last_purge = now
        from .reconnect import recover
        for job in (recover, expire_bans, enforce_search_timeout, enforce_idle_timeout,
                    run_match_tick, process_broadcasts, maybe_daily_backup):
            await run(job)
        from . import growth
        await run(growth.notify_stale_queue_waiters)
        if tick % 3 == 0:
            await run(growth.rescue_incomplete_signups)
            await run(growth.check_alerts)
            from .uploads import cleanup
            from .bot_admin import cleanup_restore_wait
            cleanup()
            cleanup_restore_wait()
        today = service.tehran_day()
        if last_daily != today:
            await run(daily_maintenance_jobs)
            last_daily = today
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
        stale = list((await s.execute(select(QueueEntry).where(
            QueueEntry.created_at < cutoff).limit(50))).scalars())
        people = list((await s.execute(select(User).where(User.id.in_([e.user_id for e in stale])))).scalars())
        for entry in stale:
            await s.delete(entry)
    for user in people:
        await bot_mod.send(user, "search_timeout", keyboard=bot_mod.search_cta(user.language))


async def enforce_idle_timeout() -> None:
    from . import bot as bot_mod
    notices = []
    async with session_scope() as s:
        cfg = await service.get_setting(s, "matching")
        cutoff = utcnow() - timedelta(seconds=cfg.get("idleTimeoutSec", 900))
        stale = list((await s.execute(select(Chat).where(
            Chat.status == "active", Chat.last_activity_at < cutoff).limit(50))).scalars())
        for chat in stale:
            await service.end_chat(s, chat, "idle_timeout")
            for uid in (chat.user_a_id, chat.user_b_id):
                user = await s.get(User, uid)
                if user:
                    notices.append((user,chat.id))
    for user,cid in notices:
        await bot_mod.send(user, "chat_idle_timeout", keyboard=bot_mod.search_cta(user.language))
        await bot_mod.retire_controls(user,cid)


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
            return due_ids
    except Exception:
        log.exception("scheduled broadcast promotion failed")
        return []


async def process_broadcasts() -> None:
    await promote_scheduled_broadcasts()
    active = RUNTIME.setdefault("broadcast_active", set())
    if active:
        return
    async with session_scope() as s:
        bid = (await s.execute(select(BroadcastCampaign.id).where(
            BroadcastCampaign.status == "sending").order_by(BroadcastCampaign.id).limit(1))).scalar_one_or_none()
    if bid is not None:
        active.add(bid)
        spawn(_run_broadcast(bid, active))


async def _run_broadcast(bid: int, active: set) -> None:
    from . import bot as bot_mod
    try:
        while not RUNTIME.get("restore_in_progress"):
            async with session_scope() as s:
                camp = await s.get(BroadcastCampaign,bid)
                if not camp or camp.status != "sending":
                    return
                targets = await service.broadcast_targets(s,camp.audience,camp.last_user_id,25)
                if not targets:
                    camp.status = "completed"
                    return
                message,rate = camp.message,max(1,min(camp.rate_limit_per_sec,25))
                delivered,failed,cursor = camp.delivered_count,camp.failed_count,camp.last_user_id
            for user in targets:
                if RUNTIME.get("restore_in_progress"):
                    return
                adp = bot_mod.adapter(user.platform)
                try:
                    if adp is None:
                        raise PlatformError("sendMessage",503,"adapter unavailable")
                    await adp.send_message(user.platform_user_id,message)
                    delivered += 1
                except PlatformError as exc:
                    failed += 1
                    if exc.error_code == 403:
                        from .growth import mark_bot_blocked
                        await mark_bot_blocked(user.id,user.platform,str(exc))
                cursor = user.id
                await asyncio.sleep(1.0/rate)
            # Durable cursor: after a hard crash at most one batch can repeat.
            async with session_scope() as s:
                camp = await s.get(BroadcastCampaign,bid)
                if camp:
                    camp.last_user_id, camp.delivered_count, camp.failed_count = cursor,delivered,failed
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("broadcast %s failed",bid)
        async with session_scope() as s:
            camp = await s.get(BroadcastCampaign,bid)
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
    monotonic = asyncio.get_running_loop().time()
    if monotonic - RUNTIME.get("backup_last_attempt", -3600) < 300:
        return
    RUNTIME["backup_last_attempt"] = monotonic
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
    from .inbox import enqueue
    adp = RUNTIME.get(f"adapter_{platform}")
    if not adp:
        return
    stop = asyncio.Event()
    log.info("starting long-polling fallback for %s", platform)
    start_offset = 0
    # Supervisor: poll_updates returns only when `stop` is set (shutdown). If
    # it ever exits or raises for any other reason, restart it — the poller
    # must never go dark permanently.
    while not stop.is_set():
        try:
            await adp.poll_updates(lambda upd: enqueue(platform, upd),
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
    from .inbox import worker
    tasks = [asyncio.create_task(scheduler_loop())]
    tasks.extend(asyncio.create_task(worker(i)) for i in range(max(1, settings.update_workers) + 1))
    for plat in ("telegram", "bale"):
        if RUNTIME.get(f"adapter_{plat}") and not RUNTIME.get(f"webhook_set_{plat}"):
            tasks.append(asyncio.create_task(polling_task(plat)))
    log.info("kanar app started (webhooks: tg=%s bale=%s)",
             RUNTIME.get("webhook_set_telegram"), RUNTIME.get("webhook_set_bale"))
    yield
    for t in tasks:
        t.cancel()
    for task in list(_BG_TASKS):
        task.cancel()
    await asyncio.gather(*tasks, *list(_BG_TASKS), return_exceptions=True)
    for plat in ("telegram", "bale"):
        adp = RUNTIME.get(f"adapter_{plat}")
        if adp:
            await adp.close()
    from .uploads import clear_uploads
    clear_uploads()
    await db.dispose_engine()


from .http_limits import BodyLimitMiddleware

app = FastAPI(title="HamChat — Anonymous Chat", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(BodyLimitMiddleware)


@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    path = request.url.path
    if (path.startswith("/api/admin/v1") and
            request.method in ("POST", "PUT", "PATCH", "DELETE") and
            not path.endswith(("/auth/login",
                               "/auth/change-initial-password"))):
        sid = request.cookies.get(SESSION_COOKIE)
        token = request.headers.get("X-CSRF-Token")
        # v2.4: stateless CSRF derived from the session id — admin sessions now
        # survive deploys/restarts (the old in-memory map died on every boot).
        expected = security.csrf_token(sid or "", settings.webhook_secret) if sid else ""
        import secrets
        if not sid or not expected or not token or not secrets.compare_digest(token.encode(), expected.encode()):
            return JSONResponse({"detail": "توکن CSRF نامعتبر است. دوباره وارد شوید.",
                                 "code": "CSRF"}, status_code=403)
    mutating = request.method in ("POST", "PUT", "PATCH", "DELETE") and path.startswith("/api/")
    if mutating and RUNTIME.get("restore_in_progress"):
        return JSONResponse({"detail":"بازیابی در حال اجراست؛ کمی صبر کنید.", "code":"RESTORE_BUSY"}, status_code=503)
    if mutating:
        RUNTIME["admin_writes"] = RUNTIME.get("admin_writes", 0) + 1
    try:
        response = await call_next(request)
    finally:
        if mutating:
            RUNTIME["admin_writes"] -= 1
    # Rehydrate CSRF on /me and every authenticated response, including after refresh.
    sid = request.cookies.get(SESSION_COOKIE)
    if sid and path.startswith("/api/admin/v1") and path not in ("/api/admin/v1/auth/login", "/api/admin/v1/auth/change-initial-password", "/api/admin/v1/auth/change-password") and response.status_code < 400:
        response.headers["X-CSRF-Token"] = security.csrf_token(sid, settings.webhook_secret)
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; img-src 'self' data: blob:; connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'")
    if settings.public_url.startswith("https://"):
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


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
    return JSONResponse(status_code=200 if db_ok else 503, content={"status": "ok" if db_ok else "degraded", "db": db_ok,
            "webhooks": {"telegram": bool(RUNTIME.get("webhook_set_telegram")),
                         "bale": bool(RUNTIME.get("webhook_set_bale"))},
            "modes": {"telegram": "webhook" if RUNTIME.get("webhook_set_telegram")
                      else "polling" if RUNTIME.get("adapter_telegram") else "off",
                      "bale": "polling" if RUNTIME.get("adapter_bale") else "off"},
            "maintenance": maintenance})


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
    from .inbox import enqueue, validate_update
    import json
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > settings.webhook_max_bytes:
            return JSONResponse({"detail": "Too large"}, status_code=413)
    try:
        upd = json.loads(raw)
        validate_update(upd)
    except (ValueError, TypeError):
        return JSONResponse({"detail": "Invalid update"}, status_code=400)
    await enqueue(platform, upd)  # database errors return 5xx; platform must retry
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
from .commerce_admin import router as commerce_router
app.include_router(commerce_router)


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
        return FileResponse(f, headers={"Cache-Control":"no-cache"})
    return JSONResponse({"app": "kanar", "panel": "webadmin bundle missing"})


@app.get("/{full_path:path}")
async def spa(full_path: str, request: Request):
    if full_path.startswith(("api/", "webhook/", "health")):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    f = (WEB_DIR / full_path).resolve()
    if f.is_relative_to(WEB_DIR.resolve()) and f.is_file():
        headers = {"Cache-Control":"public, max-age=86400"} if f.suffix in (".js", ".css", ".woff2") else {"Cache-Control":"no-cache"}
        if f.suffix in (".js", ".css") and "gzip" in request.headers.get("accept-encoding", "") and f.with_suffix(f.suffix+".gz").exists():
            return FileResponse(f.with_suffix(f.suffix+".gz"), media_type="text/javascript" if f.suffix==".js" else "text/css",
                headers={**headers,"Content-Encoding":"gzip","Vary":"Accept-Encoding"})
        return FileResponse(f, headers={**headers,"Vary":"Accept-Encoding"})
    f = WEB_DIR / "index.html"
    if f.exists():
        return FileResponse(f, headers={"Cache-Control":"no-cache"})
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
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, proxy_headers=False,
                log_level="info", access_log=False, limit_concurrency=100)


if __name__ == "__main__":
    main()
