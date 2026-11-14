"""User-facing bot logic (shared by Telegram and Bale) — v3."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import delete, func, select

from . import db, service
from .config import RUNTIME, settings
from .db import (AnonThread, Block, Chat, ChatMessage, MediaType, Payment,
                 QueueEntry, Report, StorePackage, User, session_scope, utcnow)
from .i18n import (GENDER_LABEL, GREGORIAN_MONTHS, JALALI_MONTHS, LANG_NAMES,
                   PLATFORM_LABEL, tr)
from .platform import CROSS_OK, PlatformError, UnsupportedMethod
from .moderation import check_display_name

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
    """v2.2: "دریافت پاداش" replaces the daily-bonus button; language moved to
    the profile submenu."""
    lang = user.language
    rows = []
    # row 1: chat state — "next partner" while chatting, search otherwise
    async with session_scope() as s:
        u = await s.get(User, user.id)
        chatting = await service.active_chat_for(s, u.id)
        queued = (await s.execute(select(QueueEntry).where(
            QueueEntry.user_id == u.id))).scalar_one_or_none()
    if chatting:
        rows.append([btn("⏭ " + tr(lang, "btn_next"), "chat_next"),
                     btn(tr(lang, "btn_end"), "chat_end")])
    elif queued:
        rows.append([btn(tr(lang, "btn_stop_search"), "stop_search")])
    else:
        rows.append([btn(tr(lang, "btn_search"), "search")])
    rows.append([btn(tr(lang, "btn_profile"), "profile")])
    rows.append([btn(tr(lang, "btn_anon_link"), "anon_link"),
                 btn(tr(lang, "btn_direct_msg"), "direct_msg")])
    rows.append([btn(tr(lang, "btn_coins"), "wallet"),
                 btn(tr(lang, "btn_rewards"), "rewards")])
    rows.append([btn(tr(lang, "btn_help"), "help")])
    if await _is_admin(user):
        rows.append([btn(tr(lang, "btn_admin"), "admin_open")])
    await send(user, "main_menu", keyboard=kb(*rows))


async def show_rewards(user: User) -> None:
    """v2.2 (item 7): rewards submenu — invite friends, daily bonus, back home."""
    lang = user.language
    await send(user, "rewards_title", keyboard=kb(
        [btn(tr(lang, "btn_myref"), "referral")],
        [btn(tr(lang, "btn_bonus"), "bonus")],
        [btn(tr(lang, "btn_back"), "back_home")]))


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


def tg_link(param: str) -> str:
    return f"https://t.me/{RUNTIME.get('tg_username', 'kanarchatbot')}?start={param}"


def bale_link(param: str) -> str:
    return f"https://ble.ir/{RUNTIME.get('bale_username', 'kchatbot')}?start={param}"


def deep_link(user: User, param: str) -> str:
    """The link on the user's OWN platform."""
    return tg_link(param) if user.platform == "telegram" else bale_link(param)


# ------------------------------------------------------------ registration ----
def anon_start_payload(code: str) -> str:
    """v2.2 (item 7): anonymous-link start payload WITHOUT '/' or '_' characters.
    Legacy payloads ("msg_<code>") keep working via cmd_start."""
    return "m" + code


async def cmd_start(user: User, payload: str = "") -> None:
    async with session_scope() as s:
        u = await s.get(User, user.id)
        if u.status == "deleted":
            u.status = "active"
            u.deleted_at = None
        # --- deep-link payloads work for BOTH new and already-registered users ---
        anon_code = None
        if payload.startswith("msg_"):
            anon_code = payload[4:]          # legacy v3 links
        elif payload.startswith("m") and len(payload) > 1 and not payload.startswith("msg"):
            anon_code = payload[1:]          # v2.2 clean payload (no '/' or '_')
        if anon_code:
            target = await service.find_by_anon_code(s, anon_code)
            if target and target.id != u.id and target.status == "active":
                blocked = await s.execute(select(Block).where(
                    Block.blocker_id == target.id, Block.blocked_id == u.id))
                if blocked.scalar_one_or_none():
                    await main_menu(u)
                    return
                u.state = {"step": "anon_msg", "target": target.id}
                await send(u, "anon_msg_intro")
                return
            # invalid/expired code or the owner themself -> normal entry
            if u.age == 0 or not u.accepted_rules_at:
                u.state = {"step": "language"}
                await send(u, "welcome", keyboard=kb(
                    [btn(LANG_NAMES["fa"], "lang_fa"), btn(LANG_NAMES["en"], "lang_en"),
                     btn(LANG_NAMES["ru"], "lang_ru")]))
            else:
                await main_menu(u)
            return
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
                await main_menu(u)
                return
            await send(u, "link_expired")
            await main_menu(u)
            return
        if payload.startswith("ref_") and (u.age == 0 or not u.accepted_rules_at):
            r = await s.execute(select(User).where(User.referral_code == payload[4:]))
            ref = r.scalar_one_or_none()
            if ref and ref.id != u.id and ref.platform == u.platform:
                from .db import Referral
                existing = await s.execute(select(Referral).where(
                    Referral.referrer_id == ref.id, Referral.referred_id == u.id))
                if not existing.scalar_one_or_none():
                    s.add(Referral(code=payload[4:], referrer_id=ref.id, referred_id=u.id))
                    u.referred_by_id = ref.id
        if u.age == 0 or not u.accepted_rules_at:
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
    """Rules only (v3): read and accept — no privacy message."""
    user.state = {"step": "rules"}
    await send(user, "rules_title")
    await send(user, "rules_body")
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
    elif cmd == "/coins" or cmd == "/wallet":
        await show_wallet(user)
    elif cmd == "/store" or cmd == "/buy":
        await show_store(user)
    elif cmd == "/language":
        user.state = {"step": "language"}
        await send(user, "choose_lang", keyboard=kb(
            [btn(LANG_NAMES["fa"], "lang_fa"), btn(LANG_NAMES["en"], "lang_en"),
             btn(LANG_NAMES["ru"], "lang_ru")], [btn(tr(lang, "btn_back"), "back_home")]))
    elif cmd == "/rules":
        await send(user, "rules_title")
        await send(user, "rules_body", keyboard=back_kb(lang))
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
        await show_anon_links(user)
    elif cmd == "/msg":
        user.state = {"step": "direct_msg_target"}
        await send(user, "direct_msg_intro", keyboard=back_kb(lang))
    elif cmd == "/gift":
        await start_gift(user)
    elif cmd == "/report":
        # v2.2: the reason is chosen in an extra step (item 2)
        await ask_report_reason(user)
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
async def show_anon_links(user: User) -> None:
    """v3: every user gets TWO anonymous links — one for Telegram, one for Bale.
    v2.2: start payloads contain no '/' or '_' characters (item 7)."""
    async with session_scope() as s:
        u = await s.get(User, user.id)
        code = await service.ensure_anon_link(s, u)
    await send(user, "anon_link_info",
               tg_link=tg_link(anon_start_payload(code)),
               bale_link=bale_link(anon_start_payload(code)),
               keyboard=back_kb(user.language))


# ------------------------------------------------------ anon threads (v3) ----
def anon_thread_kb(lang: str, thread_id: Optional[int] = None):
    """Reply / Report / Block buttons under every received anonymous message.
    v2.1 FIX: every button carries ITS thread id, so replies/report/block always
    target the exact sender of that message (previously only the newest thread
    was addressed, which broke reply for older messages)."""
    suffix = str(thread_id) if thread_id else ""
    return kb([btn(tr(lang, "btn_anon_reply"), f"anonr_{suffix}"),
               btn(tr(lang, "btn_anon_report"), f"anonrep_{suffix}"),
               btn(tr(lang, "btn_anon_block"), f"anonblk_{suffix}")])


async def deliver_anon_message(session, sender: User, owner: User, text: str,
                               is_reply: bool = False) -> bool:
    """Deliver an anonymous message to the link owner with action buttons.
    Creates/updates the AnonThread. Returns True when delivered."""
    blocked = await session.execute(select(Block).where(
        Block.blocker_id == owner.id, Block.blocked_id == sender.id))
    if blocked.scalar_one_or_none():
        return False
    thread = (await session.execute(select(AnonThread).where(
        AnonThread.owner_id == owner.id, AnonThread.sender_id == sender.id))).scalar_one_or_none()
    if thread is None:
        thread = AnonThread(owner_id=owner.id, sender_id=sender.id,
                            status="open", message_count=0, last_text="")
        session.add(thread)
        await session.flush()
    thread.last_text = text[:2000]
    thread.message_count = (thread.message_count or 0) + 1
    thread.updated_at = utcnow()
    adp = adapter(owner.platform)
    if adp is None:
        return False
    try:
        key = "anon_reply_received" if is_reply else "anon_msg_received"
        await adp.send_message(owner.platform_user_id, tr(owner.language, key, text=text[:3000]),
                               keyboard=anon_thread_kb(owner.language, thread.id))
        return True
    except PlatformError as exc:
        log.info("anon delivery failed: %s", exc)
        return False


async def anon_reply_flow(owner: User, text: str, thread_id: Optional[int] = None) -> None:
    """Owner replies to a specific anonymous thread (v2.1: exact thread, and the
    reply keeps the conversation flowing through BOTH directions' threads)."""
    async with session_scope() as s:
        u = await s.get(User, owner.id)
        u.state = None
        ok, reason = await service.check_message(s, text)
        if not ok:
            await send(u, "msg_rejected_profanity" if reason == "profanity" else "msg_rejected_contact")
            await service.apply_violation(s, u, reason, text[:60])
            return
        q = select(AnonThread).where(AnonThread.owner_id == u.id)
        if thread_id:
            q = q.where(AnonThread.id == thread_id)
        thread = (await s.execute(q.order_by(AnonThread.updated_at.desc())
                                  .limit(1))).scalar_one_or_none()
        if not thread or thread.status != "open":
            await main_menu(u)
            return
        thread.last_text = text[:2000]
        thread.updated_at = utcnow()
        sender = await s.get(User, thread.sender_id)
        if sender and sender.status == "active":
            blocked = await s.execute(select(Block).where(
                Block.blocker_id == sender.id, Block.blocked_id == u.id))
            if not blocked.scalar_one_or_none():
                adp = adapter(sender.platform)
                if adp:
                    try:
                        await adp.send_message(
                            sender.platform_user_id,
                            tr(sender.language, "anon_reply_received", text=text[:3000]),
                            keyboard=anon_thread_kb(sender.language, thread.id))
                    except PlatformError as exc:
                        log.info("anon reply delivery failed: %s", exc)
        # never reveal whether the sender received it
    await send(owner, "anon_reply_sent")


async def anon_report(owner: User, thread_marker: str = "", reason: str = "anon_message") -> None:
    """Report the sender of the most recent (or marked) anonymous thread.
    v2.2: the report carries the reason chosen in the extra step (item 2)."""
    async with session_scope() as s:
        u = await s.get(User, owner.id)
        q = select(AnonThread).where(AnonThread.owner_id == u.id)
        if thread_marker and thread_marker.isdigit():
            q = q.where(AnonThread.id == int(thread_marker))
        thread = (await s.execute(q.order_by(AnonThread.updated_at.desc())
                                  .limit(1))).scalar_one_or_none()
        if not thread:
            await send(u, "invalid_input")
            return
        sender = await s.get(User, thread.sender_id)
        if not sender:
            return
        dup = await s.execute(select(Report).where(
            Report.reporter_id == u.id, Report.reported_id == sender.id,
            Report.created_at >= utcnow() - timedelta(hours=24)))
        if dup.scalar_one_or_none():
            await send(u, "report_dup", keyboard=anon_thread_kb(u.language, thread.id))
            return
        s.add(Report(chat_id=None, reporter_id=u.id, reported_id=sender.id,
                     reason=reason[:24], details=(thread.last_text or "")[:500]))
        sender.reports_received += 1
        u.reports_sent += 1
        cfg = await service.get_setting(s, "moderation")
        window = utcnow() - timedelta(hours=cfg.get("autoBanWindowHours", 24))
        unique = await s.execute(
            select(func.count(func.distinct(Report.reporter_id))).where(
                Report.reported_id == sender.id, Report.created_at >= window))
        count = unique.scalar() or 0
        if count >= cfg.get("autoBanReportThreshold", 3) and sender.status == "active":
            await service.ban_user(s, sender, "temporary", "auto_3_reports",
                                   f"{count} گزارش مستقل در ۲۴ ساعت", "system",
                                   hours=cfg.get("autoBanDurationHours", 24))
    await send(owner, "anon_report_done")


async def anon_block(owner: User, thread_marker: str = "") -> None:
    """Block the sender of the most recent (or marked) anonymous thread —
    they can never send anonymous messages to this owner again."""
    async with session_scope() as s:
        u = await s.get(User, owner.id)
        q = select(AnonThread).where(AnonThread.owner_id == u.id)
        if thread_marker and thread_marker.isdigit():
            q = q.where(AnonThread.id == int(thread_marker))
        thread = (await s.execute(q.order_by(AnonThread.updated_at.desc())
                                  .limit(1))).scalar_one_or_none()
        if not thread:
            await main_menu(u)
            return
        thread.status = "blocked"
        sender = await s.get(User, thread.sender_id)
        if sender:
            dup = await s.execute(select(Block).where(
                Block.blocker_id == u.id, Block.blocked_id == sender.id))
            if not dup.scalar_one_or_none():
                s.add(Block(blocker_id=u.id, blocked_id=sender.id))
    await send(owner, "anon_blocked_info")


# -------------------------------------------------------- direct msg (14) ----
async def direct_message_flow(user: User, target_text: str) -> None:
    """Resolve target by @username or numeric ID, then ask for the message text."""
    target_text = target_text.strip()
    target = None
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
    async with session_scope() as s:
        u = await s.get(User, user.id)
        u.state = {"step": "direct_msg_text", "target": target.id if target else None}
    # deliberately identical prompt whether or not the target exists (mystery!)
    await send(user, "ask_msg_text", keyboard=back_kb(user.language))


async def deliver_direct_message(sender: User, target_id: Optional[int], text: str) -> None:
    """Deliver anonymously; NEVER reveal the outcome to the sender (item 14)."""
    async with session_scope() as s:
        u = await s.get(User, sender.id)
        u.state = None
        if target_id:
            target = await s.get(User, target_id)
            if target and target.status == "active":
                blocked = await s.execute(select(Block).where(
                    Block.blocker_id == target.id, Block.blocked_id == u.id))
                if not blocked.scalar_one_or_none():
                    # direct messages also create a thread so the target can reply
                    thread = (await s.execute(select(AnonThread).where(
                        AnonThread.owner_id == target.id,
                        AnonThread.sender_id == u.id))).scalar_one_or_none()
                    if thread is None:
                        thread = AnonThread(owner_id=target.id, sender_id=u.id,
                                            status="open", message_count=0, last_text="")
                        s.add(thread)
                        await s.flush()
                    thread.last_text = text[:2000]
                    thread.message_count = (thread.message_count or 0) + 1
                    thread.updated_at = utcnow()
                    adp = adapter(target.platform)
                    if adp:
                        try:
                            await adp.send_message(
                                target.platform_user_id,
                                tr(target.language, "anon_msg_received", text=text[:3000]),
                                keyboard=anon_thread_kb(target.language, thread.id))
                        except PlatformError as exc:
                            log.info("direct message delivery failed (hidden): %s", exc)
    # neutral response — no delivery status revealed
    await send(sender, "direct_msg_neutral")


# ------------------------------------------- account linking: REMOVED in v2.1 ----
# The "link Telegram/Bale accounts" feature was removed from the user UI.
# Deep-links /start lnk_XXX are still answered politely for old links.


async def legacy_link_expired(user: User) -> None:
    await send(user, "link_expired")
    await main_menu(user)


# -------------------------------------------------------------- gifts (18) ----
GIFT_COINS = [100, 300, 700, 1500]


async def start_gift(user: User) -> None:
    async with session_scope() as s:
        chat = await service.active_chat_for(s, user.id)
        if not chat:
            await send(user, "not_in_chat", keyboard=search_cta(user.language))
            return
        store_cfg = await service.get_setting(s, "store")
    rows = []
    for coins in GIFT_COINS:
        stars = service.coins_price_stars(store_cfg, coins)   # 1 star = 20 coins
        toman = service.coins_price_toman(store_cfg, coins)   # 500 Toman = 1 coin
        label = tr(user.language, "pay_stars", stars=stars, coins=coins) \
            if user.platform == "telegram" else tr(user.language, "pay_bale", toman=toman, coins=coins)
        rows.append([btn("🎁 " + label, f"gift_{coins}")])
    rows.append([btn(tr(user.language, "btn_back"), "chat_back")])
    example_coins = 100
    price = f"⭐ {service.coins_price_stars(store_cfg, example_coins)} استارز" \
        if user.platform == "telegram" \
        else f"💳 {service.coins_price_toman(store_cfg, example_coins):,} تومان".replace(",", "٬")
    await send(user, "gift_title", price=price, coins=example_coins, keyboard=rows)


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
        store_cfg = await service.get_setting(s, "store")
        payload = f"gift:{coins}:{user.id}:{partner.id}"
        try:
            if user.platform == "telegram":
                await adp.send_invoice(user.platform_user_id, f"🎁 هدیه {coins} سکه",
                                       "اهدای سکه به هم‌چت ناشناس", payload,
                                       [{"label": f"gift {coins}",
                                         "amount": service.coins_price_stars(store_cfg, coins)}])
            else:
                if not settings.bale_provider_token:
                    await send(user, "pay_unavailable")
                    return
                await adp.send_invoice(user.platform_user_id, f"🎁 هدیه {coins} سکه",
                                       "اهدای سکه به هم‌چت ناشناس", payload,
                                       [{"label": f"gift {coins}",
                                         "amount": service.coins_price_toman(store_cfg, coins) * 10}])
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
        eco = await service.get_setting(s, "economy")
        app_cfg = await service.get_setting(s, "app")
    if not app_cfg.get("enableGenderFilter", True):
        await _enqueue(user, "everyone")
        return
    await send(user, "gender_filter_q", cost=eco["genderFilterMatchCost"], keyboard=kb(
        [btn(tr(lang, "gf_everyone"), "gf_everyone")],
        [btn(tr(lang, "gf_girls"), "gf_female"), btn(tr(lang, "gf_boys"), "gf_male")],
        [btn(tr(lang, "btn_back"), "back_home")]))


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
    try:
        async with session_scope() as s:
            in_queue = (await s.execute(select(func.count(QueueEntry.id)))).scalar() or 0
            if in_queue >= 2:
                return
            cutoff = utcnow() - timedelta(minutes=30)
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
            seeker_group = service.age_group(seeker.age)
            for cand in candidates:
                if service.age_group(cand.age) != seeker_group:
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


async def run_match_cycle() -> None:
    """Try to pair queued users; notify both on success."""
    while True:
        async with session_scope() as s:
            matched = await service.try_match(s)
            if not matched:
                return
            ua, ub, _charged = matched
            for me, other in ((ua, ub), (ub, ua)):
                gender = GENDER_LABEL.get(other.gender or "male", GENDER_LABEL["male"])[me.language]
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
                        await adp.send_message(me.platform_user_id,
                                               "⬇️💬 دکمه‌های کنترل گفتگو:",
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


# ------------------------------------------------------------- reports ----
# v2.2 (item 2): the reporter always picks a reason first.
REPORT_REASONS = [("report_bad_name", "bad_name"), ("report_abuse", "abuse"),
                  ("report_spam", "spam"), ("report_other", "other")]


async def ask_report_reason(user: User, kind: str = "chat",
                            thread_id: Optional[int] = None) -> None:
    """Extra step after the report button: choose a reason (item 2)."""
    lang = user.language
    rows = []
    for i18n_key, rid in REPORT_REASONS:
        data = (f"anonrep_{thread_id}_{rid}" if kind == "anon" else f"report_{rid}")
        rows.append([btn(tr(lang, i18n_key), data)])
    rows.append([btn(tr(lang, "btn_back"), "chat_back" if kind == "chat" else "back_home")])
    await send(user, "report_reason_title", keyboard=kb(*rows))


async def do_report(user: User, reason: str, details: str = "") -> None:
    async with session_scope() as s:
        u = await s.get(User, user.id)
        chat = await service.active_chat_for(s, u.id)
        if not chat:
            await send(u, "not_in_chat", keyboard=search_cta(u.language))
            return
        partner = await partner_of(s, chat, u)
        res = await service.create_report(s, chat, u, partner, reason, details or "—")
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
        s.add(Block(blocker_id=u.id, blocked_id=partner.id))
        await service.end_chat(s, chat, "left", u.id)
        await send(partner, "blocked_by_partner", keyboard=search_cta(partner.language))
    await send(user, "block_done")
    await main_menu(user)


# ------------------------------------------------------------- profile ----
async def show_profile(user: User) -> None:
    lang = user.language
    # v3 FIX: safe gender lookup (gender may be unset during registration)
    gender_entry = GENDER_LABEL.get(user.gender or "male", GENDER_LABEL["male"])
    gender = gender_entry[lang]
    if user.birth_year:
        if user.birth_calendar == "jalali":
            month_name = JALALI_MONTHS[(user.birth_month or 1) - 1].strip()
            year_label = user.birth_year  # shown in gregorian; convert for fa users
            jy, _jm, _jd = service.gregorian_to_jalali(
                user.birth_year, user.birth_month or 1, user.birth_day or 1)
            year_label = jy
            bday = tr(lang, "birthday_set",
                      date=f"{jy} / {month_name} / {user.birth_day} (شمسی 🌙)")
        else:
            month_name = GREGORIAN_MONTHS["fa"][(user.birth_month or 1) - 1].strip()
            bday = tr(lang, "birthday_set",
                      date=f"{user.birth_year} / {month_name} / {user.birth_day} (میلادی 🌍)")
        bday += "\n⚠️🔒 تاریخ تولد قابل تغییر نیست."
    else:
        bday = tr(lang, "birthday_not_set")
    coins = user.coin_balance
    async with session_scope() as s:
        u = await s.get(User, user.id)
        coins = u.coin_balance
    rows = [
        [btn(tr(lang, "btn_edit_name"), "edit_name"),
         btn(tr(lang, "btn_edit_age"), "edit_age")],
        [btn(tr(lang, "btn_edit_bio"), "edit_bio"),
         btn(tr(lang, "btn_edit_photo"), "edit_photo")],
        [btn(tr(lang, "btn_anon_link"), "anon_link"),
         btn(tr(lang, "btn_direct_msg"), "direct_msg")],
        # v2.2 (item 7): language switch lives in the profile submenu now
        [btn(tr(lang, "btn_language"), "language")],
    ]
    # v2.2 (item 4): the "ثبت تاریخ تولد و دریافت پاداش" option is REMOVED —
    # the birthdate is collected once during registration.
    await send(user, "profile_card", name=user.display_name, age=user.age,
               gender=gender, bio=user.bio or "—", coins=coins,
               birthday=bday, keyboard=back_kb(lang, rows))
    if user.photo_file_id:
        adp = adapter(user.platform)
        if adp:
            try:
                await adp.send_media(user.platform_user_id, "photo", user.photo_file_id)
            except PlatformError:
                pass


# ------------------------- birthdate registration (v2.2, item 4) ----
def _months_for(lang: str, calendar: str):
    if calendar == "jalali":
        return JALALI_MONTHS
    return GREGORIAN_MONTHS.get(lang) or GREGORIAN_MONTHS["en"]


BIRTH_PAGE_SIZE = 10  # selectable years per inline page


def _birth_year_range(calendar: str) -> tuple[int, int]:
    """(oldest, youngest) selectable birth years — age range 13..60."""
    import datetime as _dt
    today = _dt.date.today()
    if calendar == "jalali":
        jy, _jm, _jd = service.gregorian_to_jalali(today.year, today.month, today.day)
        return jy - 60, jy - 13
    return today.year - 60, today.year - 13


async def begin_birthdate(user: User, calendar: str = "") -> None:
    """v2.2 (item 4): registration collects birthdate year -> month -> day with
    inline buttons, then asks the user to confirm the computed age."""
    state = user.state or {}
    cal = calendar or state.get("calendar") or \
        ("jalali" if user.language == "fa" else "gregorian")
    async with session_scope() as s:
        u = await s.get(User, user.id)
        u.state = {"step": "birth_year", "calendar": cal, "page": 0}
    # keep the in-memory object in sync: send_birth_year_page reads user.state
    user.state = {"step": "birth_year", "calendar": cal, "page": 0}
    await send_birth_year_page(user, 0)


def _year_rows(user: User, calendar: str, page: int) -> list:
    min_y, max_y = _birth_year_range(calendar)
    page = max(0, page)
    newest = max_y - page * BIRTH_PAGE_SIZE
    years = [y for y in range(newest, newest - BIRTH_PAGE_SIZE, -1) if y >= min_y]
    rows = []
    for i in range(0, len(years), 5):
        rows.append([btn(str(y), f"byear_{y}") for y in years[i:i + 5]])
    nav = []
    if newest - BIRTH_PAGE_SIZE >= min_y:
        nav.append(btn(tr(user.language, "birth_year_older"), f"bypage_{page + 1}"))
    if page > 0:
        nav.append(btn(tr(user.language, "birth_year_newer"), f"bypage_{page - 1}"))
    if nav:
        rows.append(nav)
    rows.append([btn(tr(user.language, "btn_back_step"), "bcal_back")])
    return rows


async def send_birth_year_page(user: User, page: int) -> None:
    state = user.state or {}
    calendar = state.get("calendar", "jalali")
    await send(user, "birth_year_q",
               keyboard=_year_rows(user, calendar, max(0, page)))


async def birth_year_chosen(user: User, year: int) -> None:
    state = user.state or {}
    calendar = state.get("calendar", "jalali")
    min_y, max_y = _birth_year_range(calendar)
    if not (min_y <= year <= max_y):
        await send(user, "invalid_input")
        return
    async with session_scope() as s:
        u = await s.get(User, user.id)
        u.state = {"step": "birth_month", "calendar": calendar, "year": year}
    months = _months_for(user.language, calendar)
    rows = [[btn(months[i].strip(), f"bmon_{i + 1}"), btn(months[i + 6].strip(), f"bmon_{i + 7}")]
            for i in range(6)]
    rows.append([btn(tr(user.language, "btn_back_step"), "bmon_back")])
    await send(user, "birthday_ask_month", keyboard=rows)


async def birth_month_chosen(user: User, month: int) -> None:
    state = user.state or {}
    calendar = state.get("calendar", "jalali")
    maxday = 31 if calendar == "gregorian" else service.JALALI_MONTH_DAYS[month - 1]
    async with session_scope() as s:
        u = await s.get(User, user.id)
        u.state = {"step": "birth_day", "calendar": calendar,
                   "year": state.get("year"), "month": month, "maxday": maxday}
    months = _months_for(user.language, calendar)
    rows = []
    for i in range(0, maxday, 6):
        rows.append([btn(str(d), f"bday_{d}") for d in range(i + 1, min(i + 6, maxday) + 1)])
    rows.append([btn(tr(user.language, "btn_back_step"), "bday_back")])
    await send(user, "birthday_ask_day", month=months[month - 1].strip(), maxday=maxday,
               keyboard=rows)


async def birth_day_chosen(user: User, day: int) -> None:
    state = user.state or {}
    calendar = state.get("calendar", "jalali")
    year = state.get("year")
    month = state.get("month", 1)
    maxday = state.get("maxday", 31)
    if not year or not (1 <= day <= maxday):
        await send(user, "invalid_input")
        return
    if calendar == "jalali" and month == 12 and day == 30:
        day = 29  # esfand 30 only in leap years — clamp
    if calendar == "jalali":
        gy, gm, gd = service.jalali_to_gregorian(year, month, day)
    else:
        gy, gm, gd = year, month, day
    age = service.compute_age_from_birthday(gy, gm, gd)
    async with session_scope() as s:
        u = await s.get(User, user.id)
        u.state = {"step": "birth_confirm", "calendar": calendar,
                   "year": year, "month": month, "day": day}
    if age < 13:
        # item 4: minimum age is 13 — send the notice and restart the picker
        await send(user, "under_13")
        await begin_birthdate(user, calendar)
        return
    await send(user, "age_confirm_q", age=age, keyboard=kb(
        [btn(tr(user.language, "age_confirm_yes"), "bage_ok")],
        [btn(tr(user.language, "btn_back_step"), "bage_back"),
         btn(tr(user.language, "age_confirm_no"), "bage_redo")]))


async def birth_confirm_ok(user: User) -> None:
    state = user.state or {}
    calendar = state.get("calendar", "jalali")
    year, month, day = state.get("year"), state.get("month", 1), state.get("day")
    if not (year and day):
        await send(user, "invalid_input")
        return
    async with session_scope() as s:
        u = await s.get(User, user.id)
        new_age = service.save_birthday(u, calendar, year, month, day)
        eco = await service.get_setting(s, "economy")
        reward = eco.get("birthdayReward", 10)
        if not u.birthday_rewarded:
            u.birthday_rewarded = True
            await service.credit(s, u, reward, "birthday_reward", "پاداش ثبت تاریخ تولد",
                                 idempotency_key=f"bday:{u.id}")
        else:
            reward = 0
        u.state = {"step": "gender"}
        user.birth_year, user.birth_month, user.birth_day = \
            u.birth_year, u.birth_month, u.birth_day
        user.birth_calendar, user.age = u.birth_calendar, u.age
        user.state = {"step": "gender"}
    await send(user, "birthday_saved", coins=reward or eco.get("birthdayReward", 10))
    await send(user, "ask_gender", keyboard=kb(
        [btn(tr(user.language, "girl"), "reg_female"),
         btn(tr(user.language, "boy"), "reg_male")]))


async def birth_redo(user: User) -> None:
    """User rejected the computed age — restart the birthdate picker."""
    async with session_scope() as s:
        u = await s.get(User, user.id)
        state = u.state or {}
        u.state = {"step": "birth_year", "calendar": state.get("calendar", "jalali"), "page": 0}
    user.state = {"step": "birth_year", "calendar": (user.state or {}).get("calendar", "jalali"),
                  "page": 0}
    await send_birth_year_page(user, 0)


async def resend_birth_intro(user: User) -> None:
    """Back-step target from the year page: re-ask the calendar choice."""
    calendar = (user.state or {}).get("calendar", "jalali")
    async with session_scope() as s:
        u = await s.get(User, user.id)
        u.state = {"step": "birth_calendar", "calendar": calendar}
    user.state = {"step": "birth_calendar", "calendar": calendar}
    await send(user, "birth_intro", keyboard=kb(
        [btn(tr(user.language, "birth_jalali"), "bcal_jalali"),
         btn(tr(user.language, "birth_gregorian"), "bcal_gregorian")],
        [btn(tr(user.language, "btn_back_step"), "back_name")]))


async def resend_birth_year_page(user: User) -> None:
    """Back-step target from the month page: re-show the year page."""
    state = user.state or {}
    calendar = state.get("calendar", "jalali")
    page = int(state.get("page") or 0)
    async with session_scope() as s:
        u = await s.get(User, user.id)
        u.state = {"step": "birth_year", "calendar": calendar, "page": page}
    user.state = {"step": "birth_year", "calendar": calendar, "page": page}
    await send_birth_year_page(user, page)


async def resend_birth_month_page(user: User) -> None:
    """Back-step target from the day page: re-show the month page."""
    state = user.state or {}
    calendar = state.get("calendar", "jalali")
    async with session_scope() as s:
        u = await s.get(User, user.id)
        u.state = {"step": "birth_month", "calendar": calendar,
                   "year": state.get("year"), "page": state.get("page", 0)}
    user.state = {"step": "birth_month", "calendar": calendar,
                  "year": state.get("year"), "page": state.get("page", 0)}
    months = _months_for(user.language, calendar)
    rows = [[btn(months[i].strip(), f"bmon_{i + 1}"), btn(months[i + 6].strip(), f"bmon_{i + 7}")]
            for i in range(6)]
    rows.append([btn(tr(user.language, "btn_back_step"), "bmon_back")])
    await send(user, "birthday_ask_month", keyboard=rows)


async def resend_birth_day_page(user: User) -> None:
    """Back-step target from the age-confirm page: re-show the day page."""
    state = user.state or {}
    calendar = state.get("calendar", "jalali")
    month = int(state.get("month") or 1)
    maxday = int(state.get("maxday") or (31 if calendar == "gregorian" else 31))
    async with session_scope() as s:
        u = await s.get(User, user.id)
        u.state = {"step": "birth_day", "calendar": calendar, "year": state.get("year"),
                   "month": month, "maxday": maxday, "page": state.get("page", 0)}
    user.state = {"step": "birth_day", "calendar": calendar, "year": state.get("year"),
                  "month": month, "maxday": maxday, "page": state.get("page", 0)}
    months = _months_for(user.language, calendar)
    rows = []
    for i in range(0, maxday, 6):
        rows.append([btn(str(d), f"bday_{d}") for d in range(i + 1, min(i + 6, maxday) + 1)])
    rows.append([btn(tr(user.language, "btn_back_step"), "bday_back")])
    await send(user, "birthday_ask_day", month=months[month - 1].strip(), maxday=maxday,
               keyboard=rows)


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
    # v2.2 (item 1): display names must not contain offensive vocabulary
    if not check_display_name(name).ok:
        await send(user, "name_rejected")
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
        if service.age_group(age) != service.age_group(old):
            await s.execute(delete(QueueEntry).where(QueueEntry.user_id == u.id))
        u.state = None
    await send(user, "name_age_changed", cost=cost)
    await show_profile(user)


async def show_referral(user: User) -> None:
    """v2.2 (item 6): BOTH the Telegram and Bale invite links are shown, with
    the note that joining via either one rewards both people."""
    async with session_scope() as s:
        eco = await service.get_setting(s, "economy")
    code = user.referral_code
    await send(user, "referral_info", tg_link=tg_link(f"ref_{code}"),
               bale_link=bale_link(f"ref_{code}"),
               coins=eco["referralRewardPerParty"], keyboard=back_kb(user.language))
    await send(user, "referral_count", count=user.referral_count)


async def show_wallet(user: User) -> None:
    """v2.2: wallet menu — balance + buy-coins entry (payment tickets via /paysupport)."""
    lang = user.language
    async with session_scope() as s:
        u = await s.get(User, user.id)
        coins = u.coin_balance
    rows = [
        [btn(tr(lang, "btn_store"), "store")],
    ]
    await send(user, "wallet_title", coins=coins, keyboard=back_kb(lang, rows))


async def show_store(user: User) -> None:
    """v2.2: coin purchase packages + custom amount (inside the wallet menu)."""
    rows = []
    async with session_scope() as s:
        pkgs = (await s.execute(select(StorePackage).where(StorePackage.active == True))).scalars()  # noqa
        app_cfg = await service.get_setting(s, "app")
        pay_tg = app_cfg.get("paymentsTelegramEnabled", True) and bool(settings.telegram_bot_token)
        pay_bale = app_cfg.get("paymentsBaleEnabled", False) and bool(settings.bale_provider_token)
        has_any = False
    for p in pkgs:
        if user.platform == "telegram" and p.telegram_enabled and pay_tg:
            rows.append([btn(tr(user.language, "pay_stars", stars=p.telegram_stars_price,
                                coins=p.coins), f"buy_{p.id}")])
            has_any = True
        elif user.platform == "bale" and p.bale_enabled and pay_bale:
            rows.append([btn(tr(user.language, "pay_bale", toman=p.bale_toman_price,
                                coins=p.coins), f"buy_{p.id}")])
            has_any = True
    if has_any:
        # v2.2 (item 3): custom amount — the user picks the coin count
        rows.append([btn(tr(user.language, "store_custom"), "custom_coins")])
    if not rows:
        await send(user, "pay_unavailable", keyboard=back_kb(user.language))
        return
    # back goes to the wallet (parent menu in 2.1)
    rows.append([btn("👛 " + tr(user.language, "btn_coins"), "wallet"),
                 btn(tr(user.language, "btn_back"), "back_home")])
    await send(user, "store_title", keyboard=rows)


async def start_custom_coins(user: User) -> None:
    """v2.2 (item 3): ask for the desired coin count."""
    async with session_scope() as s:
        store_cfg = await service.get_setting(s, "store")
        u = await s.get(User, user.id)
        u.state = {"step": "custom_coins"}
    await send(user, "store_custom_ask",
               min=store_cfg.get("customMinCoins", 10),
               max=store_cfg.get("customMaxCoins", 5000),
               toman=f"{store_cfg.get('tomanPerCoin', 500):,}".replace(",", "٬"),
               cps=store_cfg.get("coinsPerStar", 20),
               keyboard=back_kb(user.language))


async def start_custom_invoice(user: User, coins: int) -> None:
    """v2.2 (item 3): price the custom amount with the configured rates and
    send the invoice. Example: 50 coins -> 25,000 Toman on Bale."""
    adp = adapter(user.platform)
    if not adp:
        return
    async with session_scope() as s:
        u = await s.get(User, user.id)
        store_cfg = await service.get_setting(s, "store")
        app_cfg = await service.get_setting(s, "app")
        pay_tg = app_cfg.get("paymentsTelegramEnabled", True) and bool(settings.telegram_bot_token)
        pay_bale = app_cfg.get("paymentsBaleEnabled", False) and bool(settings.bale_provider_token)
        if user.platform == "telegram" and not pay_tg:
            await send(u, "pay_unavailable")
            return
        if user.platform == "bale" and not pay_bale:
            await send(u, "pay_unavailable")
            return
        u.state = None
        payload = f"custom:{coins}:{u.id}"
        title = tr(u.language, "custom_invoice_title", coins=coins)
        desc = tr(u.language, "invoice_desc")
        try:
            if u.platform == "telegram":
                stars = service.coins_price_stars(store_cfg, coins)
                await adp.send_invoice(u.platform_user_id, title, desc, payload,
                                       [{"label": f"{coins} coins", "amount": stars}])
            else:
                toman = service.coins_price_toman(store_cfg, coins)
                await adp.send_invoice(u.platform_user_id, title, desc, payload,
                                       [{"label": f"{coins} coins", "amount": toman * 10}])
        except UnsupportedMethod as exc:
            await send(u, "pay_unavailable")
            log.info("custom invoice unavailable: %s", exc)
        except PlatformError as exc:
            log.warning("custom invoice failed: %s", exc)


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
                await adp.send_invoice(u.platform_user_id, f"🪙 {pkg.coins} سکه هم‌چت",
                                       "خرید سکه برای چت ناشناس هم‌چت", payload,
                                       [{"label": f"{pkg.coins} coins", "amount": pkg.telegram_stars_price}])
            else:
                await adp.send_invoice(u.platform_user_id, f"🪙 {pkg.coins} سکه هم‌چت",
                                       "خرید سکه برای چت ناشناس هم‌چت", payload,
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
                                 "هدیه از هم‌چت ناشناس",
                                 reference_id=charge, idempotency_key=f"pay:{charge}")
            await send(payer, "gift_sent_payer", coins=coins)
            await send(partner, "gift_received", coins=coins)
        return
    if payload.startswith("custom:"):
        # v2.2 (item 3): custom-amount purchase — coins priced via store rates
        _, coins_raw, user_row_id = payload.split(":", 2)
        coins = int(coins_raw)
        async with session_scope() as s:
            existing = await s.execute(select(Payment).where(
                Payment.telegram_payment_charge_id == charge))
            if existing.scalar_one_or_none():
                return
            user = await s.get(User, int(user_row_id))
            if not user:
                return
            s.add(Payment(platform=platform, user_id=user.id, package_id="custom",
                          coins=coins, amount=sp.get("total_amount", 0),
                          currency=sp.get("currency", "XTR"), status="succeeded",
                          telegram_payment_charge_id=charge,
                          provider_payment_charge_id=sp.get("provider_payment_charge_id"),
                          invoice_payload=payload))
            await service.credit(s, user, coins, "store_purchase",
                                 f"خرید دلخواه {coins} سکه",
                                 reference_id=charge, idempotency_key=f"pay:{charge}")
            await send(user, "pay_ok", coins=coins)
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
        # --- owner forwards a backup file during restore wait-state ---
        doc = msg.get("document")
        if doc:
            from .bot_admin import RESTORE_WAIT, admin_document
            if str(user.platform_user_id) in RESTORE_WAIT:
                try:
                    data = await adp.download_file(doc["file_id"], 200 * 1024 * 1024)
                    if await admin_document(user, data,
                                            caption=msg.get("caption") or "",
                                            filename=doc.get("file_name") or ""):
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
            # v2.2 (item 1): reject offensive display names in fa/en/ru
            name = text.strip()
            if not (3 <= len(name) <= 20):
                await send(user, "invalid_input")
                return
            if not check_display_name(name).ok:
                await send(user, "name_rejected")
                return
            user.display_name = name
            # v2.2 (item 4): birthdate replaces the free-text age question
            user.state = {"step": "birth_calendar"}
            await send(user, "birth_intro", keyboard=kb(
                [btn(tr(user.language, "birth_jalali"), "bcal_jalali"),
                 btn(tr(user.language, "birth_gregorian"), "bcal_gregorian")],
                [btn(tr(user.language, "btn_back_step"), "back_name")]))
            return
        if step in ("birth_calendar",):
            await send(user, "invalid_input")
            return
        if step == "birth_year":
            # inline year buttons are the main path; typed years are accepted too
            if text.strip().isdigit():
                await s.commit()
                await birth_year_chosen(user, int(text.strip()))
            else:
                await send(user, "invalid_input")
            return
        if step == "birth_month":
            if text.strip().isdigit() and 1 <= int(text.strip()) <= 12:
                await s.commit()
                await birth_month_chosen(user, int(text.strip()))
            else:
                await send(user, "invalid_input")
            return
        if step == "birth_day":
            if text.strip().isdigit():
                await s.commit()
                await birth_day_chosen(user, int(text.strip()))
            else:
                await send(user, "invalid_input")
            return
        if step in ("birth_confirm",):
            await send(user, "invalid_input")
            return
        if step == "custom_coins":
            # v2.2 (item 3): custom coin amount -> price + invoice
            if not text.strip().isdigit():
                await send(user, "store_custom_invalid",
                           min=10, max=5000)
                return
            coins = int(text.strip())
            async with session_scope() as cfg_scope:
                store_cfg = await service.get_setting(cfg_scope, "store")
            cmin = int(store_cfg.get("customMinCoins", 10))
            cmax = int(store_cfg.get("customMaxCoins", 5000))
            if not (cmin <= coins <= cmax):
                await send(user, "store_custom_invalid", min=cmin, max=cmax)
                return
            await s.commit()
            await start_custom_invoice(user, coins)
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
        if step == "anon_msg":
            # anonymous link message (item 4) — delivered with reply/report/block buttons
            ok, reason = await service.check_message(s, text)
            if not ok:
                await send(user, "msg_rejected_profanity" if reason == "profanity"
                           else "msg_rejected_contact")
                await service.apply_violation(s, user, reason, text[:60])
                return
            target = await s.get(User, state.get("target", 0))
            # keep the anon_msg state so the sender can continue the thread
            if target and target.status == "active":
                delivered = await deliver_anon_message(s, user, target, text)
                if not delivered:
                    user.state = None
                    await send(user, "anon_sender_blocked_notice")
                    return
            await send(user, "anon_msg_sent")
            return
        if step == "anon_reply":
            await s.commit()
            await anon_reply_flow(user, text, state.get("thread"))
            return
        if step == "direct_msg_target":
            await s.commit()
            await direct_message_flow(user, text)
            return
        if step == "direct_msg_text":
            ok, reason = await service.check_message(s, text)
            if not ok:
                await send(user, "msg_rejected_profanity" if reason == "profanity"
                           else "msg_rejected_contact")
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
            # v2.1: admins keep full access during maintenance
            if not await _is_admin(user):
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
                await send(user, "msg_rejected_profanity" if reason == "profanity"
                           else "msg_rejected_contact")
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
    filename = (media.get("file_name") or f"hamchat_{mtype}")
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
        # v2.1: during maintenance only admins may use buttons
        app_cfg = await service.get_setting(s, "app")
        if app_cfg.get("maintenanceMode") and not await _is_admin(user):
            await send(user, "maintenance")
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
        # --- v2.2 birthdate registration callbacks (item 4) ---
        if data in ("bcal_jalali", "bcal_gregorian"):
            await s.commit()
            await begin_birthdate(user, "jalali" if data == "bcal_jalali" else "gregorian")
            return
        if data == "bcal_back":       # year page -> calendar choice
            await s.commit()
            await resend_birth_intro(user)
            return
        if data == "bmon_back":       # month page -> year page
            await s.commit()
            await resend_birth_year_page(user)
            return
        if data == "bday_back":       # day page -> month page
            await s.commit()
            await resend_birth_month_page(user)
            return
        if data == "bage_back":       # age confirm -> day page
            await s.commit()
            await resend_birth_day_page(user)
            return
        if data == "back_name":       # calendar choice -> re-enter display name
            user.state = {"step": "name"}
            await send(user, "ask_name")
            return
        if data.startswith("byear_"):
            await s.commit()
            await birth_year_chosen(user, int(data[6:]))
            return
        if data.startswith("bypage_"):
            await s.commit()
            page = max(0, int(data[7:]))
            state = user.state or {}
            user.state = {"step": "birth_year", "calendar": state.get("calendar", "jalali"),
                          "page": page}
            s.add(user)
            await s.commit()
            await send_birth_year_page(user, page)
            return
        if data.startswith("bmon_"):
            await s.commit()
            await birth_month_chosen(user, int(data[5:]))
            return
        if data.startswith("bday_"):
            await s.commit()
            await birth_day_chosen(user, int(data[5:]))
            return
        if data == "bage_ok":
            await s.commit()
            await birth_confirm_ok(user)
            return
        if data == "bage_redo":
            await s.commit()
            await birth_redo(user)
            return
        # legacy v2.1 keyboards: the profile no longer offers birthday registration
        if data == "set_birthday":
            await s.commit()
            await show_profile(user)
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
            await show_anon_links(user)
            return
        if data == "direct_msg":
            user.state = {"step": "direct_msg_target"}
            await send(user, "direct_msg_intro", keyboard=back_kb(user.language))
            return
        if data == "wallet":
            await s.commit()
            await show_wallet(user)
            return
        # v2.1: account linking removed — legacy buttons/links answer politely
        if data in ("link_account", "link_gen"):
            await s.commit()
            await legacy_link_expired(user)
            return
        # --- anonymous thread actions (v3) ---
        if data.startswith("anonrep_"):
            # v2.2 (item 2): "anonrep_<tid>" opens the reason menu,
            # "anonrep_<tid>_<reason>" files the report with that reason.
            marker = data[len("anonrep_"):]
            await s.commit()
            if "_" in marker:
                tid, _, reason = marker.partition("_")
                await anon_report(user, tid, reason)
            else:
                await ask_report_reason(user, "anon",
                                        int(marker) if marker.isdigit() else None)
            return
        if data.startswith("anonblk_"):
            marker = data[len("anonblk_"):]
            await s.commit()
            await anon_block(user, marker)
            return
        if data.startswith("anonr"):
            marker = data[len("anonr_"):]
            user.state = {"step": "anon_reply",
                          "thread": int(marker) if marker.isdigit() else None}
            await send(user, "anon_reply_prompt")
            return
        # --- v2.2 (item 2): chat report with reason selection ---
        if data == "chat_report":
            await s.commit()
            await ask_report_reason(user, "chat")
            return
        if data.startswith("report_") and data[7:] in ("bad_name", "abuse", "spam", "other"):
            await s.commit()
            await do_report(user, data[7:])
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
            await s.commit()
            await show_wallet(user)
            return
        if data == "store":
            await s.commit()
            await show_store(user)
            return
        if data == "custom_coins":
            # v2.2 (item 3): custom-amount coin purchase
            await s.commit()
            await start_custom_coins(user)
            return
        if data == "rewards":
            # v2.2 (item 7): rewards submenu (invite friends / daily bonus / home)
            await s.commit()
            await show_rewards(user)
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
