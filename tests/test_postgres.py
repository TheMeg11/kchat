"""Real PostgreSQL only. Use `npm run test:postgres` for a disposable database."""

import asyncio
import os
from datetime import timedelta
import pytest
import pytest_asyncio
from sqlalchemy import select, text, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from app import db, service, orders, backup
from app.config import settings, RUNTIME
from app.migrations import migrate

URL = os.getenv("TEST_PG_URL", "")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not URL, reason="Set TEST_PG_URL to an isolated *_test database"
    ),
]


@pytest_asyncio.fixture(autouse=True)
async def isolated(monkeypatch):
    if not URL:
        yield
        return
    if not URL.rsplit("/", 1)[-1].endswith("_test"):
        raise RuntimeError("Postgres tests require a database name ending in _test")
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()
    db.init_engine(URL)
    await db.create_all(db.get_engine())
    await migrate(db.get_engine())
    await migrate(db.get_engine())
    async with db.session_scope() as s:
        await service.seed(s)
    monkeypatch.setattr(settings, "backup_password", "pg-backup-password-1234567890")
    RUNTIME.pop("restore_in_progress", None)
    RUNTIME.pop("backup_in_progress", None)
    yield
    await db.dispose_engine()


async def person(pid="1", coins=100):
    async with db.session_scope() as s:
        user = db.User(
            platform="telegram",
            platform_user_id=pid,
            display_name="Test",
            age=25,
            gender="male",
            coin_balance=coins,
        )
        s.add(user)
        await s.flush()
        return user


async def test_concurrent_wallet_operations_and_idempotency():
    u = await person()

    async def credit(i):
        async with db.session_scope() as s:
            stale = await s.get(db.User, u.id)
            await asyncio.sleep(0.001)
            await service.credit(s, stale, 5, "test", idempotency_key=f"credit:{i}")

    await asyncio.gather(*(credit(i % 20) for i in range(40)))

    async def debit(i):
        async with db.session_scope() as s:
            stale = await s.get(db.User, u.id)
            return (
                await service.debit(s, stale, 30, "test", idempotency_key=f"debit:{i}")
                is not None
            )

    results = await asyncio.gather(*(debit(i) for i in range(10)))
    async with db.session_scope() as s:
        user = await s.get(db.User, u.id)
        assert user.coin_balance == 20 and sum(results) == 6
        assert (
            await s.execute(select(func.count(db.CoinTransaction.id)))
        ).scalar_one() == 26
        assert (
            await s.execute(select(func.count()).select_from(text("schema_migrations")))
        ).scalar_one() == 2


async def test_matching_concurrency_and_cross_position_constraint():
    users = [await person(str(i)) for i in range(12)]
    async with db.session_scope() as s:
        for user in users:
            await service.enqueue(s, user, "everyone")

    async def match():
        async with db.session_scope() as s:
            return await service.try_match(s)

    for _ in range(6):
        await asyncio.gather(*(match() for _ in range(4)))
    async with db.session_scope() as s:
        chats = list((await s.execute(select(db.Chat))).scalars())
        ids = [uid for chat in chats for uid in (chat.user_a_id, chat.user_b_id)]
        assert len(chats) == 6 and len(set(ids)) == 12
    # Existing A may not be inserted as B in a different active conversation.
    outsider = await person("99")
    with pytest.raises(IntegrityError):
        async with db.session_scope() as s:
            s.add(
                db.Chat(
                    user_a_id=outsider.id,
                    user_b_id=chats[0].user_a_id,
                    age_group="18-60",
                )
            )


async def test_concurrent_payment_settlement():
    user = await person()
    async with db.session_scope() as s:
        payload = await orders.create(s, user, 20, 1)
    msg = {
        "from": {"id": 1},
        "successful_payment": {
            "invoice_payload": payload,
            "total_amount": 1,
            "currency": "XTR",
            "telegram_payment_charge_id": "one-charge",
        },
    }
    await asyncio.gather(*(orders.settle("telegram", msg) for _ in range(6)))
    async with db.session_scope() as s:
        assert (await s.get(db.User, user.id)).coin_balance == 120
        assert (await s.execute(select(func.count(db.Payment.id)))).scalar_one() == 1


async def test_encrypted_restore_with_fks_and_serial_sequences():
    user = await person()
    async with db.session_scope() as s:
        s.add(
            db.UpdateInbox(
                platform="telegram",
                update_id=10,
                actor="1",
                partition=0,
                payload={"update_id": 10},
            )
        )
        await service.credit(s, await s.get(db.User, user.id), 20, "test")
    async with db.session_scope() as s:
        _, blob = await backup.create_backup(s, "manual")
    async with db.session_scope() as s:
        (await s.get(db.User, user.id)).display_name = "Changed"
    result = await backup.execute_restore(blob, settings.backup_password)
    assert result["status"] == "succeeded", result
    async with db.session_scope() as s:
        assert (await s.get(db.User, user.id)).display_name == "Test"
        assert (
            await s.execute(select(func.count(db.UpdateInbox.id)))
        ).scalar_one() == 1
        s.add(
            db.User(
                platform="telegram",
                platform_user_id="after",
                display_name="After",
                age=25,
                gender="male",
            )
        )
        await s.flush()
        await service.delete_account(s, await s.get(db.User, user.id))
    async with db.session_scope() as s:
        assert (
            await s.execute(select(func.count(db.CoinTransaction.id)))
        ).scalar_one() == 2


async def test_indexes_present_and_query_plan():
    async with db.session_scope() as s:
        indexes = set(
            (
                await s.execute(
                    text("SELECT indexname FROM pg_indexes WHERE schemaname='public'")
                )
            ).scalars()
        )
        assert {
            "ix_queue_fifo",
            "ix_chats_status_activity",
            "ix_users_status_seen",
            "ix_inbox_ready",
        } <= indexes
        plan = (
            (
                await s.execute(
                    text(
                        "EXPLAIN SELECT * FROM queue_entries ORDER BY created_at,id LIMIT 50"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert plan and "Limit" in plan[0]


async def commerce_pair(ended=False):
    a, b = await person("801", 1000), await person("802", 1000)
    async with db.session_scope() as s:
        for uid in (a.id, b.id):
            (await s.get(db.User, uid)).accepted_rules_at = db.utcnow()
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


async def test_commerce_simultaneous_purchase_and_gift_entitlement():
    from app import commerce as C

    a, b, cid = await commerce_pair()
    q = await C.quote(a.id, "vip30", b.id)
    results = await asyncio.gather(*(C.purchase(a.id, q.id) for _ in range(6)))
    assert sum(fresh for _, fresh in results) == 1
    async with db.session_scope() as s:
        assert (await s.get(db.User, a.id)).coin_balance == 700
        assert (await s.get(db.User, b.id)).coin_balance == 1000
        assert await C.owns(s, b.id, "games")
        assert (
            await s.execute(select(func.count(db.MemberGrant.id)))
        ).scalar_one() == 1


async def test_commerce_simultaneous_distinct_quotes_cannot_overdraw():
    from app import commerce as C

    a = await person("801", 300)
    qs = [await C.quote(a.id, "vip30") for _ in range(4)]
    results = await asyncio.gather(
        *(C.purchase(a.id, q.id) for q in qs), return_exceptions=True
    )
    assert sum(isinstance(r, C.ShopError) for r in results) == 3
    async with db.session_scope() as s:
        assert (await s.get(db.User, a.id)).coin_balance == 0
        assert (
            await s.execute(select(func.count(db.MemberGrant.id)))
        ).scalar_one() == 1


async def test_commerce_shared_last_coupon_slot_atomic():
    from app import commerce as C

    a, b, cid = await commerce_pair()
    async with db.session_scope() as s:
        s.add(
            db.PromoCode(
                code="LAST",
                gift_coins=9,
                max_uses=1,
                expires_at=db.utcnow() + timedelta(days=1),
            )
        )
    results = await asyncio.gather(
        C.redeem(a.id, "LAST"), C.redeem(b.id, "LAST"), return_exceptions=True
    )
    assert sum(isinstance(r, C.ShopError) for r in results) == 1
    async with db.session_scope() as s:
        assert (await s.get(db.PromoCode, "LAST")).used == 1
        assert (await s.execute(select(func.count(db.PromoUse.id)))).scalar_one() == 1
        assert (
            await s.execute(select(func.sum(db.User.coin_balance)))
        ).scalar_one() == 2009


async def test_commerce_first_offer_concurrent_immutable_invoice():
    from app import commerce as C

    a = await person("801")
    results = await asyncio.gather(*(C.first_order(a.id) for _ in range(5)))
    assert len({r.id for r in results}) == 1
    assert {r.coins for r in results} == {150}


async def test_commerce_game_simultaneous_move_and_persisted_restart():
    from app import commerce as C, social_games as G

    a, b, cid = await commerce_pair()
    gid = await G.invite(a.id, "ttt")
    await G.act(b.id, gid, 0, "accept")
    moves = await asyncio.gather(
        G.act(a.id, gid, 1, "0"), G.act(a.id, gid, 1, "1"), return_exceptions=True
    )
    assert sum(isinstance(r, C.ShopError) for r in moves) == 1
    G._render_locks.clear()  # No board state lives in process memory.
    async with db.session_scope() as s:
        g = await s.get(db.SocialGame, gid)
        assert g.version == 2 and g.data["board"].count("X") == 1
    await G.act(b.id, gid, 2, "4")


async def test_commerce_reconnect_accept_replay_serialized(monkeypatch):
    from app import commerce as C, reconnect as R, bot

    a, b, cid = await commerce_pair(ended=True)
    r = await R.prepare(a.id, cid)
    async with db.session_scope() as s:
        (await s.get(db.ReconnectRequest, r.id)).status = "pending"

    async def delivered(*args, **kwargs):
        return True

    monkeypatch.setattr(bot, "send_controls", delivered)
    results = await asyncio.gather(
        *(R.decide(b.id, r.id, True) for _ in range(5)), return_exceptions=True
    )
    assert sum(isinstance(r, C.ShopError) for r in results) == 4
    async with db.session_scope() as s:
        assert (await s.get(db.User, a.id)).coin_balance == 995
        assert (await s.get(db.ReconnectRequest, r.id)).status == "connected"
        assert (
            await s.execute(
                select(func.count(db.Chat.id)).where(db.Chat.status == "active")
            )
        ).scalar_one() == 1


async def test_commerce_backup_restore_deletion_and_retention_fks():
    from app import commerce as C, social_games as G, reconnect as R

    a, b, cid = await commerce_pair()
    q = await C.quote(a.id, "vip30")
    await C.purchase(a.id, q.id)
    link = await C.create_topic(a.id, "work")
    gid = await G.invite(a.id, "ttt")
    await G.act(b.id, gid, 0, "accept")
    async with db.session_scope() as s:
        s.add(
            db.PromoCode(
                code="SAFE",
                gift_coins=2,
                max_uses=1,
                expires_at=db.utcnow() + timedelta(days=1),
            )
        )
        await service.end_chat(s, await s.get(db.Chat, cid), "left", a.id)
    await C.redeem(a.id, "SAFE")
    r = await R.prepare(a.id, cid)
    async with db.session_scope() as s:
        _, blob = await backup.create_backup(s, "manual")
    result = await backup.execute_restore(blob, settings.backup_password)
    assert result["status"] == "succeeded", result
    async with db.session_scope() as s:
        assert await s.get(db.TopicLink, link.id)
        assert (await s.get(db.SocialGame, gid)).status == "playing"
        assert await s.get(db.ReconnectRequest, r.id)
        assert await C.owns(s, a.id, "games")
        assert (await s.get(db.PromoCode, "SAFE")).used == 1
        await service.delete_account(s, await s.get(db.User, a.id))
    async with db.session_scope() as s:
        assert await s.get(db.SocialGame, gid) is None
        assert await s.get(db.ReconnectRequest, r.id) is None
        assert not await C.owns(s, a.id, "games")
        assert await s.get(db.ShopQuote, q.id)  # Financial reconciliation retained.
        assert (await s.get(db.PromoCode, "SAFE")).used == 1


async def test_commerce_upgrade_from_existing_hardening_schema():
    from app import commerce as C

    new_tables = {
        "member_profiles",
        "shop_quotes",
        "member_grants",
        "promo_codes",
        "promo_uses",
        "topic_links",
        "reconnect_requests",
        "social_games",
    }
    async with db.get_engine().begin() as conn:
        for table in reversed(db.Base.metadata.sorted_tables):
            if table.name in new_tables:
                await conn.run_sync(lambda sync, t=table: t.drop(sync))
        await conn.execute(
            text("DELETE FROM schema_migrations WHERE version='20261128_commerce_v2'")
        )
    await migrate(db.get_engine())
    await migrate(db.get_engine())
    a, b, cid = await commerce_pair()
    q = await C.quote(a.id, "vip30")
    assert (await C.purchase(a.id, q.id))[1]
    async with db.session_scope() as s:
        versions = (
            await s.execute(text("SELECT count(*) FROM schema_migrations"))
        ).scalar_one()
        assert versions == 2
