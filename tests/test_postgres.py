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

URL=os.getenv("TEST_PG_URL", "")
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(not URL,reason="Set TEST_PG_URL to an isolated *_test database")]


@pytest_asyncio.fixture(autouse=True)
async def isolated(monkeypatch):
    if not URL: yield;return
    if not URL.rsplit('/',1)[-1].endswith('_test'):
        raise RuntimeError("Postgres tests require a database name ending in _test")
    engine=create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()
    db.init_engine(URL)
    await db.create_all(db.get_engine())
    await migrate(db.get_engine())
    await migrate(db.get_engine())
    async with db.session_scope() as s: await service.seed(s)
    monkeypatch.setattr(settings,"backup_password","pg-backup-password-1234567890")
    RUNTIME.pop("restore_in_progress",None);RUNTIME.pop("backup_in_progress",None)
    yield
    await db.dispose_engine()


async def person(pid="1",coins=100):
    async with db.session_scope() as s:
        user=db.User(platform="telegram",platform_user_id=pid,display_name="Test",age=25,gender="male",coin_balance=coins)
        s.add(user);await s.flush();return user


async def test_concurrent_wallet_operations_and_idempotency():
    u=await person()
    async def credit(i):
        async with db.session_scope() as s:
            stale=await s.get(db.User,u.id)
            await asyncio.sleep(.001)
            await service.credit(s,stale,5,"test",idempotency_key=f"credit:{i}")
    await asyncio.gather(*(credit(i%20) for i in range(40)))
    async def debit(i):
        async with db.session_scope() as s:
            stale=await s.get(db.User,u.id)
            return await service.debit(s,stale,30,"test",idempotency_key=f"debit:{i}") is not None
    results=await asyncio.gather(*(debit(i) for i in range(10)))
    async with db.session_scope() as s:
        user=await s.get(db.User,u.id)
        assert user.coin_balance==20 and sum(results)==6
        assert (await s.execute(select(func.count(db.CoinTransaction.id)))).scalar_one()==26
        assert (await s.execute(select(func.count()).select_from(text("schema_migrations")))).scalar_one()==1


async def test_matching_concurrency_and_cross_position_constraint():
    users=[await person(str(i)) for i in range(12)]
    async with db.session_scope() as s:
        for user in users: await service.enqueue(s,user,"everyone")
    async def match():
        async with db.session_scope() as s: return await service.try_match(s)
    for _ in range(6): await asyncio.gather(*(match() for _ in range(4)))
    async with db.session_scope() as s:
        chats=list((await s.execute(select(db.Chat))).scalars())
        ids=[uid for chat in chats for uid in (chat.user_a_id,chat.user_b_id)]
        assert len(chats)==6 and len(set(ids))==12
    # Existing A may not be inserted as B in a different active conversation.
    outsider=await person("99")
    with pytest.raises(IntegrityError):
        async with db.session_scope() as s:
            s.add(db.Chat(user_a_id=outsider.id,user_b_id=chats[0].user_a_id,age_group="18-60"))


async def test_concurrent_payment_settlement():
    user=await person()
    async with db.session_scope() as s: payload=await orders.create(s,user,20,1)
    msg={"from":{"id":1},"successful_payment":{"invoice_payload":payload,"total_amount":1,"currency":"XTR","telegram_payment_charge_id":"one-charge"}}
    await asyncio.gather(*(orders.settle("telegram",msg) for _ in range(6)))
    async with db.session_scope() as s:
        assert (await s.get(db.User,user.id)).coin_balance==120
        assert (await s.execute(select(func.count(db.Payment.id)))).scalar_one()==1


async def test_encrypted_restore_with_fks_and_serial_sequences():
    user=await person()
    async with db.session_scope() as s:
        s.add(db.UpdateInbox(platform="telegram",update_id=10,actor="1",partition=0,payload={"update_id":10}))
        await service.credit(s,await s.get(db.User,user.id),20,"test")
    async with db.session_scope() as s: _,blob=await backup.create_backup(s,"manual")
    async with db.session_scope() as s: (await s.get(db.User,user.id)).display_name="Changed"
    result=await backup.execute_restore(blob,settings.backup_password)
    assert result["status"]=="succeeded",result
    async with db.session_scope() as s:
        assert (await s.get(db.User,user.id)).display_name=="Test"
        assert (await s.execute(select(func.count(db.UpdateInbox.id)))).scalar_one()==1
        s.add(db.User(platform="telegram",platform_user_id="after",display_name="After",age=25,gender="male"))
        await s.flush()
        await service.delete_account(s,await s.get(db.User,user.id))
    async with db.session_scope() as s:
        assert (await s.execute(select(func.count(db.CoinTransaction.id)))).scalar_one()==2


async def test_indexes_present_and_query_plan():
    async with db.session_scope() as s:
        indexes=set((await s.execute(text("SELECT indexname FROM pg_indexes WHERE schemaname='public'"))).scalars())
        assert {'ix_queue_fifo','ix_chats_status_activity','ix_users_status_seen','ix_inbox_ready'} <= indexes
        plan=(await s.execute(text("EXPLAIN SELECT * FROM queue_entries ORDER BY created_at,id LIMIT 50"))).scalars().all()
        assert plan and 'Limit' in plan[0]
