"""In-bot admin panels (Telegram + Bale) — same backend, role-based."""
from __future__ import annotations

import logging
import re
import secrets

from sqlalchemy import delete, func, select

from . import backup as backup_mod
from . import db, service
from .config import RUNTIME, settings
from .db import (Appeal, Ban, BotAdmin, Report, StorePackage, User, session_scope,
                 utcnow)
from .i18n import tr
from .platform import PlatformError

log = logging.getLogger("kanar.botadmin")

# in-memory restore wait-state (chat_id -> collected parts / password / validated blob)
# v2.3 one-click flow: owner forwards the backup file with the restore password
# in its caption; the bot validates instantly and replaces data on one confirm.
RESTORE_WAIT: dict[str, dict] = {}

_CAP_PWD_RE = re.compile(r"(?:رمز\s*(?:بازیابی|نگاری)?|password)\s*[:：]\s*([^\s]{4,})", re.I)
_CAP_PART_RE = re.compile(r"بخش\s*(\d+)\s*از\s*(\d+)")
_FN_PART_RE = re.compile(r"\.(?:part)?(\d+)of(\d+)", re.I)


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


def kb(rows):
    return [list(r) for r in rows]


def btn(text, data):
    return {"text": text, "callback_data": data}


def adapter(platform):
    return RUNTIME.get(f"adapter_{platform}")


async def get_role(user: User) -> str:
    if user.platform == "telegram" and int(user.platform_user_id) in settings.owner_telegram_ids:
        return "owner"
    if user.platform == "bale" and int(user.platform_user_id) in settings.owner_bale_ids:
        return "owner"
    async with session_scope() as s:
        row = await s.execute(select(BotAdmin).where(
            BotAdmin.platform == user.platform,
            BotAdmin.platform_user_id == user.platform_user_id))
        ba = row.scalar_one_or_none()
        return ba.role if ba else ""


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
        await adp.send_message(user.platform_user_id, text, keyboard=keyboard)
    except PlatformError as exc:
        log.warning("admin send failed: %s", exc)


async def _menu(user: User, role: str) -> None:
    """Inline admin panel menu (v2.1: numeric stats, instant backup, dynamic maintenance)."""
    perms = await _perms(role)
    rows = []
    if "dashboard.view" in perms:
        rows.append([btn("📊 آمار کل", "adm_stats")])
    if "users.view" in perms:
        rows.append([btn("🔍 جستجوی کاربر", "adm_finduser"),
                     btn("📋 گزارش‌ها", "adm_reports")])
    if "bans.manage" in perms:
        rows.append([btn("⛔ مسدودی‌ها و اعتراض‌ها", "adm_bans")])
    if "economy.view" in perms or "economy.configure" in perms:
        rows.append([btn("💰 تنظیمات اقتصادی", "adm_eco")])
    if "store.manage" in perms:
        rows.append([btn("🛒 قیمت‌های فروشگاه", "adm_store")])
    if "broadcast.send" in perms:
        rows.append([btn("📢 پیام همگانی", "adm_bcast")])
    if "support.view" in perms:
        rows.append([btn("💬 تیکت‌های پشتیبانی", "adm_tickets")])
    if "backups.view" in perms:
        rows.append([btn("🔐 پشتیبان‌گیری", "adm_backup")])
    if "backups.create" in perms:
        # v2.1: instant backup directly on the main admin menu
        rows.append([btn("⚡ بکاپ فوری", "adm_backup_now")])
    if role == "owner":
        rows.append([btn("♻️ بازیابی از فایل", "adm_restore")])
    if "settings.manage" in perms:
        # v2.1: live maintenance state shown on the button itself
        async with session_scope() as s:
            cfg = await service.get_setting(s, "app")
        on = cfg.get("maintenanceMode", False)
        rows.append([btn(f"🔧 حالت تعمیر: {'🟢 روشن' if on else '🔴 خاموش'}",
                         "adm_maint")])
    if "admins.manage" in perms and role == "owner":
        rows.append([btn("👥 مدیران", "adm_admins")])
    # back-to-home on every submenu
    rows.append([btn("🏠 بازگشت به منوی اصلی", "back_home")])
    labels = {"owner": "مالک (Owner)", "senior_admin": "مدیر ارشد",
              "moderator": "ناظر", "support": "پشتیبان"}
    await _say(user, f"🛡 پنل مدیریت هم‌چت\n"
                    f"👤 نقش شما: {labels.get(role, role)}\n"
                    f"💡 یک گزینه را انتخاب کنید:", keyboard=rows or None)


async def _perms(role: str) -> list:
    async with session_scope() as s:
        perms = await service.role_permissions(s, role)
    # v2.1 FIX: "*" (owner) must expand to every known permission, otherwise the
    # owner's bot panel rendered almost empty (only role-gated rows appeared).
    if "*" in perms:
        every = set()
        for plist in service.ROLE_DEFAULT_PERMISSIONS.values():
            every.update(plist)
        every.update({"settings.manage", "admins.manage", "privacy.manage",
                      "referrals.view", "daily_rewards.manage", "audit.view",
                      "monitoring.view"})
        perms = sorted(every)
    return perms


async def admin_callback(user: User, data: str, chat_id) -> None:
    if not data.startswith("adm"):
        return
    role = await get_role(user)
    if not role:
        return
    perms = await _perms(role)
    lang = user.language

    if data == "adm_stats" and "dashboard.view" in perms:
        # v2.1: numeric-only stats with update + back buttons
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
        await _say(user, "📊 آمار کل:\n"
                        f"👥 کاربران: {users} (🔵 تلگرام: {tg} | 🟢 بله: {bal} | ✅ فعال: {active_u})\n"
                        f"💬 گفتگوهای فعال: {chats}\n"
                        f"✉️ مجموع پیام‌ها: {msgs}\n"
                        f"🪙 سکه در گردش: {coins}\n"
                        f"🚩 گزارش‌های در انتظار: {pend}\n"
                        f"⛔ مسدودی‌های فعال: {bans_n}",
                   keyboard=kb([[btn("🔄 آپدیت", "adm_stats")],
                                [btn("🏠 بازگشت به منوی اصلی", "back_home")]]))
        return

    if data == "adm_finduser" and "users.view" in perms:
        user.state = {"step": "adm_finduser"}
        await _say(user, "شناسه کاربر را بفرستید (مثل U1A2B3C4 یا platform:id مثل telegram:12345):")
        return

    if data == "adm_reports" and "reports.view" in perms:
        async with session_scope() as s:
            reps = (await s.execute(select(Report).order_by(Report.id.desc()).limit(5))).scalars()
            lines = []
            for r in reps:
                rep_u = await s.get(User, r.reported_id)
                lines.append(f"#{r.id} {r.status} | گزارش‌شده: {rep_u.public_id if rep_u else '?'} | {r.reason}\n{r.details[:80]}")
        await _say(user, "📋 آخرین گزارش‌ها:\n\n" + ("\n\n".join(lines) or "موردی نیست."))
        return

    if data == "adm_eco" and ("economy.configure" in perms or "economy.view" in perms):
        async with session_scope() as s:
            eco = await service.get_setting(s, "economy")
        txt = ("💰 تنظیمات اقتصادی فعلی:\n\n"
               f"🎁 ثبت‌نام: {eco['initialReward']} سکه\n"
               f"💬 هر پیام: {eco['messageCost']} سکه\n"
               f"📅 پاداش روزانه: {eco['dailyBonus']} سکه\n"
               f"🎯 فیلتر جنسیت: {eco['genderFilterMatchCost']} سکه\n"
               f"👥 دعوت (هر طرف): {eco['referralRewardPerParty']} سکه\n"
               f"📛 تغییر نام: {eco.get('nameChangeCost', 10)} سکه\n"
               f"🎂 تغییر سن: {eco.get('ageChangeCost', 10)} سکه\n"
               f"🎉 ثبت تولد: {eco.get('birthdayReward', 10)} سکه\n"
               f"🔗 اتصال حساب: {eco.get('accountLinkReward', 50)} سکه")
        if "economy.configure" in perms:
            txt += ("\n\n✏️ برای تغییر بفرستید:\n<code>set eco KEY VALUE</code>\n"
                    "KEYها: initial, message, daily, gender, referral, namechg, agechg, bday, link")
            user.state = {"step": "adm_eco_set"}
        await _say(user, txt, keyboard=kb([[btn("🏠 بازگشت به منوی اصلی", "back_home")]]))
        return

    if data == "adm_store" and "store.manage" in perms:
        async with session_scope() as s:
            pkgs = (await s.execute(select(StorePackage).order_by(StorePackage.coins))).scalars()
            lines = [f"🪙 {p.coins} سکه → ⭐{p.telegram_stars_price} | {p.bale_toman_price:,} تومان "
                     f"({'فعال' if p.active else 'غیرفعال'})" for p in pkgs]
            store = await service.get_setting(s, "store")
        await _say(user, "🛒 قیمت‌های فروشگاه (نرخ جاری: هر سکه = "
                        f"{store.get('tomanPerCoin', 500):,} تومان | هر ⭐ استارز = "
                        f"{store.get('coinsPerStar', 20)} سکه):\n\n"
                        + "\n".join(lines) +
                        "\n\n✏️ تغییر نرخ‌ها و قیمت بسته‌ها از پنل وب انجام می‌شود.",
                   keyboard=kb([[btn("🏠 بازگشت به منوی اصلی", "back_home")]]))
        return

    if data == "adm_bans" and "bans.manage" in perms:
        async with session_scope() as s:
            bans = (await s.execute(select(Ban).where(Ban.status == "active")
                                    .order_by(Ban.id.desc()).limit(10))).scalars()
            from .db import Appeal
            appeals = (await s.execute(select(Appeal).where(Appeal.status == "pending")
                                       .order_by(Appeal.id.desc()).limit(10))).scalars()
            blines = [f"⛔ #{b.id} {b.type} | {b.reason[:40]} | تا {b.expires_at or 'همیشه'}"
                      for b in bans] or ["موردی نیست."]
            alines = [f"📩 #{a.id} | ban#{a.ban_id} | {a.message[:50]}" for a in appeals] or ["موردی نیست."]
        await _say(user, "⛔ مسدودی‌های فعال:\n" + "\n".join(blines) +
                        "\n\n📩 اعتراض‌های در انتظار:\n" + "\n".join(alines) +
                        "\n\n💡 برای رفع مسدودی، از جستجوی کاربر استفاده کنید.",
                   keyboard=kb([[btn("🏠 بازگشت به منوی اصلی", "back_home")]]))
        return

    if data == "adm_tickets" and "support.view" in perms:
        async with session_scope() as s:
            from .db import SupportTicket
            tickets = (await s.execute(select(SupportTicket)
                                       .order_by(SupportTicket.id.desc()).limit(10))).scalars()
            lines = [f"💬 {t.ticket_no} [{t.status}] {t.subject[:40]}" for t in tickets] or ["موردی نیست."]
        await _say(user, "💬 آخرین تیکت‌های پشتیبانی:\n\n" + "\n".join(lines) +
                        "\n\n💡 پاسخ‌دهی از پنل وب انجام می‌شود.",
                   keyboard=kb([[btn("🏠 بازگشت به منوی اصلی", "back_home")]]))
        return

    if data == "adm_bcast" and "broadcast.send" in perms:
        user.state = {"step": "adm_bcast_text"}
        await _say(user, "متن پیام همگانی را بفرستید (به همه کاربران فعال هر دو پلتفرم ارسال می‌شود):")
        return

    if data == "adm_backup" and "backups.view" in perms:
        async with session_scope() as s:
            cfg = await service.get_setting(s, "backup")
        rows = []
        if "backups.create" in perms:
            rows.append([btn("🚀 پشتیبان‌گیری فوری", "adm_backup_now")])
        await _say(user, f"🔐 پشتیبان‌گیری:\nزمان‌بندی: روزانه ۰۳:۰۰ تهران\n"
                        f"آخرین اجرا: {cfg.get('lastRunDate') or '—'}\n"
                        f"رمزنگاری: AES-256-GCM\nمقصد: همین بازو → مالک", keyboard=rows or None)
        return

    if data == "adm_backup_now" and "backups.create" in perms:
        await _say(user, "⏳ در حال ساخت پشتیبان...")
        bot = RUNTIME.get("adapter_telegram")
        if not bot:
            await _say(user, "خطا: بازوی تلگرام در دسترس نیست.")
            return
        rec = await backup_mod.run_backup_job(bot, "manual", created_by=f"bot:{user.platform_user_id}")
        if rec:
            await _say(user, f"✅ پشتیبان {rec.id} ساخته شد و برای مالک ارسال گردید "
                            f"(وضعیت ارسال: {rec.delivery_status}).")
        else:
            await _say(user, "⚠️ پشتیبان‌گیری دیگری در حال اجراست.")
        return

    if data == "adm_restore" and role == "owner":
        RESTORE_WAIT[str(chat_id)] = {"parts": {}, "password": None, "expected": None}
        await _say(user, "♻️ حالت بازیابی فعال شد.\n\n"
                        "فایل پشتیبانی که ربات برایت فرستاده را همین‌جا فوروارد کن.\n"
                        "🔑 رمز بازیابی باید در کپشن فایل باشد (ربات خودش آن را در کپشن می‌نویسد؛ "
                        "اگر کپشن را دستی تغییر دادی، به شکل «رمز بازیابی: ...» بنویس).\n\n"
                        "پشتیبان بلافاصله اعتبارسنجی می‌شود و با یک کلیک جایگزین می‌شود.\n"
                        "⚠️ بازیابی کامل، همه داده‌های فعلی را با نسخه پشتیبان جایگزین می‌کند.",
                   keyboard=kb([[btn("❌ لغو", "adm_restore_cancel")]]))
        return

    if data == "adm_restore_cancel":
        RESTORE_WAIT.pop(str(chat_id), None)
        await _say(user, "لغو شد.")
        return

    if data == "adm_restore_go" and role == "owner":
        # one-click confirm: the blob was already validated in admin_document
        st = RESTORE_WAIT.get(str(chat_id)) or {}
        blob = st.get("blob") or b""
        password = st.get("password") or settings.backup_password
        RESTORE_WAIT.pop(str(chat_id), None)
        if not blob:
            await _say(user, "هیچ فایلی دریافت نشد. ابتدا فایل پشتیبان را فوروارد کن "
                             "(از منوی مدیریت ← ♻️ بازیابی از فایل).")
            return
        await _say(user, "⏳ اسنپ‌شات اضطراری گرفته می‌شود و بازیابی آغاز می‌شود "
                         "(حالت تعمیر فعال می‌شود)...")
        result = await backup_mod.execute_restore(blob, password)
        icon = "✅" if result["status"] == "succeeded" else "⚠️"
        await _say(user, f"{icon} {result['message']}\nاسنپ‌شات بازگشت: {result['rollbackSnapshotId']}\n"
                        f"مدت: {result['durationSec']} ثانیه")
        return

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
        # v2.1: dynamic toggle with confirm + back-to-menu buttons
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
        return

    if data == "adm_admins" and role == "owner":
        user.state = {"step": "adm_admins_set"}
        await _say(user, "مدیر جدید را به این شکل بفرستید:\n"
                        "addadmin PLATFORM:ID ROLE\nمثال: addadmin telegram:11111111 moderator\n"
                        "ROLEها: senior_admin | moderator | support\n"
                        "حذف: deladmin telegram:11111111")
        return

    await _menu(user, role)


async def admin_text_flow(user: User, text: str) -> bool:
    """Handle admin FSM steps inside chat; returns True if consumed."""
    state = user.state or {}
    step = state.get("step")
    if not step or not step.startswith("adm"):
        return False
    role = await get_role(user)
    if not role:
        return False

    if step == "adm_finduser":
        async with session_scope() as s:
            target = None
            q = text.strip()
            if ":" in q:
                plat, _, pid = q.partition(":")
                r = await s.execute(select(User).where(User.platform == plat.strip(),
                                                       User.platform_user_id == pid.strip()))
                target = r.scalar_one_or_none()
            else:
                r = await s.execute(select(User).where(User.public_id == q.upper()))
                target = r.scalar_one_or_none()
            if not target:
                await _say(user, "کاربر یافت نشد.")
                return True
            perms = await service.role_permissions(s, role)
            ban = await service.active_ban(s, target)
            info = (f"👤 {target.public_id} | {target.display_name}\n"
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
            rows.append([btn("🏠 بازگشت به منوی اصلی", "back_home")])
            await _say(user, info, keyboard=rows or None)
        return True

    if step == "adm_eco_set" and "economy.configure" in perms:
        parts = text.split()
        if len(parts) == 4 and parts[0] == "set" and parts[1] == "eco":
            keymap = {"initial": "initialReward", "message": "messageCost", "daily": "dailyBonus",
                      "gender": "genderFilterMatchCost", "referral": "referralRewardPerParty",
                      "namechg": "nameChangeCost", "agechg": "ageChangeCost",
                      "bday": "birthdayReward", "link": "accountLinkReward"}
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
                await _say(user, f"✅ {parts[2]} = {val} ثبت شد.")
            except (ValueError, KeyError):
                await _say(user, "❌ مقدار نامعتبر.")
        else:
            await _say(user, "دستور نامعتبر. مثال: set eco daily 10")
        return True

    if step == "adm_bcast_text" and "broadcast.send" in perms:
        from .db import BroadcastCampaign
        async with session_scope() as s:
            targets = await service.broadcast_targets(s, "all")
            camp = BroadcastCampaign(title=f"manual-{secrets.token_hex(2)}", message=text[:4000],
                                     audience="all", audience_label="همه کاربران",
                                     rate_limit_per_sec=20, status="sending",
                                     target_count=len(targets),
                                     created_by=f"bot:{user.platform_user_id}")
            s.add(camp)
            await s.flush()
            camp_id = camp.id
        RUNTIME.setdefault("broadcast_queue", []).append(camp_id)
        await _say(user, f"📢 کمپین #{camp_id} برای {len(targets)} کاربر آغاز شد.")
        return True

    if step == "adm_admins_set" and role == "owner":
        parts = text.split()
        async with session_scope() as s:
            if len(parts) == 3 and parts[0] == "addadmin":
                plat, _, pid = parts[1].partition(":")
                s.add(BotAdmin(platform=plat, platform_user_id=pid, role=parts[2]))
                await service.audit(s, actor_type="bot", admin_id=user.platform_user_id,
                                    admin_name=user.display_name, admin_role=role,
                                    action="admin.created", module="roles",
                                    target_type="bot_admin", target_id=parts[1],
                                    details=f"role={parts[2]}")
                await _say(user, f"✅ مدیر {parts[1]} با نقش {parts[2]} اضافه شد.")
            elif len(parts) == 2 and parts[0] == "deladmin":
                plat, _, pid = parts[1].partition(":")
                await s.execute(delete(BotAdmin).where(BotAdmin.platform == plat,
                                                       BotAdmin.platform_user_id == pid))
                await _say(user, "✅ حذف شد.")
            else:
                await _say(user, "دستور نامعتبر.")
        return True
    return False


async def admin_document(user: User, file_bytes: bytes,
                         caption: str = "", filename: str = "") -> bool:
    """Owner forwards a backup file during restore wait-state (v2.3 one-click).

    The forwarded caption carries everything needed for the restore — most
    importantly the encryption password (the bot itself puts it there when
    delivering the backup). The blob is validated instantly; one confirm
    button replaces all current data with the backup.
    """
    chat_key = str(user.platform_user_id)
    st = RESTORE_WAIT.get(chat_key)
    if st is None:
        return False
    role = await get_role(user)
    if role != "owner":
        return False
    pwd, idx, total = _parse_restore_caption(caption, filename)
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
    v = backup_mod.validate_blob(blob, password=password)
    if not v["valid"]:
        if not expected:
            # might be part 1 of a multi-part archive (or a wrong/missing password)
            await _say(user, f"📄 فایل {received} دریافت شد ({len(file_bytes) / 1e6:.1f} MB) "
                             "اما هنوز یک آرشیو کامل و معتبر نیست.\n"
                             "• اگر پشتیبان چندبخشی است، بقیه بخش‌ها را هم فوروارد کن.\n"
                             "• اگر رمز در کپشن نبود، هنگام فوروارد بنویس: رمز بازیابی: ...")
            return True
        await _say(user, "❌ آرشیو نامعتبر است:\n" + "\n".join(v["warnings"]))
        RESTORE_WAIT.pop(chat_key, None)
        return True
    st["blob"] = blob
    await _say(user,
               f"✅ پشتیبان معتبر است — حجم {v['sizeMb']} MB | اسکیما {v['schemaVersion']} "
               f"| چک‌سام SHA-256 تایید شد.\n"
               "⚠️ با تأیید، همه داده‌های فعلی با این نسخه جایگزین می‌شود "
               "(اسنپ‌شات اضطراری هم گرفته می‌شود).",
               keyboard=kb([[btn("♻️ جایگزینی با این پشتیبان", "adm_restore_go")],
                            [btn("❌ لغو", "adm_restore_cancel")]]))
    return True


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
        elif data.startswith("admuc_") and "users.coins" in perms:
            amount = int(act)
            if amount >= 0:
                await service.credit(s, target, amount, "admin_grant", "اعطای دستی مدیر")
            else:
                await service.debit(s, target, -amount, "admin_deduct", "کسر دستی مدیر")
            await service.audit(s, actor_type="bot", admin_id=user.platform_user_id,
                                admin_name=user.display_name, admin_role=role,
                                action="user.coins_granted", module="economy",
                                target_type="user", target_id=target.public_id,
                                details=f"{amount:+d}")
            await _say(user, f"🪙 موجودی {target.public_id}: {target.coin_balance}")
    return True
