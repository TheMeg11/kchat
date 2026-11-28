"""Monetization acceptance/security tests, using isolated synthetic users only."""

from datetime import timedelta
import pytest
from sqlalchemy import select, func
from test_all import clean_db, adapters, mk_user
from test_hardening import client
from app import (
    db,
    service,
    orders,
    commerce as C,
    shop_bot,
    social_games,
    reconnect,
    bot,
)
from app.platform import PlatformError

pytestmark = pytest.mark.asyncio


async def pair(ended=False):
    a = await mk_user(pid="71001", coins=2000)
    b = await mk_user(pid="71002", coins=100)
    async with db.session_scope() as s:
        chat = db.Chat(
            user_a_id=a.id,
            user_b_id=b.id,
            age_group="18-60",
            status="ended" if ended else "active",
            end_reason="left" if ended else None,
            ended_at=db.utcnow() if ended else None,
        )
        s.add(chat)
        await s.flush()
        return a, b, chat.id


async def balance(uid):
    async with db.session_scope() as s:
        return (await s.get(db.User, uid)).coin_balance


async def buy(uid, sku, target=None):
    q = await C.quote(uid, sku, target)
    return await C.purchase(uid, q.id)


async def test_vip_quote_confirmation_idempotency_stacking_and_expiry():
    u = await mk_user(coins=2000)
    q = await C.quote(u.id, "vip30")
    assert await balance(u.id) == 2000
    first, fresh = await C.purchase(u.id, q.id)
    assert fresh and await balance(u.id) == 1700
    assert not (await C.purchase(u.id, q.id))[1]
    async with db.session_scope() as s:
        assert await C.owns(s, u.id, "games") and await C.owns(s, u.id, "links")
        assert not await C.pass_active(s, u.id)  # No hidden unlimited-message promise.
        assert (
            await s.execute(select(func.count(db.MemberGrant.id)))
        ).scalar_one() == 1
    await buy(u.id, "vip30")
    async with db.session_scope() as s:
        expiry = (
            await s.execute(select(func.max(db.MemberGrant.expires_at)))
        ).scalar_one()
        assert expiry > db.utcnow() + timedelta(days=59)
        grants = list((await s.execute(select(db.MemberGrant))).scalars())
        for grant in grants:
            grant.expires_at = db.utcnow() - timedelta(seconds=1)
    async with db.session_scope() as s:
        assert not await C.owns(s, u.id, "games")


async def test_quote_ownership_expiry_price_snapshot_and_no_negative_balance():
    u = await mk_user(coins=1)
    other = await mk_user(pid="200")
    q = await C.quote(u.id, "vip30")
    with pytest.raises(C.ShopError, match="STALE"):
        await C.purchase(other.id, q.id)
    with pytest.raises(C.ShopError, match="BALANCE"):
        await C.purchase(u.id, q.id)
    assert await balance(u.id) == 1
    async with db.session_scope() as s:
        await service.credit(s, await s.get(db.User, u.id), 500, "test")
        cfg = dict(C.DEFAULTS, vip30=900)
        await service.set_setting(s, "monetization", cfg)
    await C.purchase(u.id, q.id)
    assert await balance(u.id) == 201  # Honor quoted 300, not new price 900.
    stale = await C.quote(u.id, "pass60")
    async with db.session_scope() as s:
        (await s.get(db.ShopQuote, stale.id)).expires_at = db.utcnow() - timedelta(
            seconds=1
        )
    with pytest.raises(C.ShopError, match="STALE"):
        await C.purchase(u.id, stale.id)


async def test_discount_real_quota_expiry_and_single_use():
    a = await mk_user(coins=1000)
    b = await mk_user(pid="200", coins=1000)
    async with db.session_scope() as s:
        s.add(
            db.PromoCode(
                code="TEN",
                percent=10,
                max_uses=1,
                expires_at=db.utcnow() + timedelta(days=1),
            )
        )
    assert await C.redeem(a.id, "ten") == ("discount", 10)
    await C.redeem(b.id, "TEN")
    qa = await C.quote(a.id, "vip30")
    qb = await C.quote(b.id, "vip30")
    assert qa.coins == 270 and await balance(a.id) == 1000
    await C.purchase(a.id, qa.id)
    with pytest.raises(C.ShopError, match="PROMO_INVALID"):
        await C.purchase(b.id, qb.id)
    assert await balance(b.id) == 1000
    with pytest.raises(C.ShopError, match="PROMO_INVALID"):
        await C.redeem(a.id, "TEN")


async def test_gift_code_credits_once_and_never_cash_out():
    a = await mk_user(coins=0)
    async with db.session_scope() as s:
        s.add(
            db.PromoCode(
                code="GIFT",
                gift_coins=7,
                max_uses=1,
                expires_at=db.utcnow() + timedelta(days=1),
            )
        )
    assert await C.redeem(a.id, "GIFT") == ("gift", 7)
    assert await balance(a.id) == 7
    with pytest.raises(C.ShopError):
        await C.redeem(a.id, "GIFT")
    async with db.session_scope() as s:
        assert (await s.execute(select(func.count(db.PromoUse.id)))).scalar_one() == 1


async def test_first_purchase_invoice_is_single_and_settles_once():
    a = await mk_user(coins=0)
    one = await C.first_order(a.id)
    two = await C.first_order(a.id)
    assert (
        one.id == two.id
        and one.coins == 150
        and one.amount == 5
        and one.currency == "XTR"
    )
    msg = {
        "from": {"id": int(a.platform_user_id)},
        "successful_payment": {
            "invoice_payload": "order:" + one.id,
            "currency": "XTR",
            "total_amount": 5,
            "telegram_payment_charge_id": "first-charge",
        },
    }
    assert await orders.settle("telegram", msg)
    assert await orders.settle("telegram", msg) is None
    assert await balance(a.id) == 150
    with pytest.raises(C.ShopError, match="FIRST_USED"):
        await C.first_order(a.id)


async def test_gift_membership_uses_recipient_and_respects_block_and_opt_out():
    a, b, cid = await pair()
    q, fresh = await buy(a.id, "vip30", b.id)
    assert fresh and await balance(a.id) == 1700 and await balance(b.id) == 100
    async with db.session_scope() as s:
        assert await C.owns(s, b.id, "games") and not await C.owns(s, a.id, "vip30")
        s.add(db.Block(blocker_id=b.id, blocked_id=a.id))
    with pytest.raises(C.ShopError, match="UNAVAILABLE"):
        await C.quote(a.id, "pass60", b.id)


async def test_visual_gift_failure_refunds_once(adapters):
    tg, _ = adapters
    a, b, cid = await pair()
    q, fresh = await buy(a.id, "rose", b.id)
    assert await balance(a.id) == 1997
    original = tg.send_message

    async def fail(chat_id, *args, **kwargs):
        if str(chat_id) == b.platform_user_id:
            raise PlatformError("sendMessage", 503, "test-failure")
        return await original(chat_id, *args, **kwargs)

    tg.send_message = fail
    await shop_bot.deliver_purchase(a, q, fresh)
    await C.refund_visual(q.id)
    assert await balance(a.id) == 2000
    async with db.session_scope() as s:
        assert (await s.get(db.ShopQuote, q.id)).status == "refunded"


async def test_topic_links_unlock_limit_owner_scoping_and_revocation(adapters):
    a, b, cid = await pair()
    with pytest.raises(C.ShopError, match="LOCKED"):
        await C.create_topic(a.id, "work")
    await buy(a.id, "links")
    links = [await C.create_topic(a.id, key) for key in shop_bot.TOPICS]
    with pytest.raises(C.ShopError, match="LIMIT"):
        await C.create_topic(a.id, "work")
    await shop_bot.handle(b, "m:unlink:" + links[0].id)
    async with db.session_scope() as s:
        assert await s.get(db.TopicLink, links[0].id)
    await bot.cmd_start(b, "t" + links[0].id)
    async with db.session_scope() as s:
        state = (await s.get(db.User, b.id)).state
        assert state["topic"] == links[0].id and state["target"] == a.id
    await shop_bot.handle(a, "m:unlink:" + links[0].id)
    async with db.session_scope() as s:
        assert await s.get(db.TopicLink, links[0].id) is None


async def test_card_preview_free_and_theme_applies_only_with_access(adapters):
    a = await mk_user(coins=100)
    await shop_bot.preview(a, "theme_ocean")
    assert await balance(a.id) == 100
    async with db.session_scope() as s:
        assert (await s.execute(select(func.count(db.ShopQuote.id)))).scalar_one() == 0
    await buy(a.id, "theme_ocean")
    async with db.session_scope() as s:
        assert "🌊" in await C.profile_card(s, a, a, "sample")
    with pytest.raises(C.ShopError, match="OWNED"):
        await C.quote(a.id, "theme_ocean")


async def test_pass_zero_message_cost_only_until_expiry(adapters):
    a, b, cid = await pair()
    await buy(a.id, "pass60")
    before = await balance(a.id)
    await bot.process_update(
        "telegram",
        {
            "update_id": 88101,
            "message": {
                "message_id": 11,
                "chat": {"id": 71001},
                "from": {"id": 71001},
                "text": "سلام دوست خوب",
            },
        },
    )
    assert await balance(a.id) == before
    async with db.session_scope() as s:
        grant = (
            await s.execute(
                select(db.MemberGrant).where(db.MemberGrant.sku == "pass60")
            )
        ).scalar_one()
        grant.expires_at = db.utcnow() - timedelta(seconds=1)
    await bot.process_update(
        "telegram",
        {
            "update_id": 88102,
            "message": {
                "message_id": 12,
                "chat": {"id": 71001},
                "from": {"id": 71001},
                "text": "امروز چطوری",
            },
        },
    )
    assert await balance(a.id) == before - 1


async def test_tictactoe_consent_board_turns_replay_and_win():
    a, b, cid = await pair()
    outsider = await mk_user(pid="300")
    gid = await social_games.invite(a.id, "ttt")
    with pytest.raises(C.ShopError):
        await social_games.act(a.id, gid, 0, "accept")
    with pytest.raises(C.ShopError):
        await social_games.act(outsider.id, gid, 0, "accept")
    await social_games.act(b.id, gid, 0, "accept")
    async with db.session_scope() as s:
        g = await s.get(db.SocialGame, gid)
        chat = await s.get(db.Chat, cid)
        _, board = social_games.render(g, chat, a)
        assert len(board[:3]) == 3 and all(len(row) == 3 for row in board[:3])
        assert all(
            len(button["callback_data"].encode()) <= 64
            for row in board
            for button in row
        )
    with pytest.raises(C.ShopError, match="MOVE"):
        await social_games.act(b.id, gid, 1, "0")
    await social_games.act(a.id, gid, 1, "0")
    with pytest.raises(C.ShopError, match="STALE"):
        await social_games.act(a.id, gid, 1, "1")
    for uid, version, cell in (
        (b.id, 2, "3"),
        (a.id, 3, "1"),
        (b.id, 4, "4"),
        (a.id, 5, "2"),
    ):
        await social_games.act(uid, gid, version, cell)
    async with db.session_scope() as s:
        g = await s.get(db.SocialGame, gid)
        assert g.status == "won" and g.data["winner"] == a.id
    with pytest.raises(C.ShopError, match="LOCKED"):
        await social_games.invite(a.id, "ttt")
    assert await balance(a.id) == 2000


async def test_cooperative_challenge_clues_and_guests_free():
    a, b, cid = await pair()
    gid = await social_games.invite(a.id, "coop")
    await social_games.act(b.id, gid, 0, "accept")
    for uid, version, answer in ((b.id, 1, "5"), (a.id, 2, "6"), (b.id, 3, "4")):
        await social_games.act(uid, gid, version, answer)
    async with db.session_scope() as s:
        g = await s.get(db.SocialGame, gid)
        assert g.status == "won" and g.data["round"] == 3
    assert await balance(b.id) == 100 and await balance(a.id) == 2000


async def test_old_game_cannot_act_on_ended_or_new_chat():
    a, b, cid = await pair()
    gid = await social_games.invite(a.id, "ttt")
    async with db.session_scope() as s:
        await service.end_chat(s, await s.get(db.Chat, cid), "left", a.id)
    with pytest.raises(C.ShopError, match="STALE"):
        await social_games.act(b.id, gid, 0, "accept")


async def test_reconnect_decline_no_charge_no_repeat_request(adapters):
    a, b, cid = await pair(ended=True)
    r = await reconnect.prepare(a.id, cid)
    assert r.coins == 5 and r.toman_equivalent == 1000 and await balance(a.id) == 2000
    await reconnect.confirm(a.id, r.id)
    assert await balance(a.id) == 2000
    assert await reconnect.decide(b.id, r.id, False) is None
    assert await balance(a.id) == 2000
    with pytest.raises(C.ShopError):
        await reconnect.prepare(a.id, cid)


async def test_reconnect_accept_only_target_pays_requester_once(adapters):
    a, b, cid = await pair(ended=True)
    outsider = await mk_user(pid="300")
    r = await reconnect.prepare(a.id, cid)
    await reconnect.confirm(a.id, r.id)
    with pytest.raises(C.ShopError):
        await reconnect.decide(outsider.id, r.id, True)
    new = await reconnect.decide(b.id, r.id, True)
    assert (
        new
        and new != cid
        and await balance(a.id) == 1995
        and await balance(b.id) == 100
    )
    with pytest.raises(C.ShopError):
        await reconnect.decide(b.id, r.id, True)
    assert await balance(a.id) == 1995


async def test_reconnect_failure_compensated_and_not_an_active_chat(
    adapters, monkeypatch
):
    a, b, cid = await pair(ended=True)
    r = await reconnect.prepare(a.id, cid)
    await reconnect.confirm(a.id, r.id)

    async def fail(*args, **kwargs):
        return False

    monkeypatch.setattr(bot, "send_controls", fail)
    assert await reconnect.decide(b.id, r.id, True) is None
    assert await balance(a.id) == 2000
    async with db.session_scope() as s:
        assert (await s.get(db.ReconnectRequest, r.id)).status == "refunded"
        assert await service.active_chat_for(s, a.id) is None


@pytest.mark.parametrize("reason", ["block", "report", "expired", "optout", "newchat"])
async def test_reconnect_never_bypasses_safety_or_staleness(reason):
    a, b, cid = await pair(ended=True)
    async with db.session_scope() as s:
        if reason == "block":
            s.add(db.Block(blocker_id=b.id, blocked_id=a.id))
        elif reason == "report":
            s.add(
                db.Report(
                    chat_id=cid, reporter_id=b.id, reported_id=a.id, reason="spam"
                )
            )
        elif reason == "expired":
            (await s.get(db.Chat, cid)).ended_at = db.utcnow() - timedelta(minutes=11)
        elif reason == "optout":
            (await C.profile(s, b.id)).allow_reconnect = False
        else:
            s.add(
                db.Chat(
                    user_a_id=a.id,
                    user_b_id=b.id,
                    age_group="18-60",
                    status="ended",
                    ended_at=db.utcnow(),
                )
            )
    with pytest.raises(C.ShopError, match="UNAVAILABLE"):
        await reconnect.prepare(a.id, cid)
    assert await balance(a.id) == 2000


async def test_admin_prices_promos_permissions_and_csrf():
    async with client("support") as c:
        assert (await c.get("/api/admin/v1/monetization")).status_code == 403
    # Reuse the same account with an explicit role change for the owner actions.
    async with db.session_scope() as s:
        admin = (await s.execute(select(db.WebAdmin))).scalar_one()
        admin.role = "owner"
    import httpx
    from app.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        auth = await c.post(
            "/api/admin/v1/auth/login",
            json={"username": "hardening", "password": "OwnerPassword123!"},
        )
        headers = {"X-CSRF-Token": auth.headers["x-csrf-token"]}
        cfg = (await c.get("/api/admin/v1/monetization")).json()["settings"]
        cfg["reconnectToman"] = 1200
        assert (await c.put("/api/admin/v1/monetization", json=cfg)).status_code == 403
        assert (
            await c.put("/api/admin/v1/monetization", json=cfg, headers=headers)
        ).status_code == 200
        assert (await c.get("/api/admin/v1/monetization")).json()["settings"][
            "reconnectToman"
        ] == 1200
        promo = {
            "code": "VALID",
            "percent": 15,
            "max_uses": 10,
            "expires_at": (db.utcnow() + timedelta(days=1)).isoformat() + "Z",
        }
        assert (
            await c.post(
                "/api/admin/v1/monetization/promos", json=promo, headers=headers
            )
        ).status_code == 200
        assert (
            await c.post(
                "/api/admin/v1/monetization/promos", json=promo, headers=headers
            )
        ).status_code == 409
        promo.update(code="INVALID", gift_coins=10)
        assert (
            await c.post(
                "/api/admin/v1/monetization/promos", json=promo, headers=headers
            )
        ).status_code == 422
        assert (
            await c.delete("/api/admin/v1/monetization/promos/VALID", headers=headers)
        ).status_code == 200


async def test_purge_and_deletion_clear_new_chat_dependencies():
    a, b, cid = await pair(ended=True)
    r = await reconnect.prepare(a.id, cid)
    async with db.session_scope() as s:
        s.add(
            db.SocialGame(
                id="test-game",
                chat_id=cid,
                host_id=a.id,
                kind="ttt",
                expires_at=db.utcnow(),
            )
        )
        await service.delete_account(s, await s.get(db.User, a.id))
    async with db.session_scope() as s:
        assert await s.get(db.ReconnectRequest, r.id) is None
        assert await s.get(db.SocialGame, "test-game") is None
        assert (await s.get(db.User, a.id)).status == "deleted"


async def test_recovery_after_interrupted_paid_delivery(adapters):
    a, b, cid = await pair(ended=True)
    r = await reconnect.prepare(a.id, cid)
    async with db.session_scope() as s:
        rdb = await s.get(db.ReconnectRequest, r.id)
        await service.debit(s, await s.get(db.User, a.id), rdb.coins, "reconnect_cost")
        new = db.Chat(user_a_id=a.id, user_b_id=b.id, age_group="18-60")
        s.add(new)
        await s.flush()
        rdb.new_chat_id = new.id
        rdb.status = "connecting"
        rdb.expires_at = db.utcnow() - timedelta(seconds=1)
    await reconnect.recover()
    await reconnect.recover()
    assert await balance(a.id) == 2000
    async with db.session_scope() as s:
        assert await service.active_chat_for(s, a.id) is None


async def test_visual_interrupted_delivery_recovers_without_double_credit(adapters):
    a, b, cid = await pair()
    q, _ = await buy(a.id, "coffee", b.id)
    async with db.session_scope() as s:
        (await s.get(db.ShopQuote, q.id)).created_at = db.utcnow() - timedelta(
            minutes=11
        )
    await reconnect.recover()
    await reconnect.recover()
    assert await balance(a.id) == 2000


async def test_gift_recipient_optout_rechecked_at_confirmation():
    a, b, cid = await pair()
    q = await C.quote(a.id, "pass60", b.id)
    async with db.session_scope() as s:
        (await C.profile(s, b.id)).allow_gifts = False
    with pytest.raises(C.ShopError, match="UNAVAILABLE"):
        await C.purchase(a.id, q.id)
    assert await balance(a.id) == 2000


@pytest.mark.parametrize("lang", ["fa", "en", "ru"])
async def test_navigation_samples_no_charge_all_languages(adapters, lang):
    a = await mk_user(coins=500, lang=lang)
    for action in [
        "m:home",
        "m:cat:vip",
        "m:cat:fun",
        "m:cat:identity",
        "m:item:vip30",
        "m:preview:games",
        "m:preview:starters",
        "m:preview:theme_ocean",
        "m:preferences",
        "m:links",
    ]:
        await shop_bot.handle(a, action)
    assert await balance(a.id) == 500
    async with db.session_scope() as s:
        assert (await s.execute(select(func.count(db.ShopQuote.id)))).scalar_one() == 0
    tg, _ = adapters
    assert all(
        len(b.get("callback_data", "").encode()) <= 64
        for _, _, keyboard in tg.sent
        if keyboard
        for row in keyboard
        for b in row
    )


async def test_new_callbacks_registration_maintenance_and_ban_gates(adapters):
    a = await mk_user(coins=500)

    async def callback(data, n):
        await bot.process_update(
            "telegram",
            {
                "update_id": 88000 + n,
                "callback_query": {
                    "id": str(n),
                    "from": {"id": 100},
                    "message": {"chat": {"id": 100}},
                    "data": data,
                },
            },
        )

    async with db.session_scope() as s:
        (await s.get(db.User, a.id)).age = 0
    await callback("m:quote:vip30", 1)
    async with db.session_scope() as s:
        assert (await s.execute(select(func.count(db.ShopQuote.id)))).scalar_one() == 0
        (await s.get(db.User, a.id)).age = 25
        app = await service.get_setting(s, "app")
        app["maintenanceMode"] = True
        await service.set_setting(s, "app", app)
    await callback("m:quote:vip30", 2)
    async with db.session_scope() as s:
        assert (await s.execute(select(func.count(db.ShopQuote.id)))).scalar_one() == 0
        app["maintenanceMode"] = False
        await service.set_setting(s, "app", app)
        (await s.get(db.User, a.id)).status = "banned"
    await callback("m:quote:vip30", 3)
    assert await balance(a.id) == 500


async def test_promo_entry_can_exit_to_command_or_other_menu(adapters):
    a = await mk_user(coins=100)
    await shop_bot.handle(a, "m:promo")
    await bot.process_update(
        "telegram",
        {
            "update_id": 88701,
            "message": {
                "message_id": 1,
                "chat": {"id": 100},
                "from": {"id": 100},
                "text": "/wallet",
            },
        },
    )
    async with db.session_scope() as s:
        assert not (await s.get(db.User, a.id)).state


async def test_deleting_recipient_compensates_pending_gift_to_payer():
    a, b, cid = await pair()
    q, _ = await buy(a.id, "rose", b.id)
    assert await balance(a.id) == 1997
    async with db.session_scope() as s:
        await service.delete_account(s, await s.get(db.User, b.id))
    assert await balance(a.id) == 2000 and await balance(b.id) == 0
    await C.refund_visual(q.id)
    assert await balance(a.id) == 2000


async def test_opening_store_cancels_invisible_compose_flow(adapters):
    a = await mk_user(coins=100)
    async with db.session_scope() as s:
        (await s.get(db.User, a.id)).state = {"step": "anon_msg", "target": 999}
    await bot.process_update(
        "telegram",
        {
            "update_id": 88991,
            "callback_query": {
                "id": "shop",
                "from": {"id": 100},
                "message": {"chat": {"id": 100}},
                "data": "store",
            },
        },
    )
    async with db.session_scope() as s:
        assert (await s.get(db.User, a.id)).state is None


async def test_game_boards_recreated_when_deleted_without_resetting_game(
    adapters, monkeypatch
):
    tg, _ = adapters
    a, b, cid = await pair()
    gid = await social_games.invite(a.id, "ttt")
    await social_games.publish(gid)
    async with db.session_scope() as s:
        g = await s.get(db.SocialGame, gid)
        assert g.message_a and g.message_b
    await social_games.act(b.id, gid, 0, "accept")

    async def missing(*args, **kwargs):
        raise PlatformError(
            "editMessageText", 400, "Bad Request: message to edit not found"
        )

    monkeypatch.setattr(tg, "edit_message", missing, raising=False)
    await social_games.publish(gid)
    async with db.session_scope() as s:
        g = await s.get(db.SocialGame, gid)
        assert g.version == 1 and g.status == "playing"
        assert g.message_a > 2 and g.message_b > 2
    await social_games.publish("already-deleted")
