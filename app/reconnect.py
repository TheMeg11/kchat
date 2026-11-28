"""One neutral reconnect request per ended chat; no paid unsolicited message.

Only the requester pays, after explicit two-sided consent. Delivery failures and
interrupted handshakes are compensated with idempotent ledger keys.
"""

from datetime import timedelta
import math
import secrets
from sqlalchemy import select, func, or_, text, delete
from . import db, service, commerce
from .config import RUNTIME
from .platform import PlatformError


async def eligible(s, old_id, requester_id):
    old = await s.get(db.Chat, old_id)
    if (
        not old
        or requester_id not in (old.user_a_id, old.user_b_id)
        or old.status != "ended"
        or old.end_reason not in {"left", "next"}
        or not old.ended_at
        or old.ended_at < db.utcnow() - timedelta(minutes=10)
    ):
        raise commerce.ShopError("UNAVAILABLE")
    other = old.user_b_id if requester_id == old.user_a_id else old.user_a_id
    users = await commerce.lock_users(s, [requester_id, other])
    if not await commerce.allowed_pair(s, users.get(requester_id), users.get(other)):
        raise commerce.ShopError("UNAVAILABLE")
    if not (await commerce.profile(s, other)).allow_reconnect:
        raise commerce.ShopError("UNAVAILABLE")
    if (
        await s.execute(
            select(db.Report.id).where(db.Report.chat_id == old_id).limit(1)
        )
    ).scalar_one_or_none():
        raise commerce.ShopError("UNAVAILABLE")
    newer = (
        await s.execute(
            select(db.Chat.id)
            .where(
                db.Chat.id > old_id,
                or_(db.Chat.user_a_id.in_(users), db.Chat.user_b_id.in_(users)),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if (
        newer
        or await service.active_chat_for(s, requester_id)
        or await service.active_chat_for(s, other)
    ):
        raise commerce.ShopError("UNAVAILABLE")
    return old, users[requester_id], users[other]


async def prepare(uid, old_id):
    async with db.session_scope() as s:
        if not (await commerce.config(s))["enabled"]:
            raise commerce.ShopError("UNAVAILABLE")
        old, u, target = await eligible(s, old_id, uid)
        r = (
            await s.execute(
                select(db.ReconnectRequest).where(
                    db.ReconnectRequest.old_chat_id == old_id
                )
            )
        ).scalar_one_or_none()
        if r:
            if (
                r.requester_id == uid
                and r.status == "quoted"
                and r.expires_at > db.utcnow()
            ):
                return r
            raise commerce.ShopError("UNAVAILABLE")
        rates = await service.get_setting(s, "store")
        cost = max(
            1,
            math.ceil(
                (await commerce.config(s))["reconnectToman"]
                / max(1, rates["tomanPerCoin"])
            ),
        )
        r = db.ReconnectRequest(
            id=secrets.token_hex(8),
            old_chat_id=old_id,
            requester_id=uid,
            target_id=target.id,
            coins=cost,
            toman_equivalent=cost * max(1, rates["tomanPerCoin"]),
            status="quoted",
            expires_at=db.utcnow() + timedelta(minutes=3),
        )
        s.add(r)
        await s.flush()
        return r


async def confirm(uid, rid):
    async with db.session_scope() as s:
        r = (
            await s.execute(
                select(db.ReconnectRequest)
                .where(db.ReconnectRequest.id == rid)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if (
            not r
            or r.requester_id != uid
            or r.status != "quoted"
            or r.expires_at <= db.utcnow()
        ):
            raise commerce.ShopError("STALE")
        _, user, target = await eligible(s, r.old_chat_id, uid)
        if not (await commerce.config(s))["enabled"]:
            raise commerce.ShopError("UNAVAILABLE")
        recent = (
            await s.execute(
                select(func.count(db.ReconnectRequest.id)).where(
                    db.ReconnectRequest.requester_id == uid,
                    db.ReconnectRequest.status != "quoted",
                    db.ReconnectRequest.created_at
                    > db.utcnow() - timedelta(minutes=10),
                )
            )
        ).scalar_one()
        if recent >= 3:
            raise commerce.ShopError("LIMIT")
        if user.coin_balance < r.coins:
            raise commerce.ShopError("BALANCE")
        r.status = "pending"
        r.expires_at = db.utcnow() + timedelta(minutes=2)
    adp = RUNTIME.get(f"adapter_{target.platform}")
    L = lambda fa, en, ru=None: commerce.local(target, fa, en, ru)
    try:
        if not adp:
            raise PlatformError("sendMessage", 503, "unavailable")
        await adp.send_message(
            target.platform_user_id,
            L(
                "↩️ هم‌چت قبلی یک درخواست اتصال دوباره فرستاده است.\nقبول یا رد کاملاً اختیاری است؛ برای تو رایگان است.\nبا قبول، جستجوی فعلی متوقف می‌شود. اعتبار: ۲ دقیقه.",
                "↩️ Your previous partner requests a reconnect. Accept or decline freely; free for you. Accepting stops your current search. Expires in 2 minutes.",
                "↩️ Предыдущий собеседник просит соединиться снова. Можно отказаться; для вас бесплатно. Принятие остановит поиск. Срок: 2 минуты.",
            ),
            keyboard=[
                [
                    {
                        "text": L("✅ قبول", "✅ Accept", "✅ Принять"),
                        "callback_data": f"m:ra:{rid}",
                    },
                    {
                        "text": L("❌ رد", "❌ Decline", "❌ Отклонить"),
                        "callback_data": f"m:rd:{rid}",
                    },
                ]
            ],
        )
    except PlatformError:
        async with db.session_scope() as s:
            r = await s.get(db.ReconnectRequest, rid)
            if r.status == "pending":
                r.status = "unavailable"
        raise commerce.ShopError("UNAVAILABLE")


async def decide(uid, rid, accept):
    async with db.session_scope() as s:
        # Same mutex as normal matching; do not race queue consumption.
        if s.bind.dialect.name == "postgresql":
            await s.execute(text("SELECT pg_advisory_xact_lock(71389202)"))
        r = (
            await s.execute(
                select(db.ReconnectRequest)
                .where(db.ReconnectRequest.id == rid)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if (
            not r
            or r.target_id != uid
            or r.status != "pending"
            or r.expires_at <= db.utcnow()
        ):
            raise commerce.ShopError("STALE")
        if not accept:
            r.status = "declined"
            return None
        old, user, target = await eligible(s, r.old_chat_id, r.requester_id)
        if not (await commerce.config(s))["enabled"]:
            raise commerce.ShopError("UNAVAILABLE")
        if (
            await service.debit(
                s,
                user,
                r.coins,
                "reconnect_cost",
                "اتصال مجدد با رضایت دوطرفه",
                idempotency_key=f"reconnect:{rid}",
            )
            is None
        ):
            raise commerce.ShopError("BALANCE")
        await s.execute(
            delete(db.QueueEntry).where(db.QueueEntry.user_id.in_([user.id, target.id]))
        )
        chat = db.Chat(
            user_a_id=min(user.id, target.id),
            user_b_id=max(user.id, target.id),
            cross_platform=user.platform != target.platform,
            age_group=old.age_group,
            status="active",
        )
        s.add(chat)
        await s.flush()
        r.new_chat_id, r.status = chat.id, "connecting"
        r.expires_at = db.utcnow() + timedelta(minutes=2)
        user.total_chats += 1
        target.total_chats += 1
        people = [user, target]
    from . import bot

    delivered = []
    for person in people:
        delivered.append(
            await bot.send_controls(
                person,
                chat.id,
                text=commerce.local(
                    person,
                    "↩️ اتصال دوباره با رضایت هر دو برقرار شد.",
                    "↩️ Reconnected by mutual consent.",
                    "↩️ Соединение восстановлено по взаимному согласию.",
                ),
            )
        )
    await finish(rid, all(delivered))
    return chat.id if all(delivered) else None


async def finish(rid, success):
    async with db.session_scope() as s:
        r = (
            await s.execute(
                select(db.ReconnectRequest)
                .where(db.ReconnectRequest.id == rid)
                .with_for_update()
            )
        ).scalar_one()
        if r.status != "connecting":
            return
        chat = await s.get(db.Chat, r.new_chat_id)
        if success:
            r.status = "connected"
            return
        if chat and chat.status == "active":
            await service.end_chat(s, chat, "delivery_failed")
        user = (await commerce.lock_users(s, [r.requester_id]))[r.requester_id]
        await service.credit(
            s,
            user,
            r.coins,
            "reconnect_refund",
            "اتصال مجدد ناموفق",
            idempotency_key=f"reconnect-refund:{rid}",
        )
        r.status = "refunded"
    from . import bot

    for uid in (r.requester_id, r.target_id):
        async with db.session_scope() as s:
            person = await s.get(db.User, uid)
        await bot.retire_controls(person, r.new_chat_id)
        await bot.send(person, "not_in_chat", keyboard=bot.search_cta(person.language))


async def recover():
    # A process may stop between debit and the two delivery acknowledgements.
    async with db.session_scope() as s:
        rows = list(
            (
                await s.execute(
                    select(db.ReconnectRequest)
                    .where(
                        db.ReconnectRequest.status == "connecting",
                        db.ReconnectRequest.expires_at < db.utcnow(),
                    )
                    .limit(20)
                )
            ).scalars()
        )
    for r in rows:
        await finish(r.id, False)
    async with db.session_scope() as s:
        stale = list(
            (
                await s.execute(
                    select(db.ShopQuote.id)
                    .where(
                        db.ShopQuote.sku.in_(commerce.VISUAL),
                        db.ShopQuote.status == "paid",
                        db.ShopQuote.created_at < db.utcnow() - timedelta(minutes=10),
                    )
                    .limit(20)
                )
            ).scalars()
        )
    for quote_id in stale:
        await commerce.refund_visual(quote_id)
