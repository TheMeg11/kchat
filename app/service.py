"""Core business logic: settings, economy, matching, moderation, reports."""
from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import delete, func, select, text, update

from . import db
from .db import (AdminNotification, AllowlistPhrase, Appeal, AppSetting, AuditLog,
                 Ban, Block, BroadcastCampaign, Chat, ChatMessage, CoinTransaction,
                 DailyClaim, DictionaryWord, MediaType, Payment, ProcessedUpdate,
                 QueueEntry, RecentPair, Referral, Report, RolePermission, StorePackage,
                 SupportMessage, SupportTicket, User, ViolationLog, utcnow)

DEFAULT_SETTINGS: dict[str, Any] = {
    "economy": {"initialReward": 20, "messageCost": 1, "dailyBonus": 5,
                "genderFilterMatchCost": 5, "referralRewardPerParty": 50,
                "nameChangeCost": 10, "ageChangeCost": 10, "birthdayReward": 10,
                "accountLinkReward": 50,
                "updatedBy": "system", "updatedAt": None},
    # v2.4: friend-list feature + signup-rescue cadence + panel alerting
    "growth": {"rescueEnabled": True, "rescueMaxPerDay": 200,
               "reminderHours": [6, 24, 72], "maxFriends": 50,
               "updatedBy": "system", "updatedAt": None},
    # v2.4: age-group separation between 13-17 and 18-60 is REMOVED by product
    # decision (chats are fully moderated) — the matching engine no longer
    # filters on age_group; the column stays for analytics only.
    "matching": {"enabled": True, "maxQueueSize": 1000, "searchTimeoutSec": 300,
                 "idleTimeoutSec": 900, "repeatMatchCooldownMin": 30,
                 "genderFilterEnabled": True, "ageGroupSeparation": False},
    "moderation": {"blockUrls": True, "blockTelegramHandles": True, "blockPhoneNumbers": True,
                   "blockInstagramIds": True, "autoBanReportThreshold": 3,
                   "autoBanWindowHours": 24, "autoBanDurationHours": 24,
                   "profanityEscalationHours": [24, 168, 720, 0]},  # 0 = permanent
    "app": {"maintenanceMode": False, "maintenanceMessage": "سیستم در حال به‌روزرسانی است.",
            "allowNewRegistrations": True, "enableReferralSystem": True,
            "enableDailyRewards": True, "enableGenderFilter": True,
            "enableSupportTickets": True, "defaultLanguage": "fa",
            "maxMessageLength": 4000, "supportBotHandle": "@kanarchatbot",
            "paymentsTelegramEnabled": True, "paymentsBaleEnabled": False},
    "privacy": {"chatRetentionDays": 30, "accountDeletionGraceHours": 0,
                "deleteChatsOnAccountDeletion": True,
                "privacyPolicyFa": "", "rulesFa": "", "autoPurgeSchedule": "hourly",
                "backupRetentionDisclosureFa": "آرشیوهای پشتیبان رمزنگاری‌شده ممکن است متن گفتگوهای قدیمی‌تر از ۳۰ روز را در نسخه‌های قبلی خود حفظ کنند."},
    "backup": {"autoBackupEnabled": True, "scheduleTimeTehran": "03:00",
               "retentionDays": 90, "encryptionEnabled": True, "lastRunDate": ""},
    # v2.2: coin-store conversion rates (changeable via settings/economy);
    # v2.4.4: default rate 200 Toman = 1 coin (live value changed 2026-11-17)
    "store": {"tomanPerCoin": 200,        # 200 Toman = 1 coin
              "coinsPerStar": 20,         # 1 Telegram Star = 20 coins
              "customMinCoins": 10, "customMaxCoins": 5000,
              "updatedBy": "system", "updatedAt": None},
    "platforms": {
        "telegram": {"pollingFallback": True, "dropPendingOnRestart": False,
                     "webhookMaxConnections": 60, "retryAttempts": 4},
        "bale": {"pollingFallback": True, "dropPendingOnRestart": False,
                 "webhookMaxConnections": 40, "retryAttempts": 4}},
}

ROLE_DEFAULT_PERMISSIONS = {
    "owner": ["*"],
    "senior_admin": ["dashboard.view", "users.view", "users.edit", "users.ban", "users.coins",
                     "chats.view_reported", "reports.view", "reports.resolve", "moderation.manage",
                     "bans.manage", "economy.view", "store.manage", "payments.view", "payments.refund",
                     "referrals.view", "daily_rewards.manage", "media.configure", "broadcast.send",
                     "support.view", "support.reply", "audit.view", "backups.view", "backups.create",
                     "monitoring.view", "platforms.manage"],
    "moderator": ["dashboard.view", "users.view", "chats.view_reported", "reports.view",
                  "reports.resolve", "moderation.manage", "bans.manage", "appeals.review",
                  "support.view", "audit.view"],
    "support": ["dashboard.view", "users.view", "support.view", "support.reply",
                "reports.view", "payments.view"],
}

PERMISSION_KEYS = [
    "dashboard.view", "users.view", "users.edit", "users.delete", "users.ban", "users.coins",
    "chats.view_reported", "chats.view_all", "reports.view", "reports.resolve",
    "moderation.manage", "bans.manage", "appeals.review", "economy.view", "economy.configure",
    "store.manage", "payments.view", "payments.refund", "referrals.view",
    "daily_rewards.manage", "media.configure", "matching.configure", "broadcast.send",
    "support.view", "support.reply", "admins.manage", "roles.manage", "audit.view",
    "backups.view", "backups.create", "backups.restore", "platforms.manage",
    "settings.manage", "privacy.manage", "security.manage", "monitoring.view",
]

MEDIA_KEYS = ["text", "photo", "voice", "video", "document", "audio", "sticker", "gif",
              "location", "contact", "video_note", "poll"]

TEHRAN = timezone(timedelta(hours=3, minutes=30))


def tehran_now() -> datetime:
    return datetime.now(TEHRAN)


def tehran_day(d: Optional[datetime] = None) -> str:
    return (d or tehran_now()).strftime("%Y-%m-%d")


# ------------------------------------------------------------- settings ----
async def get_setting(session, key: str) -> Any:
    row = await session.get(AppSetting, key)
    if row is None:
        # v2.3.4: hand out a COPY — the live DEFAULT_SETTINGS dict must never
        # leak to callers (a caller mutating the returned config corrupted the
        # process-wide defaults, e.g. maintenanceMode sticking on).
        base = DEFAULT_SETTINGS.get(key)
        return dict(base) if isinstance(base, dict) else base
    base = DEFAULT_SETTINGS.get(key)
    if isinstance(base, dict) and isinstance(row.value, dict):
        merged = dict(base)
        merged.update(row.value)
        return merged
    return row.value


async def set_setting(session, key: str, value: Any) -> None:
    row = await session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value


def has_perm(role: str, permissions: list, perm: str) -> bool:
    if role == "owner":
        return True
    return "*" in permissions or perm in permissions


async def role_permissions(session, role: str) -> list:
    row = await session.get(RolePermission, role)
    if row and row.permissions:
        return list(row.permissions)
    return list(ROLE_DEFAULT_PERMISSIONS.get(role, []))


async def audit(session, *, actor_type="web", admin_id="", admin_name="", admin_role="",
                action="", module="settings", target_type="", target_id="",
                result="success", ip="", details="") -> None:
    session.add(AuditLog(actor_type=actor_type, admin_id=str(admin_id), admin_name=admin_name,
                         admin_role=admin_role, action=action, module=module,
                         target_type=target_type, target_id=str(target_id), result=result,
                         ip_address=ip, details=details[:2000]))


async def notify(session, title: str, body: str, severity="info", route="/") -> None:
    session.add(AdminNotification(title=title[:120], body=body[:2000], severity=severity, route=route))


# --------------------------------------------------------------- economy ----
async def credit(session, user: User, amount: int, type_: str, description: str = "",
                 reference_id: str = "", idempotency_key: str = "") -> CoinTransaction:
    assert amount > 0
    if idempotency_key:
        existing = await session.execute(
            select(CoinTransaction).where(CoinTransaction.idempotency_key == idempotency_key))
        found = existing.scalar_one_or_none()
        if found:
            return found
    user.coin_balance += amount
    txn = CoinTransaction(user_id=user.id, amount=amount, balance_after=user.coin_balance,
                          type=type_, description=description, reference_id=reference_id,
                          idempotency_key=idempotency_key or None)
    session.add(txn)
    return txn


async def debit(session, user: User, amount: int, type_: str, description: str = "",
                reference_id: str = "", idempotency_key: str = "") -> Optional[CoinTransaction]:
    """Atomically deduct; returns None when balance is insufficient (no negative balances)."""
    assert amount > 0
    if idempotency_key:
        existing = await session.execute(
            select(CoinTransaction).where(CoinTransaction.idempotency_key == idempotency_key))
        found = existing.scalar_one_or_none()
        if found:
            return found
    # Lock the wallet row on Postgres to serialize concurrent spends.
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        await session.execute(select(User).where(User.id == user.id).with_for_update())
        await session.refresh(user)
    if user.coin_balance < amount:
        return None
    user.coin_balance -= amount
    txn = CoinTransaction(user_id=user.id, amount=-amount, balance_after=user.coin_balance,
                          type=type_, description=description, reference_id=reference_id,
                          idempotency_key=idempotency_key or None)
    session.add(txn)
    return txn


async def grant_daily_bonus(session, user: User) -> int:
    eco = await get_setting(session, "economy")
    if not (await get_setting(session, "app")).get("enableDailyRewards", True):
        return 0
    day = tehran_day()
    exists = await session.execute(
        select(DailyClaim).where(DailyClaim.user_id == user.id, DailyClaim.day == day))
    if exists.scalar_one_or_none():
        return 0
    session.add(DailyClaim(user_id=user.id, day=day, coins=eco["dailyBonus"]))
    await credit(session, user, eco["dailyBonus"], "daily_bonus",
                 f"پاداش روزانه {day}", idempotency_key=f"daily:{user.id}:{day}")
    return eco["dailyBonus"]


async def on_first_chat_completed(session, user: User) -> None:
    """Referral reward to both parties fires once, after the referred user's first valid chat."""
    if user.first_chat_completed_at is not None or not user.referred_by_id:
        return
    user.first_chat_completed_at = utcnow()
    referrer = await session.get(User, user.referred_by_id)
    if referrer is None or referrer.status != "active":
        return
    if not (await get_setting(session, "app")).get("enableReferralSystem", True):
        return
    eco = await get_setting(session, "economy")
    reward = eco["referralRewardPerParty"]
    pair = await session.execute(select(Referral).where(
        Referral.referrer_id == referrer.id, Referral.referred_id == user.id))
    ref = pair.scalar_one_or_none()
    if ref is None:
        ref = Referral(code=referrer.referral_code, referrer_id=referrer.id, referred_id=user.id)
        session.add(ref)
    if ref.status == "rewarded":
        return
    # Anti-farming: same-platform only, referrer must be active, code used once per pair (unique).
    ref.status = "rewarded"
    referrer.referral_count += 1
    await credit(session, referrer, reward, "referral_reward",
                 f"دعوت کاربر {user.public_id}", idempotency_key=f"ref:ref:{user.id}")
    await credit(session, user, reward, "referral_reward",
                 f"پاداش دعوت از {referrer.public_id}", idempotency_key=f"ref:new:{user.id}")


# ------------------------------------------------------------- matching ----
async def active_chat_for(session, user_id: int) -> Optional[Chat]:
    res = await session.execute(
        select(Chat).where(Chat.status == "active",
                           (Chat.user_a_id == user_id) | (Chat.user_b_id == user_id)))
    return res.scalar_one_or_none()


async def is_blocked(session, blocker_id: int, target_id: int) -> bool:
    res = await session.execute(select(Block).where(
        Block.blocker_id == blocker_id, Block.blocked_id == target_id))
    return res.scalar_one_or_none() is not None


async def enqueue(session, user: User, gender_filter: str) -> bool:
    if await active_chat_for(session, user.id) is not None:
        return False
    exists = await session.execute(select(QueueEntry).where(QueueEntry.user_id == user.id))
    if exists.scalar_one_or_none():
        return False
    session.add(QueueEntry(user_id=user.id, age_group=age_group(user.age),
                           gender=user.gender, gender_filter=gender_filter))
    return True


def age_group(age: int) -> str:
    return "13-17" if age < 18 else "18-60"


# ------------------------------------------------- birthdays / dynamic age ----
JALALI_MONTH_DAYS = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]  # esfand 29 (leap ignored for range check)


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    """Standard algorithm (Behrooz Farsi / jalaali) — no external deps."""
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + jd
    if jm < 7:
        days += (jm - 1) * 31
    else:
        days += ((jm - 7) * 30) + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    sal_a = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gm = 0
    for i in range(12):
        if gd <= sal_a[i]:
            break
        gm = i + 1
    gd -= sal_a[gm - 1]
    if _is_gregorian_leap(gy) and gm > 2:
        # already accounted via sal_a? no: sal_a excludes leap day
        pass
    return gy, gm, gd


def _is_gregorian_leap(y: int) -> bool:
    return (y % 4 == 0 and y % 100 != 0) or y % 400 == 0


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy - 1600
    gm2 = gm - 1
    gd2 = gd - 1
    g_day_no = 365 * gy2 + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
    g_day_no += g_d_m[gm2]
    if gm2 > 1 and ((gy2 % 4 == 0 and gy2 % 100 != 0) or (gy2 % 400 == 0)):
        g_day_no += 1
    g_day_no += gd2
    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053
    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461
    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365
    if j_day_no < 186:
        jm = 1 + j_day_no // 31
        jd = 1 + (j_day_no % 31)
    else:
        jm = 7 + (j_day_no - 186) // 30
        jd = 1 + ((j_day_no - 186) % 30)
    return jy, jm, jd


def compute_age_from_birthday(by: int, bm: int, bd: int) -> int:
    """Age in whole years (gregorian reference)."""
    from datetime import date
    today = date.today()
    years = today.year - by
    if (today.month, today.day) < (bm, bd):
        years -= 1
    return years


async def update_dynamic_ages(session) -> int:
    """Refresh ages from stored birthdays (called daily)."""
    import datetime as _dt
    res = await session.execute(select(User).where(User.birth_year.isnot(None)))
    changed = 0
    today = _dt.date.today()
    for u in res.scalars():
        new_age = compute_age_from_birthday(u.birth_year, u.birth_month, u.birth_day)
        if new_age != u.age:
            u.age = max(13, min(60, new_age))
            # v2.4: queue re-join on age-group change is no longer needed —
            # matching no longer separates age groups.
            changed += 1
    return changed


async def today_birthdays(session) -> list[User]:
    import datetime as _dt
    today = _dt.date.today()
    res = await session.execute(select(User).where(
        User.birth_month == today.month, User.birth_day == today.day,
        User.status == "active"))
    return list(res.scalars())


async def grant_birthday_gift(session, user: User) -> int:
    """v2.1: 50-coin gift on the user's actual birthday (once per year, idempotent)."""
    import datetime as _dt
    eco = await get_setting(session, "economy")
    amount = eco.get("birthdayGiftCoins", 50)
    year = _dt.date.today().year
    key = f"bdaygift:{user.id}:{year}"
    exists = await session.execute(
        select(CoinTransaction).where(CoinTransaction.idempotency_key == key))
    if exists.scalar_one_or_none():
        return 0
    await credit(session, user, amount, "birthday_gift",
                 f"🎁 کادوی روز تولد {year}", idempotency_key=key)
    return amount


def save_birthday(user: User, calendar: str, y: int, m: int, d: int) -> int:
    """v2.1: store year/month/day + derive the age. Returns the new age."""
    if calendar == "jalali":
        gy, gm, gd = jalali_to_gregorian(y, m, d)
    else:
        gy, gm, gd = y, m, d
    age = compute_age_from_birthday(gy, gm, gd)
    user.birth_year, user.birth_month, user.birth_day, user.birth_calendar = gy, gm, gd, calendar
    user.age = max(13, min(60, age))
    return user.age


# --------------------------------------------- anonymous link & linking ----
def new_anon_code() -> str:
    return "a" + secrets.token_hex(5)


async def ensure_anon_link(session, user: User) -> str:
    if not user.anon_link_code:
        user.anon_link_code = new_anon_code()
    return user.anon_link_code


async def find_by_anon_code(session, code: str) -> Optional[User]:
    res = await session.execute(select(User).where(User.anon_link_code == code))
    return res.scalar_one_or_none()


def new_link_code() -> str:
    return "l" + secrets.token_hex(5)


async def start_account_link(session, user: User) -> str:
    user.link_code = new_link_code()
    user.link_code_expires = utcnow() + timedelta(minutes=10)
    return user.link_code


async def confirm_account_link(session, initiator: User, confirmer: User) -> str:
    """Link two cross-platform accounts; reward both once. Returns status."""
    if initiator.platform == confirmer.platform:
        return "same_platform"
    if initiator.linked_user_id or confirmer.linked_user_id:
        return "already"
    if not initiator.link_code or initiator.link_code_expires < utcnow():
        return "expired"
    eco = await get_setting(session, "economy")
    reward = eco.get("accountLinkReward", 50)
    initiator.linked_user_id = confirmer.id
    confirmer.linked_user_id = initiator.id
    initiator.link_code = None
    initiator.link_code_expires = None
    if not initiator.link_rewarded:
        await credit(session, initiator, reward, "account_link_reward",
                     "پاداش اتصال حساب",
                     idempotency_key=f"link:{min(initiator.id, confirmer.id)}:{max(initiator.id, confirmer.id)}:a")
        initiator.link_rewarded = True
    if not confirmer.link_rewarded:
        await credit(session, confirmer, reward, "account_link_reward",
                     "پاداش اتصال حساب",
                     idempotency_key=f"link:{min(initiator.id, confirmer.id)}:{max(initiator.id, confirmer.id)}:b")
        confirmer.link_rewarded = True
    return "ok"


def _filters_compatible(a: QueueEntry, b: QueueEntry) -> bool:
    if a.gender_filter == "female" and b.gender != "female":
        return False
    if a.gender_filter == "male" and b.gender != "male":
        return False
    if b.gender_filter == "female" and a.gender != "female":
        return False
    if b.gender_filter == "male" and a.gender != "male":
        return False
    return True


async def try_match(session) -> Optional[tuple[User, User, bool]]:
    """FIFO matching with row locks; returns (user_a, user_b, charged_b_id) or None.

    charged_b_id marks the entry whose gender filter caused the coin charge.
    """
    dialect = session.bind.dialect.name if session.bind is not None else ""
    if dialect == "postgresql":
        res = await session.execute(
            select(QueueEntry).order_by(QueueEntry.created_at, QueueEntry.id)
            .with_for_update(skip_locked=True).limit(500))
    else:
        res = await session.execute(
            select(QueueEntry).order_by(QueueEntry.created_at, QueueEntry.id).limit(500))
    entries = list(res.scalars())
    if len(entries) < 2:
        return None
    eco = await get_setting(session, "economy")
    cost = eco["genderFilterMatchCost"]
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            a, b = entries[i], entries[j]
            # v2.4: age-group separation (13-17 vs 18-60) REMOVED by product
            # decision — chats are fully moderated; matching is open to all ages.
            if await is_blocked(session, a.user_id, b.user_id) or await is_blocked(session, b.user_id, a.user_id):
                continue
            if not _filters_compatible(a, b):
                continue
            cooldown = (await get_setting(session, "matching")).get("repeatMatchCooldownMin", 30)
            since = utcnow() - timedelta(minutes=cooldown)
            # v2.4.4: limit(1)+first() — a pair can have SEVERAL RecentPair rows
            # inside the window (two chats in different orientations, restored
            # rows, …) and scalar_one_or_none() raised MultipleResultsFound,
            # crashing every match attempt and the scheduler match tick (prod
            # incident 2026-11-17). We only need to know "at least one exists".
            recent = await session.execute(select(RecentPair.id).where(
                ((RecentPair.user_a_id == a.user_id) & (RecentPair.user_b_id == b.user_id)) |
                ((RecentPair.user_a_id == b.user_id) & (RecentPair.user_b_id == a.user_id)),
                RecentPair.ended_at >= since).limit(1))
            if recent.first():
                continue
            ua, ub = await session.get(User, a.user_id), await session.get(User, b.user_id)
            if not ua or not ub or ua.status != "active" or ub.status != "active":
                continue
            # verify affordability BEFORE any debit so a failed pair never charges
            if a.gender_filter != "everyone" and ua.coin_balance < cost:
                continue
            if b.gender_filter != "everyone" and ub.coin_balance < cost:
                continue
            charged = None
            if a.gender_filter != "everyone":
                await debit(session, ua, cost, "gender_filter_cost",
                            "هزینه فیلتر جنسیت", idempotency_key=f"gf:{a.id}")
                charged = a.user_id
            if b.gender_filter != "everyone":
                await debit(session, ub, cost, "gender_filter_cost",
                            "هزینه فیلتر جنسیت", idempotency_key=f"gf:{b.id}")
                charged = charged or b.user_id
            chat = Chat(user_a_id=ua.id, user_b_id=ub.id, cross_platform=(ua.platform != ub.platform),
                        age_group=a.age_group)
            session.add(chat)
            ua.total_chats += 1
            ub.total_chats += 1
            for e in (a, b):
                await session.delete(e)
            return ua, ub, charged
    return None


async def end_chat(session, chat: Chat, reason: str, by_user_id: Optional[int] = None) -> None:
    if chat.status != "active":
        return
    chat.status = "ended" if reason in ("left", "idle_timeout", "next") else "terminated"
    chat.end_reason = reason
    chat.ended_at = utcnow()
    session.add(RecentPair(user_a_id=chat.user_a_id, user_b_id=chat.user_b_id))
    for uid in (chat.user_a_id, chat.user_b_id):
        u = await session.get(User, uid)
        if u:
            await on_first_chat_completed(session, u)
    if by_user_id:
        pass


# ------------------------------------------------------------ moderation ----
async def dictionary(session):
    res = await session.execute(select(DictionaryWord).order_by(DictionaryWord.id.desc()))
    return [(w.word, w.severity, w.action) for w in res.scalars()]


async def allowlist(session):
    res = await session.execute(select(AllowlistPhrase))
    return [p.phrase for p in res.scalars()]


async def check_message(session, text: str) -> tuple[bool, Optional[str]]:
    from . import moderation as mod
    if not text:
        return True, None
    if not mod.check_profanity(text, await dictionary(session), await allowlist(session)).ok:
        return False, "profanity"
    cfg = await get_setting(session, "moderation")
    if not mod.check_contact(text, cfg).ok:
        return False, "contact"
    return True, None


async def apply_violation(session, user: User, rule: str, snippet: str) -> dict:
    """Progressive penalty: warn -> 24h -> 7d -> 30d -> permanent."""
    cfg = await get_setting(session, "moderation")
    escalation = cfg.get("profanityEscalationHours", [24, 168, 720, 0])
    counter = user.profanity_count + (1 if rule == "profanity" else 0)
    if rule == "profanity":
        user.profanity_count = counter
    else:
        user.contact_violation_count += 1
        counter = user.contact_violation_count
    session.add(ViolationLog(user_id=user.id, rule=rule, snippet=snippet,
                             action_taken="warn" if counter == 1 else "ban"))
    if counter == 1:
        return {"action": "warn"}
    hours = escalation[min(counter - 2, len(escalation) - 1)]
    if hours == 0:
        await ban_user(session, user, "permanent", "content_filter",
                        f"تکرار نقض قوانین ({rule})", "system")
        return {"action": "perm_ban"}
    await ban_user(session, user, "temporary", "content_filter",
                   f"نقض قوانین ({rule}) — بار {counter}", "system", hours=hours)
    return {"action": "temp_ban", "hours": hours}


async def ban_user(session, user: User, type_: str, source: str, reason: str,
                   issued_by: str = "system", hours: Optional[int] = None) -> Ban:
    ban = Ban(user_id=user.id, type=type_, source=source, reason=reason, issued_by=issued_by,
              appeal_eligible=(type_ == "permanent"),
              expires_at=(utcnow() + timedelta(hours=hours)) if type_ == "temporary" and hours else None)
    session.add(ban)
    user.status = "banned"
    user.ban_count += 1
    # kick out of queue and chat
    await session.execute(delete(QueueEntry).where(QueueEntry.user_id == user.id))
    chat = await active_chat_for(session, user.id)
    if chat:
        await end_chat(session, chat, "terminated", user.id)
    await notify(session, f"مسدودسازی {user.public_id}",
                 f"کاربر {user.display_name} ({user.platform}) مسدود شد: {reason}",
                 "warning", "/bans")
    return ban


async def active_ban(session, user: User) -> Optional[Ban]:
    res = await session.execute(
        select(Ban).where(Ban.user_id == user.id, Ban.status == "active")
        .order_by(Ban.id.desc()))
    for ban in res.scalars():
        if ban.type == "permanent":
            return ban
        if ban.expires_at and ban.expires_at > utcnow():
            return ban
        ban.status = "expired"
    return None


async def create_report(session, chat: Chat, reporter: User, reported: User,
                        reason: str, details: str) -> dict:
    dup = await session.execute(select(Report).where(
        Report.chat_id == chat.id, Report.reporter_id == reporter.id,
        Report.reported_id == reported.id))
    if dup.scalar_one_or_none():
        return {"created": False, "auto_ban": False}
    session.add(Report(chat_id=chat.id, reporter_id=reporter.id, reported_id=reported.id,
                       reason=reason, details=details[:2000]))
    reported.reports_received += 1
    reporter.reports_sent += 1
    chat.has_report = True
    cfg = await get_setting(session, "moderation")
    window = utcnow() - timedelta(hours=cfg.get("autoBanWindowHours", 24))
    unique = await session.execute(
        select(func.count(func.distinct(Report.reporter_id))).where(
            Report.reported_id == reported.id, Report.created_at >= window))
    count = unique.scalar() or 0
    if count >= cfg.get("autoBanReportThreshold", 3) and reported.status == "active":
        await ban_user(session, reported, "temporary", "auto_3_reports",
                       f"{count} گزارش مستقل در ۲۴ ساعت", "system",
                       hours=cfg.get("autoBanDurationHours", 24))
        return {"created": True, "auto_ban": True}
    return {"created": True, "auto_ban": False}


# ------------------------------------------------------------ retention ----
async def purge_expired(session) -> dict:
    cfg = await get_setting(session, "privacy")
    days = cfg.get("chatRetentionDays", 30)
    cutoff = utcnow() - timedelta(days=days)
    m = (await session.execute(delete(ChatMessage).where(ChatMessage.created_at < cutoff))).rowcount
    c = (await session.execute(delete(Chat).where(Chat.status != "active", Chat.ended_at < cutoff))).rowcount
    u = (await session.execute(delete(ProcessedUpdate).where(
        ProcessedUpdate.created_at < utcnow() - timedelta(hours=48)))).rowcount
    v = (await session.execute(delete(ViolationLog).where(
        ViolationLog.created_at < cutoff))).rowcount
    d = (await session.execute(delete(RecentPair).where(
        RecentPair.ended_at < utcnow() - timedelta(days=7)))).rowcount
    # v2.4: expired web-admin sessions (30-day trusted-device lifetime)
    w = (await session.execute(delete(db.WebSession).where(
        db.WebSession.created_at < utcnow() - timedelta(days=30)))).rowcount
    # expired temporary bans
    await session.execute(update(Ban).where(
        Ban.type == "temporary", Ban.status == "active", Ban.expires_at < utcnow()).values(status="expired"))
    return {"messages": m, "chats": c, "updates": u, "violations": v, "pairs": d,
            "web_sessions": w}


async def delete_account(session, user: User) -> None:
    uid = user.id
    for tbl in (ChatMessage,):  # messages
        await session.execute(delete(tbl).where(tbl.sender_id == uid))
    for chat in (await session.execute(select(Chat).where(
            (Chat.user_a_id == uid) | (Chat.user_b_id == uid)))).scalars():
        await end_chat(session, chat, "terminated", uid)
        await session.delete(chat)
    for model in (QueueEntry, DailyClaim, CoinTransaction, Payment):
        await session.execute(delete(model).where(model.user_id == uid))
    await session.execute(delete(Block).where((Block.blocker_id == uid) | (Block.blocked_id == uid)))
    await session.execute(delete(RecentPair).where(
        (RecentPair.user_a_id == uid) | (RecentPair.user_b_id == uid)))
    await session.execute(delete(Report).where(
        (Report.reporter_id == uid) | (Report.reported_id == uid)))
    for model in (Appeal, Referral):
        col = Appeal.user_id if model is Appeal else Referral.referred_id
        await session.execute(delete(model).where(col == uid))
        if model is Referral:
            await session.execute(delete(Referral).where(Referral.referrer_id == uid))
    user.display_name = "Deleted User"
    user.username = None
    user.bio = None
    user.photo_file_id = None
    user.coin_balance = 0
    user.status = "deleted"
    user.deleted_at = utcnow()
    user.state = None


# ------------------------------------------------------------ broadcast ----
async def broadcast_targets(session, audience: str) -> list[User]:
    q = select(User).where(User.status == "active", User.bot_blocked == False)  # noqa
    if audience == "telegram":
        q = q.where(User.platform == "telegram")
    elif audience == "bale":
        q = q.where(User.platform == "bale")
    elif audience.startswith("language_"):
        q = q.where(User.language == audience.split("_", 1)[1])
    elif audience == "active_7d":
        q = q.where(User.last_seen_at >= utcnow() - timedelta(days=7))
    elif audience == "inactive_7d":
        q = q.where(User.last_seen_at < utcnow() - timedelta(days=7), User.age > 0)
    elif audience == "low_balance":
        q = q.where(User.coin_balance < 10)
    elif audience == "incomplete":
        # v2.4 segmented broadcast: users whose registration never completed
        q = q.where(User.age == 0)
    return list((await session.execute(q)).scalars())


AUDIENCE_LABELS = {
    "all": "همه کاربران (تلگرام + بله)", "telegram": "مخاطبان تلگرام", "bale": "مخاطبان بله",
    "language_fa": "کاربران فارسی‌زبان", "language_en": "کاربران انگلیسی‌زبان",
    "language_ru": "کاربران روسی‌زبان", "active_7d": "کاربران فعال ۷ روز اخیر",
    "inactive_7d": "کاربران غایب بیش از ۷ روز", "low_balance": "کاربران با موجودی کمتر از ۱۰ سکه",
    "incomplete": "ثبت‌نام‌های ناتمام",
}


# ----------------------------------------------------------------- seed ----
# v2.2 packages (item 3): 10k=20, 20k=40, 50k=100, 75k=150, 100k=200 coins
# Stars price = ceil(coins / coinsPerStar) with coinsPerStar = 20.
V22_PACKAGES = [
    ("pkg_20", 20, "۲۰ سکه", None, 1, 10_000),
    ("pkg_40", 40, "۴۰ سکه", None, 2, 20_000),
    ("pkg_100", 100, "۱۰۰ سکه", "محبوب", 5, 50_000),
    ("pkg_150", 150, "۱۵۰ سکه", "پرفروش", 8, 75_000),      # 7.5 stars -> 8
    ("pkg_200", 200, "۲۰۰ سکه", "بهترین ارزش", 10, 100_000),
]


def coins_price_toman(store_cfg: dict, coins: int) -> int:
    """Item 3: Toman price for N coins (rate kept in settings, changeable)."""
    return int(coins) * int(store_cfg.get("tomanPerCoin", 500))


def coins_price_stars(store_cfg: dict, coins: int) -> int:
    """Item 3: Telegram Stars price for N coins (Star invoices are integers)."""
    cps = max(1, int(store_cfg.get("coinsPerStar", 20)))
    import math
    return math.ceil(int(coins) / cps)


# Pricing rule (v2.2): 500 Toman = 1 coin, 1 Star = 20 coins; packages migrated
# in-place (old rows are deactivated, never deleted — payment history keeps working).
async def seed(session) -> None:
    marker = await session.get(AppSetting, "storePackagesVersion")
    if marker is None or marker.value != 2:
        v22_ids = {pid for pid, *_rest in V22_PACKAGES}
        for pid, coins, title, badge, stars, toman in V22_PACKAGES:
            p = await session.get(StorePackage, pid)
            if p is None:
                session.add(StorePackage(id=pid, coins=coins, title=title, badge=badge,
                                         telegram_stars_price=stars, bale_toman_price=toman,
                                         bale_enabled=True))
            else:
                p.coins, p.title, p.badge = coins, title, badge
                p.telegram_stars_price, p.bale_toman_price = stars, toman
                p.bale_enabled = True
        for p in (await session.execute(select(StorePackage))).scalars():
            if p.id not in v22_ids:
                p.active = False          # retire legacy packages, keep history
        await set_setting(session, "storePackagesVersion", 2)
    # v2.4: referral reward raised 20 -> 50 per party — only when the stored
    # value still equals the OLD default (admin-customized values are kept).
    eco_ver = await session.get(AppSetting, "economyDefaultsVersion")
    if eco_ver is None or eco_ver.value != 2:
        eco = await get_setting(session, "economy")
        if eco.get("referralRewardPerParty", 50) == 20:
            eco["referralRewardPerParty"] = 50
            eco["updatedBy"] = "seed:v2.4"
            eco["updatedAt"] = utcnow().isoformat()
            await set_setting(session, "economy", eco)
        await set_setting(session, "economyDefaultsVersion", 2)
    medias = await session.execute(select(MediaType))
    if list(medias.scalars()) == []:
        session.add_all([MediaType(key=k, enabled=True) for k in MEDIA_KEYS])
    for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
        if await session.get(RolePermission, role) is None:
            session.add(RolePermission(role=role, permissions=perms))
    words = await session.execute(select(DictionaryWord).limit(1))
    if words.scalar_one_or_none() is None:
        from .moderation import DEFAULT_DICTIONARY
        for w, sev, act in DEFAULT_DICTIONARY:
            session.add(DictionaryWord(word=w, severity=sev, action=act, added_by="system"))
    for phrase in ("خودم", "خواهر", "امام", "اسلام"):
        if not (await session.execute(select(AllowlistPhrase).where(
                AllowlistPhrase.phrase == phrase))).scalar_one_or_none():
            session.add(AllowlistPhrase(phrase=phrase, note="جلوگیری از مثبت کاذب", added_by="system"))
