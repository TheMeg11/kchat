"""Application entrypoint: FastAPI app, webhooks, schedulers, SPA hosting."""
from __future__ import annotations

import asyncio
import logging
import secrets as pysecrets
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

WEB_DIR = Path(__file__).resolve().parent.parent / "webadmin"
SESSION_COOKIE = "kanar_admin_session"


# ------------------------------------------------------------- startup ----
async def init_app() -> None:
    db.init_engine(settings.database_url)
    engine = db.get_engine()
    await db.create_all(engine)
    async with session_scope() as s:
        await service.seed(s)
    RUNTIME["path_secret"] = pysecrets.token_urlsafe(16)
    if settings.telegram_bot_token:
        RUNTIME["adapter_telegram"] = TelegramAdapter(
            settings.telegram_bot_token, settings.webhook_secret)
    if settings.bale_bot_token:
        RUNTIME["adapter_bale"] = BaleAdapter(settings.bale_bot_token,
                                              settings.bale_provider_token)
    await ensure_webadmin()
    await setup_commands()
    await setup_webhooks()


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
    if not settings.public_url:
        log.info("PUBLIC_URL not set — bots will use long polling fallback")
        return
    for plat in ("telegram", "bale"):
        adp = RUNTIME.get(f"adapter_{plat}")
        if not adp:
            continue
        url = f"{settings.public_url.rstrip('/')}/webhook/{plat}/{RUNTIME['path_secret']}"
        try:
            import asyncio as _a
            await _a.wait_for(adp.set_webhook(url), timeout=20)
            RUNTIME[f"webhook_set_{plat}"] = True
            log.info("webhook registered for %s -> %s", plat, url)
        except (PlatformError, _a.TimeoutError) as exc:
            log.warning("setWebhook failed for %s: %s (falling back to polling)", plat, exc)


async def ensure_webadmin() -> None:
    """Create the initial Owner web-admin once; deliver credentials via the
    delivery bot (never stored in plaintext anywhere)."""
    async with session_scope() as s:
        existing = (await s.execute(
            __import__("sqlalchemy").select(WebAdmin).limit(1))).scalar_one_or_none()
    if existing is not None:
        return
    username = security.random_username()
    password = security.random_password()
    async with session_scope() as s:
        s.add(WebAdmin(username=username, full_name="مالک کانار", email="",
                       password_hash=security.hash_password(password),
                       role="owner", must_change_password=True, status="active"))
    log.info("initial web admin created: %s", username)
    if settings.delivery_bot_token:
        panel = settings.public_url or "(PUBLIC_URL تنظیم نشده)"
        text = ("🔐 پنل مدیریت وب کانار — اطلاعات ورود اولیه\n\n"
                f"آدرس: {panel}\nنام کاربری: <code>{username}</code>\n"
                f"رمز عبور: <code>{password}</code>\n\n"
                "⚠️ این رمز فقط همین یک‌بار نمایش داده می‌شود؛ در اولین ورود باید آن را تغییر دهید "
                "و TOTP (Google Authenticator) را فعال کنید.\n"
                "کدهای بازیابی پس از فعال‌سازی TOTP نمایش داده خواهند شد.")
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
    while True:
        try:
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
            # daily jobs: dynamic ages + birthday greetings (Tehran day)
            today = service.tehran_day()
            if last_daily != today:
                last_daily = today
                await daily_maintenance_jobs()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(20)


async def daily_maintenance_jobs() -> None:
    """Once per Tehran day: refresh ages from birthdays + send greetings."""
    from . import bot as bot_mod
    from .i18n import tr
    try:
        async with session_scope() as s:
            changed = await service.update_dynamic_ages(s)
            if changed:
                log.info("dynamic age refresh: %d users", changed)
        async with session_scope() as s:
            for u in await service.today_birthdays(s):
                try:
                    await bot_mod.adapter(u.platform) and None
                    adp = bot_mod.adapter(u.platform)
                    if adp:
                        await adp.send_message(
                            u.platform_user_id,
                            tr(u.language, "birthday_greet", name=u.display_name))
                except Exception:
                    log.exception("birthday greet failed for %s", u.public_id)
    except Exception:
        log.exception("daily maintenance jobs failed")


async def expire_bans() -> None:
    from sqlalchemy import select, update
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
            await service.end_chat(s, c, "idle_timeout")
            for uid in (c.user_a_id, c.user_b_id):
                u = await s.get(User, uid)
                if u:
                    await bot_mod.send(u, "chat_idle_timeout")
                    await bot_mod.main_menu(u)


async def run_match_tick() -> None:
    from . import bot as bot_mod
    await bot_mod.run_match_cycle()


async def process_broadcasts() -> None:
    from . import bot as bot_mod
    queue: list = RUNTIME.get("broadcast_queue") or []
    if not queue:
        return
    bid = queue.pop(0)
    active = RUNTIME.setdefault("broadcast_active", set())
    if bid in active:
        queue.append(bid)
        return
    active.add(bid)
    asyncio.create_task(_run_broadcast(bid, active))


async def _run_broadcast(bid: int, active: set) -> None:
    from .platform import PlatformError as PE
    try:
        async with session_scope() as s:
            camp = await s.get(BroadcastCampaign, bid)
            if not camp or camp.status not in ("sending",):
                return
            targets = await service.broadcast_targets(s, camp.audience)
            camp_id, rate = camp.id, max(1, min(camp.rate_limit_per_sec, 25))
        delivered = failed = 0
        failures: dict[str, int] = {}
        for u in targets:
            async with session_scope() as s:
                camp = await s.get(BroadcastCampaign, camp_id)
                if not camp or camp.status != "sending":
                    return
                camp.delivered_count = delivered
                camp.failed_count = failed
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


async def polling_task(platform: str) -> None:
    from . import bot as bot_mod
    adp = RUNTIME.get(f"adapter_{platform}")
    if not adp:
        return
    stop = asyncio.Event()
    log.info("starting long-polling fallback for %s", platform)
    await adp.poll_updates(lambda upd: bot_mod.process_update(platform, upd), stop)


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


app = FastAPI(title="Kanar Anonymous Chat", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    path = request.url.path
    if (path.startswith("/api/admin/v1") and
            request.method in ("POST", "PUT", "PATCH", "DELETE") and
            not path.endswith(("/auth/login", "/auth/verify-totp",
                               "/auth/change-initial-password",
                               "/auth/totp/enroll/confirm",
                               "/auth/logout"))):
        sid = request.cookies.get(SESSION_COOKIE)
        token = request.headers.get("X-CSRF-Token")
        expected = RUNTIME.get("csrf", {}).get(sid or "")
        if not sid or not expected or token != expected:
            return JSONResponse({"detail": "توکن CSRF نامعتبر است. دوباره وارد شوید.",
                                 "code": "CSRF"}, status_code=403)
    return await call_next(request)


@app.get("/health")
async def health():
    try:
        async with session_scope() as s:
            await s.execute(__import__("sqlalchemy").text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "db": db_ok,
            "webhooks": {"telegram": bool(RUNTIME.get("webhook_set_telegram")),
                         "bale": bool(RUNTIME.get("webhook_set_bale"))},
            "maintenance": False}


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
    asyncio.create_task(bot_mod.process_update(platform, upd))
    return JSONResponse({"ok": True})


@app.post("/webhook/telegram/{secret}")
async def tg_webhook(secret: str, request: Request):
    return await _handle_webhook("telegram", secret, request)


@app.post("/webhook/bale/{secret}")
async def bale_webhook(secret: str, request: Request):
    return await _handle_webhook("bale", secret, request)


# admin API router
from .webadmin import router as admin_router  # noqa: E402
app.include_router(admin_router)


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
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port,
                log_level="info", access_log=False)


if __name__ == "__main__":
    main()
