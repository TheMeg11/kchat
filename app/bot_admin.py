"""In-bot admin panel (Telegram + Bale) — one shared backend, role-based.

v2.5 rewrite: the panel is now COMPLETE (parity with the web Super Admin API)
and organized into categories. Every list is paginated, every list row carries
its actions inline, and only the newest panel message keeps its keyboard
(`app.panels` strips the previous one), so the chat never fills with stale
buttons.

Categories
----------
📊 dashboard   — live numeric stats
👥 users       — user list, lookup + ban/coins, referrals, payments
🛡 moderation  — reports (resolve), bans (revoke), appeals (review),
                 dictionary/allowlist, media-type switches, matching config
💰 economy     — economy keys, store packages, payment gateways
💬 support     — tickets: read, reply from the bot, close/reopen
📢 broadcast   — campaigns: create, pause/resume/cancel
🔐 backups     — status, instant backup, restore
⚙️ settings    — app toggles, matching, privacy, daily rewards, maintenance
🖥 platforms   — token/webhook/polling health + platform config
🧾 audit/ops   — audit log, monitoring
"""
from __future__ import annotations

import logging
import time
import asyncio
import re
import secrets
from typing import Optional

from sqlalchemy import delete, func, select

from . import backup as backup_mod
from . import db, panels, service
from .config import RUNTIME, settings
from .db import (Appeal, AllowlistPhrase, AuditLog, Ban, BotAdmin, DictionaryWord,
                 MediaType, Payment, Referral, Report, StorePackage, SupportMessage,
                 SupportTicket, User, session_scope, utcnow)
from .platform import PlatformError

log = logging.getLogger("kanar.botadmin")

# in-memory restore wait-state (chat_id -> collected parts / password / validated blob)
# v2.3 one-click flow: owner forwards the backup file with the restore password
# in its caption; the bot validates instantly and replaces data on one confirm.
RESTORE_WAIT: dict[str, dict] = {}

def cleanup_restore_wait():
    now=time.monotonic()
    for key,entry in list(RESTORE_WAIT.items()):
        if entry.get("expires",0) <= now:
            RESTORE_WAIT.pop(key,None)


_CAP_PWD_RE = re.compile(r"(?:رمز\s*(?:بازیابی|نگاری)?|password)\s*[:：]\s*([^\s]{4,})", re.I)
_CAP_PART_RE = re.compile(r"بخش\s*(\d+)\s*از\s*(\d+)")
_FN_PART_RE = re.compile(r"\.(?:part)?(\d+)of(\d+)", re.I)

# page sizes per list
PAGE = 8
SMALL_PAGE = 5

ROLE_LABEL = {"owner": "مالک (Owner)", "senior_admin": "مدیر ارشد",
              "moderator": "ناظر", "support": "پشتیبان"}
PLATFORM_TITLE = {"telegram": "تلگرام", "bale": "بله"}
ROLE_ORDER = ("senior_admin", "moderator", "support")


def _parse_restore_caption(caption: str, filename: str):
    """Extract (password, partIndex, partTotal) from a forwarded backup caption/filename."""
    pwd = None
    if caption:
        m = _CAP_PWD_RE.search(caption)
        if m:
            pwd = m.group(1)
    idx = total = None
    m = _CAP_PART_RE.search(caption or "")
    if m:
        idx, total = int(m.group(1)), int(m.group(2))
    else:
        m = _FN_PART_RE.search(filename or "")
        if m:
            idx, total = int(m.group(1)), int(m.group(2))
    return pwd, idx, total


def kb(*rows):
    """Accept both kb([a, b], [c, d]) and kb([[a, b], [c, d]])."""
    if len(rows) == 1 and isinstance(rows[0], list) and rows[0] \
            and isinstance(rows[0][0], list):
        rows = tuple(rows[0])
    return [list(r) for r in rows]


def btn(text, data):
    return {"text": text, "callback_data": data}


def _on_off(value: bool) -> str:
    return "روشن ✅" if value else "خاموش ⛔"


def _nav(prefix: str, page: int, pages: int) -> list:
    """Pagination row + back-to-menu row for a list panel."""
    rows: list = []
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(btn("◀️ قبلی", f"{prefix}_{page - 1}"))
        nav.append(btn(f"📄 {page + 1}/{pages}", f"{prefix}_{page}"))
        if page < pages - 1:
            nav.append(btn("بعدی ▶️", f"{prefix}_{page + 1}"))
        rows.append(nav)
    rows.append([btn("🏠 بازگشت به منوی اصلی", "back_home")])
    return rows


def _pages(total: int, size: int = PAGE) -> int:
    return max(1, (total + size - 1) // size)


def adapter(platform):
    return RUNTIME.get(f"adapter_{platform}")


async def get_role(user: User) -> str:
    if user.platform == "telegram" and user.platform_user_id.isdigit() and \
            int(user.platform_user_id) in settings.owner_telegram_ids:
        return "owner"
    if user.platform == "bale" and user.platform_user_id.isdigit() and \
            int(user.platform_user_id) in settings.owner_bale_ids:
        return "owner"
    async with session_scope() as s:
        row = await s.execute(select(BotAdmin).where(
            BotAdmin.platform == user.platform,
            BotAdmin.platform_user_id == user.platform_user_id))
        ba = row.scalar_one_or_none()
        return ba.role if ba else ""


async def _perms(role: str) -> list:
    async with session_scope() as s:
        perms = await service.role_permissions(s, role)
    # "*" (owner) must expand to every known permission, otherwise the owner's
    # bot panel rendered almost empty (v2.1 fix, extended in v2.5 to the whole
    # PERMISSION_KEYS catalogue so new panels are reachable too).
    if "*" in perms:
        perms = sorted(set(service.PERMISSION_KEYS))
    return perms


async def admin_entry(user: User) -> None:
    role = await get_role(user)
    if not role:
        await _say(user, "⛔ شما مدیر نیستید.")
        return
    await _menu(user, role)


async def _say(user: User, text: str, keyboard=None):
    adp = adapter(user.platform)
    if not adp:
        return
    try:
        await panels.send(adp, user.platform, user.platform_user_id, text, keyboard=keyboard)
    except PlatformError as exc:
        log.warning("admin send failed: %s", exc)


# ===================================================================== menu ===
async def _menu(user: User, role: str, note: str = "") -> None:
    """Categorized root menu. The three operational shortcuts (stats / instant
    backup / maintenance) stay on the root for one-tap access."""
    perms = await _perms(role)
    rows = [[btn("📊 آمار کلی", "adm_stats")]]
    if "users.view" in perms:
        rows.append([btn("👥 کاربران", "adm_users"),
                     btn("🔎 جستجوی کاربر", "adm_finduser")])
        rows.append([btn("🔗 دعوت‌ها", "adm_referrals"),
                     btn("💳 پرداخت‌ها", "adm_payments")])
    if "reports.view" in perms or "bans.manage" in perms or "appeals.review" in perms:
        rows.append([btn("🚩 گزارش‌ها", "adm_reports"),
                     btn("⛔ مسدودی‌ها", "adm_bans")])
    if "appeals.review" in perms or "bans.manage" in perms:
        rows.append([btn("📩 اعتراض‌ها", "adm_appeals")])
    if "moderation.manage" in perms:
        rows.append([btn("🧹 فیلتر و واژه‌نامه", "admmod")])
    if "media.configure" in perms:
        rows.append([btn("📎 نوع رسانه‌ها", "admmedia")])
    if "support.view" in perms:
        rows.append([btn("💬 تیکت‌های پشتیبانی", "adm_tickets")])
    if "economy.view" in perms:
        rows.append([btn("💰 تنظیمات اقتصادی", "adm_eco")])
    if "store.manage" in perms:
        rows.append([btn("🛒 قیمت‌های فروشگاه", "adm_store")])
    if "broadcast.send" in perms:
        rows.append([btn("📢 پیام همگانی", "adm_bcast"),
                     btn("📋 کمپین‌ها", "admbcasts")])
    if "backups.view" in perms:
        rows.append([btn("🔐 پشتیبان‌گیری", "adm_backup")])
    if role == "owner":
        rows.append([btn("♻️ بازیابی از فایل", "adm_restore")])
    if "settings.manage" in perms or "matching.configure" in perms or "privacy.manage" in perms:
        rows.append([btn("⚙️ تنظیمات", "adm_settings")])
    if "platforms.manage" in perms:
        rows.append([btn("🖥 پلتفرم‌ها", "adm_platforms")])
    if "audit.view" in perms:
        rows.append([btn("🧾 لاگ عملیات", "adm_logs")])
    if "monitoring.view" in perms:
        rows.append([btn("📈 مانیتورینگ", "adm_monitor")])
    if role == "owner":
        rows.append([btn("👥 مدیران", "adm_admins")])
    # one-tap operations
    ops = []
    if "backups.create" in perms:
        ops.append(btn("⚡ بکاپ فوری", "adm_backup_now"))
    if "settings.manage" in perms:
        async with session_scope() as s:
            cfg = await service.get_setting(s, "app")
        on = cfg.get("maintenanceMode", False)
        ops.append(btn(f"🔧 تعمیر: {'🟢 روشن' if on else '🔴 خاموش'}", "adm_maint"))
    if ops:
        rows.append(ops)
    rows.append([btn("🏠 بازگشت به منوی اصلی", "back_home")])
    head = "🛡 پنل مدیریت هم‌چت\n👤 نقش شما: " + ROLE_LABEL.get(role, role)
    if note:
        head = note + "\n\n" + head
    await _say(user, head + "\n💡 یک دسته را انتخاب کنید:", keyboard=rows or None)


async def _stats(user: User) -> None:
    async with session_scope() as s:
        users = (await s.execute(select(func.count(User.id)))).scalar()
        tg = (await s.execute(select(func.count(User.id)).where(User.platform == "telegram"))).scalar()
        bal = (await s.execute(select(func.count(User.id)).where(User.platform == "bale"))).scalar()
        active_u = (await s.execute(select(func.count(User.id)).where(User.status == "active"))).scalar()
        chats = (await s.execute(select(func.count(db.Chat.id)).where(db.Chat.status == "active"))).scalar()
        msgs = (await s.execute(select(func.count(db.ChatMessage.id)))).scalar()
        pend = (await s.execute(select(func.count(Report.id)).where(Report.status == "pending"))).scalar()
        coins = (await s.execute(select(func.sum(User.coin_balance)))).scalar() or 0
        bans_n = (await s.execute(select(func.count(db.Ban.id)).where(db.Ban.status == "active"))).scalar()
        queue = (await s.execute(select(func.count(db.QueueEntry.id)))).scalar()
        tickets = (await s.execute(select(func.count(SupportTicket.id)).where(
            SupportTicket.status != "closed"))).scalar()
        pays = (await s.execute(select(func.count(Payment.id)).where(
            Payment.status == "succeeded"))).scalar()
    await _say(user, "📊 آمار کلی:\n"
                     f"👥 کاربران: {users} (🔵 تلگرام: {tg} | 🟢 بله: {bal} | ✅ فعال: {active_u})\n"
                     f"💬 گفتگوهای فعال: {chats} | 🔍 در صف: {queue}\n"
                     f"✉️ مجموع پیام‌ها: {msgs}\n"
                     f"🪙 سکه در گردش: {coins}\n"
                     f"🚩 گزارش‌های در انتظار: {pend}\n"
                     f"⛔ مسدودی‌های فعال: {bans_n}\n"
                     f"💬 تیکت‌های باز: {tickets}\n"
                     f"💳 پرداخت‌های موفق: {pays}",
               keyboard=kb([[btn("🔄 آپدیت", "adm_stats")],
                            [btn("🏠 بازگشت به منوی اصلی", "back_home")]]))


# ==================================================================== users ===
async def _lookup_users(q: str) -> list[User]:
    """Resolve ONE search box to zero or more users (v2.5).

    Accepted forms, in the order an admin is likely to type them:
      • numeric platform id   123456789      (Telegram or Bale id)
      • internal database id  123
      • username              @username / username  (case-insensitive)
      • public id             U1A2B3C4
      • platform:id           telegram:123456789 / bale:123456789
    """
    q = (q or "").strip()
    if not q:
        return []
    found: list[User] = []
    seen: set[int] = set()

    def _add(u: Optional[User]) -> None:
        if u is not None and u.id not in seen:
            seen.add(u.id)
            found.append(u)

    async with session_scope() as s:
        # explicit platform:id form — unambiguous, so stop right there
        if ":" in q:
            plat, _, pid = q.partition(":")
            plat = plat.strip().lower()
            pid = pid.strip().lstrip("@")
            if plat in ("telegram", "bale"):
                _add((await s.execute(select(User).where(
                    User.platform == plat, User.platform_user_id == pid))).scalar_one_or_none())
            return found

        # @username (or a bare handle) — usernames are case-insensitive
        if q.startswith("@"):
            uname = q[1:].strip().lower()
            if uname:
                for u in (await s.execute(select(User).where(
                        func.lower(func.coalesce(User.username, "")) == uname).limit(10))).scalars():
                    _add(u)
            return found

        # pure number: platform id first, then the internal row id
        if q.isdigit():
            for u in (await s.execute(select(User).where(
                    User.platform_user_id == q).limit(10))).scalars():
                _add(u)
            try:
                _add(await s.get(User, int(q)))
            except (ValueError, OverflowError):
                pass
            if found:
                return found

        # public id (exact, case-insensitive on input)
        _add((await s.execute(select(User).where(
            User.public_id == q.upper()))).scalar_one_or_none())

        # last resort: a bare username without the @ prefix
        uname = q.lower()
        if uname:
            for u in (await s.execute(select(User).where(
                    func.lower(func.coalesce(User.username, "")) == uname).limit(10))).scalars():
                _add(u)
    return found


async def _user_actions(user: User, role: str, perms: list, target: User) -> None:
    async with session_scope() as s:
        ban = await service.active_ban(s, target)
        info = (f"👤 {target.display_name}\n"
                f"🆔 شناسه: {target.public_id} | ردیف: {target.id}\n"
                f"🔢 آی‌دی {PLATFORM_TITLE.get(target.platform, target.platform)}: "
                f"{target.platform_user_id}\n"
                f"🔗 یوزرنیم: {'@' + target.username if target.username else '—'}\n"
                f"پلتفرم: {target.platform} | سن: {target.age} | {target.gender}\n"
                f"سکه: {target.coin_balance} | وضعیت: {target.status}\n"
                f"گفتگوها: {target.total_chats} | پیام‌ها: {target.total_messages}\n"
                f"گزارش‌های دریافتی: {target.reports_received}\n"
                f"مسدودی فعال: {'بله — ' + (ban.reason or '') if ban else 'نه'}")
    rows = []
    if "users.ban" in perms:
        rows.append([btn("⛔ مسدود ۲۴س", f"admub_{target.id}_24"),
                     btn("⛔ مسدود ۷روزه", f"admub_{target.id}_168"),
                     btn("♾ مسدود همیشگی", f"admub_{target.id}_perm")])
        if ban:
            rows.append([btn("✅ رفع مسدودی", f"admub_{target.id}_unban")])
    if "users.coins" in perms:
        rows.append([btn("🪙 +۱۰ سکه", f"admuc_{target.id}_10"),
                     btn("🪙 +۱۰۰ سکه", f"admuc_{target.id}_100")])
        rows.append([btn("🪙 −۱۰ سکه", f"admuc_{target.id}_-10"),
                     btn("🪙 −۱۰۰ سکه", f"admuc_{target.id}_-100")])
    rows.append([btn("🔎 جستجوی کاربر دیگر", "adm_finduser")])
    rows.append([btn("🏠 بازگشت به منوی اصلی", "back_home")])
    await _say(user, info, keyboard=kb(*rows) if rows else None)


async def _users_list(user: User, page: int = 0) -> None:
    async with session_scope() as s:
        total = (await s.execute(select(func.count(User.id)))).scalar()
        rows_q = (await s.execute(select(User).order_by(User.id.desc())
                                  .offset(page * PAGE).limit(PAGE))).scalars()
        users = list(rows_q)
    if not users:
        await _say(user, "👥 کاربری ثبت نشده است.",
                   keyboard=kb([[btn("🏠 بازگشت به منوی اصلی", "back_home")]]))
        return
    lines = []
    rows = []
    for u in users:
        lines.append(f"• {u.public_id} | {u.display_name[:20]} | {u.platform} | "
                     f"سکه {u.coin_balance} | {u.status}")
        rows.append([btn(f"👤 {u.public_id}", f"admuser_{u.id}")])
    for r in _nav("adm_users", page, _pages(total)):
        rows.append(r)
    await _say(user, "👥 کاربران (جدیدترین اول):\n\n" + "\n".join(lines), keyboard=kb(*rows))


async def _referrals_list(user: User, page: int = 0) -> None:
    async with session_scope() as s:
        total = (await s.execute(select(func.count(Referral.id)))).scalar()
        rows_q = (await s.execute(select(Referral).order_by(Referral.id.desc())
                                  .offset(page * PAGE).limit(PAGE))).scalars()
        refs = list(rows_q)
        ids = {r.referrer_id for r in refs} | {r.referred_id for r in refs}
        people = {u.id: u for u in (await s.execute(select(User).where(User.id.in_(ids)))).scalars()} if ids else {}
        eco = await service.get_setting(s, "economy")
    lines = []
    rows = []
    for r in refs:
        ref = people.get(r.referrer_id)
        new = people.get(r.referred_id)
        lines.append(f"• {ref.public_id if ref else '?'} → {new.public_id if new else '?'} "
                     f"| {r.status} | {r.created_at:%Y-%m-%d}")
        rows.append([btn(f"🔗 {r.id}", f"admref_{r.id}")])
    for r in _nav("adm_referrals", page, _pages(total)):
        rows.append(r)
    await _say(user, f"🔗 دعوت‌ها (هر طرف {eco.get('referralRewardPerParty')} سکه):\n\n"
                     + ("\n".join(lines) or "موردی نیست."),
               keyboard=kb(*rows))


async def _referral_detail(user: User, rid: int) -> None:
    async with session_scope() as s:
        r = await s.get(Referral, rid)
        if not r:
            await _say(user, "دعوت یافت نشد.")
            return
        ref = await s.get(User, r.referrer_id)
        new = await s.get(User, r.referred_id)
        eco = await service.get_setting(s, "economy")
        txt = (f"🔗 دعوت #{r.id}\n"
               f"کد: {r.code}\n"
               f"دعوت‌کننده: {ref.public_id if ref else '?'} "
               f"({ref.display_name if ref else '?'})\n"
               f"دعوت‌شده: {new.public_id if new else '?'} "
               f"({new.display_name if new else '?'})\n"
               f"وضعیت: {r.status}\n"
               f"پاداش هر طرف: {eco.get('referralRewardPerParty')} سکه\n"
               f"امتیاز تقلب: {r.fraud_score}\n"
               f"دلایل: {', '.join(r.fraud_reasons or []) or '—'}\n"
               f"زمان: {r.created_at:%Y-%m-%d %H:%M}")
    await _say(user, txt, keyboard=kb([[btn("◀️ بازگشت به دعوت‌ها", "adm_referrals")]]))


async def _payments_list(user: User, page: int = 0) -> None:
    async with session_scope() as s:
        total = (await s.execute(select(func.count(Payment.id)))).scalar()
        pays = list((await s.execute(select(Payment).order_by(Payment.id.desc())
                                     .offset(page * PAGE).limit(PAGE))).scalars())
        uids = {p.user_id for p in pays}
        people = {u.id: u for u in (await s.execute(select(User).where(User.id.in_(uids)))).scalars()} if uids else {}
    rows = []
    lines = []
    for p in pays:
        u = people.get(p.user_id)
        cur = "⭐" if p.currency == "XTR" else "ریال"
        lines.append(f"• #{p.id} {u.public_id if u else '?'} | {p.coins} سکه | "
                     f"{p.amount:,} {cur} | {p.status}")
        if p.status in ("succeeded","refund_pending") and p.platform == "telegram":
            rows.append([btn(f"💳 استرداد #{p.id}", f"admpay_{p.id}_refund")])
    for r in _nav("adm_payments", page, _pages(total)):
        rows.append(r)
    await _say(user, "💳 آخرین پرداخت‌ها:\n\n" + ("\n".join(lines) or "موردی نیست."),
               keyboard=kb(*rows))


# =============================================================== moderation ===
async def _reports_list(user: User, page: int = 0) -> None:
    async with session_scope() as s:
        total = (await s.execute(select(func.count(Report.id)))).scalar()
        reps = list((await s.execute(select(Report).order_by(Report.id.desc())
                                     .offset(page * SMALL_PAGE).limit(SMALL_PAGE))).scalars())
        uids = {r.reporter_id for r in reps} | {r.reported_id for r in reps}
        people = {u.id: u for u in (await s.execute(select(User).where(User.id.in_(uids)))).scalars()} if uids else {}
    rows = []
    lines = []
    for r in reps:
        rep_u = people.get(r.reported_id)
        rep_by = people.get(r.reporter_id)
        lines.append(f"#{r.id} [{r.status}] گزارش‌شده: {rep_u.public_id if rep_u else '?'} "
                     f"({rep_by.public_id if rep_by else '?'}) | {r.reason}\n"
                     f"   {r.details[:60]}")
        rows.append([btn(f"🚩 بررسی #{r.id}", f"admrep_{r.id}")])
    for r in _nav("adm_reports", page, _pages(total, SMALL_PAGE)):
        rows.append(r)
    await _say(user, "🚩 گزارش‌ها:\n\n" + ("\n\n".join(lines) or "موردی نیست."),
               keyboard=kb(*rows))


async def _report_detail(user: User, role: str, perms: list, rid: int) -> None:
    async with session_scope() as s:
        r = await s.get(Report, rid)
        if not r:
            await _say(user, "گزارش یافت نشد.")
            return
        rep_u = await s.get(User, r.reported_id)
        rep_by = await s.get(User, r.reporter_id)
        from datetime import timedelta
        uniq = (await s.execute(select(func.count(func.distinct(Report.reporter_id))).where(
            Report.reported_id == r.reported_id,
            Report.created_at >= utcnow() - timedelta(hours=24)))).scalar()
        txt = (f"🚩 گزارش #{r.id} — وضعیت: {r.status}\n"
               f"گزارش‌دهنده: {rep_by.public_id if rep_by else '?'} "
               f"({rep_by.display_name if rep_by else '?'})\n"
               f"گزارش‌شده: {rep_u.public_id if rep_u else '?'} "
               f"({rep_u.display_name if rep_u else '?'}) — "
               f"{rep_u.ban_count if rep_u else 0} مسدودی قبلی\n"
               f"دلیل: {r.reason}\nشرح: {r.details or '—'}\n"
               f"گزارش‌های مستقل ۲۴س: {uniq}\n"
               f"زمان: {r.created_at:%Y-%m-%d %H:%M}")
        rows = []
        if "reports.resolve" in perms and r.status in ("pending", "under_review"):
            rows.append([btn("✅ رد گزارش", f"admrep_{r.id}_dismiss"),
                         btn("⛔ مسدود ۲۴س", f"admrep_{r.id}_tban")])
            rows.append([btn("♾ مسدود همیشگی", f"admrep_{r.id}_pban")])
        rows.append([btn("◀️ بازگشت به گزارش‌ها", "adm_reports")])
        rows.append([btn("🏠 بازگشت به منوی اصلی", "back_home")])
    await _say(user, txt, keyboard=kb(*rows))


async def _resolve_report(user: User, role: str, rid: int, action: str) -> None:
    async with session_scope() as s:
        r = await s.get(Report, rid)
        if not r:
            await _say(user, "گزارش یافت نشد.")
            return
        if action == "dismiss":
            r.status = "dismissed"
            msg = f"✅ گزارش #{r.id} رد شد."
        else:
            r.status = "action_taken"
            target = await s.get(User, r.reported_id)
            if target:
                await service.ban_user(
                    s, target, "permanent" if action == "pban" else "temporary",
                    "manual_admin", f"اقدام بر اساس گزارش #{r.id}", f"bot:{user.platform_user_id}",
                    hours=None if action == "pban" else 24)
            msg = (f"♾ کاربر برای همیشه مسدود شد (گزارش #{r.id})." if action == "pban"
                   else f"⛔ کاربر ۲۴ ساعت مسدود شد (گزارش #{r.id}).")
        r.resolved_by = f"bot:{user.platform_user_id}"
        r.resolved_at = utcnow()
        await service.audit(s, actor_type="bot", admin_id=user.platform_user_id,
                            admin_name=user.display_name, admin_role=role,
                            action="report.resolved", module="reports",
                            target_type="report", target_id=r.id,
                            details=action)
    await _say(user, msg, keyboard=kb([[btn("◀️ بازگشت به گزارش‌ها", "adm_reports")]]))


async def _bans_list(user: User, page: int = 0) -> None:
    async with session_scope() as s:
        total = (await s.execute(select(func.count(Ban.id)).where(Ban.status == "active"))).scalar()
        bans = list((await s.execute(select(Ban).where(Ban.status == "active")
                                     .order_by(Ban.id.desc())
                                     .offset(page * PAGE).limit(PAGE))).scalars())
        uids = {b.user_id for b in bans}
        people = {u.id: u for u in (await s.execute(select(User).where(User.id.in_(uids)))).scalars()} if uids else {}
    rows = []
    lines = []
    for b in bans:
        u = people.get(b.user_id)
        lines.append(f"⛔ #{b.id} {u.public_id if u else '?'} | {b.type} | {b.reason[:40]} "
                     f"| تا {b.expires_at or 'همیشه'}")
        rows.append([btn(f"✅ رفع #{b.id}", f"admban_{b.id}_revoke")])
    for r in _nav("adm_bans", page, _pages(total)):
        rows.append(r)
    await _say(user, "⛔ مسدودی‌های فعال:\n\n" + ("\n".join(lines) or "موردی نیست."),
               keyboard=kb(*rows))


async def _appeals_list(user: User, page: int = 0) -> None:
    async with session_scope() as s:
        total = (await s.execute(select(func.count(Appeal.id)).where(
            Appeal.status == "pending"))).scalar()
        aps = list((await s.execute(select(Appeal).where(Appeal.status == "pending")
                                     .order_by(Appeal.id.desc())
                                     .offset(page * SMALL_PAGE).limit(SMALL_PAGE))).scalars())
        uids = {a.user_id for a in aps}
        people = {u.id: u for u in (await s.execute(select(User).where(User.id.in_(uids)))).scalars()} if uids else {}
    rows = []
    lines = []
    for a in aps:
        u = people.get(a.user_id)
        lines.append(f"📩 #{a.id} {u.public_id if u else '?'} | ban#{a.ban_id} | "
                     f"{a.message[:60]}")
        rows.append([btn(f"✅ پذیرش #{a.id}", f"admapp_{a.id}_approve"),
                     btn(f"❌ رد #{a.id}", f"admapp_{a.id}_reject")])
    for r in _nav("adm_appeals", page, _pages(total, SMALL_PAGE)):
        rows.append(r)
    await _say(user, "📩 اعتراض‌های در انتظار:\n\n" + ("\n".join(lines) or "موردی نیست."),
               keyboard=kb(*rows))


async def _review_appeal(user: User, role: str, aid: int, decision: str) -> None:
    async with session_scope() as s:
        a = await s.get(Appeal, aid)
        if not a:
            await _say(user, "اعتراض یافت نشد.")
            return
        a.status = "approved" if decision == "approve" else "rejected"
        a.reviewed_by = f"bot:{user.platform_user_id}"
        a.reviewed_at = utcnow()
        a.review_note = "بررسی از پنل بازو"
        if decision == "approve":
            b = await s.get(Ban, a.ban_id)
            if b:
                b.status = "revoked"
            u = await s.get(User, a.user_id)
            if u:
                u.status = "active"
                await _notify_target(u, "admin_unban_notice")
        await service.audit(s, actor_type="bot", admin_id=user.platform_user_id,
                            admin_name=user.display_name, admin_role=role,
                            action="appeal.approved" if decision == "approve" else "appeal.rejected",
                            module="bans", target_type="appeal", target_id=a.id,
                            details=decision)
    await _say(user, "✅ اعتراض پذیرفته و مسدودی رفع شد." if decision == "approve"
               else "❌ اعتراض رد شد.",
               keyboard=kb([[btn("◀️ بازگشت به اعتراض‌ها", "adm_appeals")]]))


async def _moderation_panel(user: User) -> None:
    async with session_scope() as s:
        cfg = await service.get_setting(s, "moderation")
        words = (await s.execute(select(func.count(DictionaryWord.id)))).scalar()
        allows = (await s.execute(select(func.count(AllowlistPhrase.id)))).scalar()
    rows = [
        [btn(f"📚 واژه‌نامه ({words})", "admdict")],
        [btn(f"✅ allowlist ({allows})", "admallow")],
        [btn("🎛 قوانین فیلتر", "admmodcfg")],
        [btn("🏠 بازگشت به منوی اصلی", "back_home")],
    ]
    txt = ("🧹 مدیریت فیلتر و واژه‌نامه:\n\n"
           f"• مسدودسازی لینک‌ها: {_on_off(cfg.get('blockUrls', True))}\n"
           f"• مسدودسازی آیدی تلگرام: {_on_off(cfg.get('blockTelegramHandles', True))}\n"
           f"• مسدودسازی شماره تماس: {_on_off(cfg.get('blockPhoneNumbers', True))}\n"
           f"• مسدودسازی آیدی اینستاگرام: {_on_off(cfg.get('blockInstagramIds', True))}\n"
           f"• آستانه مسدودی خودکار: {cfg.get('autoBanReportThreshold', 3)} گزارش\n"
           f"• مدت مسدودی خودکار: {cfg.get('autoBanDurationHours', 24)} ساعت")
    await _say(user, txt, keyboard=kb(*rows))


async def _mod_config_panel(user: User) -> None:
    async with session_scope() as s:
        cfg = await service.get_setting(s, "moderation")
    rows = [
        [btn(f"🔗 لینک‌ها: {_on_off(cfg.get('blockUrls', True))}", "admmodcfg_blockUrls"),
         btn(f"👤 آیدی تلگرام: {_on_off(cfg.get('blockTelegramHandles', True))}",
             "admmodcfg_blockTelegramHandles")],
        [btn(f"📞 شماره تماس: {_on_off(cfg.get('blockPhoneNumbers', True))}",
             "admmodcfg_blockPhoneNumbers"),
         btn(f"📸 اینستاگرام: {_on_off(cfg.get('blockInstagramIds', True))}",
             "admmodcfg_blockInstagramIds")],
        [btn("🔢 آستانه گزارش → متن", "admmodnum_autoBanReportThreshold"),
         btn("⏱ مدت مسدودی (ساعت)", "admmodnum_autoBanDurationHours")],
        [btn("◀️ بازگشت", "admmod")],
    ]
    await _say(user, "🎛 قوانین فیلتر — برای تغییر، دکمه موردنظر را بزن:",
               keyboard=kb(*rows))


async def _dictionary_panel(user: User, page: int = 0) -> None:
    async with session_scope() as s:
        total = (await s.execute(select(func.count(DictionaryWord.id)))).scalar()
        words = list((await s.execute(select(DictionaryWord).order_by(DictionaryWord.id.desc())
                                      .offset(page * PAGE).limit(PAGE))).scalars())
    rows = [[btn("➕ افزودن واژه", "admdictadd")]]
    lines = [f"• {w.word} | {w.severity} | {w.action} | {w.match_count} بار" for w in words]
    for w in words:
        rows.append([btn(f"🗑 {w.word}", f"admdictdel_{w.id}")])
    for r in _nav("admdict", page, _pages(total)):
        rows.append(r)
    await _say(user, "📚 واژه‌نامه فیلتر:\n\n" + ("\n".join(lines) or "خالی است."), keyboard=kb(*rows))


async def _allowlist_panel(user: User, page: int = 0) -> None:
    async with session_scope() as s:
        total = (await s.execute(select(func.count(AllowlistPhrase.id)))).scalar()
        items = list((await s.execute(select(AllowlistPhrase).order_by(AllowlistPhrase.id.desc())
                                      .offset(page * PAGE).limit(PAGE))).scalars())
    rows = [[btn("➕ افزودن عبارت مجاز", "admallowadd")]]
    lines = [f"• {p.phrase}" for p in items]
    for p in items:
        rows.append([btn(f"🗑 {p.phrase}", f"admallowdel_{p.id}")])
    for r in _nav("admallow", page, _pages(total)):
        rows.append(r)
    await _say(user, "✅ allowlist (عبارت‌های مجاز):\n\n" + ("\n".join(lines) or "خالی است."),
               keyboard=kb(*rows))


async def _media_panel(user: User) -> None:
    async with session_scope() as s:
        rows_db = {m.key: m.enabled for m in (await s.execute(select(MediaType))).scalars()}
    rows = []
    lines = []
    for key in service.MEDIA_KEYS:
        enabled = rows_db.get(key, True)
        lines.append(f"• {key}: {'مجاز ✅' if enabled else 'مسدود ⛔'}")
        rows.append([btn(f"{'✅' if enabled else '⛔'} {key}", f"admmedia_{key}")])
    rows.append([btn("🏠 بازگشت به منوی اصلی", "back_home")])
    await _say(user, "📎 نوع رسانه‌های مجاز در گفتگو:\n\n" + "\n".join(lines), keyboard=kb(*rows))


# =================================================================== economy ===
async def _eco_panel(user: User, perms: list) -> None:
    async with session_scope() as s:
        eco = await service.get_setting(s, "economy")
    keys = [("initial", "initialReward", "🎁 ثبت‌نام"), ("message", "messageCost", "💬 هر پیام"),
            ("daily", "dailyBonus", "📅 پاداش روزانه"),
            ("gender", "genderFilterMatchCost", "🎯 فیلتر جنسیت"),
            ("referral", "referralRewardPerParty", "👥 دعوت (هر طرف)"),
            ("namechg", "nameChangeCost", "📛 تغییر نام"),
            ("agechg", "ageChangeCost", "🎂 تغییر سن"),
            ("bday", "birthdayReward", "🎉 ثبت تولد"),
            ("link", "accountLinkReward", "🔗 اتصال حساب")]
    txt = "💰 تنظیمات اقتصادی:\n\n"
    rows = []
    for short, key, label in keys:
        txt += f"• {label}: {eco.get(key, 0)} سکه\n"
        if "economy.configure" in perms:
            rows.append([btn(f"✏️ {label}: {eco.get(key, 0)}", f"admeco_{short}")])
    if "economy.configure" in perms:
        txt += "\n✏️ یا بفرستید: set eco KEY VALUE"
        user.state = {"step": "adm_eco_set"}
    rows.append([btn("🏠 بازگشت به منوی اصلی", "back_home")])
    await _say(user, txt, keyboard=kb(*rows))


async def _store_panel(user: User, perms: list) -> None:
    async with session_scope() as s:
        pkgs = list((await s.execute(select(StorePackage).order_by(StorePackage.coins))).scalars())
        store = await service.get_setting(s, "store")
        app_cfg = await service.get_setting(s, "app")
    lines = []
    rows = []
    for p in pkgs:
        price_stars = service.coins_price_stars(store, p.coins)
        price_toman = service.coins_price_toman(store, p.coins)
        lines.append(f"• {p.coins} سکه → ⭐{price_stars} | {price_toman:,} تومان "
                     f"| {'فعال' if p.active else 'غیرفعال'} "
                     f"(TG {'✅' if p.telegram_enabled else '⛔'} / "
                     f"Bale {'✅' if p.bale_enabled else '⛔'})")
        if "store.manage" in perms:
            rows.append([btn(f"{'🟢' if p.active else '⚪'} {p.coins} سکه", f"admpkg_{p.id}_on"),
                         btn(f"🔵 TG {'✅' if p.telegram_enabled else '⛔'}", f"admpkg_{p.id}_tg"),
                         btn(f"🟢 Bale {'✅' if p.bale_enabled else '⛔'}", f"admpkg_{p.id}_bale")])
    txt = ("🛒 قیمت‌های فروشگاه (نرخ جاری: هر سکه = "
           f"{store.get('tomanPerCoin', 500):,} تومان | هر ⭐ استارز = "
           f"{store.get('coinsPerStar', 20)} سکه):\n\n" + "\n".join(lines))
    if "store.manage" in perms:
        tg_eff = app_cfg.get("paymentsTelegramEnabled", True) and bool(settings.telegram_bot_token)
        bale_eff = app_cfg.get("paymentsBaleEnabled", False) and bool(settings.bale_provider_token)
        tg_tok = "✓" if settings.telegram_bot_token else "✗ توکن تنظیم نشده"
        bale_tok = "✓" if settings.bale_provider_token else "✗ توکن تنظیم نشده"
        txt += ("\n\n💳 درگاه پرداخت تلگرام: " + ("روشن ✅" if tg_eff else "خاموش ⛔")
                + f" (کلید: {'روشن' if app_cfg.get('paymentsTelegramEnabled', True) else 'خاموش'} | توکن: {tg_tok})"
                + "\n💳 درگاه پرداخت بله: " + ("روشن ✅" if bale_eff else "خاموش ⛔")
                + f" (کلید: {'روشن' if app_cfg.get('paymentsBaleEnabled', False) else 'خاموش'} | توکن: {bale_tok})")
        rows.extend([
            [btn("🪙 تغییر نرخ تومان/سکه", "adm_store_rate")],
            [btn("⭐ تغییر سکه به ازای هر استارز", "adm_store_star")],
            [btn(f"💳 کلید پرداخت تلگرام: {'روشن' if app_cfg.get('paymentsTelegramEnabled', True) else 'خاموش'}",
                 "adm_pay_tg"),
             btn(f"💳 کلید پرداخت بله: {'روشن' if app_cfg.get('paymentsBaleEnabled', False) else 'خاموش'}",
                 "adm_pay_bale")],
            [btn("🏠 بازگشت به منوی اصلی", "back_home")],
        ])
        await _say(user, txt, keyboard=kb(*rows))
    else:
        await _say(user, txt, keyboard=kb([[btn("🏠 بازگشت به منوی اصلی", "back_home")]]))


# =================================================================== support ===
async def _tickets_list(user: User, page: int = 0) -> None:
    async with session_scope() as s:
        total = (await s.execute(select(func.count(SupportTicket.id)))).scalar()
        tickets = list((await s.execute(select(SupportTicket).order_by(SupportTicket.id.desc())
                                        .offset(page * SMALL_PAGE).limit(SMALL_PAGE))).scalars())
        uids = {t.user_id for t in tickets}
        people = {u.id: u for u in (await s.execute(select(User).where(User.id.in_(uids)))).scalars()} if uids else {}
    rows = []
    lines = []
    for t in tickets:
        u = people.get(t.user_id)
        lines.append(f"• {t.ticket_no} [{t.status}] {u.public_id if u else '?'} | {t.subject[:40]}")
        rows.append([btn(f"💬 {t.ticket_no}", f"admtkt_{t.id}")])
    for r in _nav("adm_tickets", page, _pages(total, SMALL_PAGE)):
        rows.append(r)
    await _say(user, "💬 آخرین تیکت‌های پشتیبانی:\n\n" + ("\n".join(lines) or "موردی نیست."),
               keyboard=kb(*rows))


async def _ticket_detail(user: User, role: str, perms: list, tid: int) -> None:
    async with session_scope() as s:
        t = await s.get(SupportTicket, tid)
        if not t:
            await _say(user, "تیکت یافت نشد.")
            return
        u = await s.get(User, t.user_id)
        msgs = list((await s.execute(select(SupportMessage).where(
            SupportMessage.ticket_id == t.id).order_by(SupportMessage.id.desc()).limit(6))).scalars())
        msgs.reverse()
        txt = (f"💬 تیکت {t.ticket_no} — {t.status}\n"
               f"کاربر: {u.public_id if u else '?'} | {u.platform if u else '?'}\n"
               f"موضوع: {t.subject}\nدسته: {t.category} | اولویت: {t.priority}\n\n")
        for m in msgs:
            who = "مدیر" if m.sender_type == "admin" else "کاربر"
            txt += f"[{who}] {m.body[:300]}\n"
        rows = []
        if "support.reply" in perms:
            rows.append([btn("✍️ پاسخ", f"admtkt_{t.id}_reply")])
        rows.append([btn("🔒 بستن", f"admtkt_{t.id}_close"),
                     btn("🔓 بازکردن", f"admtkt_{t.id}_reopen")])
        rows.append([btn("◀️ بازگشت به تیکت‌ها", "adm_tickets")])
        rows.append([btn("🏠 بازگشت به منوی اصلی", "back_home")])
    await _say(user, txt, keyboard=kb(*rows))


async def _ticket_status(user: User, role: str, tid: int, status: str) -> None:
    async with session_scope() as s:
        t = await s.get(SupportTicket, tid)
        if not t:
            await _say(user, "تیکت یافت نشد.")
            return
        t.status = status
        t.updated_at = utcnow()
        await service.audit(s, actor_type="bot", admin_id=user.platform_user_id,
                            admin_name=user.display_name, admin_role=role,
                            action="support.status", module="support",
                            target_type="ticket", target_id=t.ticket_no, details=status)
    label = "بسته" if status == "closed" else "باز"
    await _say(user, f"✅ تیکت {label} شد.",
               keyboard=kb([[btn("◀️ بازگشت به تیکت‌ها", "adm_tickets")]]))


# ================================================================= broadcasts ===
async def _bcasts_list(user: User, page: int = 0) -> None:
    async with session_scope() as s:
        total = (await s.execute(select(func.count(db.BroadcastCampaign.id)))).scalar()
        camps = list((await s.execute(select(db.BroadcastCampaign)
                                      .order_by(db.BroadcastCampaign.id.desc())
                                      .offset(page * SMALL_PAGE).limit(SMALL_PAGE))).scalars())
    rows = []
    lines = []
    for c in camps:
        lines.append(f"• #{c.id} [{c.status}] {c.audience_label} → {c.delivered_count}/"
                     f"{c.target_count} | {c.title}")
        if c.status in ("sending", "paused"):
            rows.append([btn(f"⏸ توقف #{c.id}", f"admbc_{c.id}_pause"),
                         btn(f"▶️ ادامه #{c.id}", f"admbc_{c.id}_resume")])
        if c.status in ("sending", "paused", "scheduled"):
            rows.append([btn(f"❌ لغو #{c.id}", f"admbc_{c.id}_cancel")])
    for r in _nav("admbcasts", page, _pages(total, SMALL_PAGE)):
        rows.append(r)
    await _say(user, "📋 کمپین‌های پیام همگانی:\n\n" + ("\n".join(lines) or "موردی نیست."),
               keyboard=kb(*rows))


async def _bcast_action(user: User, role: str, cid: int, action: str) -> None:
    async with session_scope() as s:
        c = await s.get(db.BroadcastCampaign, cid)
        if not c:
            await _say(user, "کمپین یافت نشد.")
            return
        if action == "pause" and c.status == "sending":
            c.status = "paused"
            msg = "⏸ کمپین متوقف شد."
        elif action == "resume" and c.status == "paused":
            c.status = "sending"
            msg = "▶️ کمپین ادامه می‌یابد."
        elif action == "cancel":
            c.status = "cancelled"
            msg = "❌ کمپین لغو شد."
        else:
            msg = "⚠️ این عملیات برای وضعیت فعلی کمپین مجاز نیست."
        await service.audit(s, actor_type="bot", admin_id=user.platform_user_id,
                            admin_name=user.display_name, admin_role=role,
                            action=f"broadcast.{action}", module="broadcast",
                            target_type="campaign", target_id=c.id, details=c.status)
    await _say(user, msg, keyboard=kb([[btn("◀️ بازگشت به کمپین‌ها", "admbcasts")]]))


# ================================================================== settings ===
APP_TOGGLES = [
    ("allowNewRegistrations", "👥 ثبت‌نام جدید"),
    ("enableReferralSystem", "🔗 سیستم دعوت"),
    ("enableDailyRewards", "🎁 پاداش روزانه"),
    ("enableGenderFilter", "🚻 فیلتر جنسیت"),
    ("enableSupportTickets", "💬 تیکت پشتیبانی"),
]


async def _settings_panel(user: User, perms: list) -> None:
    async with session_scope() as s:
        cfg = await service.get_setting(s, "app")
        match = await service.get_setting(s, "matching")
        privacy = await service.get_setting(s, "privacy")
    rows = []
    for key, label in APP_TOGGLES:
        rows.append([btn(f"{label}: {_on_off(cfg.get(key, True))}", f"admset_{key}")])
    rows.append([btn(f"🧩 جستجو و هم‌چت‌سازی: {'فعال' if match.get('enabled', True) else 'غیرفعال'}",
                     "admmatch")])
    rows.append([btn("🎁 پاداش روزانه", "admdaily")])
    rows.append([btn(f"🔒 حریم خصوصی ({privacy.get('chatRetentionDays', 30)} روز)",
                     "admpriv")])
    if "settings.manage" in perms:
        rows.append([btn(f"🔧 حالت تعمیر: {_on_off(cfg.get('maintenanceMode', False))}",
                         "adm_maint")])
    rows.append([btn("🏠 بازگشت به منوی اصلی", "back_home")])
    await _say(user, "⚙️ تنظیمات کلی — برای تغییر هر مورد، دکمه آن را بزن:",
               keyboard=kb(*rows))


async def _matching_panel(user: User) -> None:
    async with session_scope() as s:
        m = await service.get_setting(s, "matching")
    rows = [
        [btn(f"🧩 جستجو: {_on_off(m.get('enabled', True))}", "admmatch_enabled"),
         btn(f"🚻 فیلتر جنسیت: {_on_off(m.get('genderFilterEnabled', True))}",
             "admmatch_genderFilterEnabled")],
        [btn(f"🎂 تفکیک سنی: {_on_off(m.get('ageGroupSeparation', False))}",
             "admmatch_ageGroupSeparation")],
        [btn(f"⏳ تایم‌اوت جستجو: {m.get('searchTimeoutSec', 300)}s", "admmatchnum_searchTimeoutSec"),
         btn(f"💤 تایم‌اوت بی‌تحرکی: {m.get('idleTimeoutSec', 900)}s", "admmatchnum_idleTimeoutSec")],
        [btn(f"🔁 کوئل‌داون تکرار: {m.get('repeatMatchCooldownMin', 30)}m", "admmatchnum_repeatMatchCooldownMin"),
         btn(f"📏 سقف صف: {m.get('maxQueueSize', 1000)}", "admmatchnum_maxQueueSize")],
        [btn("◀️ بازگشت", "adm_settings")],
    ]
    await _say(user, "🧩 تنظیمات جستجو و هم‌چت‌سازی:", keyboard=kb(*rows))


async def _privacy_panel(user: User) -> None:
    async with session_scope() as s:
        p = await service.get_setting(s, "privacy")
    rows = [
        [btn(f"🗓 نگهداری چت: {p.get('chatRetentionDays', 30)} روز", "admprivdays"),
         btn(f"⏳ مهلت حذف حساب: {p.get('accountDeletionGraceHours', 0)}h", "admprivgrace")],
        [btn(f"🗑 حذف چت‌ها با حساب: {_on_off(p.get('deleteChatsOnAccountDeletion', True))}",
             "admprivtoggle_deleteChatsOnAccountDeletion")],
        [btn("📜 متن سیاست حریم خصوصی", "admprivpolicy")],
        [btn("📄 متن قوانین", "admprivrules")],
        [btn("◀️ بازگشت", "adm_settings")],
    ]
    await _say(user, "🔒 حریم خصوصی و نگهداری داده:", keyboard=kb(*rows))


async def _daily_panel(user: User) -> None:
    async with session_scope() as s:
        eco = await service.get_setting(s, "economy")
        app_cfg = await service.get_setting(s, "app")
        today = service.tehran_day()
        claims = (await s.execute(select(func.count(db.DailyClaim.id)).where(
            db.DailyClaim.day == today))).scalar()
    rows = [
        [btn(f"🎁 پاداش روزانه: {_on_off(app_cfg.get('enableDailyRewards', True))}",
             "admdailytoggle")],
        [btn(f"🪙 مقدار: {eco.get('dailyBonus', 5)} سکه", "admdailyamt")],
        [btn("◀️ بازگشت", "adm_settings")],
    ]
    await _say(user, f"🎁 پاداش روزانه — امروز {claims} نفر دریافت کرده‌اند:", keyboard=kb(*rows))


async def _platforms_panel(user: User) -> None:
    async with session_scope() as s:
        cfg = await service.get_setting(s, "platforms")
    rows = []
    for plat in ("telegram", "bale"):
        c = cfg.get(plat, {})
        token = settings.telegram_bot_token if plat == "telegram" else settings.bale_bot_token
        mode = ("webhook" if RUNTIME.get(f"webhook_set_{plat}")
                else "polling" if RUNTIME.get(f"adapter_{plat}") else "off")
        health = (RUNTIME.get("poll_health") or {}).get(plat) or {}
        rows.append([btn(f"{'🟢' if token else '🔴'} {plat} — {mode}",
                         f"admpl_{plat}_pollingFallback")])
        rows.append([btn(f"pollingFallback: {_on_off(c.get('pollingFallback', True))}",
                         f"admpl_{plat}_pollingFallback"),
                     btn(f"dropPending: {_on_off(c.get('dropPendingOnRestart', False))}",
                         f"admpl_{plat}_dropPendingOnRestart")])
        rows.append([btn(f"webhookMaxConnections: {c.get('webhookMaxConnections', 60)}",
                         "adm_platforms"),
                     btn(f"retryAttempts: {c.get('retryAttempts', 4)}", "adm_platforms")])
        if health:
            rows.append([btn(f"fails: {health.get('fails', 0)}", "adm_platforms")])
    rows.append([btn("🏠 بازگشت به منوی اصلی", "back_home")])
    await _say(user, "🖥 پلتفرم‌ها (توکن، حالت اتصال و تنظیمات):", keyboard=kb(*rows))


async def _monitor_panel(user: User) -> None:
    async with session_scope() as s:
        app_cfg = await service.get_setting(s, "app")
        queue = (await s.execute(select(func.count(db.QueueEntry.id)))).scalar()
        blocked = (await s.execute(select(func.count(User.id)).where(
            User.bot_blocked == True))).scalar()  # noqa
        active_b = (await s.execute(select(func.count(db.BroadcastCampaign.id)).where(
            db.BroadcastCampaign.status == "sending"))).scalar()
        pend_rep = (await s.execute(select(func.count(Report.id)).where(
            Report.status == "pending"))).scalar()
    lines = [f"🔧 حالت تعمیر: {_on_off(app_cfg.get('maintenanceMode', False))}",
             f"🔍 در صف جستجو: {queue}",
             f"🚫 کاربران بلاک‌شده (bot blocked): {blocked}",
             f"📢 کمپین‌های در حال ارسال: {active_b}",
             f"🚩 گزارش‌های در انتظار: {pend_rep}"]
    for plat in ("telegram", "bale"):
        h = (RUNTIME.get("poll_health") or {}).get(plat) or {}
        lines.append(f"• {plat}: آخرین موفقیت {int(h.get('ok', 0))} | خطا: {h.get('fails', 0)} "
                     f"| {h.get('last_error') or 'OK'}")
    rows = [[btn("🔄 آپدیت", "adm_monitor")],
            [btn("🏠 بازگشت به منوی اصلی", "back_home")]]
    await _say(user, "📈 مانیتورینگ:\n\n" + "\n".join(lines), keyboard=kb(*rows))


async def _logs_list(user: User, page: int = 0) -> None:
    async with session_scope() as s:
        total = (await s.execute(select(func.count(AuditLog.id)))).scalar()
        logs = list((await s.execute(select(AuditLog).order_by(AuditLog.id.desc())
                                     .offset(page * PAGE).limit(PAGE))).scalars())
    lines = [f"• {l.created_at:%m-%d %H:%M} | {l.action} | {l.admin_name or l.admin_id} "
             f"| {l.details[:40]}" for l in logs]
    rows = []
    for r in _nav("admlogs", page, _pages(total)):
        rows.append(r)
    await _say(user, "🧾 آخرین عملیات مدیریتی:\n\n" + ("\n".join(lines) or "موردی نیست."),
               keyboard=kb(*rows))


async def _admins_list(user: User, page: int = 0) -> None:
    async with session_scope() as s:
        total = (await s.execute(select(func.count(BotAdmin.id)))).scalar()
        admins = list((await s.execute(select(BotAdmin).order_by(BotAdmin.id)
                                       .offset(page * PAGE).limit(PAGE))).scalars())
    rows = [[btn("➕ افزودن مدیر", "admadminadd")]]
    lines = []
    for a in admins:
        lines.append(f"• {a.platform}:{a.platform_user_id} | {ROLE_LABEL.get(a.role, a.role)}")
        for role in ROLE_ORDER:
            mark = "✅ " if a.role == role else ""
            rows.append([btn(f"{mark}{ROLE_LABEL[role]}", f"admadminrole_{a.id}_{role}")])
        rows.append([btn(f"🗑 حذف {a.platform_user_id}", f"admadminx_{a.id}")])
    for r in _nav("admadmins", page, _pages(total)):
        rows.append(r)
    await _say(user, "👥 مدیران بازو (مالک‌ها از تنظیمات محیطی خوانده می‌شوند):\n\n"
                     + ("\n".join(lines) or "موردی نیست."), keyboard=kb(*rows))


# ================================================================== backups ===
async def _backup_panel(user: User, perms: list, role: str = "") -> None:
    async with session_scope() as s:
        cfg = await service.get_setting(s, "backup")
        recs = list((await s.execute(select(db.BackupRecord).order_by(
            db.BackupRecord.created_at.desc()).limit(5))).scalars())
    lines = [f"• {r.created_at:%m-%d %H:%M} | {r.trigger} | {r.status} | "
             f"{(r.size_bytes or 0) / 1e6:.1f} MB | ارسال: {r.delivery_status}"
             for r in recs] or ["موردی نیست."]
    rows = []
    if "backups.create" in perms:
        rows.append([btn("🚀 پشتیبان‌گیری فوری", "adm_backup_now")])
    if role == "owner":
        rows.append([btn("♻️ بازیابی از فایل", "adm_restore")])
    rows.append([btn("🏠 بازگشت به منوی اصلی", "back_home")])
    await _say(user, "🔐 پشتیبان‌گیری:\n"
                     f"زمان‌بندی: روزانه {cfg.get('scheduleTimeTehran', '03:00')} تهران\n"
                     f"آخرین اجرا: {cfg.get('lastRunDate') or '—'}\n"
                     f"نگهداری: {cfg.get('retentionDays', 90)} روز\n"
                     f"رمزنگاری: {'فعال' if cfg.get('encryptionEnabled', True) else 'غیرفعال'}\n"
                     "مقصد: همین بازو → مالک\n\n"
                     "📦 آخرین نسخه‌ها:\n" + "\n".join(lines), keyboard=kb(*rows))


# ================================================================ dispatcher ===
async def admin_callback(user: User, data: str, chat_id) -> None:
    if not data.startswith("adm"):
        return
    role = await get_role(user)
    if not role:
        return
    perms = await _perms(role)
    try:
        handled = await _dispatch(user, role, perms, data, chat_id)
    except Exception:
        log.exception("admin callback failed: %s", data)
        await _say(user, "⚠️ خطا در پردازش درخواست. دوباره تلاش کنید.")
        return
    if not handled:
        await _menu(user, role, note="⏳ این دکمه منقضی شده است. منوی تازه:")


async def _dispatch(user: User, role: str, perms: list, data: str, chat_id) -> bool:
    """Route one admin callback. Returns False when nothing matched."""
    # ---------------------------------------------------------- dashboard ---
    if data == "adm_stats" and "dashboard.view" in perms:
        await _stats(user)
        return True

    # -------------------------------------------------------------- users ---
    if data == "adm_finduser" and "users.view" in perms:
        user.state = {"step": "adm_finduser"}
        await _say(user, "🔎 جستجوی کاربر — کافی است یکی از این‌ها را بفرستید:\n\n"
                         "🔢 آی‌دی عددی: 123456789\n"
                         "🔗 یوزرنیم: @username یا username\n"
                         "🆔 شناسه اختصاصی: U1A2B3C4\n"
                         "📱 یا دقیق‌تر: telegram:123456789\n\n"
                         "💡 اگر چند کاربر هم‌نام پیدا شود، لیستشان را می‌دهی تا انتخاب کنی.\n"
                         "❌ لغو: «لغو»")
        return True

    if data == "adm_users" and "users.view" in perms:
        await _users_list(user, 0)
        return True
    if data.startswith("adm_users_") and "users.view" in perms:
        await _users_list(user, max(0, int(data.rsplit("_", 1)[1])))
        return True
    if data.startswith("admuser_") and "users.view" in perms:
        async with session_scope() as s:
            target = await s.get(User, int(data[8:]))
        if target:
            await _user_actions(user, role, perms, target)
        else:
            await _say(user, "کاربر یافت نشد.")
        return True

    if data == "adm_referrals" and "referrals.view" in perms:
        await _referrals_list(user, 0)
        return True
    if data.startswith("adm_referrals_") and "referrals.view" in perms:
        await _referrals_list(user, max(0, int(data.rsplit("_", 1)[1])))
        return True
    if data.startswith("admref_") and "referrals.view" in perms:
        await _referral_detail(user, int(data[len("admref_"):]))
        return True

    if data == "adm_payments" and "payments.view" in perms:
        await _payments_list(user, 0)
        return True
    if data.startswith("adm_payments_") and "payments.view" in perms:
        await _payments_list(user, max(0, int(data.rsplit("_", 1)[1])))
        return True

    if data.startswith("admpay_") and "payments.refund" in perms:
        _, rest = data.split("admpay_", 1)
        pid_s, _, act = rest.rpartition("_")
        await _refund_payment(user, role, int(pid_s))
        return True

    # ---------------------------------------------------------- moderation ---
    if data == "adm_reports" and "reports.view" in perms:
        await _reports_list(user, 0)
        return True
    if data.startswith("adm_reports_") and "reports.view" in perms:
        await _reports_list(user, max(0, int(data.rsplit("_", 1)[1])))
        return True
    if data.startswith("admrep_") and "reports.view" in perms:
        rest = data[len("admrep_"):]
        if "_" in rest:
            rid_s, _, act = rest.rpartition("_")
            await _resolve_report(user, role, int(rid_s), act)
        else:
            await _report_detail(user, role, perms, int(rest))
        return True

    if data == "adm_bans" and "bans.manage" in perms:
        await _bans_list(user, 0)
        return True
    if data.startswith("adm_bans_") and "bans.manage" in perms:
        await _bans_list(user, max(0, int(data.rsplit("_", 1)[1])))
        return True
    if data.startswith("admban_") and "bans.manage" in perms:
        ban_id = int(data[len("admban_"):].split("_", 1)[0])
        await _revoke_ban(user, role, ban_id)
        return True

    if data == "adm_appeals" and ("appeals.review" in perms or "bans.manage" in perms):
        await _appeals_list(user, 0)
        return True
    if data.startswith("adm_appeals_") and ("appeals.review" in perms or "bans.manage" in perms):
        await _appeals_list(user, max(0, int(data.rsplit("_", 1)[1])))
        return True
    if data.startswith("admapp_") and "appeals.review" in perms:
        rest = data[len("admapp_"):]
        aid_s, _, decision = rest.rpartition("_")
        await _review_appeal(user, role, int(aid_s), decision)
        return True

    if data == "admmod" and "moderation.manage" in perms:
        await _moderation_panel(user)
        return True
    if data == "admmodcfg" and "moderation.manage" in perms:
        await _mod_config_panel(user)
        return True
    if data.startswith("admmodcfg_") and "moderation.manage" in perms:
        key = data[len("admmodcfg_"):]
        async with session_scope() as s:
            cfg = await service.get_setting(s, "moderation")
            cfg[key] = not cfg.get(key, True)
            await service.set_setting(s, "moderation", cfg)
        await _say(user, f"✅ {key} → {_on_off(cfg[key])}")
        await _mod_config_panel(user)
        return True
    if data.startswith("admmodnum_") and "moderation.manage" in perms:
        key = data[len("admmodnum_"):]
        user.state = {"step": "adm_mod_num", "key": key}
        await _say(user, f"مقدار جدید «{key}» را به عدد بفرستید:")
        return True
    if data == "admdict" and "moderation.manage" in perms:
        await _dictionary_panel(user, 0)
        return True
    if data.startswith("admdict_") and data[len("admdict_"):].isdigit() and "moderation.manage" in perms:
        await _dictionary_panel(user, int(data[len("admdict_"):]))
        return True
    if data == "admdictadd" and "moderation.manage" in perms:
        user.state = {"step": "adm_dict_add"}
        await _say(user, "واژه و درجه را بفرستید:\n<code>WORD SEVERITY ACTION</code>\n"
                          "SEVERITY: low|medium|high — ACTION: block_and_warn|block|warn\n"
                          "مثال: badword high block_and_warn")
        return True
    if data.startswith("admdictdel_") and "moderation.manage" in perms:
        async with session_scope() as s:
            await s.execute(delete(DictionaryWord).where(DictionaryWord.id == int(data[len("admdictdel_"):])))
        await _dictionary_panel(user, 0)
        return True
    if data == "admallow" and "moderation.manage" in perms:
        await _allowlist_panel(user, 0)
        return True
    if data.startswith("admallow_") and data[len("admallow_"):].isdigit() and "moderation.manage" in perms:
        await _allowlist_panel(user, int(data[len("admallow_"):]))
        return True
    if data == "admallowadd" and "moderation.manage" in perms:
        user.state = {"step": "adm_allow_add"}
        await _say(user, "عبارت مجاز را بفرستید (فیلتر برای آن غیرفعال می‌شود):")
        return True
    if data.startswith("admallowdel_") and "moderation.manage" in perms:
        async with session_scope() as s:
            await s.execute(delete(AllowlistPhrase).where(AllowlistPhrase.id == int(data[len("admallowdel_"):])))
        await _allowlist_panel(user, 0)
        return True

    if data == "admmedia" and "media.configure" in perms:
        await _media_panel(user)
        return True
    if data.startswith("admmedia_") and "media.configure" in perms:
        key = data[len("admmedia_"):]
        async with session_scope() as s:
            row = await s.get(MediaType, key)
            if row is None:
                row = MediaType(key=key, enabled=False)
                s.add(row)
            else:
                row.enabled = not row.enabled
        await _media_panel(user)
        return True

    # ------------------------------------------------------------ support ---
    if data == "adm_tickets" and "support.view" in perms:
        await _tickets_list(user, 0)
        return True
    if data.startswith("adm_tickets_") and "support.view" in perms:
        await _tickets_list(user, max(0, int(data.rsplit("_", 1)[1])))
        return True
    if data.startswith("admtkt_") and "support.view" in perms:
        rest = data[len("admtkt_"):]
        if "_" in rest:
            tid_s, _, act = rest.rpartition("_")
        else:
            tid_s, act = rest, "view"
        tid = int(tid_s)
        if act == "reply":
            if "support.reply" not in perms:
                await _say(user, "⛔ مجوز پاسخ‌دهی ندارید.")
                return True
            user.state = {"step": "adm_ticket_reply", "ticket": tid}
            await _say(user, "متن پاسخ را بفرستید (برای لغو: «لغو»):")
        elif act == "close":
            await _ticket_status(user, role, tid, "closed")
        elif act == "reopen":
            await _ticket_status(user, role, tid, "open")
        else:
            await _ticket_detail(user, role, perms, tid)
        return True

    # ------------------------------------------------------------ economy ---
    if data == "adm_eco" and ("economy.view" in perms or "economy.configure" in perms):
        await _eco_panel(user, perms)
        return True
    if data.startswith("admeco_") and "economy.configure" in perms:
        short = data[len("admeco_"):]
        user.state = {"step": "adm_eco_key", "key": short}
        await _say(user, f"مقدار جدید «{short}» را به عدد بفرستید (برای لغو: «لغو»):")
        return True

    if data == "adm_store" and "store.manage" in perms:
        await _store_panel(user, perms)
        return True
    if data == "adm_store_rate" and "store.manage" in perms:
        user.state = {"step": "adm_store_rate"}
        await _say(user, "نرخ جدید هر سکه به تومان را بفرستید (فقط عدد، مثل 200).\n"
                          "همهٔ بسته‌ها و مبلغ دلخواه بلافاصله با این نرخ قیمت می‌خورند.")
        return True
    if data == "adm_store_star" and "store.manage" in perms:
        user.state = {"step": "adm_store_star"}
        await _say(user, "تعداد سکه به ازای هر ⭐ استارز را بفرستید (فقط عدد، مثل 20).")
        return True
    if data.startswith("admpkg_") and "store.manage" in perms:
        rest = data[len("admpkg_"):]
        pid, _, act = rest.rpartition("_")
        async with session_scope() as s:
            pkg = await s.get(StorePackage, pid)
            if not pkg:
                await _say(user, "بسته یافت نشد.")
                return True
            if act == "on":
                pkg.active = not pkg.active
                txt = f"✅ بسته {pkg.coins} سکه: {'فعال' if pkg.active else 'غیرفعال'}"
            elif act == "tg":
                pkg.telegram_enabled = not pkg.telegram_enabled
                txt = f"✅ تلگرام: {_on_off(pkg.telegram_enabled)}"
            else:
                pkg.bale_enabled = not pkg.bale_enabled
                txt = f"✅ بله: {_on_off(pkg.bale_enabled)}"
            await service.audit(s, actor_type="bot", admin_id=user.platform_user_id,
                                admin_name=user.display_name, admin_role=role,
                                action="store.package_updated", module="store",
                                target_type="coin_package", target_id=pkg.id, details=act)
        await _say(user, txt)
        await _store_panel(user, perms)
        return True

    if data in ("adm_pay_tg", "adm_pay_bale") and "store.manage" in perms:
        key = "paymentsTelegramEnabled" if data == "adm_pay_tg" else "paymentsBaleEnabled"
        platform_name = "تلگرام" if data == "adm_pay_tg" else "بله"
        has_token = bool(settings.telegram_bot_token if data == "adm_pay_tg"
                         else settings.bale_provider_token)
        async with session_scope() as s:
            app_cfg = await service.get_setting(s, "app")
            new_val = not app_cfg.get(key, key == "paymentsTelegramEnabled")
            app_cfg[key] = new_val
            app_cfg["updatedBy"] = f"bot:{user.platform_user_id}"
            await service.set_setting(s, "app", app_cfg)
            await service.audit(s, actor_type="bot", admin_id=user.platform_user_id,
                                admin_name=user.display_name, admin_role=role,
                                action="store.payments_toggled", module="store",
                                details=f"{key}={new_val}")
        state_txt = "روشن ✅" if new_val else "خاموش ⛔"
        tok_note = "" if has_token else ("\n⚠️ توکن پرداخت این پلتفرم تنظیم نشده — "
                                         "تا وقتی توکن ست نشود، پرداخت کار نمی‌کند.")
        await _say(user, f"✅ درگاه پرداخت {platform_name}: {state_txt}"
                         "\nفروشگاه برای کاربران این پلتفرم بلافاصله به‌روز می‌شود." + tok_note)
        await _store_panel(user, perms)
        return True

    # ----------------------------------------------------------- broadcast ---
    if data == "adm_bcast" and "broadcast.send" in perms:
        user.state = {"step": "adm_bcast_text"}
        await _say(user, "متن پیام همگانی را بفرستید. سپس مخاطب (سگمنت) را از دکمه‌های بعدی انتخاب می‌کنید.\n"
                          "(برای لغو: «لغو»)")
        return True
    if data == "adm_bcast_cancel" and "broadcast.send" in perms:
        user.state = None
        await _say(user, "لغو شد.", keyboard=kb([[btn("🏠 بازگشت به منوی اصلی", "back_home")]]))
        return True
    if data == "admbcasts" and "broadcast.send" in perms:
        await _bcasts_list(user, 0)
        return True
    if data.startswith("admbcasts_") and "broadcast.send" in perms:
        await _bcasts_list(user, max(0, int(data.rsplit("_", 1)[1])))
        return True
    if data.startswith("admbc_") and "broadcast.send" in perms:
        rest = data[len("admbc_"):]
        cid_s, _, act = rest.rpartition("_")
        await _bcast_action(user, role, int(cid_s), act)
        return True
    if data.startswith("adm_baud_") and "broadcast.send" in perms:
        audience = data[len("adm_baud_"):]
        st = user.state or {}
        if st.get("step") != "adm_bcast_aud" or not st.get("text"):
            await _say(user, "ابتدا متن پیام را بفرستید (از منوی پیام همگانی).")
            return True
        valid = {"all", "telegram", "bale", "incomplete", "active_7d", "inactive_7d", "low_balance"}
        if audience not in valid:
            await _say(user, "مخاطب نامعتبر است.")
            return True
        async with session_scope() as s:
            target_count = await service.broadcast_target_count(s, audience)
            camp = db.BroadcastCampaign(title=f"bot-{secrets.token_hex(2)}",
                                        message=st["text"][:4000], audience=audience,
                                        audience_label=service.AUDIENCE_LABELS.get(audience, audience),
                                        rate_limit_per_sec=20, status="sending",
                                        target_count=target_count,
                                        created_by=f"bot:{user.platform_user_id}")
            s.add(camp)
            await s.flush()
            camp_id = camp.id
        user.state = None
        await _say(user, f"📢 کمپین #{camp_id} برای «{service.AUDIENCE_LABELS.get(audience, audience)}» "
                         f"به {target_count} کاربر آغاز شد.",
                   keyboard=kb([[btn("📋 کمپین‌ها", "admbcasts")],
                                [btn("🏠 بازگشت به منوی اصلی", "back_home")]]))
        return True

    # ------------------------------------------------------------- backup ---
    if data == "adm_backup" and "backups.view" in perms:
        await _backup_panel(user, perms, role)
        return True
    if data == "adm_backup_now" and "backups.create" in perms:
        await _say(user, "⏳ در حال ساخت پشتیبان...")
        bot = RUNTIME.get("adapter_telegram")
        if not bot:
            await _say(user, "خطا: بازوی تلگرام در دسترس نیست.")
            return True
        rec = await backup_mod.run_backup_job(bot, "manual",
                                              created_by=f"bot:{user.platform_user_id}")
        if rec:
            await _say(user, f"✅ پشتیبان {rec.id} ساخته شد و برای مالک ارسال گردید "
                             f"(وضعیت ارسال: {rec.delivery_status}).")
        else:
            await _say(user, "⚠️ پشتیبان‌گیری دیگری در حال اجراست.")
        return True
    if data == "adm_restore" and role == "owner":
        cleanup_restore_wait()
        if len(RESTORE_WAIT) >= 4 and f"{user.platform}:{chat_id}" not in RESTORE_WAIT:
            await _say(user,"چهار بازیابی در انتظار است؛ ابتدا یک مورد را لغو کنید یا صبر کنید منقضی شود.")
            return True
        RESTORE_WAIT[f"{user.platform}:{chat_id}"] = {"parts": {}, "password": None, "expected": None, "expires":time.monotonic()+600}
        await _say(user, "♻️ حالت بازیابی فعال شد.\n\n"
                         "فایل پشتیبانی که ربات برایت فرستاده را همین‌جا فوروارد کن.\n"
                         "🔒 پشتیبان‌های جدید رمزگذاری شده‌اند؛ کلید BACKUP_PASSWORD زمان تهیه فایل لازم است.\n"
                         "🔑 کلید فعلی سرور خودکار استفاده می‌شود؛ برای فایل با کلید قدیمی، فقط در گفتگوی خصوصی با ربات وارد کنید:\n"
                         "«رمز بازیابی: ...»\n\n"
                         "پشتیبان بلافاصله اعتبارسنجی می‌شود و با یک کلیک جایگزین می‌شود.\n"
                         "⚠️ بازیابی کامل، همه داده‌های فعلی را با نسخه پشتیبان جایگزین می‌کند.",
                   keyboard=kb([[btn("❌ لغو", "adm_restore_cancel")]]))
        return True
    if data == "adm_restore_cancel":
        RESTORE_WAIT.pop(f"{user.platform}:{chat_id}", None)
        await _say(user, "لغو شد.")
        return True
    if data == "adm_restore_go" and role == "owner":
        cleanup_restore_wait()
        st = RESTORE_WAIT.get(f"{user.platform}:{chat_id}") or {}
        blob = st.get("blob") or b""
        password = st.get("password") or settings.backup_password
        RESTORE_WAIT.pop(f"{user.platform}:{chat_id}", None)
        if not blob:
            await _say(user, "هیچ فایلی دریافت نشد. ابتدا فایل پشتیبان را فوروارد کن "
                             "(از منوی مدیریت ← ♻️ بازیابی از فایل).")
            return True
        await _say(user, "⏳ اسنپ‌شات اضطراری گرفته می‌شود و بازیابی آغاز می‌شود "
                         "(حالت تعمیر فعال می‌شود)...")
        result = await backup_mod.execute_restore(blob, password)
        icon = "✅" if result["status"] == "succeeded" else "⚠️"
        await _say(user, f"{icon} {result['message']}\nاسنپ‌شات بازگشت: {result['rollbackSnapshotId']}\n"
                         f"مدت: {result['durationSec']} ثانیه")
        return True

    # ----------------------------------------------------------- settings ---
    if data == "adm_settings" and ("settings.manage" in perms or "matching.configure" in perms
                                   or "privacy.manage" in perms):
        await _settings_panel(user, perms)
        return True
    if data.startswith("admset_") and "settings.manage" in perms:
        key = data[len("admset_"):]
        async with session_scope() as s:
            cfg = await service.get_setting(s, "app")
            if key == "maintenanceMode":
                cfg[key] = not cfg.get(key, False)
            else:
                cfg[key] = not cfg.get(key, True)
            await service.set_setting(s, "app", cfg)
            await service.audit(s, actor_type="bot", admin_id=user.platform_user_id,
                                admin_name=user.display_name, admin_role=role,
                                action="settings.updated", module="settings",
                                details=f"{key}={cfg[key]}")
        await _settings_panel(user, perms)
        return True
    if data == "admmatch" and "matching.configure" in perms:
        await _matching_panel(user)
        return True
    if data.startswith("admmatch_") and data[len("admmatch_"):] in (
            "enabled", "genderFilterEnabled", "ageGroupSeparation") and "matching.configure" in perms:
        key = data[len("admmatch_"):]
        async with session_scope() as s:
            cfg = await service.get_setting(s, "matching")
            cfg[key] = not cfg.get(key, True)
            await service.set_setting(s, "matching", cfg)
        await _matching_panel(user)
        return True
    if data.startswith("admmatchnum_") and "matching.configure" in perms:
        key = data[len("admmatchnum_"):]
        user.state = {"step": "adm_match_num", "key": key}
        await _say(user, f"مقدار جدید «{key}» را به عدد بفرستید:")
        return True
    if data == "admpriv" and "privacy.manage" in perms:
        await _privacy_panel(user)
        return True
    if data == "admprivdays" and "privacy.manage" in perms:
        user.state = {"step": "adm_priv_days"}
        await _say(user, "تعداد روز نگهداری چت‌ها را بفرستید (مثلا 30):")
        return True
    if data == "admprivgrace" and "privacy.manage" in perms:
        user.state = {"step": "adm_priv_grace"}
        await _say(user, "مهلت حذف حساب (ساعت) را بفرستید (0 = فوری):")
        return True
    if data == "admprivpolicy" and "privacy.manage" in perms:
        user.state = {"step": "adm_priv_policy"}
        await _say(user, "متن سیاست حریم خصوصی را بفرستید:")
        return True
    if data == "admprivrules" and "privacy.manage" in perms:
        user.state = {"step": "adm_priv_rules"}
        await _say(user, "متن قوانین را بفرستید:")
        return True
    if data.startswith("admprivtoggle_") and "privacy.manage" in perms:
        key = data[len("admprivtoggle_"):]
        async with session_scope() as s:
            cfg = await service.get_setting(s, "privacy")
            cfg[key] = not cfg.get(key, True)
            await service.set_setting(s, "privacy", cfg)
        await _privacy_panel(user)
        return True
    if data == "admdaily" and "daily_rewards.manage" in perms:
        await _daily_panel(user)
        return True
    if data == "admdailytoggle" and "daily_rewards.manage" in perms:
        async with session_scope() as s:
            cfg = await service.get_setting(s, "app")
            cfg["enableDailyRewards"] = not cfg.get("enableDailyRewards", True)
            await service.set_setting(s, "app", cfg)
        await _daily_panel(user)
        return True
    if data == "admdailyamt" and "daily_rewards.manage" in perms:
        user.state = {"step": "adm_daily_amt"}
        await _say(user, "مقدار پاداش روزانه (سکه) را بفرستید:")
        return True

    # ---------------------------------------------------------- platforms ---
    if data == "adm_platforms" and "platforms.manage" in perms:
        await _platforms_panel(user)
        return True
    if data.startswith("admpl_") and "platforms.manage" in perms:
        rest = data[len("admpl_"):]
        plat, _, key = rest.partition("_")
        if plat in ("telegram", "bale") and key in ("pollingFallback", "dropPendingOnRestart"):
            async with session_scope() as s:
                cfg = await service.get_setting(s, "platforms")
                pc = dict(cfg.get(plat) or {})
                pc[key] = not pc.get(key, True)
                cfg[plat] = pc
                await service.set_setting(s, "platforms", cfg)
        await _platforms_panel(user)
        return True

    # ------------------------------------------------------ logs / monitor ---
    if data == "adm_logs" and "audit.view" in perms:
        await _logs_list(user, 0)
        return True
    if data.startswith("admlogs_") and "audit.view" in perms:
        await _logs_list(user, max(0, int(data.rsplit("_", 1)[1])))
        return True
    if data == "adm_monitor" and "monitoring.view" in perms:
        await _monitor_panel(user)
        return True

    # ------------------------------------------------------------- admins ---
    if data == "adm_admins" and role == "owner":
        await _admins_list(user, 0)
        return True
    if data.startswith("admadmins_") and role == "owner":
        await _admins_list(user, max(0, int(data.rsplit("_", 1)[1])))
        return True
    if data == "admadminadd" and role == "owner":
        user.state = {"step": "adm_admin_add"}
        await _say(user, "مدیر جدید را به این شکل بفرستید:\n"
                         "PLATFORM:ID ROLE\nمثال: telegram:11111111 moderator\n"
                         "ROLEها: senior_admin | moderator | support")
        return True
    if data.startswith("admadminrole_") and role == "owner":
        rest = data[len("admadminrole_"):]
        aid_s, _, new_role = rest.rpartition("_")
        if new_role in ROLE_ORDER:
            async with session_scope() as s:
                a = await s.get(BotAdmin, int(aid_s))
                if a:
                    a.role = new_role
                    await service.audit(s, actor_type="bot", admin_id=user.platform_user_id,
                                        admin_name=user.display_name, admin_role=role,
                                        action="admin.role_changed", module="roles",
                                        target_type="bot_admin", target_id=str(a.id),
                                        details=new_role)
        await _admins_list(user, 0)
        return True
    if data.startswith("admadminx_") and role == "owner":
        async with session_scope() as s:
            a = await s.get(BotAdmin, int(data[len("admadminx_"):]))
            if a:
                await s.delete(a)
                await service.audit(s, actor_type="bot", admin_id=user.platform_user_id,
                                    admin_name=user.display_name, admin_role=role,
                                    action="admin.deleted", module="roles",
                                    target_type="bot_admin", target_id=str(a.id))
        await _admins_list(user, 0)
        return True

    # ------------------------------------------------------- maintenance ---
    if data == "adm_maint" and "settings.manage" in perms:
        async with session_scope() as s:
            cfg = await service.get_setting(s, "app")
            cfg["maintenanceMode"] = not cfg.get("maintenanceMode", False)
            await service.set_setting(s, "app", cfg)
            on = cfg["maintenanceMode"]
            await service.audit(s, actor_type="bot", admin_id=user.platform_user_id,
                                admin_name=user.display_name, admin_role=role,
                                action="settings.maintenance_toggled", module="settings",
                                details=f"maintenance={'ON' if on else 'OFF'}")
        if on:
            await _say(user, "🔧 حالت تعمیر: 🟢 روشن\n\n"
                             "⚠️ ربات از این لحظه فقط برای مدیران کار می‌کند.\n"
                             "کاربران عادی پیام «در حال تعمیر» می‌بینند.",
                       keyboard=kb([[btn("🔧 خاموش کردن تعمیر", "adm_maint")],
                                    [btn("🏠 بازگشت به منوی اصلی", "back_home")]]))
        else:
            await _say(user, "🔧 حالت تعمیر: 🔴 خاموش\n\n✅ ربات برای همه کاربران فعال است.",
                       keyboard=kb([[btn("🔧 روشن کردن تعمیر", "adm_maint")],
                                    [btn("🏠 بازگشت به منوی اصلی", "back_home")]]))
        return True

    return False


async def _revoke_ban(user: User, role: str, ban_id: int) -> None:
    async with session_scope() as s:
        b = await s.get(Ban, ban_id)
        if not b or b.status != "active":
            await _say(user, "این مسدودی دیگر فعال نیست.")
            return
        b.status = "revoked"
        u = await s.get(User, b.user_id)
        if u:
            u.status = "active"
            await _notify_target(u, "admin_unban_notice")
        await service.audit(s, actor_type="bot", admin_id=user.platform_user_id,
                            admin_name=user.display_name, admin_role=role,
                            action="ban.revoked", module="bans",
                            target_type="ban", target_id=b.id, details="revoke from bot panel")
    await _say(user, "✅ مسدودی رفع شد.",
               keyboard=kb([[btn("◀️ بازگشت به مسدودی‌ها", "adm_bans")]]))


async def _refund_payment(user: User, role: str, pid: int) -> None:
    from . import orders
    try:
        await orders.refund(pid,f"bot:{user.platform}:{user.platform_user_id}")
    except orders.RefundError as exc:
        await _say(user,str(exc))
        return
    async with session_scope() as s:
        await service.audit(s,actor_type="bot",admin_id=user.platform_user_id,admin_name=user.display_name,
            admin_role=role,action="payment.refunded",module="payments",target_type="payment",target_id=pid)
    await _say(user,"✅ استرداد انجام شد.",keyboard=kb([[btn("◀️ بازگشت به پرداخت‌ها","adm_payments")]]))



# ================================================================ text flow ===
async def admin_text_flow(user: User, text: str) -> bool:
    """Handle admin FSM steps inside chat; returns True if consumed."""
    state = user.state or {}
    step = state.get("step")
    if not step or not str(step).startswith("adm"):
        return False
    role = await get_role(user)
    if not role:
        return False
    perms = await _perms(role)

    # universal cancel for every admin text step
    if text.strip().lower() in ("لغو", "cancel", "/cancel", "بازگشت", "back"):
        user.state = None
        await _menu(user, role, note="✅ عملیات لغو شد.")
        return True

    if step == "adm_finduser":
        user.state = None
        # v2.5: one box understands a numeric platform id, the internal row id,
        # @username (with or without the @), the public id and platform:id.
        targets = await _lookup_users(text)
        if not targets:
            await _say(user, "❌ کاربر یافت نشد.\n\n"
                             "این قالب‌ها را می‌توانی بفرستی:\n"
                             "🔢 123456789 (آی‌دی عددی)\n"
                             "🔗 @username یا username\n"
                             "🆔 U1A2B3C4 (شناسه اختصاصی)\n"
                             "📱 telegram:123456789",
                       keyboard=kb([[btn("🔎 جستجوی دوباره", "adm_finduser"),
                                    ], [btn("🏠 بازگشت به منوی اصلی", "back_home")]]))
            return True
        if len(targets) == 1:
            # v2.5 FIX: was service.role_permissions(s, role) — the owner's "*"
            # was never expanded, so the owner saw NO ban/coin buttons.
            await _user_actions(user, role, perms, targets[0])
            return True
        # more than one candidate: let the admin pick the exact one
        lines = []
        rows = []
        for t in targets[:10]:
            lines.append(f"• {t.display_name[:24]} | 🆔 {t.public_id} | "
                         f"🔢 {t.platform_user_id} | "
                         f"{'@' + t.username if t.username else 'بدون یوزرنیم'}")
            rows.append([btn(f"👤 {t.display_name[:20]} — {t.public_id}",
                             f"admuser_{t.id}")])
        rows.append([btn("🔎 جستجوی دوباره", "adm_finduser")])
        rows.append([btn("🏠 بازگشت به منوی اصلی", "back_home")])
        await _say(user, f"🔎 {len(targets)} کاربر پیدا شد — یکی را انتخاب کن:\n\n"
                         + "\n".join(lines), keyboard=kb(*rows))
        return True

    if step == "adm_eco_set" and "economy.configure" in perms:
        parts = text.split()
        if len(parts) == 4 and parts[0] == "set" and parts[1] == "eco":
            keymap = {"initial": "initialReward", "message": "messageCost", "daily": "dailyBonus",
                      "gender": "genderFilterMatchCost", "referral": "referralRewardPerParty",
                      "namechg": "nameChangeCost", "agechg": "ageChangeCost",
                      "bday": "birthdayReward", "link": "accountLinkReward"}
            if parts[2] not in keymap:
                await _say(user, "❌ کلید نامعتبر.")
                return True
            try:
                val = int(parts[3])
                async with session_scope() as s:
                    eco = await service.get_setting(s, "economy")
                    eco[keymap[parts[2]]] = val
                    eco["updatedBy"] = f"bot:{user.platform_user_id}"
                    await service.set_setting(s, "economy", eco)
                    await service.audit(s, actor_type="bot", admin_id=user.platform_user_id,
                                        admin_name=user.display_name, admin_role=role,
                                        action="economy.config_updated", module="economy",
                                        details=f"{parts[2]}={val}")
                user.state = None
                await _say(user, f"✅ {parts[2]} = {val} ثبت شد.")
            except ValueError:
                await _say(user, "❌ مقدار نامعتبر.")
        else:
            await _say(user, "دستور نامعتبر. مثال: set eco daily 10")
        return True

    if step == "adm_eco_key" and "economy.configure" in perms:
        keymap = {"initial": "initialReward", "message": "messageCost", "daily": "dailyBonus",
                  "gender": "genderFilterMatchCost", "referral": "referralRewardPerParty",
                  "namechg": "nameChangeCost", "agechg": "ageChangeCost",
                  "bday": "birthdayReward", "link": "accountLinkReward"}
        short = state.get("key", "")
        try:
            val = int(text.strip())
            async with session_scope() as s:
                eco = await service.get_setting(s, "economy")
                eco[keymap[short]] = val
                eco["updatedBy"] = f"bot:{user.platform_user_id}"
                await service.set_setting(s, "economy", eco)
                await service.audit(s, actor_type="bot", admin_id=user.platform_user_id,
                                    admin_name=user.display_name, admin_role=role,
                                    action="economy.config_updated", module="economy",
                                    details=f"{short}={val}")
            user.state = None
            await _say(user, f"✅ {short} = {val} ثبت شد.")
        except (KeyError, ValueError):
            await _say(user, "❌ عدد نامعتبر.")
        return True

    if step == "adm_store_rate" and "store.manage" in perms:
        try:
            val = int(text.strip().replace("٬", "").replace(",", ""))
            if val <= 0:
                raise ValueError
            async with session_scope() as s:
                store = await service.get_setting(s, "store")
                store["tomanPerCoin"] = val
                store["updatedBy"] = f"bot:{user.platform_user_id}"
                await service.set_setting(s, "store", store)
                pkgs = (await s.execute(select(StorePackage).order_by(StorePackage.coins))).scalars()
                eff = [f"🪙 {p.coins} → ⭐{service.coins_price_stars(store, p.coins)} | "
                       f"{service.coins_price_toman(store, p.coins):,} تومان" for p in pkgs]
                await service.audit(s, actor_type="bot", admin_id=user.platform_user_id,
                                    admin_name=user.display_name, admin_role=role,
                                    action="store.rate_updated", module="store",
                                    details=f"tomanPerCoin={val}")
            user.state = None
            await _say(user, f"✅ نرخ جدید ثبت شد: هر سکه = {val:,} تومان\n"
                             "قیمت همهٔ بسته‌ها خودکار به‌روز شد:\n" + "\n".join(eff))
        except ValueError:
            await _say(user, "❌ عدد نامعتبر. فقط عدد بفرستید (مثل 200).")
        return True

    if step == "adm_store_star" and "store.manage" in perms:
        try:
            val = int(text.strip())
            if val <= 0:
                raise ValueError
            async with session_scope() as s:
                store = await service.get_setting(s, "store")
                store["coinsPerStar"] = val
                store["updatedBy"] = f"bot:{user.platform_user_id}"
                await service.set_setting(s, "store", store)
                await service.audit(s, actor_type="bot", admin_id=user.platform_user_id,
                                    admin_name=user.display_name, admin_role=role,
                                    action="store.rate_updated", module="store",
                                    details=f"coinsPerStar={val}")
            user.state = None
            await _say(user, f"✅ ثبت شد: هر ⭐ استارز = {val} سکه.")
        except ValueError:
            await _say(user, "❌ عدد نامعتبر. فقط عدد بفرستید (مثل 20).")
        return True

    if step == "adm_bcast_text" and "broadcast.send" in perms:
        user.state = {"step": "adm_bcast_aud", "text": text[:4000]}
        rows = [[btn("👥 همه کاربران", "adm_baud_all")],
                [btn("🔵 فقط تلگرام", "adm_baud_telegram"),
                 btn("🟢 فقط بله", "adm_baud_bale")],
                [btn("🧩 ثبت‌نام‌های ناتمام", "adm_baud_incomplete")],
                [btn("🟢 فعال ۷ روز اخیر", "adm_baud_active_7d"),
                 btn("😴 غایب ۷+ روز", "adm_baud_inactive_7d")],
                [btn("🪙 موجودی کم", "adm_baud_low_balance")],
                [btn("❌ لغو", "adm_bcast_cancel")],
                [btn("🏠 بازگشت به منوی اصلی", "back_home")]]
        await _say(user, "✅ متن ذخیره شد. حالا مخاطب (سگمنت) پیام را انتخاب کن:",
                   keyboard=kb(*rows))
        return True

    if step == "adm_ticket_reply" and "support.reply" in perms:
        tid = int(state.get("ticket", 0))
        async with session_scope() as s:
            t = await s.get(SupportTicket, tid)
            if not t:
                await _say(user, "تیکت یافت نشد.")
                return True
            u = await s.get(User, t.user_id)
            s.add(SupportMessage(ticket_id=t.id, sender_type="admin",
                                 sender_name=user.display_name, body=text[:4000]))
            t.status = "waiting_user"
            t.updated_at = utcnow()
            await service.audit(s, actor_type="bot", admin_id=user.platform_user_id,
                                admin_name=user.display_name, admin_role=role,
                                action="support.replied", module="support",
                                target_type="ticket", target_id=t.ticket_no,
                                details="پاسخ از پنل بازو")
        if u:
            await _notify_target(u, "admin_support_reply", ticket=t.ticket_no, body=text[:3500])
        user.state = None
        await _say(user, f"✅ پاسخ برای تیکت {t.ticket_no} ارسال شد.",
                   keyboard=kb([[btn("◀️ بازگشت به تیکت‌ها", "adm_tickets")]]))
        return True

    if step == "adm_dict_add" and "moderation.manage" in perms:
        parts = text.split()
        if len(parts) < 3:
            await _say(user, "❌ فرمت: WORD SEVERITY ACTION")
            return True
        word, severity, action = parts[0].lower(), parts[1].lower(), parts[2]
        if severity not in ("low", "medium", "high") or action not in (
                "block_and_warn", "block", "warn"):
            await _say(user, "❌ مقدار نامعتبر. SEVERITY: low|medium|high — "
                             "ACTION: block_and_warn|block|warn")
            return True
        async with session_scope() as s:
            exists = (await s.execute(select(DictionaryWord).where(
                DictionaryWord.word == word))).scalar_one_or_none()
            if not exists:
                s.add(DictionaryWord(word=word[:64], severity=severity, action=action,
                                     added_by=f"bot:{user.platform_user_id}"))
            await service.audit(s, actor_type="bot", admin_id=user.platform_user_id,
                                admin_name=user.display_name, admin_role=role,
                                action="moderation.word_added", module="moderation",
                                target_type="dictionary_word", target_id=word,
                                details=f"{severity}/{action}")
        user.state = None
        await _say(user, f"✅ واژه «{word}» ثبت شد.")
        await _dictionary_panel(user, 0)
        return True

    if step == "adm_allow_add" and "moderation.manage" in perms:
        phrase = text.strip()[:64]
        if not phrase:
            await _say(user, "❌ عبارت خالی است.")
            return True
        async with session_scope() as s:
            exists = (await s.execute(select(AllowlistPhrase).where(
                AllowlistPhrase.phrase == phrase))).scalar_one_or_none()
            if not exists:
                s.add(AllowlistPhrase(phrase=phrase,
                                      added_by=f"bot:{user.platform_user_id}"))
        user.state = None
        await _say(user, f"✅ عبارت «{phrase}» به allowlist اضافه شد.")
        await _allowlist_panel(user, 0)
        return True

    if step == "adm_mod_num" and "moderation.manage" in perms:
        key = state.get("key", "")
        try:
            val = int(text.strip())
            if val < 0:
                raise ValueError
            async with session_scope() as s:
                cfg = await service.get_setting(s, "moderation")
                cfg[key] = val
                await service.set_setting(s, "moderation", cfg)
        except ValueError:
            await _say(user, "❌ عدد نامعتبر.")
            return True
        user.state = None
        await _say(user, f"✅ {key} = {val}")
        await _mod_config_panel(user)
        return True

    if step == "adm_match_num" and "matching.configure" in perms:
        key = state.get("key", "")
        try:
            val = int(text.strip())
            if val < 0:
                raise ValueError
            async with session_scope() as s:
                cfg = await service.get_setting(s, "matching")
                cfg[key] = val
                await service.set_setting(s, "matching", cfg)
        except ValueError:
            await _say(user, "❌ عدد نامعتبر.")
            return True
        user.state = None
        await _say(user, f"✅ {key} = {val}")
        await _matching_panel(user)
        return True

    if step == "adm_priv_days" and "privacy.manage" in perms:
        try:
            val = int(text.strip())
            if not (1 <= val <= 3650):
                raise ValueError
        except ValueError:
            await _say(user, "❌ عدد بین 1 و 3650 باشد.")
            return True
        async with session_scope() as s:
            cfg = await service.get_setting(s, "privacy")
            cfg["chatRetentionDays"] = val
            await service.set_setting(s, "privacy", cfg)
        user.state = None
        await _say(user, f"✅ نگهداری چت: {val} روز")
        await _privacy_panel(user)
        return True

    if step == "adm_priv_grace" and "privacy.manage" in perms:
        try:
            val = max(0, int(text.strip()))
        except ValueError:
            await _say(user, "❌ عدد نامعتبر.")
            return True
        async with session_scope() as s:
            cfg = await service.get_setting(s, "privacy")
            cfg["accountDeletionGraceHours"] = val
            await service.set_setting(s, "privacy", cfg)
        user.state = None
        await _say(user, f"✅ مهلت حذف حساب: {val} ساعت")
        await _privacy_panel(user)
        return True

    if step == "adm_priv_policy" and "privacy.manage" in perms:
        async with session_scope() as s:
            cfg = await service.get_setting(s, "privacy")
            cfg["privacyPolicyFa"] = text.strip()[:4000]
            await service.set_setting(s, "privacy", cfg)
        user.state = None
        await _say(user, "✅ سیاست حریم خصوصی ذخیره شد.")
        await _privacy_panel(user)
        return True

    if step == "adm_priv_rules" and "privacy.manage" in perms:
        async with session_scope() as s:
            cfg = await service.get_setting(s, "privacy")
            cfg["rulesFa"] = text.strip()[:4000]
            await service.set_setting(s, "privacy", cfg)
        user.state = None
        await _say(user, "✅ قوانین ذخیره شد.")
        await _privacy_panel(user)
        return True

    if step == "adm_daily_amt" and "daily_rewards.manage" in perms:
        try:
            val = int(text.strip())
            if val < 0:
                raise ValueError
        except ValueError:
            await _say(user, "❌ عدد نامعتبر.")
            return True
        async with session_scope() as s:
            eco = await service.get_setting(s, "economy")
            eco["dailyBonus"] = val
            await service.set_setting(s, "economy", eco)
        user.state = None
        await _say(user, f"✅ پاداش روزانه: {val} سکه")
        await _daily_panel(user)
        return True

    if step == "adm_admin_add" and role == "owner":
        parts = text.split()
        if len(parts) != 2 or ":" not in parts[0] or parts[1] not in ROLE_ORDER:
            await _say(user, "❌ فرمت: PLATFORM:ID ROLE\n"
                             "مثال: telegram:11111111 moderator")
            return True
        plat, _, pid = parts[0].partition(":")
        async with session_scope() as s:
            exists = (await s.execute(select(BotAdmin).where(
                BotAdmin.platform == plat, BotAdmin.platform_user_id == pid))).scalar_one_or_none()
            if exists:
                exists.role = parts[1]
            else:
                s.add(BotAdmin(platform=plat, platform_user_id=pid, role=parts[1],
                               added_by=f"bot:{user.platform_user_id}"))
            await service.audit(s, actor_type="bot", admin_id=user.platform_user_id,
                                admin_name=user.display_name, admin_role=role,
                                action="admin.created", module="roles",
                                target_type="bot_admin", target_id=parts[0],
                                details=f"role={parts[1]}")
        user.state = None
        await _say(user, f"✅ مدیر {parts[0]} با نقش {ROLE_LABEL[parts[1]]} ثبت شد.")
        await _admins_list(user, 0)
        return True

    return False


# ================================================================ documents ===
async def admin_document(user: User, file_bytes: bytes,
                         caption: str = "", filename: str = "") -> bool:
    """Owner forwards a backup file during restore wait-state (v2.3 one-click)."""
    cleanup_restore_wait()
    chat_key = f"{user.platform}:{user.platform_user_id}"
    st = RESTORE_WAIT.get(chat_key)
    if st is None:
        return False
    role = await get_role(user)
    if role != "owner":
        return False
    if not file_bytes or len(file_bytes) + sum(map(len, st["parts"].values())) > settings.restore_max_bytes or len(st["parts"]) >= 8:
        RESTORE_WAIT.pop(chat_key,None)
        await _say(user,"حجم یا تعداد بخش‌ها بیش از حد مجاز است؛ برای فایل بزرگ‌تر از پنل وب استفاده کنید.")
        return True
    st.pop("blob",None)
    pwd, idx, total = _parse_restore_caption(caption, filename)
    if total and not 1 <= total <= 8:
        RESTORE_WAIT.pop(chat_key,None)
        return True
    if pwd:
        st["password"] = pwd
    if total:
        st["expected"] = total
    st["parts"][idx or len(st["parts"]) + 1] = file_bytes
    received = len(st["parts"])
    expected = st.get("expected")
    if expected and received < expected:
        await _say(user, f"📄 بخش {received} از {expected} دریافت شد "
                         f"({len(file_bytes) / 1e6:.1f} MB). "
                         "بخش‌های باقی‌مانده را هم فوروارد کن.")
        return True
    blob = b"".join(st["parts"][i] for i in sorted(st["parts"]))
    password = st.get("password") or settings.backup_password
    v = await asyncio.to_thread(backup_mod.validate_blob, blob, "", password)
    if not v["valid"]:
        if not expected:
            await _say(user, f"📄 فایل {received} دریافت شد ({len(file_bytes) / 1e6:.1f} MB) "
                             "اما هنوز یک آرشیو کامل و معتبر نیست.\n"
                             "• اگر پشتیبان چندبخشی است، بقیه بخش‌ها را هم فوروارد کن.\n"
                             "• اگر رمز در کپشن نبود، هنگام فوروارد بنویس: رمز بازیابی: ...")
            return True
        await _say(user, "❌ آرشیو نامعتبر است:\n" + "\n".join(v["warnings"]))
        RESTORE_WAIT.pop(chat_key, None)
        return True
    st["blob"] = blob
    st["parts"].clear()
    await _say(user,
               f"✅ پشتیبان معتبر است — حجم {v['sizeMb']} MB | اسکیما {v['schemaVersion']} "
               f"| قالب و اعتبار آرشیو بررسی شد.\n"
               "⚠️ با تأیید، همه داده‌های فعلی با این نسخه جایگزین می‌شود "
               "(اسنپ‌شات اضطراری هم گرفته می‌شود).",
               keyboard=kb([[btn("♻️ جایگزینی با این پشتیبان", "adm_restore_go")],
                            [btn("❌ لغو", "adm_restore_cancel")]]))
    return True


# ============================================================ inline actions ===
async def admin_inline_action(user: User, data: str) -> bool:
    """admub_ (ban) / admuc_ (coins) inline buttons."""
    if not (data.startswith("admub_") or data.startswith("admuc_")):
        return False
    role = await get_role(user)
    if not role:
        return False
    perms = await _perms(role)
    _, _, rest = data.partition("admub_" if data.startswith("admub_") else "admuc_")
    uid_str, _, act = rest.rpartition("_")
    try:
        uid = int(uid_str)
    except ValueError:
        return False
    async with session_scope() as s:
        target = await s.get(User, uid)
        if not target:
            await _say(user, "کاربر یافت نشد.")
            return True
        if data.startswith("admub_") and "users.ban" in perms:
            if act == "unban":
                for b in (await s.execute(select(Ban).where(
                        Ban.user_id == uid, Ban.status == "active"))).scalars():
                    b.status = "revoked"
                target.status = "active"
                await service.audit(s, actor_type="bot", admin_id=user.platform_user_id,
                                    admin_name=user.display_name, admin_role=role,
                                    action="user.unbanned", module="bans",
                                    target_type="user", target_id=target.public_id)
                await _say(user, f"✅ مسدودی {target.public_id} رفع شد.")
                await _notify_target(target, "admin_unban_notice")
            else:
                if act == "perm":
                    await service.ban_user(s, target, "permanent", "manual_admin",
                                           "تصمیم مدیر", f"bot:{user.platform_user_id}")
                    await _say(user, f"♾ {target.public_id} برای همیشه مسدود شد.")
                else:
                    hours = int(act)
                    await service.ban_user(s, target, "temporary", "manual_admin",
                                           "تصمیم مدیر", f"bot:{user.platform_user_id}", hours=hours)
                    await _say(user, f"⛔ {target.public_id} برای {hours} ساعت مسدود شد.")
                await _notify_target(target, "admin_ban_notice", reason="تصمیم مدیریت")
        elif data.startswith("admuc_") and "users.coins" in perms:
            amount = int(act)
            if amount >= 0:
                await service.credit(s, target, amount, "admin_grant", "اعطای دستی مدیر")
            else:
                ok = await service.debit(s, target, -amount, "admin_deduct", "کسر دستی مدیر")
                if ok is None:
                    await _say(user, f"⚠️ موجودی {target.public_id} برای کسر کافی نیست.")
                    return True
            await service.audit(s, actor_type="bot", admin_id=user.platform_user_id,
                                admin_name=user.display_name, admin_role=role,
                                action="user.coins_granted", module="economy",
                                target_type="user", target_id=target.public_id,
                                details=f"{amount:+d}")
            await _say(user, f"🪙 موجودی {target.public_id}: {target.coin_balance}")
            await _notify_target(target,
                                 "admin_coins_grant" if amount >= 0 else "admin_coins_deduct",
                                 amount=abs(amount), reason="تصمیم مدیریت",
                                 balance=target.coin_balance)
    return True


async def _notify_target(target: User, key: str, **kw) -> None:
    """Send an admin-action notice to the target user's PV (best effort)."""
    from . import bot as bot_mod
    from .i18n import tr
    adp = bot_mod.adapter(target.platform)
    if not adp:
        return
    try:
        await adp.send_message(target.platform_user_id, tr(target.language, key, **kw))
    except PlatformError as exc:
        from . import growth
        await growth.mark_bot_blocked(target.id, target.platform, str(exc))
        log.info("admin-action PV notify failed for %s: %s", target.public_id, exc)
