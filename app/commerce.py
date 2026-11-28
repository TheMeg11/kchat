"""Wallet-funded digital goods: immutable quotes, entitlements and bounded promos.

No gateway/card data, random rewards, auto-renewal or cash-out. Existing Stars /
Bale invoices buy coins; this catalog spends those coins with explicit consent.
"""

from datetime import timedelta
import math
import secrets
from sqlalchemy import select, func, or_
from . import db, service

DEFAULTS = {
    "enabled": True,
    "vip30": 300,
    "pass60": 20,
    "starters": 10,
    "games": 25,
    "theme_ocean": 15,
    "theme_garden": 15,
    "links": 20,
    "rose": 3,
    "coffee": 5,
    "book": 8,
    "reconnectToman": 1000,
    "firstBaseCoins": 100,
    "firstBonusPct": 50,
}
# Values are configurable COIN prices, not claims about live exchange rates.
PRODUCTS = {
    "vip30": ("⭐ VIP سی‌روزه", "⭐ 30-day VIP", "⭐ VIP на 30 дней"),
    "pass60": (
        "🎫 پاس پیام ۶۰ دقیقه‌ای",
        "🎫 60-minute message pass",
        "🎫 Пропуск на 60 минут",
    ),
    "starters": (
        "💡 بسته شروع مکالمه",
        "💡 Conversation starters",
        "💡 Темы для разговора",
    ),
    "games": (
        "🎮 دوز و چالش همکاری",
        "🎮 Tic-tac-toe + co-op",
        "🎮 Крестики-нолики + кооператив",
    ),
    "theme_ocean": ("🌊 قالب اقیانوس", "🌊 Ocean card", "🌊 Карточка Океан"),
    "theme_garden": ("🌿 قالب باغ", "🌿 Garden card", "🌿 Карточка Сад"),
    "links": (
        "🔗 شش لینک موضوعی",
        "🔗 Six topic links",
        "🔗 Шесть тематических ссылок",
    ),
    "rose": ("🌹 گل برای هم‌چت", "🌹 Rose for partner", "🌹 Роза собеседнику"),
    "coffee": ("☕ قهوه برای هم‌چت", "☕ Coffee for partner", "☕ Кофе собеседнику"),
    "book": ("📚 کتاب برای هم‌چت", "📚 Book for partner", "📚 Книга собеседнику"),
}
TIMED = {"vip30": timedelta(days=30), "pass60": timedelta(minutes=60)}
VISUAL = {"rose", "coffee", "book"}


class ShopError(ValueError):
    pass


def local(user, fa, en=None, ru=None):
    return {"fa": fa, "en": en or fa, "ru": ru or en or fa}.get(user.language, fa)


def name(user, sku):
    values = PRODUCTS[sku]
    return values[{"fa": 0, "en": 1, "ru": 2}.get(user.language, 0)]


async def config(s):
    return {**DEFAULTS, **((await service.get_setting(s, "monetization")) or {})}


async def lock_users(s, ids):
    return {
        u.id: u
        for u in (
            await s.execute(
                select(db.User)
                .where(db.User.id.in_(sorted(set(ids))))
                .order_by(db.User.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalars()
    }


async def profile(s, uid):
    # Caller owns the user row lock before creating/modifying a profile.
    p = await s.get(db.MemberProfile, uid)
    if p is None:
        p = db.MemberProfile(user_id=uid)
        s.add(p)
        await s.flush()
    return p


async def allowed_pair(s, a, b):
    if not a or not b or a.id == b.id or a.status != "active" or b.status != "active":
        return False
    if not a.accepted_rules_at or not b.accepted_rules_at or not a.age or not b.age:
        return False
    return not (
        await s.execute(
            select(db.Block.id)
            .where(
                or_(
                    (db.Block.blocker_id == a.id) & (db.Block.blocked_id == b.id),
                    (db.Block.blocker_id == b.id) & (db.Block.blocked_id == a.id),
                )
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def owns(s, uid, sku):
    keys = (
        [sku, "vip30"]
        if sku in {"starters", "games", "links", "theme_ocean", "theme_garden"}
        else [sku]
    )
    return (
        await s.execute(
            select(db.MemberGrant.id)
            .where(
                db.MemberGrant.user_id == uid,
                db.MemberGrant.sku.in_(keys),
                or_(
                    db.MemberGrant.expires_at.is_(None),
                    db.MemberGrant.expires_at > db.utcnow(),
                ),
            )
            .limit(1)
        )
    ).scalar_one_or_none() is not None


async def pass_active(s, uid):
    # VIP does not silently include unlimited messages. Only this explicit pass.
    return (
        await s.execute(
            select(db.MemberGrant.id)
            .where(
                db.MemberGrant.user_id == uid,
                db.MemberGrant.sku == "pass60",
                db.MemberGrant.expires_at > db.utcnow(),
            )
            .limit(1)
        )
    ).scalar_one_or_none() is not None


async def coupon_valid(s, code, uid, lock=False):
    query = select(db.PromoCode).where(db.PromoCode.code == code)
    if lock:
        query = query.with_for_update()
    promo = (await s.execute(query)).scalar_one_or_none()
    used = (
        await s.execute(
            select(db.PromoUse.id).where(
                db.PromoUse.code == code, db.PromoUse.user_id == uid
            )
        )
    ).scalar_one_or_none()
    if (
        not promo
        or not promo.active
        or promo.expires_at <= db.utcnow()
        or promo.used >= promo.max_uses
        or used
    ):
        raise ShopError("PROMO_INVALID")
    return promo


async def redeem(uid, code):
    code = code.strip().upper()
    if (
        not 1 <= len(code) <= 32
        or not code.isascii()
        or not code.replace("-", "").isalnum()
    ):
        raise ShopError("PROMO_INVALID")
    async with db.session_scope() as s:
        u = (await lock_users(s, [uid]))[uid]
        if u.status != "active" or not u.accepted_rules_at or not u.age:
            raise ShopError("UNAVAILABLE")
        promo = await coupon_valid(s, code, uid, lock=True)
        p = await profile(s, uid)
        if promo.gift_coins:
            promo.used += 1
            s.add(db.PromoUse(code=code, user_id=uid))
            await service.credit(
                s,
                u,
                promo.gift_coins,
                "promo_gift",
                "کد هدیه",
                idempotency_key=f"promo:{code}:{uid}",
            )
            return "gift", promo.gift_coins
        p.active_coupon = code
        return "discount", promo.percent


async def quote(uid, sku, recipient_id=None):
    if sku not in PRODUCTS:
        raise ShopError("NOT_FOUND")
    async with db.session_scope() as s:
        users = await lock_users(s, [uid, recipient_id or uid])
        u, target = users.get(uid), users.get(recipient_id or uid)
        cfg = await config(s)
        if not cfg["enabled"] or not u or u.status != "active":
            raise ShopError("UNAVAILABLE")
        if not target or target.status != "active":
            raise ShopError("UNAVAILABLE")
        detail = {}
        if target.id != uid:
            if sku not in TIMED and sku not in VISUAL:
                raise ShopError("NOT_GIFTABLE")
            if target.platform != u.platform or not await allowed_pair(s, u, target):
                raise ShopError("UNAVAILABLE")
            tp = await profile(s, target.id)
            if not tp.allow_gifts:
                raise ShopError("UNAVAILABLE")
            if sku in TIMED:
                chat = await service.active_chat_for(s, uid)
                friend = (
                    await s.execute(
                        select(db.Friend.id).where(
                            db.Friend.user_id == uid, db.Friend.friend_id == target.id
                        )
                    )
                ).scalar_one_or_none()
                if not friend and (
                    not chat or target.id not in (chat.user_a_id, chat.user_b_id)
                ):
                    raise ShopError("UNAVAILABLE")
            if sku in VISUAL:
                chat = await service.active_chat_for(s, uid)
                if not chat or target.id not in (chat.user_a_id, chat.user_b_id):
                    raise ShopError("STALE")
                detail["chat"] = chat.id
        elif sku in VISUAL:
            raise ShopError("NOT_GIFTABLE")
        if sku not in TIMED and sku not in VISUAL and await owns(s, target.id, sku):
            raise ShopError("OWNED")
        p = await profile(s, uid)
        cost = int(cfg[sku])
        if p.active_coupon:
            try:
                promo = await coupon_valid(s, p.active_coupon, uid)
                detail.update(coupon=promo.code, percent=promo.percent)
                cost = max(1, math.ceil(cost * (100 - promo.percent) / 100))
            except ShopError:
                p.active_coupon = None
        detail["base"] = int(cfg[sku])
        q = db.ShopQuote(
            id=secrets.token_urlsafe(16),
            payer_id=uid,
            recipient_id=target.id,
            sku=sku,
            coins=cost,
            detail=detail,
            expires_at=db.utcnow() + timedelta(minutes=5),
        )
        s.add(q)
        await s.flush()
        return q


async def purchase(uid, quote_id):
    async with db.session_scope() as s:
        q = (
            await s.execute(
                select(db.ShopQuote)
                .where(db.ShopQuote.id == quote_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not q or q.payer_id != uid:
            raise ShopError("STALE")
        if q.status in {"paid", "delivered"}:
            return q, False
        if q.status != "quoted" or q.expires_at <= db.utcnow():
            raise ShopError("STALE")
        users = await lock_users(s, [uid, q.recipient_id])
        u, target = users[uid], users[q.recipient_id]
        if (
            not (await config(s))["enabled"]
            or u.status != "active"
            or target.status != "active"
        ):
            raise ShopError("UNAVAILABLE")
        if target.id != uid and (
            not await allowed_pair(s, u, target)
            or not (await profile(s, target.id)).allow_gifts
        ):
            raise ShopError("UNAVAILABLE")
        if target.id != uid and q.sku in TIMED:
            chat = await service.active_chat_for(s, uid)
            friend = (
                await s.execute(
                    select(db.Friend.id).where(
                        db.Friend.user_id == uid, db.Friend.friend_id == target.id
                    )
                )
            ).scalar_one_or_none()
            if not friend and (
                not chat or target.id not in (chat.user_a_id, chat.user_b_id)
            ):
                raise ShopError("UNAVAILABLE")
        if q.sku in VISUAL:
            chat = await s.get(db.Chat, q.detail["chat"])
            if not chat or chat.status != "active":
                raise ShopError("STALE")
        elif q.sku not in TIMED and await owns(s, target.id, q.sku):
            raise ShopError("OWNED")
        promo = None
        if q.detail.get("coupon"):
            promo = await coupon_valid(s, q.detail["coupon"], uid, lock=True)
            if promo.percent != q.detail["percent"]:
                raise ShopError("PROMO_INVALID")
        if (
            await service.debit(
                s,
                u,
                q.coins,
                "shop_purchase",
                q.sku,
                reference_id=q.id,
                idempotency_key=f"shop:{q.id}",
            )
            is None
        ):
            raise ShopError("BALANCE")
        if promo:
            promo.used += 1
            s.add(db.PromoUse(code=promo.code, user_id=uid))
            (await profile(s, uid)).active_coupon = None
        q.status = "paid"
        if q.sku not in VISUAL:
            expiry = None
            if q.sku in TIMED:
                latest = (
                    await s.execute(
                        select(func.max(db.MemberGrant.expires_at)).where(
                            db.MemberGrant.user_id == target.id,
                            db.MemberGrant.sku == q.sku,
                        )
                    )
                ).scalar_one()
                expiry = max(latest or db.utcnow(), db.utcnow()) + TIMED[q.sku]
            s.add(
                db.MemberGrant(
                    user_id=target.id, sku=q.sku, purchase_id=q.id, expires_at=expiry
                )
            )
            if q.sku.startswith("theme_"):
                (await profile(s, target.id)).theme = q.sku[6:]
        return q, True


async def refund_visual(quote_id):
    async with db.session_scope() as s:
        q = (
            await s.execute(
                select(db.ShopQuote)
                .where(db.ShopQuote.id == quote_id)
                .with_for_update()
            )
        ).scalar_one()
        if q.sku not in VISUAL or q.status != "paid":
            return
        user = (await lock_users(s, [q.payer_id]))[q.payer_id]
        await service.credit(
            s,
            user,
            q.coins,
            "shop_refund",
            "هدیه تحویل نشد",
            idempotency_key=f"shop-refund:{q.id}",
        )
        q.status = "refunded"


async def create_topic(uid, label):
    if label not in {"questions", "feedback", "friends", "work", "ideas", "other"}:
        raise ShopError("NOT_FOUND")
    async with db.session_scope() as s:
        await lock_users(s, [uid])
        if not await owns(s, uid, "links"):
            raise ShopError("LOCKED")
        count = (
            await s.execute(
                select(func.count(db.TopicLink.id)).where(db.TopicLink.owner_id == uid)
            )
        ).scalar_one()
        if count >= 6:
            raise ShopError("LIMIT")
        item = db.TopicLink(id=secrets.token_urlsafe(12), owner_id=uid, label=label)
        s.add(item)
        await s.flush()
        return item


async def first_order(uid):
    """One immutable first-purchase invoice per payer, not per click/browser."""
    from . import orders

    async with db.session_scope() as s:
        user = (await lock_users(s, [uid]))[uid]
        if not (await config(s))["enabled"]:
            raise ShopError("UNAVAILABLE")
        if (
            await s.execute(
                select(db.Payment.id).where(db.Payment.user_id == uid).limit(1)
            )
        ).scalar_one_or_none():
            raise ShopError("FIRST_USED")
        p = await profile(s, uid)
        previous = (
            await s.get(db.InvoiceOrder, p.first_order) if p.first_order else None
        )
        if previous:
            if previous.paid_at:
                raise ShopError("FIRST_USED")
            previous.expires_at = db.utcnow() + timedelta(minutes=30)
            return previous
        cfg, rates = await config(s), await service.get_setting(s, "store")
        coins = cfg["firstBaseCoins"] * (100 + cfg["firstBonusPct"]) // 100
        amount = (
            service.coins_price_stars(rates, cfg["firstBaseCoins"])
            if user.platform == "telegram"
            else service.coins_price_toman(rates, cfg["firstBaseCoins"]) * 10
        )
        payload = await orders.create(s, user, coins, amount, package_id="first_offer")
        p.first_order = payload[6:]
        return await s.get(db.InvoiceOrder, p.first_order)


async def profile_card(s, viewer, owner, text):
    p = await s.get(db.MemberProfile, owner.id)
    if p and p.theme != "plain" and await owns(s, owner.id, "theme_" + p.theme):
        symbol = "🌊" if p.theme == "ocean" else "🌿"
        return f"{symbol} ━━━━━━━━━━ {symbol}\n{text}\n{symbol} ━━━━━━━━━━ {symbol}"
    return text
