"""User-facing bot logic (shared by Telegram and Bale) — v2."""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import delete, select

from . import db, service
from .config import RUNTIME, settings
from .db import (Chat, ChatMessage, MediaType, Payment, QueueEntry, StorePackage,
                 User, session_scope, utcnow)
from .i18n import (GENDER_LABEL, GREGORIAN_MONTHS, JALALI_MONTHS, LANG_NAMES,
                   PLATFORM_LABEL, tr)
from .platform import CROSS_OK, PlatformError, UnsupportedMethod

log = logging.getLogger("kanar.bot")

NAME_AGE_CHANGE_COST = 10  # coins, configurable via economy settings


def kb(*rows):
    return [list(r) for r in rows]


def btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def back_kb(lang: str, extra_rows=None):
    """Every submenu ends with a Back-to-home button."""
    rows = list(extra_rows or [])
    rows.append([btn(tr(lang, "btn_back"), "back_home")])
    return rows


def adapter(platform: str):
    return RUNTIME.get(f"adapter_{platform}")


async def send(user: User, key: str, keyboard=None, reply_to=None, **kw) -> None:
    adp = adapter(user.platform)
    if not adp:
        return
    try:
        await adp.send_message(user.platform_user_id, tr(user.language, key, **kw),
                               keyboard=keyboard, reply_to=reply_to)
    except PlatformError as exc:
        log.warning("send failed to %s/%s: %s", user.platform, user.public_id, exc)


def search_cta(lang: str):
    """Inline CTA attached to 'not in chat' style messages."""
    return kb([btn(tr(lang, "btn_search"), "search")])


async def main_menu(user: User) -> None:
    lang = user.language
    await send(user, "main_menu", keyboard=kb(
        [btn(tr(lang, "btn_search"), "search"),
         btn(tr(lang, "btn_profile"), "profile")],
        [btn(tr(lang, "btn_coins"), "coins"),
         btn(tr(lang, "btn_store"), "store")],
        [btn(tr(lang, "btn_bonus"), "bonus"),
         btn(tr(lang, "btn_myref"), "referral")],
        [btn(tr(lang, "btn_language"), "language"),
         btn(tr(lang, "btn_help"), "help")],
        [btn(tr(lang, "btn_admin"), "admin_open")] if await _is_admin(user) else []))


async def _is_admin(user: User) -> bool:
    if user.platform == "telegram" and user.platform_user_id.isdigit() and \
            int(user.platform_user_id) in settings.owner_telegram_ids:
        return True
    if user.platform == "bale" and user.platform_user_id.isdigit() and \
            int(user.platform_user_id) in settings.owner_bale_ids:
        return True
    from .db import BotAdmin
    async with session_scope() as s:
        r = await s.execute(select(BotAdmin).where(
            BotAdmin.platform == user.platform,
            BotAdmin.platform_user_id == user.platform_user_id))
        return r.scalar_one_or_none() is not None


def chat_controls(lang: str):
    return kb([btn(tr(lang, "btn_next"), "chat_next"), btn(tr(lang, "btn_end"), "chat_end")],
              [btn(tr(lang, "btn_report"), "chat_report"), btn(tr(lang, "btn_block"), "chat_block")],
              [btn(tr(lang, "btn_gift"), "chat_gift")])


async def partner_of(session, chat: Chat, user: User) -> Optional[User]:
    other = chat.user_b_id if chat.user_a_id == user.id else chat.user_a_id
    return await session.get(User, other)


def deep_link(user: User, param: str) -> str:
    if user.platform == "telegram":
        return f"https://t.me/{RUNTIME.get('tg_username', 'kanarchatbot')}?start={param}"
    return f"https://ble.ir/{RUNTIME.get('bale_username', 'kchatbot')}?start={param}"


# ------------------------------------------------------------ registration ----
async def cmd_start(user: User, payload: str = "") -> None:
    async with session_scope() as s:
        u = await s.get(User, user.id)
        if u.status == "deleted":
            u.status = "active"
            u.deleted_at = None
        if u.age == 0 or not u.accepted_rules_at:
            # referral capture
            if payload.startswith("ref_"):
                r = await s.execute(select(User).where(User.referral_code == payload[4:]))
                ref = r.scalar_one_or_none()
                if ref and ref.id != u.id and ref.platform == u.platform:
                    from .db import Referral
                    existing = await s.execute(select(Referral).where(
                        Referral.referrer_id == ref.id, Referral.referred_id == u.id))
                    if not existing.scalar_one_or_none():
                        s.add(Referral(code=payload[4:], referrer_id=ref.id, referred_id=u.id))
                        u.referred_by_id = ref.id
            # anonymous-link message capture (item 4)
            if payload.startswith("msg_"):
                target = await service.find_by_anon_code(s, payload[4:])
                if target and target.id != u.id and target.status == "active":
                    u.state = {"step": "anon_msg", "target": target.id}
                    await send(u, "anon_msg_intro")
                    return
                # invalid/expired link or owner self
                u.state = {"step": "language"}
                await send(u, "welcome", keyboard=kb(
                    [btn(LANG_NAMES["fa"], "lang_fa"), btn(LANG_NAMES["en"], "lang_en"),
                     btn(LANG_NAMES["ru"], "lang_ru")]))
                return
            # account-link confirmation (item 5)
            if payload.startswith("lnk_"):
                r = await s.execute(select(User).where(User.link_code == payload[4:]))
                initiator = r.scalar_one_or_none()
                if initiator and initiator.id != u.id and initiator.platform != u.platform:
                    eco = await service.get_setting(s, "economy")
                    await send(u, "link_confirm_q",
                               platform=PLATFORM_LABEL[initiator.platform][u.language],
                               coins=eco.get("accountLinkReward", 50),
                               keyboard=kb([btn(tr(u.language, "link_confirm_btn"),
                                                f"linkok_{payload[4:]}")]))
                    return
                if initiator and initiator.platform == u.platform:
                    await send(u, "link_same_platform")
                    return
                await send(u, "link_expired")
                await main_menu(u)
                return
            u.state = {"step": "language"}
            await send(u, "welcome", keyboard=kb(
                [btn(LANG_NAMES["fa"], "lang_fa"), btn(LANG_NAMES["en"], "lang_en"),
                 btn(LANG_NAMES["ru"], "lang_ru")]))
            return
        b = await service.active_ban(s, u)
        if b:
            await _notify_ban(u, b)
            return
    await main_menu(user)


async def _notify_ban(user: User, ban) -> None:
    if ban.type == "permanent":
        await send(user, "banned_perm", reason=ban.reason)
    else:
        until = ban.expires_at.strftime("%Y-%m-%d %H:%M UTC") if ban.expires_at else "—"
        await send(user, "banned_until", reason=ban.reason, until=until)


async def begin_registration(user: User) -> None:
    user.state = {"step": "rules"}
    await send(user, "rules_title")
    await send(user, "rules_body")
    await send(user, "privacy_title")
    await send(user, "privacy_body")
    await send(user, "accept_rules", keyboard=kb([btn(tr(user.language, "accept"), "accept_rules")]))


# ------------------------------------------------------------- commands ----
async def handle_command(user: User, command: str, args: str) -> None:
    cmd = command.split("@")[0].lower()
    lang = user.language
    if cmd in ("/start",):
        await cmd_start(user, args.strip())
    elif cmd == "/help":
        await send(user, "help_body", keyboard=back_kb(lang))
    elif cmd == "/search":
        await start_search(user)
    elif cmd == "/next":
        await do_next(user)
    elif cmd == "/stop":
        await do_stop(user)
    elif cmd == "/profile":
        await show_profile(user)
    elif cmd == "/coins":
        await send(user, "coins_balance", coins=user.coin_balance, keyboard=back_kb(lang))
    elif cmd == "/store":
        await show_store(user)
    elif cmd == "/language":
        user.state = {"step": "language"}
        await send(user, "choose_lang", keyboard=kb(
            [btn(LANG_NAMES["fa"], "lang_fa"), btn(LANG_NAMES["en"], "lang_en"),
             btn(LANG_NAMES["ru"], "lang_ru")], [btn(tr(lang, "btn_back"), "back_home")]))
    elif cmd == "/rules":
        await send(user, "rules_title"); await send(user, "rules_body", keyboard=back_kb(lang))
    elif cmd == "/privacy":
        await send(user, "privacy_title"); await send(user, "privacy_body", keyboard=back_kb(lang))
    elif cmd == "/paysupport":
        await send(user, "paysupport", keyboard=kb(
            [btn(tr(lang, "pay_support_btn"), "paysupport_ticket")],
            [btn(tr(lang, "btn_back"), "back_home")]))
    elif cmd == "/bonus":
        async with session_scope() as s:
            u = await s.get(User, user.id)
            got = await service.grant_daily_bonus(s, u)
            if got:
                await send(u, "bonus_ok", coins=got, keyboard=back_kb(u.language))
            else:
                await send(u, "bonus_already", keyboard=back_kb(u.language))
            user.coin_balance = u.coin_balance
    elif cmd == "/referral":
        await show_referral(user)
    elif cmd == "/link":
        await show_anon_link(user)
    elif cmd == "/msg":
        user.state = {"step": "direct_msg_target"}
        await send(user, "direct_msg_intro", keyboard=back_kb(lang))
    elif cmd == "/gift":
        await start_gift(user)
    elif cmd == "/report":
        await do_report(user, args)
    elif cmd == "/block":
        await do_block(user)
    elif cmd == "/appeal":
        async with session_scope() as s:
            u = await s.get(User, user.id)
            b = await service.active_ban(s, u)
            if not b or b.type != "permanent":
                await send(u, "no_active_ban")
            else:
                u.state = {"step": "appeal", "ban_id": b.id}
                await send(u, "ask_appeal")
    elif cmd == "/support":
        async with session_scope() as s:
            from .db import SupportMessage, SupportTicket
            t = SupportTicket(user_id=user.id, subject=args[:60] or "پیام پشتیبانی", category="other")
            s.add(t)
            await s.flush()
            t.ticket_no = f"TKT-{t.id:05d}"
            s.add(SupportMessage(ticket_id=t.id, sender_type="user", sender_name=user.display_name,
                                 body=args or "—"))
            await send(user, "support_sent", ticket=t.ticket_no)
    elif cmd == "/admin":
        from .bot_admin import admin_entry
        await admin_entry(user)
    else:
        await main_menu(user)


# --------------------------------------------------------- anon link (4) ----
async def show_anon_link(user: User) -> None:
    async with session_scope() as s:
        u = await s.get(User, user.id)
        code = await service.ensure_anon_link(s, u)
        link = deep_link(u, f"msg_{code}")
    await send(user, "anon_link_info", link=link, keyboard=back_kb(user.language))


# -------------------------------------------------------- direct msg (14) ----
async def direct_message_flow(user: User, target_text: str) -> None:
    """Resolve target by @username or numeric ID, then ask for the message text."""
    target_text = target_text.strip()
    if target_text.startswith("@"):
        uname = target_text[1:]
        async with session_scope() as s:
            r = await s.execute(select(User).where(User.username == uname))
            target = r.scalar_one_or_none()
    elif target_text.isdigit():
        async with session_scope() as s:
            r = await s.execute(select(User).where(
                (User.platform_user_id == target_text) | (User.public_id == target_text.upper())))
            target = r.scalar_one_or_none()
    else:
        target = None
    async with session_scope() as s:
        u = await s.get(User, user.id)
        u.state = {"step": "direct_msg_text", "target": target.id if target else None}
    # deliberately identical prompt whether or not the target exists (mystery!)
    await send(user, "direct_msg_intro_done" if False else "ask_msg_text",
               keyboard=back_kb(user.language))


async def deliver_direct_message(sender: User, target_id: Optional[int], text: str) -> None:
    """Deliver anonymously; NEVER reveal the outcome to the sender (item 14)."""
    from .moderation import sanitize_snippet
    async with session_scope() as s:
        u = await s.get(User, sender.id)
        u.state = None
        if target_id:
            target = await s.get(User, target_id)
            if target and target.status == "active":
                adp = adapter(target.platform)
                if adp:
                    try:
                        await adp.send_message(
                            target.platform_user_id,
                            tr(target.language, "anon_msg_received", text=text[:3000]))
                    except PlatformError as exc:
                        log.info("direct message delivery failed (by design, hidden): %s", exc)
    # neutral response — no delivery status revealed
    await send(sender, "direct_msg_neutral")


# ------------------------------------------------------ account linking ----
async def start_link_flow(user: User) -> None:
    async with session_scope() as s:
        u = await s.get(User, user.id)
        if u.linked_user_id:
            await send(u, "link_already")
            return
        eco = await service.get_setting(s, "economy")
        reward = eco.get("accountLinkReward", 50)
        await send(u, "link_intro", coins=reward, total=reward * 2,
                   keyboard=kb([btn(tr(u.language, "link_gen"), "link_gen")],
                               [btn(tr(u.language, "btn_back"), "back_home")]))


async def generate_link(user: User) -> None:
    async with session_scope() as s:
        u = await s.get(User, user.id)
        code = await service.start_account_link(s, u)
        eco = await service.get_setting(s, "economy")
        link = deep_link(u, f"lnk_{code}")
        await send(u, "link_show", link=link, coins=eco.get("accountLinkReward", 50),
                   keyboard=back_kb(u.language))


# -------------------------------------------------------------- gifts (18) ----
GIFT_COINS = [100, 300, 700, 1500]


async def start_gift(user: User) -> None:
    async with session_scope() as s:
        chat = await service.active_chat_for(s, user.id)
        if not chat:
            await send(user, "not_in_chat", keyboard=search_cta(user.language))
            return
    rows = []
    for coins in GIFT_COINS:
        stars = coins // 20          # 100 coins = 5 stars
        toman = coins * 500          # 100 coins = 50,000 Toman
        label = tr(user.language, "pay_stars", stars=stars, coins=coins) \
            if user.platform == "telegram" else tr(user.language, "pay_bale", toman=toman, coins=coins)
        rows.append([btn("🎁 " + label, f"gift_{coins}")])
    rows.append([btn(tr(user.language, "btn_back"), "chat_back")])
    await send(user, "gift_title", price=("۵ استارز" if user.platform == "telegram" else "۵۰٫۰۰۰ تومان"),
               coins=100, keyboard=rows)


async def send_gift_invoice(user: User, coins: int) -> None:
    adp = adapter(user.platform)
    if not adp:
        return
    async with session_scope() as s:
        chat = await service.active_chat_for(s, user.id)
        if not chat:
            await send(user, "not_in_chat", keyboard=search_cta(user.language))
            return
        partner = await partner_of(s, chat, user)
        payload = f"gift:{coins}:{user.id}:{partner.id}"
        try:
            if user.platform == "telegram":
                await adp.send_invoice(user.platform_user_id, f"هدیه {coins} سکه",
                                       "اهدای سکه به هم‌چت ناشناس", payload,
                                       [{"label": f"gift {coins}", "amount": coins // 20}])
            else:
                if not settings.bale_provider_token:
                    await send(user, "pay_unavailable")
                    return
                await adp.send_invoice(user.platform_user_id, f"هدیه {coins} سکه",
                                       "اهدای سکه به هم‌چت ناشناس", payload,
                                       [{"label": f"gift {coins}", "amount": coins * 500 * 10}])
        except UnsupportedMethod:
            await send(user, "pay_unavailable")
        except PlatformError as exc:
            log.warning("gift invoice failed: %s", exc)


# ------------------------------------------------------------- searching ----
async def start_search(user: User) -> None:
    lang = user.language
    async with session_scope() as s:
        u = await s.get(User, user.id)
        b = await service.active_ban(s, u)
        if b:
            await _notify_ban(u, b)
            return
        if await service.active_chat_for(s, u.id):
            await do_next(u)
            return
    eco = None
    async with session_scope() as s:
        eco = await service.get_setting(s, "economy")
    if not (await get_app()).get("enableGenderFilter", True):
        await _enqueue(user, "everyone")
        return
    await send(user, "gender_filter_q", cost=eco["genderFilterMatchCost"], keyboard=kb(
        [btn(tr(lang, "gf_everyone"), "gf_everyone")],
        [btn(tr(lang, "gf_girls"), "gf_female"), btn(tr(lang, "gf_boys"), "gf_male")],
        [btn(tr(lang, "btn_back"), "back_home")]))


async def get_app() -> dict:
    async with session_scope() as s:
        return await service.get_setting(s, "app")


async def _enqueue(user: User, gender_filter: str) -> None:
    async with session_scope() as s:
        u = await s.get(User, user.id)
        if not await service.enqueue(s, u, gender_filter):
            return
    await send(user, "searching", keyboard=kb(
        [btn(tr(user.language, "btn_stop_search"), "stop_search")]))
    await run_match_cycle()
    # item 15: invite recently-active users to fill the queue
    await invite_recently_active(user)


async def invite_recently_active(seeker: User) -> None:
    """If the queue is short, invite users active within the last 30 minutes (item 15)."""
    from datetime import timedelta
    try:
        async with session_scope() as s:
            in_queue = (await s.execute(select(func_count(QueueEntry.id)))).scalar() or 0
            if in_queue >= 2:
                return
            cutoff = utcnow() - timedelta(minutes=30)
            # users busy in an active chat (either side)
            busy = {r[0] for r in (await s.execute(
                select(Chat.user_a_id).where(Chat.status == "active"))).all()}
            busy |= {r[0] for r in (await s.execute(
                select(Chat.user_b_id).where(Chat.status == "active"))).all()}
            candidates = (await s.execute(
                select(User).where(
                    User.status == "active", User.last_seen_at >= cutoff,
                    User.id != seeker.id,
                    (User.last_invite_at.is_(None)) | (User.last_invite_at < cutoff))
                .limit(10))).scalars()
            for cand in candidates:
                # same age pool only, not busy in a chat, not in queue
                if service.age_group(cand.age) != service.age_group(seeker.age):
                    continue
                if cand.id in busy:
                    continue
                in_q = (await s.execute(select(QueueEntry).where(
                    QueueEntry.user_id == cand.id))).scalar_one_or_none()
                if in_q:
                    continue
                cand.last_invite_at = utcnow()
                await send(cand, "invite_msg", keyboard=kb(
                    [btn(tr(cand.language, "invite_btn"), "quick_connect")]))
    except Exception:
        log.exception("invite_recently_active failed")


def func_count(col):
    from sqlalchemy import func
    return func.count(col)


async def run_match_cycle() -> None:
    """Try to pair queued users; notify both on success."""
    while True:
        async with session_scope() as s:
            matched = await service.try_match(s)
            if not matched:
                return
            ua, ub, _charged = matched
            for me, other in ((ua, ub), (ub, ua)):
                gender = GENDER_LABEL[other.gender][me.language]
                bio = other.bio or ""
                card = tr(me.language, "matched", name=other.display_name, age=other.age,
                          gender=gender, bio=bio)
                adp = adapter(me.platform)
                if adp is None:
                    continue
                try:
                    if other.photo_file_id and other.platform == me.platform:
                        await adp.send_media(me.platform_user_id, "photo", other.photo_file_id,
                                             caption=card)
                        await adp.send_message(me.platform_user_id, tr(me.language, "chat_controls_hint"),
                                               keyboard=chat_controls(me.language))
                    else:
                        if other.platform != me.platform:
                            card += "\n" + tr(me.language, "default_avatar")
                        await adp.send_message(me.platform_user_id, card,
                                               keyboard=chat_controls(me.language))
                except PlatformError as exc:
                    log.warning("match notify failed: %s", exc)


async def do_next(user: User) -> None:
    async with session_scope() as s:
        u = await s.get(User, user.id)
        chat = await service.active_chat_for(s, u.id)
        if chat:
            partner = await partner_of(s, chat, u)
            await service.end_chat(s, chat, "next", u.id)
            if partner:
                await send(partner, "partner_left", keyboard=search_cta(partner.language))
        if not (await s.execute(select(QueueEntry).where(QueueEntry.user_id == u.id))).scalar_one_or_none():
            s.add(QueueEntry(user_id=u.id, age_group=service.age_group(u.age),
                             gender=u.gender, gender_filter="everyone"))
    await send(user, "searching", keyboard=kb(
        [btn(tr(user.language, "btn_stop_search"), "stop_search")]))
    await run_match_cycle()
    await invite_recently_active(user)


async def do_stop(user: User) -> None:
    async with session_scope() as s:
        u = await s.get(User, user.id)
        removed = (await s.execute(
            delete(QueueEntry).where(QueueEntry.user_id == u.id))).rowcount
        chat = await service.active_chat_for(s, u.id)
        if chat:
            partner = await partner_of(s, chat, u)
            await service.end_chat(s, chat, "left", u.id)
            if partner:
                await send(partner, "partner_left", keyboard=search_cta(partner.language))
    if removed:
        await send(user, "search_cancel")
    else:
        await send(user, "chat_ended")
    await main_menu(user)


async def do_report(user: User, args: str = "") -> None:
    async with session_scope() as s:
        u = await s.get(User, user.id)
        chat = await service.active_chat_for(s, u.id)
        if not chat:
            await send(u, "not_in_chat", keyboard=search_cta(u.language))
            return
        partner = await partner_of(s, chat, u)
        res = await service.create_report(s, chat, u, partner, "harassment", args or "—")
        if not res["created"]:
            await send(u, "report_dup", keyboard=chat_controls(u.language))
            return
    await send(user, "report_done", keyboard=chat_controls(user.language))


async def do_block(user: User) -> None:
    async with session_scope() as s:
        u = await s.get(User, user.id)
        chat = await service.active_chat_for(s, u.id)
        if not chat:
            await send(u, "not_in_chat", keyboard=search_cta(u.language))
            return
        partner = await partner_of(s, chat, u)
        from .db import Block
        s.add(Block(blocker_id=u.id, blocked_id=partner.id))
        await service.end_chat(s, chat, "left", u.id)
        await send(partner, "blocked_by_partner", keyboard=search_cta(partner.language))
    await send(user, "block_done")
    await main_menu(user)


# ------------------------------------------------------------- profile ----
async def show_profile(user: User) -> None:
    lang = user.language
    gender = GENDER_LABEL[user.gender][user.gender and user.gender or "male"][lang] \
        if user.gender in GENDER_LABEL else GENDER_LABEL["male"][lang]
    if user.birth_year:
        if user.birth_calendar == "jalali":
            month_name = JALALI_MONTHS[(user.birth_month or 1) - 1]
            cal = "شمسی"
        else:
            month_name = GREGORIAN_MONTHS["fa"][(user.birth_month or 1) - 1]
            cal = "میلادی"
        bday = tr(lang, "birthday_set", date=f"{user.birth_day} {month_name} ({cal})")
    else:
        bday = tr(lang, "birthday_not_set")
    linked = ""
    async with session_scope() as s:
        eco = await service.get_setting(s, "economy")
        if user.linked_user_id:
            other = await s.get(User, user.linked_user_id)
            if other:
                linked = f"\n🔗 متصل به: حساب {PLATFORM_LABEL[other.platform][lang]}"
    rows = [
        [btn(tr(lang, "btn_edit_name"), "edit_name"),
         btn(tr(lang, "btn_edit_age"), "edit_age")],
        [btn(tr(lang, "btn_edit_bio"), "edit_bio"),
         btn(tr(lang, "btn_edit_photo"), "edit_photo")],
        [btn(tr(lang, "btn_birthday"), "set_birthday")],
        [btn(tr(lang, "btn_anon_link"), "anon_link")],
        [btn(tr(lang, "btn_direct_msg"), "direct_msg")],
        [btn(tr(lang, "btn_link_account"), "link_account")],
    ]
    if not user.linked_user_id:
        pass  # linking button always shown; flow reports if already linked
    await send(user, "profile_card", name=user.display_name, age=user.age,
               gender=gender, bio=user.bio or "—", coins=user.coin_balance,
               birthday=bday + linked, keyboard=back_kb(lang, rows))
    if user.photo_file_id:
        adp = adapter(user.platform)
        if adp:
            try:
                await adp.send_media(user.platform_user_id, "photo", user.photo_file_id)
            except PlatformError:
                pass


# ---------------------------------------------------------- birthday (17) ----
def _months_for(lang: str, calendar: str):
    if calendar == "jalali":
        return JALALI_MONTHS
    return GREGORIAN_MONTHS.get(lang) or GREGORIAN_MONTHS["en"]


async def start_birthday(user: User) -> None:
    calendar = "jalali" if user.language == "fa" else "gregorian"
    async with session_scope() as s:
        eco = await service.get_setting(s, "economy")
        u = await s.get(User, user.id)
        u.state = {"step": "birthday_month", "calendar": calendar}
        reward = eco.get("birthdayReward", 10)
    months = _months_for(user.language, calendar)
    rows = [[btn(months[i], f"bmon_{i + 1}")] for i in range(12)]
    rows.append([btn(tr(user.language, "btn_back"), "back_home")])
    await send(user, "birthday_intro", coins=reward, keyboard=rows)


async def birthday_month_chosen(user: User, month: int) -> None:
    state = user.state or {}
    calendar = state.get("calendar", "jalali")
    maxday = 31 if calendar == "gregorian" else service.JALALI_MONTH_DAYS[month - 1]
    async with session_scope() as s:
        u = await s.get(User, user.id)
        u.state = {"step": "birthday_day", "calendar": calendar, "month": month, "maxday": maxday}
    months = _months_for(user.language, calendar)
    await send(user, "birthday_ask_day", month=months[month - 1], maxday=maxday,
               keyboard=back_kb(user.language))


async def birthday_day_entered(user: User, day: int) -> None:
    state = user.state or {}
    calendar = state.get("calendar", "jalali")
    month = state.get("month", 1)
    maxday = state.get("maxday", 31)
    if not (1 <= day <= maxday):
        await send(user, "invalid_input")
        return
    import datetime as _dt
    today = _dt.date.today()
    if calendar == "jalali":
        jy, jm, jd = service.gregorian_to_jalali(today.year, today.month, today.day)
        if month == 12 and day == 30:  # esfand 30 only in leap years — clamp
            day = 29
        gy, gm, gd = service.jalali_to_gregorian(jy - (user.age or 15), month, day)
    else:
        gy = today.year - (user.age or 15)
        gm, gd = month, day
    # derive exact birth year from current age and current date
    age = user.age or 15
    birth_year = today.year - age
    if (today.month, today.day) < (gm, gd):
        birth_year -= 0  # birthday not passed yet this year -> age correct as-is
    # verify computed age stays within 13-60
    new_age = service.compute_age_from_birthday(birth_year, gm, gd)
    if not (13 <= new_age <= 60):
        new_age = max(13, min(60, new_age))
        birth_year = today.year - new_age
    async with session_scope() as s:
        u = await s.get(User, user.id)
        u.birth_year, u.birth_month, u.birth_day = birth_year, gm, gd
        u.birth_calendar = calendar
        u.age = new_age
        eco = await service.get_setting(s, "economy")
        reward = eco.get("birthdayReward", 10)
        if not u.birthday_rewarded:
            u.birthday_rewarded = True
            await service.credit(s, u, reward, "birthday_reward", "پاداش ثبت تاریخ تولد",
                                 idempotency_key=f"bday:{u.id}")
        else:
            reward = 0
    await send(user, "birthday_saved", coins=reward or eco.get("birthdayReward", 10))
    await show_profile(user)


# --------------------------------------------------------- name/age edit ----
async def start_edit_name(user: User) -> None:
    async with session_scope() as s:
        eco = await service.get_setting(s, "economy")
        u = await s.get(User, user.id)
        u.state = {"step": "edit_name"}
        cost = eco.get("nameChangeCost", NAME_AGE_CHANGE_COST)
    await send(user, "ask_new_name", cost=cost, keyboard=back_kb(user.language))


async def start_edit_age(user: User) -> None:
    async with session_scope() as s:
        eco = await service.get_setting(s, "economy")
        u = await s.get(User, user.id)
        u.state = {"step": "edit_age"}
        cost = eco.get("ageChangeCost", NAME_AGE_CHANGE_COST)
    await send(user, "ask_new_age", cost=cost, keyboard=back_kb(user.language))


async def apply_edit_name(user: User, name: str) -> None:
    if not (3 <= len(name.strip()) <= 20):
        await send(user, "invalid_input")
        return
    async with session_scope() as s:
        u = await s.get(User, user.id)
        eco = await service.get_setting(s, "economy")
        cost = eco.get("nameChangeCost", NAME_AGE_CHANGE_COST)
        txn = await service.debit(s, u, cost, "name_change", "هزینه تغییر نام",
                                   idempotency_key=f"namechg:{u.id}:{u.display_name}")
        if txn is None:
            await send(u, "no_coins", keyboard=back_kb(u.language))
            return
        u.display_name = name.strip()
        u.state = None
    await send(user, "name_age_changed", cost=cost)
    await show_profile(user)


async def apply_edit_age(user: User, age: int) -> None:
    if not (13 <= age <= 60):
        await send(user, "invalid_input")
        return
    async with session_scope() as s:
        u = await s.get(User, user.id)
        eco = await service.get_setting(s, "economy")
        cost = eco.get("ageChangeCost", NAME_AGE_CHANGE_COST)
        old = u.age
        txn = await service.debit(s, u, cost, "age_change", "هزینه تغییر سن",
                                   idempotency_key=f"agechg:{u.id}:{old}")
        if txn is None:
            await send(u, "no_coins", keyboard=back_kb(u.language))
            return
        u.age = age
        # age-group change -> re-queue into the correct pool
        if service.age_group(age) != service.age_group(old):
            await s.execute(delete(QueueEntry).where(QueueEntry.user_id == u.id))
        u.state = None
    await send(user, "name_age_changed", cost=cost)
    await show_profile(user)


async def show_referral(user: User) -> None:
    async with session_scope() as s:
        eco = await service.get_setting(s, "economy")
    link = deep_link(user, f"ref_{user.referral_code}")
    await send(user, "referral_info", link=link, coins=eco["referralRewardPerParty"])
    await send(user, "referral_count", count=user.referral_count, keyboard=back_kb(user.language))


async def show_store(user: User) -> None:
    rows = []
    async with session_scope() as s:
        pkgs = (await s.execute(select(StorePackage).where(StorePackage.active == True))).scalars()  # noqa
        app_cfg = await service.get_setting(s, "app")
        pay_tg = app_cfg.get("paymentsTelegramEnabled", True) and bool(settings.telegram_bot_token)
        pay_bale = app_cfg.get("paymentsBaleEnabled", False) and bool(settings.bale_provider_token)
    for p in pkgs:
        if user.platform == "telegram" and p.telegram_enabled and pay_tg:
            rows.append([btn(tr(user.language, "pay_stars", stars=p.telegram_stars_price,
                                coins=p.coins), f"buy_{p.id}")])
        elif user.platform == "bale" and p.bale_enabled and pay_bale:
            rows.append([btn(tr(user.language, "pay_bale", toman=p.bale_toman_price,
                                coins=p.coins), f"buy_{p.id}")])
    if not rows:
        await send(user, "pay_unavailable", keyboard=back_kb(user.language))
        return
    rows.append([btn(tr(user.language, "btn_back"), "back_home")])
    await send(user, "store_title", keyboard=rows)


# --------------------------------------------------------------- payments ----
async def start_purchase(user: User, package_id: str) -> None:
    adp = adapter(user.platform)
    if not adp:
        return
    async with session_scope() as s:
        pkg = await s.get(StorePackage, package_id)
        u = await s.get(User, user.id)
        if not pkg:
            return
        payload = f"pkg:{pkg.id}:{u.id}"
        try:
            if u.platform == "telegram":
                await adp.send_invoice(u.platform_user_id, f"{pkg.coins} سکه کانار",
                                       "خرید سکه برای چت ناشناس کانار", payload,
                                       [{"label": f"{pkg.coins} coins", "amount": pkg.telegram_stars_price}])
            else:
                await adp.send_invoice(u.platform_user_id, f"{pkg.coins} سکه کانار",
                                       "خرید سکه برای چت ناشناس کانار", payload,
                                       [{"label": f"{pkg.coins} coins", "amount": pkg.bale_toman_price * 10}])
        except UnsupportedMethod as exc:
            await send(u, "pay_unavailable")
            log.info("invoice unavailable: %s", exc)
        except PlatformError as exc:
            log.warning("invoice failed: %s", exc)


async def handle_pre_checkout(platform: str, query: dict) -> None:
    adp = adapter(platform)
    if not adp:
        return
    ok = bool(query.get("invoice_payload"))
    await adp.call("answerPreCheckoutQuery", {
        "pre_checkout_query_id": query["id"], "ok": ok,
        **({} if ok else {"error_message": "خطا در پردازش سفارش؛ لطفاً دوباره تلاش کنید."})})


async def handle_successful_payment(platform: str, msg: dict) -> None:
    sp = msg.get("successful_payment") or {}
    payload = sp.get("invoice_payload", "")
    charge = sp.get("telegram_payment_charge_id") or sp.get("provider_payment_charge_id") or ""
    if not charge:
        return
    if payload.startswith("gift:"):
        # gift flow (item 18): payer pays, PARTNER receives the coins
        _, coins, payer_id, partner_id = payload.split(":", 3)
        coins, payer_id, partner_id = int(coins), int(payer_id), int(partner_id)
        async with session_scope() as s:
            existing = await s.execute(select(Payment).where(
                Payment.telegram_payment_charge_id == charge))
            if existing.scalar_one_or_none():
                return
            payer = await s.get(User, payer_id)
            partner = await s.get(User, partner_id)
            if not payer or not partner:
                return
            s.add(Payment(platform=platform, user_id=payer.id, package_id=f"gift_{coins}",
                          coins=0, amount=sp.get("total_amount", 0),
                          currency=sp.get("currency", "XTR"), status="succeeded",
                          telegram_payment_charge_id=charge,
                          provider_payment_charge_id=sp.get("provider_payment_charge_id"),
                          invoice_payload=payload))
            await service.credit(s, partner, coins, "gift_received",
                                 f"هدیه از هم‌چت ناشناس",
                                 reference_id=charge, idempotency_key=f"pay:{charge}")
            await send(payer, "gift_sent_payer", coins=coins)
            await send(partner, "gift_received", coins=coins)
        return
    if not payload.startswith("pkg:"):
        return
    _, pkg_id, user_row_id = payload.split(":", 2)
    async with session_scope() as s:
        existing = await s.execute(select(Payment).where(
            Payment.telegram_payment_charge_id == charge))
        if existing.scalar_one_or_none():
            return
        user = await s.get(User, int(user_row_id))
        pkg = await s.get(StorePackage, pkg_id)
        if not user or not pkg:
            return
        s.add(Payment(platform=platform, user_id=user.id, package_id=pkg.id, coins=pkg.coins,
                      amount=sp.get("total_amount", 0), currency=sp.get("currency", "XTR"),
                      status="succeeded", telegram_payment_charge_id=charge,
                      provider_payment_charge_id=sp.get("provider_payment_charge_id"),
                      invoice_payload=payload))
        await service.credit(s, user, pkg.coins, "store_purchase",
                             f"خرید بسته {pkg.coins} سکه",
                             reference_id=charge, idempotency_key=f"pay:{charge}")
        await send(user, "pay_ok", coins=pkg.coins)


# ------------------------------------------------------------ text/media ----
async def handle_message(platform: str, msg: dict) -> None:
    adp = adapter(platform)
    from_u = msg.get("from", {})
    text = msg.get("text") or msg.get("caption") or ""
    if not from_u.get("id"):
        return
    async with session_scope() as s:
        res = await s.execute(select(User).where(
            User.platform == platform, User.platform_user_id == str(from_u["id"])))
        user = res.scalar_one_or_none()
        if user is None:
            user = User(platform=platform, platform_user_id=str(from_u["id"]),
                        display_name=(from_u.get("first_name") or "User")[:20], age=0,
                        gender="male", username=from_u.get("username"))
            s.add(user)
            await s.flush()
        user.last_seen_at = utcnow()
        # keep username fresh for /msg lookups
        if from_u.get("username") and from_u["username"] != user.username:
            user.username = from_u["username"]

        # --- registration FSM ---
        state = user.state or {}
        step = state.get("step")
        if text.startswith("/"):
            await s.commit()
            cmd, _, args = text.partition(" ")
            await handle_command(user, cmd, args.strip())
            return
        # --- admin chat flows ---
        if step and str(step).startswith("adm"):
            from .bot_admin import admin_text_flow
            if await admin_text_flow(user, text):
                return
        # --- owner uploads backup file during restore wait-state ---
        doc = msg.get("document")
        if doc:
            from .bot_admin import RESTORE_WAIT, admin_document
            if str(user.platform_user_id) in RESTORE_WAIT:
                try:
                    data = await adp.download_file(doc["file_id"], 200 * 1024 * 1024)
                    if await admin_document(user, data):
                        return
                except Exception:
                    log.exception("restore upload download failed")
        # --- profile photo upload step ---
        if step == "photo":
            if msg.get("photo"):
                user.photo_file_id = msg["photo"][-1].get("file_id")
            user.state = None
            await send(user, "profile_updated")
            await show_profile(user)
            return
        if step == "language":
            return  # handled via callback only
        if step == "rules":
            await begin_registration(user)
            return
        if step == "name":
            name = text.strip()
            if not (3 <= len(name) <= 20):
                await send(user, "invalid_input")
                return
            user.display_name = name
            user.state = {"step": "age"}
            await send(user, "ask_age")
            return
        if step == "age":
            try:
                age = int(text.strip())
            except ValueError:
                await send(user, "invalid_input")
                return
            if not (13 <= age <= 60):
                await send(user, "invalid_input")
                return
            user.age = age
            user.state = {"step": "gender"}
            await send(user, "ask_gender", keyboard=kb(
                [btn(tr(user.language, "girl"), "reg_female"), btn(tr(user.language, "boy"), "reg_male")]))
            return
        if step == "bio":
            if text.strip() != "/skip":
                user.bio = text.strip()[:100]
            user.state = None
            await send(user, "profile_updated")
            await show_profile(user)
            return
        if step == "edit_name":
            await s.commit()
            await apply_edit_name(user, text)
            return
        if step == "edit_age":
            try:
                age = int(text.strip())
            except ValueError:
                await send(user, "invalid_input")
                return
            await s.commit()
            await apply_edit_age(user, age)
            return
        if step == "birthday_day":
            try:
                day = int(text.strip())
            except ValueError:
                await send(user, "invalid_input")
                return
            await s.commit()
            await birthday_day_entered(user, day)
            return
        if step == "anon_msg":
            # anonymous link message (item 4)
            from .moderation import check_contact, check_profanity
            from . import moderation as _mod
            ok, reason = await service.check_message(s, text)
            if not ok:
                if reason == "profanity":
                    await send(user, "msg_rejected_profanity")
                else:
                    await send(user, "msg_rejected_contact")
                await service.apply_violation(s, user, reason, text[:60])
                return
            target = await s.get(User, state.get("target", 0))
            user.state = None
            if target and target.status == "active":
                adp_t = adapter(target.platform)
                if adp_t:
                    try:
                        await adp_t.send_message(
                            target.platform_user_id,
                            tr(target.language, "anon_msg_received", text=text[:3000]))
                    except PlatformError as exc:
                        log.info("anon link delivery failed: %s", exc)
            await send(user, "anon_msg_sent")
            return
        if step == "direct_msg_target":
            await s.commit()
            await direct_message_flow(user, text)
            return
        if step == "direct_msg_text":
            ok, reason = await service.check_message(s, text)
            if not ok:
                if reason == "profanity":
                    await send(user, "msg_rejected_profanity")
                else:
                    await send(user, "msg_rejected_contact")
                await service.apply_violation(s, user, reason, text[:60])
                return
            await s.commit()
            await deliver_direct_message(user, state.get("target"), text)
            return
        if step == "appeal":
            from .db import Appeal, Ban
            ban = await s.get(Ban, state.get("ban_id", 0))
            if ban:
                s.add(Appeal(ban_id=ban.id, user_id=user.id, message=text[:2000]))
            user.state = None
            await send(user, "appeal_sent")
            return

        # --- ban / maintenance gates ---
        ban = await service.active_ban(s, user)
        if ban:
            await _notify_ban(user, ban)
            return
        app_cfg = await service.get_setting(s, "app")
        if app_cfg.get("maintenanceMode"):
            await send(user, "maintenance")
            return

        # --- in-chat relay ---
        chat = await service.active_chat_for(s, user.id)
        if chat is None:
            await main_menu(user)
            return
        partner = await partner_of(s, chat, user)

        # determine message type
        mtype = None
        media = None
        if msg.get("text"):
            mtype = "text"
        elif msg.get("photo"):
            mtype = "photo"
            media = msg["photo"][-1]
        elif msg.get("voice"):
            mtype, media = "voice", msg["voice"]
        elif msg.get("video"):
            mtype, media = "video", msg["video"]
        elif msg.get("animation"):
            mtype, media = "animation", msg["animation"]
        elif msg.get("audio"):
            mtype, media = "audio", msg["audio"]
        elif msg.get("document"):
            mtype, media = "document", msg["document"]
        elif msg.get("sticker"):
            mtype, media = "sticker", msg["sticker"]
        elif msg.get("video_note"):
            mtype, media = "video_note", msg["video_note"]
        elif msg.get("location"):
            mtype = "location"
        elif msg.get("contact"):
            mtype = "contact"
        else:
            mtype = "unsupported"

        # admin media type switch
        mt = await s.get(MediaType, mtype if mtype != "unsupported" else "text")
        if mt is not None and not mt.enabled:
            await send(user, "msg_rejected_media")
            return
        if mtype in ("sticker", "video_note", "poll") and partner.platform != platform:
            await send(user, "msg_rejected_cross")
            return
        if mtype not in ("text",) and partner.platform != platform and mtype not in CROSS_OK:
            await send(user, "msg_rejected_cross")
            return

        # moderation (text + captions)
        if text:
            ok, reason = await service.check_message(s, text)
            if not ok:
                if reason == "profanity":
                    await send(user, "msg_rejected_profanity")
                else:
                    await send(user, "msg_rejected_contact")
                res = await service.apply_violation(s, user, reason if reason == "profanity" else "contact",
                                                    text[:60])
                if res["action"] != "warn":
                    await _notify_ban(user, await service.active_ban(s, user))
                    partner_notify = await service.active_chat_for(s, partner.id)
                    if partner_notify:
                        await send(partner, "partner_left", keyboard=search_cta(partner.language))
                return

        # charge sender BEFORE delivery; refund if delivery fails
        eco = await service.get_setting(s, "economy")
        cost = eco.get("messageCost", 1)
        if cost > 0:
            txn = await service.debit(s, user, cost, "message_cost", "هزینه ارسال پیام",
                                       idempotency_key=None)
            if txn is None:
                await send(user, "no_coins", keyboard=back_kb(user.language))
                return

        delivered = False
        padp = adapter(partner.platform)
        try:
            if mtype == "text":
                await padp.send_message(partner.platform_user_id, text,
                                        keyboard=chat_controls(partner.language),
                                        reply_to=None)
                delivered = True
            else:
                delivered = await relay_media(adp, padp, partner, msg, mtype, media, text)
        except UnsupportedMethod:
            await send(user, "msg_rejected_cross")
        except PlatformError as exc:
            log.warning("relay failed: %s", exc)

        if not delivered:
            if cost > 0:
                await service.credit(s, user, cost, "refund", "بازگشت هزینه پیام ارسال‌نشده")
            await send(user, "invalid_input")
            return

        # store message record
        s.add(ChatMessage(chat_id=chat.id, sender_id=user.id, msg_type=mtype,
                          text=text if text else None,
                          media_file_id=(media or {}).get("file_id") if media else None,
                          media_file_unique_id=(media or {}).get("file_unique_id") if media else None,
                          media_size=(media or {}).get("file_size") if media else None,
                          mime=(media or {}).get("mime_type") if media else None))
        chat.message_count += 1
        chat.last_activity_at = utcnow()
        user.total_messages += 1
        # first valid chat activity unlocks referral rewards (spec: first valid chat)
        await service.on_first_chat_completed(s, user)


async def relay_media(src, dst, partner: User, msg: dict, mtype: str,
                      media: Optional[dict], caption: str) -> bool:
    if partner.platform == src.platform:
        if mtype == "location":
            loc = msg["location"]
            await dst.call("sendLocation", {"chat_id": partner.platform_user_id,
                                            "latitude": loc["latitude"], "longitude": loc["longitude"]})
            return True
        if mtype == "contact":
            c = msg["contact"]
            await dst.call("sendContact", {"chat_id": partner.platform_user_id,
                                           "phone_number": c["phone_number"],
                                           "first_name": c.get("first_name", "")})
            return True
        await dst.send_media(partner.platform_user_id, mtype, media.get("file_id"),
                             caption=caption)
        return True
    if mtype == "location":
        loc = msg["location"]
        await dst.call("sendLocation", {"chat_id": partner.platform_user_id,
                                        "latitude": loc["latitude"], "longitude": loc["longitude"]})
        return True
    if mtype == "contact":
        c = msg["contact"]
        await dst.call("sendContact", {"chat_id": partner.platform_user_id,
                                       "phone_number": c["phone_number"],
                                       "first_name": c.get("first_name", "")})
        return True
    data = await src.download_file(media["file_id"], settings.cross_media_max_bytes)
    filename = (media.get("file_name") or f"kanar_{mtype}")
    await dst.send_media(partner.platform_user_id, mtype, bytes_data=data,
                         filename=filename, caption=caption)
    return True


# -------------------------------------------------------------- callbacks ----
async def handle_callback(platform: str, query: dict) -> None:
    adp = adapter(platform)
    data = query.get("data") or ""
    from_u = query.get("from", {})
    msg = query.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    try:
        await adp.answer_callback(query["id"])
    except PlatformError:
        pass
    async with session_scope() as s:
        res = await s.execute(select(User).where(
            User.platform == platform, User.platform_user_id == str(from_u.get("id", ""))))
        user = res.scalar_one_or_none()
        if user is None:
            return
        user.last_seen_at = utcnow()
        if data.startswith("lang_"):
            user.language = data[5:]
            await send(user, "language_set")
            if not user.accepted_rules_at:
                await begin_registration(user)
            else:
                await main_menu(user)
            return
        if data == "back_home":
            user.state = None
            await main_menu(user)
            return
        if data == "chat_back":
            user.state = None
            await send(user, "chat_controls_hint", keyboard=chat_controls(user.language))
            return
        if data == "accept_rules":
            user.accepted_rules_at = utcnow()
            user.state = {"step": "name"}
            await send(user, "ask_name")
            return
        if data in ("reg_female", "reg_male"):
            user.gender = data[4:]
            user.state = None
            eco = await service.get_setting(s, "economy")
            if not user.registration_rewarded:
                user.registration_rewarded = True
                await service.credit(s, user, eco["initialReward"], "registration_reward",
                                     "جایزه ثبت‌نام",
                                     idempotency_key=f"reg:{user.platform}:{user.platform_user_id}")
            await send(user, "reg_done", coins=eco["initialReward"])
            await main_menu(user)
            return
        if data.startswith("bmon_"):
            await s.commit()
            await birthday_month_chosen(user, int(data[5:]))
            return
        if data == "set_birthday":
            await s.commit()
            await start_birthday(user)
            return
        if data == "edit_name":
            await s.commit()
            await start_edit_name(user)
            return
        if data == "edit_age":
            await s.commit()
            await start_edit_age(user)
            return
        if data == "anon_link":
            await s.commit()
            await show_anon_link(user)
            return
        if data == "direct_msg":
            user.state = {"step": "direct_msg_target"}
            await send(user, "direct_msg_intro", keyboard=back_kb(user.language))
            return
        if data == "link_account":
            await s.commit()
            await start_link_flow(user)
            return
        if data == "link_gen":
            await s.commit()
            await generate_link(user)
            return
        if data.startswith("linkok_"):
            code = data[7:]
            r = await s.execute(select(User).where(User.link_code == code))
            initiator = r.scalar_one_or_none()
            if not initiator:
                await send(user, "link_expired")
                return
            status = await service.confirm_account_link(s, initiator, user)
            eco = await service.get_setting(s, "economy")
            reward = eco.get("accountLinkReward", 50)
            if status == "ok":
                await send(initiator, "link_done", coins=reward,
                           other_platform=PLATFORM_LABEL[user.platform][initiator.language])
                await send(user, "link_done", coins=reward,
                           other_platform=PLATFORM_LABEL[initiator.platform][user.language])
            elif status == "same_platform":
                await send(user, "link_same_platform")
            elif status == "already":
                await send(user, "link_already")
            else:
                await send(user, "link_expired")
            return
        if data.startswith("gift_"):
            await s.commit()
            await send_gift_invoice(user, int(data[5:]))
            return
        if data == "chat_gift":
            await s.commit()
            await start_gift(user)
            return
        if data == "quick_connect":
            await s.commit()
            await _enqueue(user, "everyone")
            await send(user, "invite_joined")
            return
        if data == "paysupport_ticket":
            from .db import SupportMessage, SupportTicket
            t = SupportTicket(user_id=user.id, subject="پشتیبانی پرداخت", category="payment")
            s.add(t)
            await s.flush()
            t.ticket_no = f"TKT-{t.id:05d}"
            s.add(SupportMessage(ticket_id=t.id, sender_type="user",
                                 sender_name=user.display_name, body="درخواست بررسی پرداخت"))
            await send(user, "support_sent", ticket=t.ticket_no, keyboard=back_kb(user.language))
            return
        if data == "stop_search":
            await s.commit()
            await do_stop(user)
            return
        if data.startswith("gf_"):
            await s.commit()
            await _enqueue(user, data[3:])
            return
        if data == "search":
            await s.commit()
            await start_search(user)
            return
        if data == "profile":
            await s.commit()
            await show_profile(user)
            return
        if data == "coins":
            await send(user, "coins_balance", coins=user.coin_balance, keyboard=back_kb(user.language))
            return
        if data == "store":
            await s.commit()
            await show_store(user)
            return
        if data == "bonus":
            got = await service.grant_daily_bonus(s, user)
            if got:
                await send(user, "bonus_ok", coins=got, keyboard=back_kb(user.language))
            else:
                await send(user, "bonus_already", keyboard=back_kb(user.language))
            return
        if data == "referral":
            await s.commit()
            await show_referral(user)
            return
        if data == "language":
            user.state = {"step": "language"}
            await send(user, "choose_lang", keyboard=kb(
                [btn(LANG_NAMES["fa"], "lang_fa"), btn(LANG_NAMES["en"], "lang_en"),
                 btn(LANG_NAMES["ru"], "lang_ru")], [btn(tr(user.language, "btn_back"), "back_home")]))
            return
        if data == "help":
            await send(user, "help_body", keyboard=back_kb(user.language))
            return
        if data == "admin_open":
            await s.commit()
            from .bot_admin import admin_entry
            await admin_entry(user)
            return
        if data == "edit_bio":
            user.state = {"step": "bio"}
            await send(user, "ask_bio")
            return
        if data == "edit_photo":
            user.state = {"step": "photo"}
            await send(user, "ask_photo")
            return
        if data.startswith("buy_"):
            await s.commit()
            await start_purchase(user, data[4:])
            return
        if data == "chat_next":
            await s.commit()
            await do_next(user)
            return
        if data == "chat_end":
            await s.commit()
            await do_stop(user)
            return
        if data == "chat_report":
            await s.commit()
            await do_report(user)
            return
        if data == "chat_block":
            await s.commit()
            await do_block(user)
            return
        # admin callbacks
        from .bot_admin import admin_callback
        await s.commit()
        await admin_callback(user, data, chat_id)


# ------------------------------------------------------------------ update ----
async def process_update(platform: str, upd: dict) -> None:
    """Entry point with update deduplication."""
    uid = upd.get("update_id")
    if uid is None:
        return
    async with session_scope() as s:
        from .db import ProcessedUpdate
        try:
            s.add(ProcessedUpdate(platform=platform, update_id=uid))
            await s.flush()
        except Exception:
            await s.rollback()
            return  # duplicate update
    try:
        if "message" in upd and upd["message"]:
            msg = upd["message"]
            if msg.get("successful_payment"):
                await handle_successful_payment(platform, msg)
            else:
                await handle_message(platform, msg)
        elif "edited_message" in upd and upd["edited_message"]:
            pass  # edits are not relayed
        elif "callback_query" in upd and upd["callback_query"]:
            await handle_callback(platform, upd["callback_query"])
        elif "pre_checkout_query" in upd and upd["pre_checkout_query"]:
            await handle_pre_checkout(platform, upd["pre_checkout_query"])
    except Exception:
        log.exception("update processing failed (%s)", platform)
