"""Security/UX regressions for the approved hardening work."""
from contextlib import asynccontextmanager
import asyncio
import gzip
import io
import json
from datetime import timedelta
from pathlib import Path
import httpx
import pytest
from sqlalchemy import select, func, event
from test_all import clean_db, adapters, mk_user, FakeAdapter
from app import db, service, security, backup, inbox, orders
from app.config import RUNTIME, settings
from app.main import app
from app.limits import login_limiter

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def client(role="owner"):
    async with db.session_scope() as s:
        s.add(db.WebAdmin(username="hardening",password_hash=security.hash_password("OwnerPassword123!"),role=role))
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test")
    r = await c.post("/api/admin/v1/auth/login",json={"username":"hardening","password":"OwnerPassword123!"})
    assert r.status_code == 200, r.text
    try:
        yield c
    finally:
        await c.aclose()


async def test_refresh_csrf_logout_and_expiry():
    async with client() as c:
        for _ in range(3):
            me = await c.get("/api/admin/v1/auth/me")
            assert me.headers["x-csrf-token"]
            r = await c.post("/api/admin/v1/notifications/read-all",headers={"X-CSRF-Token":me.headers["x-csrf-token"]})
            assert r.status_code == 204
        sid = c.cookies["kanar_admin_session"]
        async with db.session_scope() as s:
            sess = await s.get(db.WebSession,sid)
            sess.created_at = db.utcnow()-timedelta(days=settings.session_days+1)
        assert (await c.get("/api/admin/v1/auth/me")).status_code == 401
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test")
    async with c:
        r = await c.post("/api/admin/v1/auth/login",json={"username":"hardening","password":"OwnerPassword123!"})
        r = await c.post("/api/admin/v1/auth/logout",headers={"X-CSRF-Token":r.headers["x-csrf-token"]})
        assert r.status_code == 204 and "kanar_admin_session" not in c.cookies


async def test_search_all_users_normalized_and_paginated():
    async with db.session_scope() as s:
        for i in range(510):
            s.add(db.User(platform="telegram",platform_user_id=str(10000+i),display_name="علی کاربر" if i==0 else "شخص",
                username="Kian_User" if i==0 else None,city="تهران" if i==0 else "",age=25,gender="male"))
    async with client() as c:
        for q in ("۱۰۰۰۰", "@KIAN_user", "علي", "تهران", "1"):
            r = await c.get("/api/admin/v1/users",params={"q":q})
            assert r.status_code == 200 and any(u["platformUserId"]=="10000" for u in r.json()), (q,r.text)
        assert (await c.get("/api/admin/v1/users",params={"q":"%"})).json() == []
        r = await c.get("/api/admin/v1/users",params={"page":2,"pageSize":50})
        assert len(r.json())==50 and r.headers["x-total-count"]=="510"


async def test_support_cannot_read_chat_or_global_security():
    async with client("support") as c:
        assert (await c.get("/api/admin/v1/chats")).status_code==403
        assert (await c.get("/api/admin/v1/security/overview")).status_code==403


async def test_login_security_event_survives_rollback_and_ip_spoof():
    async with client() as c:
        for i in range(6):
            r = await c.post("/api/admin/v1/auth/login",json={"username":"hardening","password":"wrong"},headers={"X-Forwarded-For":f"1.2.3.{i}"})
            assert r.status_code==401
        r = await c.post("/api/admin/v1/auth/login",json={"username":"hardening","password":"wrong"},headers={"X-Forwarded-For":"9.9.9.9"})
        assert r.status_code==429
    async with db.session_scope() as s:
        events = list((await s.execute(select(db.SecurityEvent).where(db.SecurityEvent.type=="login_failed"))).scalars())
        assert len(events)==6 and all(e.ip_address=="127.0.0.1" for e in events)


async def test_order_amount_identity_expiry_and_idempotency(adapters):
    payer = await mk_user(pid="7001",coins=100)
    async with db.session_scope() as s:
        payload = await orders.create(s,payer,20,1,package_id="pkg_20")
    event = {"from":{"id":7001},"invoice_payload":payload,"currency":"XTR","total_amount":1}
    async with db.session_scope() as s:
        assert await orders.find(s,"telegram",event)
        for change in ({"total_amount":2},{"from":{"id":7002}},{"currency":"IRR"}):
            assert await orders.find(s,"telegram",dict(event,**change)) is None
        order = await s.get(db.InvoiceOrder,payload[6:])
        order.expires_at=db.utcnow()-timedelta(seconds=1)
    async with db.session_scope() as s:
        assert await orders.find(s,"telegram",event) is None
    msg={"from":{"id":7001},"successful_payment":dict(event,telegram_payment_charge_id="safe-charge")}
    assert await orders.settle("telegram",msg)
    assert await orders.settle("telegram",msg) is None
    async with db.session_scope() as s:
        assert (await s.get(db.User,payer.id)).coin_balance==120


async def test_inbox_persists_before_ack_and_retries(monkeypatch):
    from app import bot
    attempts=[]
    async def flaky(platform,payload):
        attempts.append(payload["update_id"])
        if len(attempts)==1: raise RuntimeError("transient")
    monkeypatch.setattr(bot,"process_update",flaky)
    await inbox.enqueue("telegram",{"update_id":200,"message":{"from":{"id":5},"text":"hi"}})
    await inbox.enqueue("telegram",{"update_id":200,"message":{"from":{"id":5},"text":"hi"}})
    async with db.session_scope() as s:
        row=(await s.execute(select(db.UpdateInbox))).scalar_one()
        partition=row.partition
    await inbox.process_one(partition)
    async with db.session_scope() as s:
        row=(await s.execute(select(db.UpdateInbox))).scalar_one()
        assert row.status=="queued"
        row.available_at=db.utcnow()-timedelta(seconds=1)
    await inbox.process_one(partition)
    async with db.session_scope() as s:
        row=(await s.execute(select(db.UpdateInbox))).scalar_one()
        assert row.status=="done" and row.payload=={} and len(attempts)==2


async def test_webhook_size_and_schema(monkeypatch,adapters):
    monkeypatch.setitem(RUNTIME,"path_secret","safe")
    # FakeAdapter's header verification is irrelevant: Bale authenticates secret path.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as c:
        assert (await c.post("/webhook/bale/safe",json=[])).status_code==400
        assert (await c.post("/webhook/bale/safe",content=b"x"*(settings.webhook_max_bytes+1))).status_code==413
        r=await c.post("/webhook/bale/safe",json={"update_id":3,"message":{"from":{"id":8},"text":"hi"}})
        assert r.status_code==200
    async with db.session_scope() as s:
        assert (await s.execute(select(func.count(db.UpdateInbox.id)))).scalar_one()==1


async def test_matching_query_budget(adapters):
    people=[]
    async with db.session_scope() as s:
        for i in range(50):
            u=db.User(platform="telegram",platform_user_id=str(5000+i),display_name="Test",age=25,gender="male",coin_balance=30)
            s.add(u);await s.flush();people.append(u.id)
            await service.enqueue(s,u,"female") # all incompatible: old algorithm queried per pair
    queries=[]
    def count(*args): queries.append(1)
    event.listen(db.get_engine().sync_engine,"before_cursor_execute",count)
    try:
        async with db.session_scope() as s:
            assert await service.try_match(s) is None
    finally:
        event.remove(db.get_engine().sync_engine,"before_cursor_execute",count)
    assert len(queries)<=10, len(queries)


async def test_bounded_archive_and_session_bound_upload(monkeypatch):
    u=await mk_user()
    async with db.session_scope() as s:
        _,blob=await backup.create_backup(s,"manual")
    assert blob.startswith(security.BACKUP_MAGIC)
    assert backup.validate_blob(blob,password=settings.backup_password)["valid"]
    assert not backup.validate_blob(blob,password="wrong")["valid"]
    monkeypatch.setattr(settings,"restore_expanded_max_bytes",128)
    assert not backup.validate_blob(blob,password=settings.backup_password)["valid"]
    monkeypatch.setattr(settings,"restore_expanded_max_bytes",256*1024*1024)
    from app import uploads
    from starlette.datastructures import UploadFile
    token,_ = await uploads.store(UploadFile(io.BytesIO(blob),filename="backup.enc"),"session-a")
    with pytest.raises(ValueError): uploads.consume(token,"session-b")
    assert uploads.consume(token,"session-a")==blob
    assert not uploads._uploads


async def test_restore_web_without_totp_and_real_upload(adapters):
    await mk_user(pid="8001",name="Before")
    async with db.session_scope() as s:
        _,blob=await backup.create_backup(s,"manual")
    async with client() as c:
        token=(await c.get("/api/admin/v1/auth/me")).headers["x-csrf-token"]
        headers={"X-CSRF-Token":token}
        r=await c.post("/api/admin/v1/backups/restore/validate-file",files={"file":("backup.enc",blob)},headers=headers)
        assert r.status_code==200 and r.json()["valid"],r.text
        r=await c.post("/api/admin/v1/backups/restore/execute",json={"uploadId":r.json()["uploadId"],"confirmationPhrase":"RESTORE HAMCHAT DB","password":"OwnerPassword123!"},headers=headers)
        assert r.status_code==200 and r.json()["status"]=="succeeded",r.text
        assert (await c.get("/api/admin/v1/auth/me")).status_code==401


async def test_chat_history_is_lazy_and_bounded():
    a=await mk_user(pid="911"); b=await mk_user(pid="912")
    async with db.session_scope() as s:
        chat=db.Chat(user_a_id=a.id,user_b_id=b.id,age_group="18-60")
        s.add(chat);await s.flush();cid=chat.id
        for i in range(350):s.add(db.ChatMessage(chat_id=cid,sender_id=a.id,msg_type="text",text=str(i)))
    async with client() as c:
        r=await c.get("/api/admin/v1/chats")
        assert r.json()["chats"][0]["messages"]==[]
        r=await c.get(f"/api/admin/v1/chats/{cid}/messages")
        assert len(r.json()["messages"])==100 and r.json()["hasMore"]
        assert r.json()["messages"][-1]["text"]=="349"


async def test_cache_invalidated_on_admin_dictionary_change():
    async with db.session_scope() as s:
        await service.dictionary(s)
    async with db.session_scope() as s:
        s.add(db.DictionaryWord(word="hardeningword",severity="high",action="block"))
        await s.flush()
        await s.commit()
    async with db.session_scope() as s:
        assert any(w[0]=="hardeningword" for w in await service.dictionary(s))


async def test_password_rotation_keeps_current_browser_and_revokes_old_sessions():
    async with client() as c:
        old_sid=c.cookies["kanar_admin_session"]
        token=(await c.get("/api/admin/v1/auth/me")).headers["x-csrf-token"]
        r=await c.post("/api/admin/v1/auth/change-password",json={"currentPassword":"OwnerPassword123!","newPassword":"RotatedPassword123!"},headers={"X-CSRF-Token":token})
        assert r.status_code==200,r.text
        new_sid=c.cookies["kanar_admin_session"]
        assert new_sid != old_sid and r.headers["x-csrf-token"]==security.csrf_token(new_sid,settings.webhook_secret)
        assert (await c.get("/api/admin/v1/auth/me")).status_code==200
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test",cookies={"kanar_admin_session":old_sid}) as other:
            assert (await other.get("/api/admin/v1/auth/me")).status_code==401


async def test_gift_refund_recovers_recipient_coins_once(adapters):
    tg,_=adapters
    calls=[]
    async def refund(pid,charge):calls.append((pid,charge))
    tg.refund_payment=refund
    payer=await mk_user(pid="6661",coins=50)
    recipient=await mk_user(pid="6662",coins=10)
    async with db.session_scope() as s:
        payload=await orders.create(s,payer,20,1,recipient=recipient)
    await orders.settle("telegram",{"from":{"id":6661},"successful_payment":{"invoice_payload":payload,"currency":"XTR","total_amount":1,"telegram_payment_charge_id":"gift-refund-charge"}})
    async with db.session_scope() as s: pid=(await s.execute(select(db.Payment.id))).scalar_one()
    await orders.refund(pid,"owner")
    await orders.refund(pid,"owner")
    async with db.session_scope() as s:
        assert (await s.get(db.User,payer.id)).coin_balance==50
        assert (await s.get(db.User,recipient.id)).coin_balance==10
        assert (await s.get(db.Payment,pid)).status=="refunded"
    assert len(calls)==1


async def test_broadcast_api_count_and_cursor_resume_with_gateway_failure(adapters):
    from app import main
    from app.platform import PlatformError
    tg,_=adapters
    users=[await mk_user(pid=str(9100+i)) for i in range(4)]
    sent=[]
    async def send_message(pid,text):
        sent.append(str(pid))
        if str(pid)==users[2].platform_user_id:
            raise PlatformError("sendMessage",500,"transient gateway error")
        return {"message_id":1}
    tg.send_message=send_message
    async with client() as c:
        token=(await c.get("/api/admin/v1/auth/me")).headers["x-csrf-token"]
        r=await c.post("/api/admin/v1/broadcasts",headers={"X-CSRF-Token":token},json={"title":"cursor-test","message":"hello","audience":"all","rateLimitPerSec":25})
        assert r.status_code==200,r.text
        assert r.json()["targetCount"]==4
        bid=int(r.json()["id"])
    async with db.session_scope() as s:
        campaign=await s.get(db.BroadcastCampaign,bid)
        campaign.last_user_id=users[0].id
        campaign.delivered_count=1
    active={bid}
    await main._run_broadcast(bid,active)
    assert not active and sent==[u.platform_user_id for u in users[1:]]
    async with db.session_scope() as s:
        campaign=await s.get(db.BroadcastCampaign,bid)
        assert campaign.status=="completed" and campaign.delivered_count==3 and campaign.failed_count==1
        assert campaign.last_user_id==users[-1].id


async def test_refund_timeout_reserves_coins_until_gateway_reconciliation(adapters):
    from app.platform import PlatformError
    tg,_=adapters
    payer=await mk_user(pid="9200",coins=30)
    async with db.session_scope() as s:
        p=db.Payment(platform="telegram",user_id=payer.id,coins=20,amount=1,status="succeeded",telegram_payment_charge_id="timeout-charge")
        s.add(p);await s.flush();pid=p.id
    async def fail(*args):raise PlatformError("refundStarPayment",504,"timeout")
    tg.refund_payment=fail
    with pytest.raises(orders.RefundError,match="قطعی"):
        await orders.refund(pid,"owner")
    async with db.session_scope() as s:
        assert (await s.get(db.User,payer.id)).coin_balance==10
        assert (await s.get(db.Payment,pid)).status=="refund_pending"
    async def reconciled(*args):raise PlatformError("refundStarPayment",400,"CHARGE_ALREADY_REFUNDED")
    tg.refund_payment=reconciled
    await orders.refund(pid,"owner")
    async with db.session_scope() as s:
        assert (await s.get(db.User,payer.id)).coin_balance==10
        assert (await s.get(db.Payment,pid)).status=="refunded"


async def test_telemetry_bounded_and_unknown_not_fabricated(monkeypatch):
    from app import telemetry
    telemetry._samples.clear()
    monkeypatch.setattr(telemetry,"_previous",None)
    monkeypatch.setattr(telemetry,"_read",lambda path:"")
    now=[0.0]
    monkeypatch.setattr(telemetry.time,"monotonic",lambda:now[0])
    first=telemetry.sample()
    assert first["cpuUsagePct"] is None and first["ramLimitMb"] is None and first["ramUsageMb"] is None
    for i in range(310):
        now[0]+=31
        result=telemetry.sample()
    assert len(result["telemetrySeries"])==288
    telemetry._samples.clear()
    monkeypatch.setattr(telemetry,"_previous",None)


async def test_bot_restore_state_expires_and_is_platform_scoped():
    from app import bot_admin
    import time
    bot_admin.RESTORE_WAIT.clear()
    bot_admin.RESTORE_WAIT["telegram:123"]={"expires":time.monotonic()-1,"blob":b"secret"}
    bot_admin.RESTORE_WAIT["bale:123"]={"expires":time.monotonic()+600}
    bot_admin.cleanup_restore_wait()
    assert "telegram:123" not in bot_admin.RESTORE_WAIT and "bale:123" in bot_admin.RESTORE_WAIT
    bot_admin.RESTORE_WAIT.clear()


@pytest.mark.parametrize("step,extra",[
    ("birth_month",{"year":2005}),
    ("birth_day",{"year":2005,"month":2,"maxday":31}),
    ("birth_confirm",{"year":2005,"month":2,"day":12}),
    ("gender",{}),
])
async def test_start_resumes_signup_without_losing_collected_fields(step,extra,adapters):
    from app import bot
    user=await mk_user(pid="9300")
    state={"step":step,"calendar":"gregorian","after_reg":{"anon":999},**extra}
    async with db.session_scope() as s:
        user=await s.get(db.User,user.id)
        user.age=21 if step=="gender" else 0
        user.accepted_rules_at=db.utcnow()
        user.state=state
    await bot.process_update("telegram",{"update_id":93001,"message":{"message_id":1,"chat":{"id":9300},"from":{"id":9300},"text":"/start"}})
    async with db.session_scope() as s:
        user=await s.get(db.User,user.id)
        assert user.state==state
        assert not (await s.execute(select(db.QueueEntry).where(db.QueueEntry.user_id==user.id))).scalar_one_or_none()


async def test_birthdate_calendar_validation_and_persian_digits(adapters):
    from app import bot
    assert service.birthday_month_days("gregorian",2004,2)==29
    assert service.birthday_month_days("gregorian",2005,2)==28
    assert service.birthday_month_days("jalali",1399,12)==30
    assert service.birthday_month_days("jalali",1400,12)==29
    with pytest.raises(ValueError):service.birthday_month_days("jalali",1400,13)
    u=await mk_user(pid="9400")
    async with db.session_scope() as s:
        user=await s.get(db.User,u.id)
        user.age=0
        user.accepted_rules_at=db.utcnow()
        user.state={"step":"birth_year","calendar":"gregorian"}
    await bot.process_update("telegram",{"update_id":94001,"message":{"message_id":1,"chat":{"id":9400},"from":{"id":9400},"text":"۲۰۰۵"}})
    async with db.session_scope() as s:
        user=await s.get(db.User,u.id)
        assert user.state["year"]==2005 and user.state["step"]=="birth_month"
        user.state={"step":"birth_day","calendar":"gregorian","year":2005,"month":2,"maxday":31}
    await bot.birth_day_chosen(user,31)
    async with db.session_scope() as s:
        assert (await s.get(db.User,u.id)).state["step"]=="birth_day"


async def test_concurrent_upload_reservations_are_bounded():
    from app import uploads
    uploads.clear_uploads()
    ready=asyncio.Event()
    release=asyncio.Event()
    started=[]
    class WaitingFile:
        def __init__(self):self.closed=False
        async def read(self,size):
            started.append(self)
            if len(started)==4:ready.set()
            await release.wait()
            return b""
        async def close(self):self.closed=True
    files=[WaitingFile() for _ in range(4)]
    tasks=[asyncio.create_task(uploads.store(f,f"upload-session-{i}")) for i,f in enumerate(files)]
    try:
        await asyncio.wait_for(ready.wait(),2)
        rejected=WaitingFile()
        with pytest.raises(ValueError,match="TOO_MANY_UPLOADS"):
            await uploads.store(rejected,"fifth-session")
        assert rejected.closed
        release.set()
        await asyncio.gather(*tasks)
        assert len(uploads._uploads)==4 and not uploads._receiving
    finally:
        release.set()
        await asyncio.gather(*tasks,return_exceptions=True)
        uploads.clear_uploads()
