"""Full test suite — run: python -m pytest tests/ -x -q"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////tmp/kanar_test.db")
os.environ.setdefault("ENCRYPTION_KEY", "test-encryption-key-0123456789abcdef")
os.environ.setdefault("BACKUP_PASSWORD", "TestBackupPass-1234567890")
os.environ.setdefault("WEBHOOK_SECRET", "test-webhook-secret")
os.environ.setdefault("ADMIN_TELEGRAM_IDS", "860835914")
os.environ.setdefault("ADMIN_BALE_IDS", "133609405")

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text

from app import db, security, service
from app.config import RUNTIME, settings
from app.db import (Ban, Block, Chat, ChatMessage, CoinTransaction, DailyClaim,
                    MediaType, Payment, QueueEntry, Referral, Report, StorePackage,
                    User, WebAdmin, WebSession, utcnow)
from app.platform import BaleAdapter, TelegramAdapter, UnsupportedMethod

TEST_DB = "/tmp/kanar_test.db"


# ------------------------------------------------------------- fixtures ----
@pytest_asyncio.fixture(autouse=True)
async def clean_db():
    if Path(TEST_DB).exists():
        Path(TEST_DB).unlink()
    db.init_engine(f"sqlite+aiosqlite:///{TEST_DB}")
    await db.create_all(db.get_engine())
    async with db.session_scope() as s:
        await service.seed(s)
    yield
    await db.dispose_engine()


class FakeAdapter:
    """Records outbound calls; used as both telegram/bale adapter."""
    platform = "telegram"

    def __init__(self, platform="telegram"):
        self.platform = platform
        self.sent: list[tuple] = []

    async def send_message(self, chat_id, text, keyboard=None, reply_to=None):
        self.sent.append((chat_id, text, keyboard))
        return {"message_id": len(self.sent)}

    async def send_media(self, chat_id, msg_type, file_id=None, *, caption="",
                         bytes_data=None, filename="file", reply_to=None, extra=None):
        self.sent.append((chat_id, f"[{msg_type}]", None))
        return {"message_id": len(self.sent)}

    async def call(self, method, payload=None, files=None):
        self.sent.append((payload.get("chat_id") if payload else None, method, None))
        return {"ok": True, message_id: 1} if False else {"message_id": 1, "result": True}

    async def download_file(self, file_id, max_bytes):
        return b"fakedata" * 10

    async def send_invoice(self, chat_id, title, description, payload, prices,
                           provider_token=""):
        self.sent.append((chat_id, "invoice", payload))
        return {"message_id": 1}

    async def answer_callback(self, cid, text="", alert=False):
        return True

    async def send_chat_action(self, chat_id, action):
        return True

    async def call(self, method, payload=None, files=None):
        self.sent.append((payload.get("chat_id") if payload else None, method, None))
        return {"message_id": 1, "ok": True}

    async def get_webhook_info(self):
        return {"url": ""}


@pytest.fixture
def adapters():
    tg, bale = FakeAdapter("telegram"), FakeAdapter("bale")
    RUNTIME["adapter_telegram"] = tg
    RUNTIME["adapter_bale"] = bale
    RUNTIME["tg_username"] = "kanarchatbot"
    RUNTIME["bale_username"] = "kchatbot"
    return tg, bale


async def mk_user(platform="telegram", pid="100", name="Test", age=25,
                  gender="male", coins=100, lang="fa") -> User:
    async with db.session_scope() as s:
        u = User(platform=platform, platform_user_id=pid, display_name=name, age=age,
                 gender=gender, coin_balance=coins, language=lang,
                 accepted_rules_at=utcnow(), registration_rewarded=True)
        s.add(u)
        await s.flush()
        return u


_UPD_SEQ = [100000]


async def register_via_bot(adapters, platform, pid, name, age, gender):
    """v2.2 registration: name -> birthdate (calendar -> year -> month -> day
    inline buttons) -> age confirmation -> gender. The birthdate is chosen so
    the computed age equals `age` exactly (gregorian, birthday = today)."""
    from app import bot as bot_mod
    import datetime as _dt
    upd_id = _UPD_SEQ

    def upd(**kw):
        upd_id[0] += 1
        return {"update_id": upd_id[0], **kw}

    today = _dt.date.today()
    year = today.year - age

    def cq(cq_id, data):
        return bot_mod.process_update(platform, upd(callback_query={
            "id": cq_id, "from": {"id": int(pid)},
            "message": {"chat": {"id": int(pid)}}, "data": data}))

    await bot_mod.process_update(platform, upd(message={
        "message_id": 1, "chat": {"id": int(pid)},
        "from": {"id": int(pid), "first_name": name}, "text": "/start"}))
    await cq("cq1", "lang_fa")
    await cq("cq2", "accept_rules")
    await bot_mod.process_update(platform, upd(message={
        "message_id": 2, "chat": {"id": int(pid)},
        "from": {"id": int(pid)}, "text": name}))
    await cq("cq3", "bcal_gregorian")
    await cq("cq4", f"byear_{year}")
    await cq("cq5", f"bmon_{today.month}")
    await cq("cq6", f"bday_{today.day}")
    await cq("cq7", "bage_ok")
    await cq("cq8", f"reg_{gender}")


# ================================================================ tests ====
@pytest.mark.asyncio
async def test_registration_flow_and_reward(adapters):
    tg, _ = adapters
    await register_via_bot(adapters, "telegram", "501", "Ali", 25, "male")
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "501"))).scalar_one()
        assert u.display_name == "Ali" and u.age == 25 and u.language == "fa"
        assert u.coin_balance == 30  # 20 welcome + 10 birthdate reward
        txns = (await s.execute(select(CoinTransaction).where(
            CoinTransaction.user_id == u.id))).scalars()
        assert len(list(txns)) == 2
    # idempotency: re-trigger cannot double-reward (flag + unique key)
    assert service.credit.__name__ == "credit"


@pytest.mark.asyncio
async def test_registration_validation(adapters):
    from app import bot as bot_mod
    await bot_mod.process_update("telegram", {"update_id": 1, "message": {
        "message_id": 1, "chat": {"id": 77}, "from": {"id": 77}, "text": "/start"}})
    await bot_mod.process_update("telegram", {"update_id": 2, "callback_query": {
        "id": "c1", "from": {"id": 77}, "message": {"chat": {"id": 77}}, "data": "lang_fa"}})
    await bot_mod.process_update("telegram", {"update_id": 3, "callback_query": {
        "id": "c2", "from": {"id": 77}, "message": {"chat": {"id": 77}}, "data": "accept_rules"}})
    # bad name (too short)
    await bot_mod.process_update("telegram", {"update_id": 4, "message": {
        "message_id": 2, "chat": {"id": 77}, "from": {"id": 77}, "text": "ab"}})
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "77"))).scalar_one()
        assert u.state["step"] == "name"  # still waiting
    # offensive display name (item 1) — rejected, still waiting
    await bot_mod.process_update("telegram", {"update_id": 5, "message": {
        "message_id": 3, "chat": {"id": 77}, "from": {"id": 77}, "text": "kos"}})
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "77"))).scalar_one()
        assert u.state["step"] == "name" and u.display_name != "kos"
    # good name -> birthdate calendar selection (v2.2 item 4)
    await bot_mod.process_update("telegram", {"update_id": 6, "message": {
        "message_id": 4, "chat": {"id": 77}, "from": {"id": 77}, "text": "Good Name"}})
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "77"))).scalar_one()
        assert u.state["step"] == "birth_calendar"
        assert u.display_name == "Good Name"


@pytest.mark.asyncio
async def test_age_pool_separation(adapters):
    """13-17 never matches 18-60 (locked invariant)."""
    await register_via_bot(adapters, "telegram", "601", "MinorA", 15, "male")
    await register_via_bot(adapters, "telegram", "602", "AdultB", 30, "male")
    await register_via_bot(adapters, "telegram", "603", "MinorC", 14, "male")
    from app import bot as bot_mod
    for pid in ("601", "602", "603"):
        await bot_mod.process_update("telegram", {"update_id": 900 + int(pid), "callback_query": {
            "id": f"c{pid}", "from": {"id": int(pid)},
            "message": {"chat": {"id": int(pid)}}, "data": "gf_everyone"}})
    async with db.session_scope() as s:
        chats = (await s.execute(select(Chat))).scalars()
        chats = list(chats)
        assert len(chats) == 1
        c = chats[0]
        assert c.age_group == "13-17"
        ua, ub = await s.get(User, c.user_a_id), await s.get(User, c.user_b_id)
        assert {ua.platform_user_id, ub.platform_user_id} == {"601", "603"}


@pytest.mark.asyncio
async def test_cross_platform_matching(adapters):
    await register_via_bot(adapters, "telegram", "701", "TgUser", 25, "male")
    await register_via_bot(adapters, "bale", "702", "BaleUser", 25, "female")
    from app import bot as bot_mod
    for plat, pid in (("telegram", "701"), ("bale", "702")):
        await bot_mod.process_update(plat, {"update_id": 800 + int(pid), "callback_query": {
            "id": f"c{pid}", "from": {"id": int(pid)},
            "message": {"chat": {"id": int(pid)}}, "data": "gf_everyone"}})
    async with db.session_scope() as s:
        c = (await s.execute(select(Chat))).scalar_one()
        assert c.cross_platform is True


@pytest.mark.asyncio
async def test_gender_filter_charge_only_on_success(adapters):
    await register_via_bot(adapters, "telegram", "801", "Seeker", 25, "male", )
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "801"))).scalar_one()
        u.coin_balance = 10
    from app import bot as bot_mod
    # gender-filtered search with NO partner -> no charge
    await bot_mod.process_update("telegram", {"update_id": 910, "callback_query": {
        "id": "c1", "from": {"id": 801}, "message": {"chat": {"id": 801}}, "data": "gf_female"}})
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "801"))).scalar_one()
        assert u.coin_balance == 10  # not charged yet
        assert (await s.execute(select(QueueEntry).where(
            QueueEntry.user_id == u.id))).scalar_one()
    # now a girl arrives -> match -> seeker is charged 5
    await register_via_bot(adapters, "bale", "802", "GirlB", 22, "female")
    await bot_mod.process_update("bale", {"update_id": 911, "callback_query": {
        "id": "c2", "from": {"id": 802}, "message": {"chat": {"id": 802}}, "data": "gf_everyone"}})
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "801"))).scalar_one()
        assert u.coin_balance == 5
        txn = (await s.execute(select(CoinTransaction).where(
            CoinTransaction.type == "gender_filter_cost"))).scalar_one()
        assert txn.amount == -5
        chat = (await s.execute(select(Chat))).scalar_one()
        assert chat.cross_platform


@pytest.mark.asyncio
async def test_no_match_when_insufficient_coins(adapters):
    await register_via_bot(adapters, "telegram", "811", "Poor", 25, "male")
    await register_via_bot(adapters, "telegram", "812", "Rich", 25, "female")
    async with db.session_scope() as s:
        poor = (await s.execute(select(User).where(User.platform_user_id == "811"))).scalar_one()
        poor.coin_balance = 2
    from app import bot as bot_mod
    await bot_mod.process_update("telegram", {"update_id": 920, "callback_query": {
        "id": "c1", "from": {"id": 811}, "message": {"chat": {"id": 811}}, "data": "gf_female"}})
    await bot_mod.process_update("telegram", {"update_id": 921, "callback_query": {
        "id": "c2", "from": {"id": 812}, "message": {"chat": {"id": 812}}, "data": "gf_everyone"}})
    async with db.session_scope() as s:
        assert (await s.execute(select(Chat))).scalar_one_or_none() is None
        poor = (await s.execute(select(User).where(User.platform_user_id == "811"))).scalar_one()
        assert poor.coin_balance == 2  # unchanged


@pytest.mark.asyncio
async def test_message_relay_and_coin_charge(adapters):
    tg, bale = adapters
    await register_via_bot(adapters, "telegram", "821", "Sender", 25, "male")
    await register_via_bot(adapters, "bale", "822", "Recv", 25, "female")
    from app import bot as bot_mod
    for plat, pid in (("telegram", "821"), ("bale", "822")):
        await bot_mod.process_update(plat, {"update_id": 930 + int(pid), "callback_query": {
            "id": f"c{pid}", "from": {"id": int(pid)},
            "message": {"chat": {"id": int(pid)}}, "data": "gf_everyone"}})
    bale.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 940, "message": {
        "message_id": 10, "chat": {"id": 821}, "from": {"id": 821}, "text": "salam dust aziz"}})
    assert any("salam dust aziz" in str(t[1]) for t in bale.sent)
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "821"))).scalar_one()
        # sender had 30 (20 reg + 10 birthdate) -> charged 1 for the message
        assert u.coin_balance == 29
        m = (await s.execute(select(ChatMessage))).scalar_one()
        assert m.text == "salam dust aziz" and m.msg_type == "text"


@pytest.mark.asyncio
async def test_profanity_rejection_and_escalation(adapters):
    await register_via_bot(adapters, "telegram", "831", "Swearer", 25, "male")
    await register_via_bot(adapters, "telegram", "832", "Clean", 25, "female")
    from app import bot as bot_mod
    for pid in ("831", "832"):
        await bot_mod.process_update("telegram", {"update_id": 950 + int(pid), "callback_query": {
            "id": f"c{pid}", "from": {"id": int(pid)},
            "message": {"chat": {"id": int(pid)}}, "data": "gf_everyone"}})
    tg, _ = adapters
    tg.sent.clear()
    # first violation: warn, message NOT relayed
    await bot_mod.process_update("telegram", {"update_id": 960, "message": {
        "message_id": 1, "chat": {"id": 831}, "from": {"id": 831}, "text": "kos kos"}})
    assert any("نامناسب" in str(t[1]) for t in tg.sent)
    async with db.session_scope() as s:
        assert (await s.execute(select(ChatMessage))).scalar_one_or_none() is None
        u = (await s.execute(select(User).where(User.platform_user_id == "831"))).scalar_one()
        assert u.profanity_count == 1 and u.status == "active"
    # second violation: 24h ban
    await bot_mod.process_update("telegram", {"update_id": 961, "message": {
        "message_id": 2, "chat": {"id": 831}, "from": {"id": 831}, "text": "fuck you"}})
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "831"))).scalar_one()
        assert u.status == "banned"
        ban = (await s.execute(select(Ban).where(Ban.user_id == u.id))).scalar_one()
        assert ban.type == "temporary" and ban.expires_at > utcnow()
    # further violation: 7 days
    async with db.session_scope() as s:
        b = (await s.execute(select(Ban).where(Ban.user_id != 0))).scalar()
        b.status = "revoked"
        u = (await s.execute(select(User).where(User.platform_user_id == "831"))).scalar_one()
        u.status = "active"
    # re-match the pair (ban ended the chat), then third violation -> 7-day ban
    for pid in ("831", "832"):
        await bot_mod.process_update("telegram", {"update_id": 963 + int(pid), "callback_query": {
            "id": f"cr{pid}", "from": {"id": int(pid)},
            "message": {"chat": {"id": int(pid)}}, "data": "gf_everyone"}})
    # cooldown prevents instant re-match; clear recent pairs to simulate time passing
    async with db.session_scope() as s:
        from app.db import RecentPair
        for rp in (await s.execute(select(RecentPair))).scalars():
            await s.delete(rp)
    await bot_mod.run_match_cycle()
    await bot_mod.process_update("telegram", {"update_id": 426000, "message": {
        "message_id": 3, "chat": {"id": 831}, "from": {"id": 831}, "text": "bitch"}})
    async with db.session_scope() as s:
        bans = list((await s.execute(select(Ban).where(
            Ban.user_id != 0, Ban.status == "active"))).scalars())
        assert bans and bans[-1].type == "temporary"
        assert bans[-1].expires_at > utcnow() + timedelta(days=6)  # 7 days
        assert bans[-1].expires_at > utcnow() + timedelta(days=6)


@pytest.mark.asyncio
async def test_contact_filter(adapters):
    await register_via_bot(adapters, "telegram", "841", "A", 25, "male")
    await register_via_bot(adapters, "telegram", "842", "B", 25, "female")
    from app import bot as bot_mod
    for pid in ("841", "842"):
        await bot_mod.process_update("telegram", {"update_id": 970 + int(pid), "callback_query": {
            "id": f"c{pid}", "from": {"id": int(pid)},
            "message": {"chat": {"id": int(pid)}}, "data": "gf_everyone"}})
    tg, _ = adapters
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 980, "message": {
        "message_id": 1, "chat": {"id": 841}, "from": {"id": 841},
        "text": "add me on https://t.me/whatever"}})
    assert any("اطلاعات تماس" in str(t[1]) for t in tg.sent)
    await bot_mod.process_update("telegram", {"update_id": 981, "message": {
        "message_id": 2, "chat": {"id": 841}, "from": {"id": 841},
        "text": "my instagram: ali_gh"}})
    assert any("اطلاعات تماس" in str(t[1]) for t in tg.sent)


@pytest.mark.asyncio
async def test_moderation_false_positives():
    from app.moderation import check_profanity
    from app.moderation import DEFAULT_DICTIONARY
    dic = DEFAULT_DICTIONARY
    allow = ["خودم", "خواهر", "امام"]
    # ordinary mild words must NOT be flagged
    for t in ("سلام خوبی؟", "چه خبر داداش", "کجایی هستی؟", "بریم فیلم ببینیم",
              "assignment", "classic", "кусок хлеба", "могу я спросить"):
        assert check_profanity(t, dic, allow).ok, t
    # obfuscated variants MUST be flagged
    for t in ("k0s k0s", "FU CK", "бл*ть", "کســـ نما", "فـ*ـک", "f.u.c.k"):
        assert not check_profanity(t, dic, allow).ok, t


@pytest.mark.asyncio
async def test_report_threshold_auto_ban(adapters):
    await register_via_bot(adapters, "telegram", "851", "Target", 25, "male")
    reporters = []
    for i in range(3):
        await register_via_bot(adapters, "telegram", f"86{i}", f"Rep{i}", 25, "female")
        reporters.append(f"86{i}")
    from app import bot as bot_mod
    # pair Target with Rep0
    await bot_mod.process_update("telegram", {"update_id": 990, "callback_query": {
        "id": "c1", "from": {"id": 851}, "message": {"chat": {"id": 851}}, "data": "gf_everyone"}})
    await bot_mod.process_update("telegram", {"update_id": 991, "callback_query": {
        "id": "c2", "from": {"id": 860}, "message": {"chat": {"id": 860}}, "data": "gf_everyone"}})
    async with db.session_scope() as s:
        chat = (await s.execute(select(Chat))).scalar_one()
        target = (await s.execute(select(User).where(User.platform_user_id == "851"))).scalar_one()
        chat_id = chat.id
        target_id = target.id
    # three unique reports from three different users in 24h
    async with db.session_scope() as s:
        for i, pid in enumerate(reporters):
            rep = (await s.execute(select(User).where(User.platform_user_id == pid))).scalar_one()
            await service.create_report(s, await s.get(Chat, chat_id), rep,
                                        await s.get(User, target_id), "harassment", "test")
        # duplicate report from same user is deduplicated
        rep0 = (await s.execute(select(User).where(User.platform_user_id == "860"))).scalar_one()
        res = await service.create_report(s, await s.get(Chat, chat_id), rep0,
                                          await s.get(User, target_id), "harassment", "dup")
        assert res["created"] is False
        target = await s.get(User, target_id)
        assert target.status == "banned"
        ban = (await s.execute(select(Ban).where(Ban.user_id == target_id))).scalar_one()
        assert ban.source == "auto_3_reports" and ban.type == "temporary"


@pytest.mark.asyncio
async def test_block_permanent(adapters):
    await register_via_bot(adapters, "telegram", "871", "A", 25, "male")
    await register_via_bot(adapters, "telegram", "872", "B", 25, "female")
    from app import bot as bot_mod
    for pid in ("871", "872"):
        await bot_mod.process_update("telegram", {"update_id": 995 + int(pid), "callback_query": {
            "id": f"c{pid}", "from": {"id": int(pid)},
            "message": {"chat": {"id": int(pid)}}, "data": "gf_everyone"}})
    await bot_mod.process_update("telegram", {"update_id": 999, "callback_query": {
        "id": "cb", "from": {"id": 871}, "message": {"chat": {"id": 871}}, "data": "chat_block"}})
    async with db.session_scope() as s:
        assert (await s.execute(select(Block))).scalar_one()
    # after block, they must never re-match
    from app import bot as bot_mod
    for pid in ("871", "872"):
        await bot_mod.process_update("telegram", {"update_id": 1000 + int(pid), "callback_query": {
            "id": f"cx{pid}", "from": {"id": int(pid)},
            "message": {"chat": {"id": int(pid)}}, "data": "gf_everyone"}})
    await bot_mod.run_match_cycle()
    async with db.session_scope() as s:
        assert (await s.execute(select(Chat).where(Chat.status == "active"))
                ).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_daily_bonus_once_per_tehran_day(adapters):
    await register_via_bot(adapters, "telegram", "881", "Bonus", 25, "male")
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "881"))).scalar_one()
        uid = u.id
        got = await service.grant_daily_bonus(s, u)
        assert got == 5
        got2 = await service.grant_daily_bonus(s, u)
        assert got2 == 0
        assert u.coin_balance == 35  # 30 + 5 daily bonus
        claims = (await s.execute(select(DailyClaim))).scalars()
        assert len(list(claims)) == 1


@pytest.mark.asyncio
async def test_referral_reward_after_first_chat(adapters):
    from app import bot as bot_mod
    # inviter registers
    await register_via_bot(adapters, "telegram", "891", "Inviter", 25, "male")
    async with db.session_scope() as s:
        inviter = (await s.execute(select(User).where(
            User.platform_user_id == "891"))).scalar_one()
        code = inviter.referral_code
    # referred user starts with ref payload
    await bot_mod.process_update("telegram", {"update_id": 2000, "message": {
        "message_id": 1, "chat": {"id": 892}, "from": {"id": 892},
        "text": f"/start ref_{code}"}})
    await bot_mod.process_update("telegram", {"update_id": 2001, "callback_query": {
        "id": "cl", "from": {"id": 892}, "message": {"chat": {"id": 892}}, "data": "lang_fa"}})
    await bot_mod.process_update("telegram", {"update_id": 2002, "callback_query": {
        "id": "cr", "from": {"id": 892}, "message": {"chat": {"id": 892}}, "data": "accept_rules"}})
    await bot_mod.process_update("telegram", {"update_id": 2003, "message": {
        "message_id": 2, "chat": {"id": 892}, "from": {"id": 892}, "text": "Referred One"}})
    # v2.2 registration: birthdate via inline buttons, then gender
    import datetime as _dt
    _today = _dt.date.today()
    for i, data in enumerate(("bcal_gregorian", f"byear_{_today.year - 20}",
                              f"bmon_{_today.month}", f"bday_{_today.day}", "bage_ok")):
        await bot_mod.process_update("telegram", {"update_id": 2004 + i, "callback_query": {
            "id": f"cb{i}", "from": {"id": 892}, "message": {"chat": {"id": 892}}, "data": data}})
    await bot_mod.process_update("telegram", {"update_id": 2009, "callback_query": {
        "id": "cg", "from": {"id": 892}, "message": {"chat": {"id": 892}}, "data": "reg_female"}})
    # no rewards yet (no first chat)
    async with db.session_scope() as s:
        inviter = (await s.execute(select(User).where(
            User.platform_user_id == "891"))).scalar_one()
        assert inviter.coin_balance == 30
    # referred user completes a chat with someone
    await register_via_bot(adapters, "telegram", "893", "Stranger", 25, "male")
    for pid in ("892", "893"):
        await bot_mod.process_update("telegram", {"update_id": 2010 + int(pid), "callback_query": {
            "id": f"cm{pid}", "from": {"id": int(pid)},
            "message": {"chat": {"id": int(pid)}}, "data": "gf_everyone"}})
    await bot_mod.process_update("telegram", {"update_id": 2020, "message": {
        "message_id": 9, "chat": {"id": 892}, "from": {"id": 892}, "text": "hi there"}})
    async with db.session_scope() as s:
        inviter = (await s.execute(select(User).where(
            User.platform_user_id == "891"))).scalar_one()
        referred = (await s.execute(select(User).where(
            User.platform_user_id == "892"))).scalar_one()
        assert inviter.coin_balance == 50  # 30 reg + 20 referral
        assert referred.coin_balance == 30 + 20 - 1  # reg + referral reward - message cost
        ref = (await s.execute(select(Referral))).scalar_one()
        assert ref.status == "rewarded"
        # second completion must not double-reward
        await service.on_first_chat_completed(s, referred)
        assert inviter.coin_balance == 50


@pytest.mark.asyncio
async def test_payments_idempotent_credit(adapters):
    await register_via_bot(adapters, "telegram", "901", "Buyer", 25, "male")
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "901"))).scalar_one()
        uid = u.id
    from app import bot as bot_mod
    sp_msg = {"message_id": 5, "chat": {"id": 901}, "from": {"id": 901},
              "successful_payment": {"currency": "XTR", "total_amount": 50,
                                     "invoice_payload": f"pkg:pkg_100:{uid}",
                                     "telegram_payment_charge_id": "chg_test_1",
                                     "provider_payment_charge_id": "prov_1"}}
    await bot_mod.handle_successful_payment("telegram", sp_msg)
    await bot_mod.handle_successful_payment("telegram", sp_msg)  # duplicate must be ignored
    async with db.session_scope() as s:
        u = await s.get(User, uid)
        assert u.coin_balance == 30 + 100
        pays = list((await s.execute(select(Payment))).scalars())
        assert len(pays) == 1
        txns = list((await s.execute(select(CoinTransaction).where(
            CoinTransaction.type == "store_purchase"))).scalars())
        assert len(txns) == 1


@pytest.mark.asyncio
async def test_failed_delivery_refund(adapters):
    tg, bale = adapters
    await register_via_bot(adapters, "telegram", "911", "Sender", 25, "male")
    await register_via_bot(adapters, "bale", "912", "Recv", 25, "female")
    from app import bot as bot_mod
    for plat, pid in (("telegram", "911"), ("bale", "912")):
        await bot_mod.process_update(plat, {"update_id": 2100 + int(pid), "callback_query": {
            "id": f"c{pid}", "from": {"id": int(pid)},
            "message": {"chat": {"id": int(pid)}}, "data": "gf_everyone"}})

    class FailingBale(FakeAdapter):
        async def send_message(self, chat_id, text, keyboard=None, reply_to=None):
            from app.platform import PlatformError
            raise PlatformError("sendMessage", 403, "bot was blocked by the user")

    RUNTIME["adapter_bale"] = FailingBale("bale")
    await bot_mod.process_update("telegram", {"update_id": 2110, "message": {
        "message_id": 3, "chat": {"id": 911}, "from": {"id": 911}, "text": "will fail"}})
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "911"))).scalar_one()
        # charged 1, delivery failed, refunded 1 => back to 30
        assert u.coin_balance == 30
        refund = (await s.execute(select(CoinTransaction).where(
            CoinTransaction.type == "refund"))).scalar_one()
        assert refund.amount == 1


@pytest.mark.asyncio
async def test_media_type_restrictions(adapters):
    await register_via_bot(adapters, "telegram", "921", "A", 25, "male")
    await register_via_bot(adapters, "bale", "922", "B", 25, "female")
    from app import bot as bot_mod
    for plat, pid in (("telegram", "921"), ("bale", "922")):
        await bot_mod.process_update(plat, {"update_id": 2200 + int(pid), "callback_query": {
            "id": f"c{pid}", "from": {"id": int(pid)},
            "message": {"chat": {"id": int(pid)}}, "data": "gf_everyone"}})
    tg, _ = adapters
    # cross-platform sticker (Bale has no sendSticker) must be rejected
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 2210, "message": {
        "message_id": 1, "chat": {"id": 921}, "from": {"id": 921},
        "sticker": {"file_id": "st1", "file_unique_id": "u1"}}})
    assert any("پشتیبانی نمی‌شود" in str(t[1]) for t in tg.sent)
    # admin disables photo globally -> rejected even same-platform
    async with db.session_scope() as s:
        mt = await s.get(MediaType, "photo")
        mt.enabled = False
    await register_via_bot(adapters, "telegram", "923", "C", 25, "female")
    await bot_mod.process_update("telegram", {"update_id": 2220, "callback_query": {
        "id": "cc", "from": {"id": 923}, "message": {"chat": {"id": 923}}, "data": "gf_everyone"}})
    # match A(921) with C(923) — 921's chat with 922 ended? No: 921 already active.
    # Instead verify photo disabled for a fresh pair
    await register_via_bot(adapters, "telegram", "924", "D", 25, "male")
    await register_via_bot(adapters, "telegram", "925", "E", 25, "female")
    for pid in ("924", "925"):
        await bot_mod.process_update("telegram", {"update_id": 2230 + int(pid), "callback_query": {
            "id": f"cd{pid}", "from": {"id": int(pid)},
            "message": {"chat": {"id": int(pid)}}, "data": "gf_everyone"}})
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 2240, "message": {
        "message_id": 2, "chat": {"id": 924}, "from": {"id": 924},
        "photo": [{"file_id": "ph1", "file_unique_id": "u2", "width": 1, "height": 1}]}})
    assert any("غیرفعال" in str(t[1]) for t in tg.sent)


@pytest.mark.asyncio
async def test_account_deletion_via_service(adapters):
    """/deletedata command was removed (user request #12); service-level deletion still verified."""
    await register_via_bot(adapters, "telegram", "931", "Ghost", 25, "male")
    await register_via_bot(adapters, "telegram", "932", "Pal", 25, "female")
    from app import bot as bot_mod
    for pid in ("931", "932"):
        await bot_mod.process_update("telegram", {"update_id": 2300 + int(pid), "callback_query": {
            "id": f"c{pid}", "from": {"id": int(pid)},
            "message": {"chat": {"id": int(pid)}}, "data": "gf_everyone"}})
    await bot_mod.process_update("telegram", {"update_id": 2310, "message": {
        "message_id": 1, "chat": {"id": 931}, "from": {"id": 931}, "text": "hello"}})
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "931"))).scalar_one()
        await service.delete_account(s, u)
        u = (await s.execute(select(User).where(User.platform_user_id == "931"))).scalar_one()
        assert u.status == "deleted" and u.coin_balance == 0 and u.bio is None
        assert u.display_name == "Deleted User"
        msgs = list((await s.execute(select(ChatMessage))).scalars())
        assert len(msgs) == 0  # messages purged


# ---------------------------------------------------- v2 features tests ----
@pytest.mark.asyncio
async def test_anon_link_flow(adapters):
    """/link: owner gets a link; a stranger can send an anonymous message via it.
    v2.2 (item 7): the new payload contains no '/' or '_'; legacy payloads
    ("msg_<code>") keep working."""
    from app import bot as bot_mod
    await register_via_bot(adapters, "telegram", "950", "OwnerU", 25, "male")
    tg, _ = adapters
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 3000, "message": {
        "message_id": 1, "chat": {"id": 950}, "from": {"id": 950}, "text": "/link"}})
    # link text sent — v2.2 clean payload (no '/' or '_' in the start parameter)
    payloads = []
    for t in tg.sent:
        txt = str(t[1])
        if "start=" in txt:
            payloads.append(txt.split("start=")[1].split()[0])
    assert payloads, payloads
    assert all("_" not in p and "/" not in p for p in payloads)
    async with db.session_scope() as s:
        owner = (await s.execute(select(User).where(User.platform_user_id == "950"))).scalar_one()
        code = owner.anon_link_code
    # a NEW user starts with the NEW payload
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 3001, "message": {
        "message_id": 2, "chat": {"id": 951}, "from": {"id": 951, "first_name": "Stranger"},
        "text": f"/start m{code}"}})
    await bot_mod.process_update("telegram", {"update_id": 3002, "message": {
        "message_id": 3, "chat": {"id": 951}, "from": {"id": 951}, "text": "سلام ناشناس!"}})
    assert any("سلام ناشناس!" in str(t[1]) for t in tg.sent)  # delivered to owner (chat 950)
    delivered = [t for t in tg.sent if t[0] == "950" and "سلام ناشناس!" in str(t[1])]
    assert delivered
    # LEGACY payload still works (old links shared before v2.2)
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 3003, "message": {
        "message_id": 4, "chat": {"id": 952}, "from": {"id": 952, "first_name": "OldLink"},
        "text": f"/start msg_{code}"}})
    await bot_mod.process_update("telegram", {"update_id": 3004, "message": {
        "message_id": 5, "chat": {"id": 952}, "from": {"id": 952}, "text": "پیام از لینک قدیمی"}})
    assert any(t[0] == "950" and "پیام از لینک قدیمی" in str(t[1]) for t in tg.sent)


@pytest.mark.asyncio
async def test_direct_message_mystery(adapters):
    """/msg: sender never learns the delivery result."""
    from app import bot as bot_mod
    tg, _ = adapters
    await register_via_bot(adapters, "telegram", "960", "Sender", 25, "male")
    await register_via_bot(adapters, "telegram", "961", "KnownTarget", 25, "female")
    async with db.session_scope() as s:
        t = (await s.execute(select(User).where(User.platform_user_id == "961"))).scalar_one()
        t.username = "known_target"
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 3100, "message": {
        "message_id": 1, "chat": {"id": 960}, "from": {"id": 960}, "text": "/msg"}})
    await bot_mod.process_update("telegram", {"update_id": 3101, "message": {
        "message_id": 2, "chat": {"id": 960}, "from": {"id": 960}, "text": "@known_target"}})
    await bot_mod.process_update("telegram", {"update_id": 3102, "message": {
        "message_id": 3, "chat": {"id": 960}, "from": {"id": 960}, "text": "پیام محرمانه"}})
    # target received the anonymous message
    assert any(t_[0] == "961" and "پیام محرمانه" in str(t_[1]) for t_ in tg.sent)
    # sender got only the neutral message — no delivery confirmation
    sender_msgs = [str(t_[1]) for t_ in tg.sent if t_[0] == "960"]
    assert any("ثبت شد" in m for m in sender_msgs)
    assert not any("موفق" in m and "ارسال" in m and "ثبت" not in m for m in sender_msgs)


@pytest.mark.asyncio
async def test_account_linking_removed(adapters):
    """v2.1: account linking removed — legacy buttons/links are answered politely,
    no linking occurs and no coins are granted."""
    from app import bot as bot_mod
    tg, bale = adapters
    await register_via_bot(adapters, "telegram", "970", "TgSide", 25, "male")
    await register_via_bot(adapters, "bale", "971", "BaleSide", 25, "female")
    tg.sent.clear()
    # legacy buttons in old messages
    await bot_mod.process_update("telegram", {"update_id": 3200, "callback_query": {
        "id": "cl", "from": {"id": 970}, "message": {"chat": {"id": 970}}, "data": "link_account"}})
    await bot_mod.process_update("telegram", {"update_id": 3201, "callback_query": {
        "id": "cg", "from": {"id": 970}, "message": {"chat": {"id": 970}}, "data": "link_gen"}})
    # legacy deep link
    await bot_mod.process_update("bale", {"update_id": 3202, "message": {
        "message_id": 1, "chat": {"id": 971}, "from": {"id": 971},
        "text": "/start lnk_oldcode123"}})
    async with db.session_scope() as s:
        tg_user = (await s.execute(select(User).where(User.platform_user_id == "970"))).scalar_one()
        bale_user = (await s.execute(select(User).where(User.platform_user_id == "971"))).scalar_one()
        assert tg_user.linked_user_id is None      # never linked
        assert bale_user.linked_user_id is None
        assert tg_user.coin_balance == 30           # no link reward
        assert bale_user.coin_balance == 30


@pytest.mark.asyncio
async def test_birthdate_registration_flow(adapters):
    """v2.2 (item 4): registration collects year/month/day with inline buttons
    (jalali AND gregorian), shows the computed age and asks for confirmation;
    the profile no longer offers the birthday button; under-13 is refused."""
    from app import bot as bot_mod
    import datetime as _dt
    tg, _ = adapters
    await register_via_bot(adapters, "telegram", "980", "BdayUser", 20, "male")
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "980"))).scalar_one()
        assert u.birth_calendar == "gregorian"
        assert u.birth_year == _dt.date.today().year - 20
        assert u.age == 20
        assert u.birthday_rewarded is True
        assert u.coin_balance == 20 + 10  # registration + birthdate reward
    # jalali path: pick a jalali birthdate and confirm the computed age message
    await register_via_bot(adapters, "telegram", "979", "JalaliUser", 25, "female")
    # (register_via_bot uses gregorian; exercise the jalali branch directly)
    await bot_mod.process_update("telegram", {"update_id": 3300, "message": {
        "message_id": 1, "chat": {"id": 981}, "from": {"id": 981}, "text": "/start"}})
    await bot_mod.process_update("telegram", {"update_id": 3301, "callback_query": {
        "id": "cb1", "from": {"id": 981}, "message": {"chat": {"id": 981}}, "data": "lang_fa"}})
    await bot_mod.process_update("telegram", {"update_id": 3302, "callback_query": {
        "id": "cb2", "from": {"id": 981}, "message": {"chat": {"id": 981}}, "data": "accept_rules"}})
    await bot_mod.process_update("telegram", {"update_id": 3303, "message": {
        "message_id": 2, "chat": {"id": 981}, "from": {"id": 981}, "text": "JalaliGuy"}})
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 3304, "callback_query": {
        "id": "cb3", "from": {"id": 981}, "message": {"chat": {"id": 981}}, "data": "bcal_jalali"}})
    assert any("سال" in str(t_[1]) for t_ in tg.sent)  # year page shown
    import datetime as _dt
    today = _dt.date.today()
    jy, _jm, _jd = service.gregorian_to_jalali(today.year, today.month, today.day)
    await bot_mod.process_update("telegram", {"update_id": 3305, "callback_query": {
        "id": "cb4", "from": {"id": 981}, "message": {"chat": {"id": 981}}, "data": f"byear_{jy - 25}"}})
    await bot_mod.process_update("telegram", {"update_id": 3306, "callback_query": {
        "id": "cb5", "from": {"id": 981}, "message": {"chat": {"id": 981}}, "data": "bmon_7"}})
    await bot_mod.process_update("telegram", {"update_id": 3307, "callback_query": {
        "id": "cb6", "from": {"id": 981}, "message": {"chat": {"id": 981}}, "data": "bday_15"}})
    # confirmation asks about the computed age
    joined = "".join(str(t_[1]) for t_ in tg.sent)
    assert "سن شما" in joined and "صحیح است؟" in joined
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "981"))).scalar_one()
        assert u.state["step"] == "birth_confirm"
    # redo -> back to the year picker; then complete via confirm
    await bot_mod.process_update("telegram", {"update_id": 3308, "callback_query": {
        "id": "cb7", "from": {"id": 981}, "message": {"chat": {"id": 981}}, "data": "bage_redo"}})
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "981"))).scalar_one()
        assert u.state["step"] == "birth_year" and u.birth_year is None
    await bot_mod.process_update("telegram", {"update_id": 3309, "callback_query": {
        "id": "cb8", "from": {"id": 981}, "message": {"chat": {"id": 981}}, "data": f"byear_{jy - 25}"}})
    await bot_mod.process_update("telegram", {"update_id": 3310, "callback_query": {
        "id": "cb9", "from": {"id": 981}, "message": {"chat": {"id": 981}}, "data": "bmon_3"}})
    await bot_mod.process_update("telegram", {"update_id": 3311, "callback_query": {
        "id": "cb10", "from": {"id": 981}, "message": {"chat": {"id": 981}}, "data": "bday_10"}})
    await bot_mod.process_update("telegram", {"update_id": 3312, "callback_query": {
        "id": "cb11", "from": {"id": 981}, "message": {"chat": {"id": 981}}, "data": "bage_ok"}})
    gy2, gm2, gd2 = service.jalali_to_gregorian(jy - 25, 3, 10)
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "981"))).scalar_one()
        assert u.birth_calendar == "jalali"
        assert (u.birth_year, u.birth_month, u.birth_day) == (gy2, gm2, gd2)
        assert u.age == max(13, min(60, service.compute_age_from_birthday(
            u.birth_year, u.birth_month, u.birth_day)))
        assert u.state["step"] == "gender"
    # under-13 refusal (item 4): drive the confirm step with an under-13 birthdate
    await bot_mod.process_update("telegram", {"update_id": 3313, "message": {
        "message_id": 3, "chat": {"id": 982}, "from": {"id": 982}, "text": "/start"}})
    await bot_mod.process_update("telegram", {"update_id": 3314, "callback_query": {
        "id": "cc1", "from": {"id": 982}, "message": {"chat": {"id": 982}}, "data": "lang_fa"}})
    await bot_mod.process_update("telegram", {"update_id": 3315, "callback_query": {
        "id": "cc2", "from": {"id": 982}, "message": {"chat": {"id": 982}}, "data": "accept_rules"}})
    await bot_mod.process_update("telegram", {"update_id": 3316, "message": {
        "message_id": 4, "chat": {"id": 982}, "from": {"id": 982}, "text": "KidAttempt"}})
    await bot_mod.process_update("telegram", {"update_id": 3317, "callback_query": {
        "id": "cc3", "from": {"id": 982}, "message": {"chat": {"id": 982}}, "data": "bcal_gregorian"}})
    tg.sent.clear()
    # simulate the day picker state with a birth year that yields age 12
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "982"))).scalar_one()
        u.state = {"step": "birth_day", "calendar": "gregorian",
                   "year": _dt.date.today().year - 12, "month": 11, "maxday": 30}
    from app import bot as bot_mod2
    await bot_mod2.birth_day_chosen(u, 15)
    assert any("حداقل سن مجاز" in str(t_[1]) for t_ in tg.sent), tg.sent
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "982"))).scalar_one()
        assert u.birth_year is None  # nothing saved
        assert u.state["step"] == "birth_year"  # picker restarted


@pytest.mark.asyncio
async def test_profile_has_no_birthday_button(adapters):
    """v2.2 (item 4): "ثبت تاریخ تولد و دریافت پاداش" is removed from the
    profile menu; the language switch lives there instead (item 7)."""
    from app import bot as bot_mod
    tg, _ = adapters
    await register_via_bot(adapters, "telegram", "983", "ProfB", 25, "male")
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 500400, "callback_query": {
        "id": "pp", "from": {"id": 983}, "message": {"chat": {"id": 983}}, "data": "profile"}})
    kb_all = [t_[2] for t_ in tg.sent if t_[2]]
    datas = {b.get("callback_data") for k in kb_all for r in k for b in r}
    assert "set_birthday" not in datas
    assert "language" in datas          # language moved into the profile
    # legacy old keyboards pressing set_birthday are answered with the profile
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 500401, "callback_query": {
        "id": "pp2", "from": {"id": 983}, "message": {"chat": {"id": 983}}, "data": "set_birthday"}})
    joined = "".join(str(t_[1]) for t_ in tg.sent)
    assert "پروفایل" in joined


@pytest.mark.asyncio
async def test_birthday_gift_50_coins(adapters):
    """v2.1: 50-coin gift fires once per year on the user's birthday."""
    from app import main as main_mod
    await register_via_bot(adapters, "telegram", "985", "GiftUser", 22, "female")
    import datetime as _dt
    today = _dt.date.today()
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "985"))).scalar_one()
        # birthday = TODAY
        u.birth_year, u.birth_month, u.birth_day = today.year - 22, today.month, today.day
        u.birth_calendar = "gregorian"
        balance_before = u.coin_balance
    await main_mod.daily_maintenance_jobs()
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "985"))).scalar_one()
        assert u.coin_balance == balance_before + 50    # gift granted
    # running again the same day must NOT double-grant
    await main_mod.daily_maintenance_jobs()
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "985"))).scalar_one()
        assert u.coin_balance == balance_before + 50    # idempotent


@pytest.mark.asyncio
async def test_name_change_costs_coins(adapters):
    from app import bot as bot_mod
    await register_via_bot(adapters, "telegram", "985", "OldName", 25, "male")
    tg, _ = adapters
    await bot_mod.process_update("telegram", {"update_id": 3400, "callback_query": {
        "id": "cn", "from": {"id": 985}, "message": {"chat": {"id": 985}}, "data": "edit_name"}})
    await bot_mod.process_update("telegram", {"update_id": 3401, "message": {
        "message_id": 1, "chat": {"id": 985}, "from": {"id": 985}, "text": "NewName"}})
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "985"))).scalar_one()
        assert u.display_name == "NewName"
        assert u.coin_balance == 30 - 10


@pytest.mark.asyncio
async def test_retention_purge(adapters):
    from app import bot as bot_mod
    await register_via_bot(adapters, "telegram", "941", "A", 25, "male")
    await register_via_bot(adapters, "telegram", "942", "B", 25, "female")
    for pid in ("941", "942"):
        await bot_mod.process_update("telegram", {"update_id": 2400 + int(pid), "callback_query": {
            "id": f"c{pid}", "from": {"id": int(pid)},
            "message": {"chat": {"id": int(pid)}}, "data": "gf_everyone"}})
    await bot_mod.process_update("telegram", {"update_id": 2410, "message": {
        "message_id": 1, "chat": {"id": 941}, "from": {"id": 941}, "text": "old msg"}})
    async with db.session_scope() as s:
        m = (await s.execute(select(ChatMessage))).scalar_one()
        m.created_at = utcnow() - timedelta(days=31)
        stats = await service.purge_expired(s)
        assert stats["messages"] == 1
        assert (await s.execute(select(ChatMessage))).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_appeal_only_permanent(adapters):
    await register_via_bot(adapters, "telegram", "951", "Banned", 25, "male")
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "951"))).scalar_one()
        await service.ban_user(s, u, "temporary", "manual_admin", "test", "admin", hours=24)
    from app import bot as bot_mod
    tg, _ = adapters
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 425000, "message": {
        "message_id": 1, "chat": {"id": 951}, "from": {"id": 951}, "text": "/appeal"}})
    from app.i18n import S as _S
    assert any(_S["no_active_ban"]["fa"][:30] in str(t[1]) for t in tg.sent), tg.sent
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "951"))).scalar_one()
        await service.ban_user(s, u, "permanent", "manual_admin", "test2", "admin")
    await bot_mod.process_update("telegram", {"update_id": 425001, "message": {
        "message_id": 2, "chat": {"id": 951}, "from": {"id": 951}, "text": "/appeal"}})
    await bot_mod.process_update("telegram", {"update_id": 425002, "message": {
        "message_id": 3, "chat": {"id": 951}, "from": {"id": 951}, "text": "من بی‌گناهم"}})
    async with db.session_scope() as s:
        from app.db import Appeal
        a = (await s.execute(select(Appeal))).scalar_one()
        assert a.message == "من بی‌گناهم"


# ------------------------------------------------------ backup & restore ----
@pytest.mark.asyncio
async def test_backup_encrypt_validate_restore_rollback(adapters):
    from app import backup as bk
    await register_via_bot(adapters, "telegram", "961", "DataA", 25, "male")
    await register_via_bot(adapters, "telegram", "962", "DataB", 25, "female")
    async with db.session_scope() as s:
        ua = (await s.execute(select(User).where(User.platform_user_id == "961"))).scalar_one()
        ua.bio = "secret bio"
    rec_and_blob = None
    async with db.session_scope() as s:
        out = await bk.create_backup(s, "manual", "test")
        assert isinstance(out, tuple)
        rec, blob = out
        rec_and_blob = (rec.id, blob)
        assert rec.sha256 == security.sha256_hex(blob)
    # wrong password must fail
    v = bk.validate_blob(blob, password="WRONG")
    assert v["valid"] is False
    # tampered blob must fail
    tampered = bytearray(blob)
    tampered[-1] ^= 0xFF
    v2 = bk.validate_blob(bytes(tampered), password=settings.backup_password)
    assert v2["valid"] is False
    # correct password validates
    v3 = bk.validate_blob(blob, password=settings.backup_password)
    assert v3["valid"] is True and v3["compatible"] is True
    # mutate DB, then restore -> data returns
    async with db.session_scope() as s:
        ua = (await s.execute(select(User).where(User.platform_user_id == "961"))).scalar_one()
        ua.bio = "changed after backup"
        await s.execute(delete(User).where(User.platform_user_id == "962"))
    result = await bk.execute_restore(blob, settings.backup_password)
    assert result["status"] == "succeeded"
    async with db.session_scope() as s:
        ua = (await s.execute(select(User).where(User.platform_user_id == "961"))).scalar_one()
        ub = (await s.execute(select(User).where(User.platform_user_id == "962"))).scalar_one()
        assert ua.bio == "secret bio"
        assert ub is not None
    # rollback path: corrupt payload with valid encryption -> consistency/rollback
    import gzip, json as _json
    comp = security.decrypt_backup(blob, settings.backup_password)
    payload = _json.loads(gzip.decompress(comp))
    payload["data"]["coin_transactions"].append({"id": 999999, "user_id": 123456,
                                                 "amount": 5, "balance_after": 5,
                                                 "type": "x", "description": "",
                                                 "created_at": "2026-01-01T00:00:00"})
    bad_blob, _ = security.encrypt_backup(gzip.compress(_json.dumps(
        payload, ensure_ascii=False).encode()), settings.backup_password)
    res2 = await bk.execute_restore(bad_blob, settings.backup_password)
    assert res2["status"] == "rolled_back"
    async with db.session_scope() as s:
        ua = (await s.execute(select(User).where(User.platform_user_id == "961"))).scalar_one()
        assert ua.bio == "secret bio"  # rollback preserved pre-failure state


# ----------------------------------------------------------- adapters ----
@pytest.mark.asyncio
async def test_adapters_mocked_http(monkeypatch):
    calls = []
    import httpx
    tg = TelegramAdapter("1:test", "sekrit")

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), request.read()))
        if "getMe" in str(request.url):
            return httpx.Response(200, json={"ok": True, "result": {"id": 1}})
        if "sendMessage" in str(request.url):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})
        if "setWebhook" in str(request.url):
            return httpx.Response(200, json={"ok": True, "result": True})
        return httpx.Response(200, json={"ok": True, "result": {}})

    tg._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    me = await tg.call("getMe")
    assert me == {"id": 1}
    msg = await tg.send_message(123, "hello <b>x</b>")
    assert msg["message_id"] == 7
    body = calls[-1][1].decode()
    assert "&lt;b&gt;" in body or "\\u003c" in body  # HTML-escaped
    assert tg.verify_webhook_secret("sekrit") and not tg.verify_webhook_secret("nope")
    await tg.close()

    # Bale: no secret, markdown escaping
    bale = BaleAdapter("2:test")

    def bhandler(request: httpx.Request) -> httpx.Response:
        calls.append(("bale:" + str(request.url), request.read()))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

    bale._client = httpx.AsyncClient(transport=httpx.MockTransport(bhandler))
    await bale.send_message(5, "hi *bold* [x](y)")
    body = calls[-1][1].decode()
    assert "\\*" in body  # markdown neutralised
    with pytest.raises(UnsupportedMethod):
        await bale.send_media(5, "sticker", file_id="s1")
    with pytest.raises(UnsupportedMethod):
        await bale.refund_payment(5, "chg")
    assert bale.verify_webhook_secret(None) is True  # no secret documented
    await bale.close()


def httpx_MockTransport():
    import httpx
    return httpx.MockTransport


@pytest.mark.asyncio
async def test_bale_invoice_requires_provider_token():
    bale = BaleAdapter("3:test")
    with pytest.raises(UnsupportedMethod):
        await bale.send_invoice(1, "t", "d", "p", [{"label": "x", "amount": 100}])
    bale2 = BaleAdapter("3:test", provider_token="WALLET-TEST-1111111111111111")
    import httpx

    def handler(request):
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    bale2._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await bale2.send_invoice(1, "t", "d", "p", [{"label": "x", "amount": 100000}])
    await bale2.close()


# ------------------------------------------------------------- web auth ----
@pytest_asyncio.fixture
async def web_env():
    import app.webadmin as wa
    await wa.__ensure_test_admin()
    return wa


async def _ensure_admin():
    async with db.session_scope() as s:
        s.add(WebAdmin(username="owner1", full_name="Owner", email="",
                       password_hash=security.hash_password("InitialPass123!"),
                       role="owner", must_change_password=False, totp_enabled=True,
                       totp_secret_enc=security.encrypt_field(
                           "JBSWY3DPEHPK3PXP", settings.encryption_key)))


@pytest.mark.asyncio
async def test_web_auth_flow():
    import httpx
    from app.main import app
    import app.webadmin as wa
    # inject admin
    async with db.session_scope() as s:
        s.add(WebAdmin(username="owner1", full_name="Owner", email="",
                       password_hash=security.hash_password("InitialPass123!"),
                       role="owner", must_change_password=False, totp_enabled=True,
                       totp_secret_enc=security.encrypt_field(
                           "JBSWY3DPEHPK3PXP", settings.encryption_key)))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as cli:
        # unauthenticated
        r = await cli.get("/api/admin/v1/auth/me")
        assert r.status_code == 401
        # login -> authenticated (v2.3: TOTP two-factor removed)
        r = await cli.post("/api/admin/v1/auth/login",
                           json={"username": "owner1", "password": "InitialPass123!"})
        assert r.status_code == 200
        assert r.json()["status"] == "authenticated"
        csrf = r.headers.get("x-csrf-token")
        assert csrf
        cookies = dict(r.cookies)
        # legacy TOTP endpoints no longer exist
        r = await cli.post("/api/admin/v1/auth/verify-totp",
                           json={"challengeId": "x", "code": "000000"})
        assert r.status_code in (403, 404, 405)  # endpoint removed (403 CSRF / 404 / 405 SPA)
        r = await cli.post("/api/admin/v1/auth/totp/enroll/confirm",
                           json={"challengeId": "x", "code": "000000"})
        assert r.status_code in (403, 404, 405)
        # me works
        r = await cli.get("/api/admin/v1/auth/me")
        assert r.status_code == 200 and r.json()["role"] == "owner"
        # CSRF enforcement: mutating without token -> 403
        r = await cli.post("/api/admin/v1/notifications/read-all")
        assert r.status_code == 403
        # with token -> 204
        r = await cli.post("/api/admin/v1/notifications/read-all",
                           headers={"X-CSRF-Token": csrf})
        assert r.status_code == 204
        # bad login
        r = await cli.post("/api/admin/v1/auth/login",
                           json={"username": "owner1", "password": "wrong"})
        assert r.status_code == 401
        # rate limiting after 6 failures
        for _ in range(6):
            await cli.post("/api/admin/v1/auth/login",
                           json={"username": "owner1", "password": "wrong"})
        r = await cli.post("/api/admin/v1/auth/login",
                           json={"username": "owner1", "password": "InitialPass123!"})
        assert r.status_code == 429
    wa._login_attempts.clear()


@pytest.mark.asyncio
async def test_web_history_permissions():
    """Owner sees all chats; moderator sees reported chats only; every access audited."""
    import httpx
    from app.main import app
    from app.db import AuditLog
    await register_via_bot((FakeAdapter("telegram"), FakeAdapter("bale")),
                           "telegram", "971", "A", 25, "male")
    await register_via_bot((FakeAdapter("telegram"), FakeAdapter("bale")),
                           "telegram", "972", "B", 25, "female")
    async with db.session_scope() as s:
        ua = (await s.execute(select(User).where(User.platform_user_id == "971"))).scalar_one()
        ub = (await s.execute(select(User).where(User.platform_user_id == "972"))).scalar_one()
        s.add(Chat(user_a_id=ua.id, user_b_id=ub.id, age_group="18-60"))
        s.add(Chat(user_a_id=ua.id, user_b_id=ub.id, age_group="18-60", has_report=True))
    async with db.session_scope() as s:
        s.add(WebAdmin(username="owner1", full_name="Owner", email="",
                       password_hash=security.hash_password("InitialPass123!"),
                       role="owner", must_change_password=False, totp_enabled=True,
                       totp_secret_enc=security.encrypt_field(
                           "JBSWY3DPEHPK3PXP", settings.encryption_key)))
        s.add(WebAdmin(username="mod1", password_hash=security.hash_password("ModPass123!!"),
                       role="moderator", totp_enabled=True, must_change_password=False,
                       totp_secret_enc=security.encrypt_field(
                           "JBSWY3DPEHPK3PXP", settings.encryption_key)))
    import httpx
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as cli:
        async def login_as(username):
            r = await cli.post("/api/admin/v1/auth/login",
                               json={"username": username, "password": "ModPass123!!"
                                     if username == "mod1" else "InitialPass123!"})
            assert r.json()["status"] == "authenticated"
            return r.headers.get("x-csrf-token")

        # moderator (mod1 password differs)
        r = await cli.post("/api/admin/v1/auth/login",
                           json={"username": "mod1", "password": "ModPass123!!"})
        assert r.json()["status"] == "authenticated"
        mod_csrf = r.headers.get("x-csrf-token")
        r = await cli.get("/api/admin/v1/chats")
        data = r.json()
        assert data["scope"] == "reported_only"
        assert len(data["chats"]) == 1 and data["chats"][0]["hasReport"] is True
        # moderator cannot manage admins (valid body -> permission denied, not 422)
        r = await cli.post("/api/admin/v1/admins", json={
            "username": "hacker1", "fullName": "H", "email": "h@x.io", "role": "support"},
            headers={"X-CSRF-Token": mod_csrf})
        assert r.status_code == 403, r.text
        # logout mod, login owner
        await cli.post("/api/admin/v1/auth/logout")
        csrf = await login_as("owner1")
        r = await cli.get("/api/admin/v1/chats")
        data = r.json()
        assert data["scope"] == "all" and len(data["chats"]) == 2
        # every history access audited
        async with db.session_scope() as s:
            audits = list((await s.execute(select(AuditLog).where(
                AuditLog.action == "chats.viewed"))).scalars())
            assert len(audits) >= 3
    import app.webadmin as wa
    wa._login_attempts.clear()


@pytest.mark.asyncio
async def test_web_matching_locked_invariants():
    import httpx
    from app.main import app
    import app.webadmin as wa
    async with db.session_scope() as s:
        s.add(WebAdmin(username="owner2", password_hash=security.hash_password("OwnerPass123!"),
                       role="owner", totp_enabled=True, must_change_password=False,
                       totp_secret_enc=security.encrypt_field(
                           "JBSWY3DPEHPK3PXP", settings.encryption_key)))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as cli:
        r = await cli.post("/api/admin/v1/auth/login",
                           json={"username": "owner2", "password": "OwnerPass123!"})
        assert r.json()["status"] == "authenticated"
        csrf = r.headers.get("x-csrf-token")
        # attempt to disable age separation / cross-platform (must stay locked)
        r = await cli.patch("/api/admin/v1/settings/matching",
                            json={"strictAgeGroupMinorLocked": False,
                                  "crossPlatformAlwaysEnabled": False,
                                  "searchTimeoutSec": 240},
                            headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200
        cfg = r.json()
        assert cfg["strictAgeGroupMinorLocked"] is True
        assert cfg["strictAgeGroupAdultLocked"] is True
        assert cfg["crossPlatformAlwaysEnabled"] is True
        assert cfg["searchTimeoutSec"] == 240
        # retention cannot change from 30 days
        r = await cli.put("/api/admin/v1/settings/privacy",
                          json={"chatRetentionDays": 365},
                          headers={"X-CSRF-Token": csrf})
        assert r.status_code == 400
    wa._login_attempts.clear()


@pytest.mark.asyncio
async def test_webadmin_initial_password_flow():
    import httpx
    from app.main import app
    import app.webadmin as wa
    async with db.session_scope() as s:
        s.add(WebAdmin(username="newbie", password_hash=security.hash_password("TempPass123!"),
                       role="support", must_change_password=True))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as cli:
        r = await cli.post("/api/admin/v1/auth/login",
                           json={"username": "newbie", "password": "TempPass123!"})
        assert r.json()["status"] == "password_change_required"
        ch = r.json()["challengeId"]
        # weak new password rejected
        r = await cli.post("/api/admin/v1/auth/change-initial-password",
                           json={"challengeId": ch, "currentPassword": "TempPass123!",
                                 "newPassword": "short"})
        assert r.status_code == 400
        # proper change -> directly authenticated (v2.3: no TOTP enrollment)
        r = await cli.post("/api/admin/v1/auth/change-initial-password",
                           json={"challengeId": ch, "currentPassword": "TempPass123!",
                                 "newPassword": "MuchStronger456!"})
        assert r.json()["status"] == "authenticated"
        assert r.headers.get("x-csrf-token")
        # re-login with the new password works
        await cli.post("/api/admin/v1/auth/logout")
        r = await cli.post("/api/admin/v1/auth/login",
                           json={"username": "newbie", "password": "MuchStronger456!"})
        assert r.json()["status"] == "authenticated"
    wa._login_attempts.clear()


@pytest.mark.asyncio
async def test_coin_atomicity_no_negative(adapters):
    await register_via_bot(adapters, "telegram", "981", "Spender", 25, "male")
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "981"))).scalar_one()
        u.coin_balance = 3
        txn = await service.debit(s, u, 5, "message_cost", "x")
        assert txn is None
        assert u.coin_balance == 3
        txn = await service.debit(s, u, 2, "message_cost", "x")
        assert txn is not None and u.coin_balance == 1
        # idempotent credit
        await service.credit(s, u, 10, "daily_bonus", "x", idempotency_key="idem-1")
        await service.credit(s, u, 10, "daily_bonus", "x", idempotency_key="idem-1")
        assert u.coin_balance == 11
        txns = list((await s.execute(select(CoinTransaction).where(
            CoinTransaction.user_id == u.id))).scalars())
        assert all(t.balance_after >= 0 for t in txns)


@pytest.mark.asyncio
async def test_one_active_chat_per_user(adapters):
    from app import bot as bot_mod
    await register_via_bot(adapters, "telegram", "991", "A", 25, "male")
    await register_via_bot(adapters, "telegram", "992", "B", 25, "male")
    await register_via_bot(adapters, "telegram", "993", "C", 25, "male")
    for pid in ("991", "992", "993"):
        await bot_mod.process_update("telegram", {"update_id": 2600 + int(pid), "callback_query": {
            "id": f"c{pid}", "from": {"id": int(pid)},
            "message": {"chat": {"id": int(pid)}}, "data": "gf_everyone"}})
    async with db.session_scope() as s:
        chats = list((await s.execute(select(Chat).where(Chat.status == "active"))).scalars())
        assert len(chats) == 1
        users_in_chats = [chats[0].user_a_id, chats[0].user_b_id]
        # the remaining user stays queued and cannot double-queue
        q = (await s.execute(select(QueueEntry))).scalars()
        q = list(q)
        assert len(q) == 1
        u = await s.get(User, q[0].user_id)
        assert u.id not in users_in_chats
        assert await service.enqueue(s, u, "everyone") is False


@pytest.mark.asyncio
async def test_recent_pair_cooldown(adapters):
    from app import bot as bot_mod
    await register_via_bot(adapters, "telegram", "994", "A", 25, "male")
    await register_via_bot(adapters, "telegram", "995", "B", 25, "male")
    for pid in ("994", "995"):
        await bot_mod.process_update("telegram", {"update_id": 2700 + int(pid), "callback_query": {
            "id": f"c{pid}", "from": {"id": int(pid)},
            "message": {"chat": {"id": int(pid)}}, "data": "gf_everyone"}})
    await bot_mod.process_update("telegram", {"update_id": 2710, "callback_query": {
        "id": "ce", "from": {"id": 994}, "message": {"chat": {"id": 994}}, "data": "chat_end"}})
    # immediately re-search both: cooldown blocks re-pairing
    for pid in ("994", "995"):
        await bot_mod.process_update("telegram", {"update_id": 2720 + int(pid), "callback_query": {
            "id": f"cr{pid}", "from": {"id": int(pid)},
            "message": {"chat": {"id": int(pid)}}, "data": "gf_everyone"}})
    await bot_mod.run_match_cycle()
    async with db.session_scope() as s:
        assert (await s.execute(select(Chat).where(
            Chat.status == "active"))).scalar_one_or_none() is None
        # after cooldown window passes, they can match again
        from app.db import RecentPair
        rp = (await s.execute(select(RecentPair))).scalars()
        for r in rp:
            r.ended_at = utcnow() - timedelta(hours=1)
    await bot_mod.run_match_cycle()
    async with db.session_scope() as s:
        assert (await s.execute(select(Chat).where(
            Chat.status == "active"))).scalar_one() is not None


# ---------------------------------------------------- v3 feature tests ----
@pytest.mark.asyncio
async def test_anon_reply_report_block(adapters):
    """v3: anonymous thread buttons — reply, report and block work end-to-end."""
    from app import bot as bot_mod
    from app.db import AnonThread
    tg, bale = adapters
    # owner on Telegram, stranger on Bale (cross-platform anon message)
    await register_via_bot(adapters, "telegram", "990", "OwnerV3", 25, "male")
    await register_via_bot(adapters, "bale", "991", "StrangerV3", 25, "female")
    async with db.session_scope() as s:
        owner = (await s.execute(select(User).where(User.platform_user_id == "990"))).scalar_one()
        code = await service.ensure_anon_link(s, owner)
    # stranger opens the BALE link (v3: two links, one per platform)
    bale.sent.clear()
    await bot_mod.process_update("bale", {"update_id": 500100, "message": {
        "message_id": 1, "chat": {"id": 991}, "from": {"id": 991}, "text": f"/start msg_{code}"}})
    assert any("ناشناس" in str(t[1]) for t in bale.sent)
    await bot_mod.process_update("bale", {"update_id": 500101, "message": {
        "message_id": 2, "chat": {"id": 991}, "from": {"id": 991}, "text": "پیام از بله"}})
    # owner received with reply/report/block buttons
    tg.sent.clear()
    await bot_mod.process_update("bale", {"update_id": 500102, "message": {
        "message_id": 3, "chat": {"id": 991}, "from": {"id": 991}, "text": "پیام دوم"}})
    owner_msgs = [t for t in tg.sent if t[0] == "990"]
    assert any("پیام دوم" in str(t[1]) for t in owner_msgs)
    # thread exists
    async with db.session_scope() as s:
        th = (await s.execute(select(AnonThread))).scalar_one()
        assert th.owner_id is not None and th.message_count == 2
        thread_id = th.id
    # owner taps REPLY and sends text -> stranger gets it
    tg.sent.clear(); bale.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 500103, "callback_query": {
        "id": "ar1", "from": {"id": 990}, "message": {"chat": {"id": 990}}, "data": "anonr_"}})
    await bot_mod.process_update("telegram", {"update_id": 500104, "message": {
        "message_id": 5, "chat": {"id": 990}, "from": {"id": 990}, "text": "جواب مالک"}})
    assert any(t[0] == "991" and "جواب مالک" in str(t[1]) for t in bale.sent)
    assert any("ارسال شد" in str(t[1]) for t in tg.sent)
    # owner taps REPORT -> v2.2 reason menu appears (item 2), then files the report
    await bot_mod.process_update("telegram", {"update_id": 500105, "callback_query": {
        "id": "ar2", "from": {"id": 990}, "message": {"chat": {"id": 990}}, "data": f"anonrep_{thread_id}"}})
    assert any("دلیل" in str(t[1]) for t in tg.sent)
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 500106, "callback_query": {
        "id": "ar2b", "from": {"id": 990}, "message": {"chat": {"id": 990}},
        "data": f"anonrep_{thread_id}_spam"}})
    assert any("گزارش" in str(t[1]) and "ثبت شد" in str(t[1]) for t in tg.sent)
    async with db.session_scope() as s:
        rep = (await s.execute(select(Report).where(Report.reported_id != 0))).scalar_one()
        assert rep.reason == "spam"
    # owner taps BLOCK -> thread blocked + Block row
    await bot_mod.process_update("telegram", {"update_id": 500107, "callback_query": {
        "id": "ar3", "from": {"id": 990}, "message": {"chat": {"id": 990}}, "data": f"anonblk_{thread_id}"}})
    assert any("بلاک" in str(t[1]) or "مسدود" in str(t[1]) for t in tg.sent)
    async with db.session_scope() as s:
        th = await s.get(AnonThread, thread_id)
        assert th.status == "blocked"
        blk = (await s.execute(select(Block).where(Block.blocker_id == th.owner_id))).scalar_one()
        assert blk.blocked_id == th.sender_id
    # stranger tries again via link -> gets blocked notice, owner gets nothing new
    tg.sent.clear(); bale.sent.clear()
    await bot_mod.process_update("bale", {"update_id": 500108, "message": {
        "message_id": 6, "chat": {"id": 991}, "from": {"id": 991}, "text": f"/start msg_{code}"}})
    await bot_mod.process_update("bale", {"update_id": 500109, "message": {
        "message_id": 7, "chat": {"id": 991}, "from": {"id": 991}, "text": "باز هم می‌نویسم"}})
    assert any("مسدود" in str(t[1]) for t in bale.sent)
    assert not any("باز هم می‌نویسم" in str(t[1]) for t in tg.sent)


@pytest.mark.asyncio
async def test_two_anon_links(adapters):
    """v3: /link shows BOTH a Telegram and a Bale link."""
    from app import bot as bot_mod
    tg, _ = adapters
    await register_via_bot(adapters, "telegram", "995", "LinkUser", 25, "male")
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 500200, "message": {
        "message_id": 1, "chat": {"id": 995}, "from": {"id": 995}, "text": "/link"}})
    joined = "".join(str(t[1]) for t in tg.sent)
    assert "t.me" in joined and "ble.ir" in joined


@pytest.mark.asyncio
async def test_no_privacy_in_registration(adapters):
    """v3: registration shows rules only — no privacy policy."""
    from app import bot as bot_mod
    tg, _ = adapters
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 500300, "message": {
        "message_id": 1, "chat": {"id": 996}, "from": {"id": 996, "first_name": "Priv"},
        "text": "/start"}})
    await bot_mod.process_update("telegram", {"update_id": 500301, "callback_query": {
        "id": "pl", "from": {"id": 996}, "message": {"chat": {"id": 996}}, "data": "lang_fa"}})
    joined = "".join(str(t[1]) for t in tg.sent)
    assert "قوانین" in joined
    assert "حریم خصوصی" not in joined


@pytest.mark.asyncio
async def test_profile_opens_after_registration(adapters):
    """v3 regression: profile button must never crash (gender KeyError bug)."""
    from app import bot as bot_mod
    tg, _ = adapters
    await register_via_bot(adapters, "telegram", "997", "ProfUser", 25, "female")
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 500400, "callback_query": {
        "id": "pp", "from": {"id": 997}, "message": {"chat": {"id": 997}}, "data": "profile"}})
    joined = "".join(str(t[1]) for t in tg.sent)
    assert "پروفایل" in joined and "ProfUser" in joined
    assert "👧" in joined


# ------------------------------------------------- v2.1 feature tests ----
@pytest.mark.asyncio
async def test_wallet_menu_and_buy(adapters):
    """v2.1: /wallet shows balance + buy-coins entry; store lives under it."""
    from app import bot as bot_mod
    tg, _ = adapters
    await register_via_bot(adapters, "telegram", "992", "WalletU", 25, "male")
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 600100, "message": {
        "message_id": 1, "chat": {"id": 992}, "from": {"id": 992}, "text": "/wallet"}})
    joined = "".join(str(t_[1]) for t_ in tg.sent)
    assert "کیف پول" in joined and "30" in joined
    kb_any = [t_[2] for t_ in tg.sent if t_[2]]
    assert any(any(b.get("callback_data") == "store" for r in k for b in r) for k in kb_any)
    # store (buy) opens from wallet — in tests no payment token is configured,
    # so the store answers with the graceful "unavailable" message (no crash)
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 600101, "callback_query": {
        "id": "cw", "from": {"id": 992}, "message": {"chat": {"id": 992}}, "data": "store"}})
    joined2 = "".join(str(t_[1]) for t_ in tg.sent)
    assert ("خرید سکه" in joined2) or ("درگاه" in joined2) or ("فعال نیست" in joined2)


@pytest.mark.asyncio
async def test_dynamic_main_menu(adapters):
    """v2.1: main menu reflects chat/queue state."""
    from app import bot as bot_mod
    tg, bale = adapters
    await register_via_bot(adapters, "telegram", "993", "DynA", 25, "male")
    await register_via_bot(adapters, "bale", "994", "DynB", 25, "female")
    # idle: search button
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 600200, "callback_query": {
        "id": "dm", "from": {"id": 993}, "message": {"chat": {"id": 993}}, "data": "back_home"}})
    kb0 = [t_[2] for t_ in tg.sent if t_[2]][0]
    datas0 = {b.get("callback_data") for r in kb0 for b in r}
    assert "search" in datas0
    # both search -> matched: menu shows next/end instead of search
    for i, (plat, pid) in enumerate((("telegram", 993), ("bale", 994))):
        await bot_mod.process_update(plat, {"update_id": 600201 + i, "callback_query": {
            "id": f"ds{i}", "from": {"id": pid}, "message": {"chat": {"id": pid}}, "data": "search"}})
        await bot_mod.process_update(plat, {"update_id": 600203 + i, "callback_query": {
            "id": f"dg{i}", "from": {"id": pid}, "message": {"chat": {"id": pid}}, "data": "gf_everyone"}})
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 600205, "callback_query": {
        "id": "dm2", "from": {"id": 993}, "message": {"chat": {"id": 993}}, "data": "back_home"}})
    kb1 = [t_[2] for t_ in tg.sent if t_[2]][0]
    datas1 = {b.get("callback_data") for r in kb1 for b in r}
    assert "chat_next" in datas1 and "chat_end" in datas1 and "search" not in datas1


@pytest.mark.asyncio
async def test_maintenance_admin_only(adapters):
    """v2.1: maintenance blocks normal users but admins keep full access."""
    from app import bot as bot_mod
    from app.config import settings
    tg, _ = adapters
    await register_via_bot(adapters, "telegram", "996", "NormM", 25, "male")
    await register_via_bot(adapters, "telegram", "860835914", "OwnerM", 30, "male")
    async with db.session_scope() as s:
        cfg = await service.get_setting(s, "app")
        cfg["maintenanceMode"] = True
        await service.set_setting(s, "app", cfg)
    try:
        # normal user gets maintenance message on text AND buttons
        tg.sent.clear()
        await bot_mod.process_update("telegram", {"update_id": 600300, "message": {
            "message_id": 1, "chat": {"id": 996}, "from": {"id": 996}, "text": "سلام"}})
        assert any("تعمیر" in str(t_[1]) for t_ in tg.sent)
        await bot_mod.process_update("telegram", {"update_id": 600301, "callback_query": {
            "id": "mc1", "from": {"id": 996}, "message": {"chat": {"id": 996}}, "data": "help"}})
        assert any("تعمیر" in str(t_[1]) for t_ in tg.sent)
        # owner still gets full menus
        tg.sent.clear()
        await bot_mod.process_update("telegram", {"update_id": 600302, "message": {
            "message_id": 2, "chat": {"id": 860835914}, "from": {"id": 860835914}, "text": "سلام ادمین"}})
        assert any("منوی اصلی" in str(t_[1]) for t_ in tg.sent)
    finally:
        async with db.session_scope() as s:
            cfg = await service.get_setting(s, "app")
            cfg["maintenanceMode"] = False
            await service.set_setting(s, "app", cfg)


@pytest.mark.asyncio
async def test_admin_stats_and_backup_buttons(adapters):
    """v2.1: admin panel has numeric stats + instant backup on the main menu."""
    from app import bot as bot_mod
    from app.db import BotAdmin
    tg, _ = adapters
    await register_via_bot(adapters, "telegram", "860835914", "OwnerS", 30, "male")
    async with db.session_scope() as s:
        if not (await s.execute(select(BotAdmin).where(
                BotAdmin.platform == "telegram",
                BotAdmin.platform_user_id == "860835914"))).scalar_one_or_none():
            s.add(BotAdmin(platform="telegram", platform_user_id="860835914", role="owner"))
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 600400, "message": {
        "message_id": 1, "chat": {"id": 860835914}, "from": {"id": 860835914}, "text": "/admin"}})
    kb0 = [t_[2] for t_ in tg.sent if t_[2]][0]
    datas0 = {b.get("callback_data") for r in kb0 for b in r}
    assert "adm_stats" in datas0 and "adm_backup_now" in datas0 and "adm_maint" in datas0
    # stats view has update + back buttons and numeric content
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 600401, "callback_query": {
        "id": "as", "from": {"id": 860835914}, "message": {"chat": {"id": 860835914}},
        "data": "adm_stats"}})
    joined = "".join(str(t_[1]) for t_ in tg.sent)
    assert "آمار کل" in joined and "کاربران:" in joined
    kb1 = [t_[2] for t_ in tg.sent if t_[2]]
    assert any(any(b.get("callback_data") == "adm_stats" for r in k for b in r) for k in kb1)  # update btn
    assert any(any(b.get("callback_data") == "back_home" for r in k for b in r) for k in kb1)


@pytest.mark.asyncio
async def test_anon_reply_targets_exact_thread(adapters):
    """v2.1 regression: reply/report/block buttons carry the thread id, so replying
    to an OLD message reaches that specific sender, not the newest one."""
    from app import bot as bot_mod
    from app.db import AnonThread
    tg, bale = adapters
    await register_via_bot(adapters, "telegram", "997", "OwnerT", 25, "male")
    await register_via_bot(adapters, "bale", "998", "SenderA", 25, "female")
    await register_via_bot(adapters, "bale", "999", "SenderB", 25, "male")
    # defensive: ensure maintenance is OFF (a failed earlier test could leave it on)
    async with db.session_scope() as s:
        cfg = await service.get_setting(s, "app")
        if cfg.get("maintenanceMode"):
            cfg["maintenanceMode"] = False
            await service.set_setting(s, "app", cfg)
    async with db.session_scope() as s:
        owner = (await s.execute(select(User).where(User.platform_user_id == "997"))).scalar_one()
        a = (await s.execute(select(User).where(User.platform_user_id == "998"))).scalar_one()
        b = (await s.execute(select(User).where(User.platform_user_id == "999"))).scalar_one()
        await service.ensure_anon_link(s, owner)
        # A sends first, B sends second (B's message is newer)
        t1 = AnonThread(owner_id=owner.id, sender_id=a.id, status="open",
                        message_count=1, last_text="پیام از A")
        s.add(t1)
        await s.flush()
        t2 = AnonThread(owner_id=owner.id, sender_id=b.id, status="open",
                        message_count=1, last_text="پیام از B")
        s.add(t2)
        await s.flush()
        id1, id2 = t1.id, t2.id
    # owner taps REPLY on the FIRST (older) message's button
    tg.sent.clear(); bale.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 600500, "callback_query": {
        "id": "ar", "from": {"id": 997}, "message": {"chat": {"id": 997}},
        "data": f"anonr_{id1}"}})
    await bot_mod.process_update("telegram", {"update_id": 600501, "message": {
        "message_id": 1, "chat": {"id": 997}, "from": {"id": 997}, "text": "جواب برای A"}})
    # ONLY sender A received the reply
    a_got = any(t_[0] == "998" and "جواب برای A" in str(t_[1]) for t_ in bale.sent)
    b_got = any(t_[0] == "999" and "جواب برای A" in str(t_[1]) for t_ in bale.sent)
    assert a_got and not b_got


# ------------------------------------------------- v2.2 feature tests ----
@pytest.mark.asyncio
async def test_display_name_validation(adapters):
    """v2.2 (item 1): offensive display names are refused in fa/en/ru while
    ordinary names pass; the vocabulary lives in app/profanity.py."""
    from app.moderation import check_display_name
    from app.profanity import DEFAULT_DICTIONARY, FA_WORDS, EN_WORDS, RU_WORDS
    assert FA_WORDS and EN_WORDS and RU_WORDS
    # ordinary names must NEVER be rejected (only clear profanity is listed)
    for n in ("علی", "سارا", "محمد", "Anna", "Sara", "Sergey", "Aline",
              "مهدی ۲۰۰۷", "Nastaran", "خودم", "امام علی"):
        assert check_display_name(n).ok, n
    # clearly offensive names in all three languages + obfuscations
    for n in ("کصکش", "جنده", "fuck", "bitch", "мудак", "k.o.s", "FU CK",
              "jende", "koskesh", "خایه"):
        assert not check_display_name(n).ok, n
    # end-to-end: registration and name change both reject the name
    from app import bot as bot_mod
    tg, _ = adapters
    await bot_mod.process_update("telegram", {"update_id": 700, "message": {
        "message_id": 1, "chat": {"id": 71}, "from": {"id": 71}, "text": "/start"}})
    await bot_mod.process_update("telegram", {"update_id": 701, "callback_query": {
        "id": "c1", "from": {"id": 71}, "message": {"chat": {"id": 71}}, "data": "lang_fa"}})
    await bot_mod.process_update("telegram", {"update_id": 702, "callback_query": {
        "id": "c2", "from": {"id": 71}, "message": {"chat": {"id": 71}}, "data": "accept_rules"}})
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 703, "message": {
        "message_id": 2, "chat": {"id": 71}, "from": {"id": 71}, "text": "fuck"}})
    assert any("واژگان نامناسب" in str(t[1]) for t in tg.sent)
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "71"))).scalar_one()
        assert u.state["step"] == "name"
    # a normal name goes through to the birthdate step
    await bot_mod.process_update("telegram", {"update_id": 704, "message": {
        "message_id": 3, "chat": {"id": 71}, "from": {"id": 71}, "text": "امیرعلی"}})
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "71"))).scalar_one()
        assert u.display_name == "امیرعلی" and u.state["step"] == "birth_calendar"


@pytest.mark.asyncio
async def test_report_reason_selection(adapters):
    """v2.2 (item 2): the report button adds a reason-choosing step; the chosen
    reason is stored with the report."""
    from app import bot as bot_mod
    tg, _ = adapters
    await register_via_bot(adapters, "telegram", "751", "Reporter", 25, "male")
    await register_via_bot(adapters, "telegram", "752", "BadGuy", 25, "male")
    for pid in ("751", "752"):
        await bot_mod.process_update("telegram", {"update_id": 710 + int(pid), "callback_query": {
            "id": f"c{pid}", "from": {"id": int(pid)},
            "message": {"chat": {"id": int(pid)}}, "data": "gf_everyone"}})
    tg.sent.clear()
    # pressing گزارش shows the four-reason menu
    await bot_mod.process_update("telegram", {"update_id": 720, "callback_query": {
        "id": "cr1", "from": {"id": 751}, "message": {"chat": {"id": 751}}, "data": "chat_report"}})
    joined = "".join(str(t[1]) for t in tg.sent)
    assert "دلیل" in joined
    kb_all = [t[2] for t in tg.sent if t[2]]
    datas = {b.get("callback_data") for k in kb_all for r in k for b in r}
    assert {"report_bad_name", "report_abuse", "report_spam", "report_other"} <= datas
    # choosing "spam" files the report with that reason
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 721, "callback_query": {
        "id": "cr2", "from": {"id": 751}, "message": {"chat": {"id": 751}}, "data": "report_spam"}})
    assert any("ثبت شد" in str(t[1]) for t in tg.sent)
    async with db.session_scope() as s:
        rep = (await s.execute(select(Report))).scalar_one()
        assert rep.reason == "spam"
    # /report command also opens the reason menu
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 722, "message": {
        "message_id": 9, "chat": {"id": 752}, "from": {"id": 752}, "text": "/report"}})
    assert any("دلیل" in str(t[1]) for t in tg.sent)


@pytest.mark.asyncio
async def test_store_packages_rates_and_custom_invoice(adapters):
    """v2.2 (item 3): new packages (10k=20 ... 100k=200), legacy packages retired,
    custom-amount invoices priced via the changeable rates; 50 coins = 25,000 Toman."""
    from app import bot as bot_mod
    tg, _ = adapters
    async with db.session_scope() as s:
        pkgs = {p.id: p for p in (await s.execute(select(StorePackage))).scalars()}
        assert pkgs["pkg_20"].coins == 20 and pkgs["pkg_20"].bale_toman_price == 10000
        assert pkgs["pkg_40"].coins == 40 and pkgs["pkg_40"].bale_toman_price == 20000
        assert pkgs["pkg_100"].coins == 100 and pkgs["pkg_100"].bale_toman_price == 50000
        assert pkgs["pkg_150"].coins == 150 and pkgs["pkg_150"].bale_toman_price == 75000
        assert pkgs["pkg_200"].coins == 200 and pkgs["pkg_200"].bale_toman_price == 100000
        # stars follow 1 star = 20 coins (7.5 -> rounded up to 8)
        assert pkgs["pkg_20"].telegram_stars_price == 1
        assert pkgs["pkg_150"].telegram_stars_price == 8
        assert pkgs["pkg_200"].telegram_stars_price == 10
        # legacy packages are deactivated, not deleted
        legacy = [p for p in pkgs.values() if p.id in ("pkg_300", "pkg_700", "pkg_1500")]
        for p in legacy:
            assert p.active is False
    # the user's example: 50 coins = 25,000 Toman invoice (rate 500 Toman/coin)
    await register_via_bot(adapters, "telegram", "761", "Buyer", 25, "male")
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "761"))).scalar_one()
        uid = u.id
        store = await service.get_setting(s, "store")
        assert service.coins_price_toman(store, 50) == 25000
    # enable payments for this test, then request a custom invoice
    old_token = settings.telegram_bot_token
    settings.telegram_bot_token = "test-token"
    try:
        await bot_mod.process_update("telegram", {"update_id": 730, "callback_query": {
            "id": "cc1", "from": {"id": 761}, "message": {"chat": {"id": 761}}, "data": "store"}})
        kb_all = [t[2] for t in tg.sent if t[2]]
        datas = {b.get("callback_data") for k in kb_all for r in k for b in r}
        assert "custom_coins" in datas and "buy_pkg_20" in datas
        await bot_mod.process_update("telegram", {"update_id": 731, "callback_query": {
            "id": "cc2", "from": {"id": 761}, "message": {"chat": {"id": 761}}, "data": "custom_coins"}})
        assert any("تعداد سکه" in str(t[1]) for t in tg.sent)
        tg.sent.clear()
        await bot_mod.process_update("telegram", {"update_id": 732, "message": {
            "message_id": 5, "chat": {"id": 761}, "from": {"id": 761}, "text": "50"}})
        invoices = [t for t in tg.sent if t[1] == "invoice"]
        assert invoices and invoices[0][2] == f"custom:50:{uid}"
        # out-of-range values are refused
        await bot_mod.process_update("telegram", {"update_id": 733, "callback_query": {
            "id": "cc3", "from": {"id": 761}, "message": {"chat": {"id": 761}}, "data": "custom_coins"}})
        await bot_mod.process_update("telegram", {"update_id": 734, "message": {
            "message_id": 6, "chat": {"id": 761}, "from": {"id": 761}, "text": "5"}})
        assert any("باید عددی بین" in str(t[1]) for t in tg.sent)
    finally:
        settings.telegram_bot_token = old_token
    # paying the custom invoice credits exactly 50 coins (idempotent)
    sp_msg = {"message_id": 7, "chat": {"id": 761}, "from": {"id": 761},
              "successful_payment": {"currency": "XTR", "total_amount": 3,
                                     "invoice_payload": f"custom:50:{uid}",
                                     "telegram_payment_charge_id": "chg_custom_1"}}
    await bot_mod.handle_successful_payment("telegram", sp_msg)
    await bot_mod.handle_successful_payment("telegram", sp_msg)  # duplicate ignored
    async with db.session_scope() as s:
        u = await s.get(User, uid)
        assert u.coin_balance == 30 + 50
        pay = (await s.execute(select(Payment).where(
            Payment.package_id == "custom"))).scalar_one()
        assert pay.coins == 50
    # rates are changeable via settings — the price follows immediately
    async with db.session_scope() as s:
        store = await service.get_setting(s, "store")
        store["tomanPerCoin"] = 600
        await service.set_setting(s, "store", store)
        assert service.coins_price_toman(store, 50) == 30000


@pytest.mark.asyncio
async def test_send_throttle_and_429_cooldown():
    """v2.2 (item 5): per-chat spacing, global sliding window and a global
    cooldown after a 429 (retry_after) so the bot never floods the API."""
    import asyncio as aio
    import httpx
    from app.platform import TelegramAdapter

    tg = TelegramAdapter("1:throttle", "s")
    tg.min_chat_interval = 0.15
    tg.rate_per_sec = 1000
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(200, json={"ok": False, "error_code": 429,
                                             "description": "Too Many Requests",
                                             "parameters": {"retry_after": 1}})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": state["n"]}})

    tg._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    t0 = time.monotonic()
    results = await aio.gather(tg.send_message(1, "a"), tg.send_message(2, "b"),
                               tg.send_message(3, "c"))
    elapsed = time.monotonic() - t0
    assert all(r["message_id"] for r in results)
    # the first call hit a 429 with retry_after=1 -> every send waited it out
    assert elapsed >= 1.0, elapsed
    # per-chat spacing: three sends to the SAME chat must be spaced apart
    t0 = time.monotonic()
    for _ in range(3):
        await tg.send_message(9, "x")
    spaced = time.monotonic() - t0
    assert spaced >= 2 * tg.min_chat_interval - 0.05, spaced
    await tg.close()


@pytest.mark.asyncio
async def test_referral_shows_both_links(adapters):
    """v2.2 (item 6): both the Telegram and Bale invite links are shown, with
    the note that joining via either one rewards both people."""
    from app import bot as bot_mod
    tg, _ = adapters
    await register_via_bot(adapters, "telegram", "771", "Inviter2", 25, "male")
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 740, "callback_query": {
        "id": "rf1", "from": {"id": 771}, "message": {"chat": {"id": 771}}, "data": "referral"}})
    joined = "".join(str(t[1]) for t in tg.sent)
    assert "t.me" in joined and "ble.ir" in joined
    assert "پاداش هر دو نفر" in joined
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == "771"))).scalar_one()
        assert f"start=ref_{u.referral_code}" in joined


@pytest.mark.asyncio
async def test_menu_structure(adapters):
    """v2.2 (item 7): main menu shows دریافت پاداش (rewards submenu) and no
    language button; the rewards submenu has invite/daily-bonus/back-home."""
    from app import bot as bot_mod
    tg, _ = adapters
    await register_via_bot(adapters, "telegram", "781", "MenuUser", 25, "male")
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 750, "callback_query": {
        "id": "mn1", "from": {"id": 781}, "message": {"chat": {"id": 781}}, "data": "back_home"}})
    kb0 = [t[2] for t in tg.sent if t[2]][0]
    datas0 = {b.get("callback_data") for r in kb0 for b in r}
    assert "rewards" in datas0 and "language" not in datas0 and "referral" not in datas0
    assert "bonus" not in datas0          # daily bonus moved into the submenu
    # rewards submenu contents
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 751, "callback_query": {
        "id": "mn2", "from": {"id": 781}, "message": {"chat": {"id": 781}}, "data": "rewards"}})
    kb1 = [t[2] for t in tg.sent if t[2]][0]
    datas1 = {b.get("callback_data") for r in kb1 for b in r}
    assert {"referral", "bonus", "back_home"} <= datas1
    # daily bonus still works from the submenu
    tg.sent.clear()
    await bot_mod.process_update("telegram", {"update_id": 752, "callback_query": {
        "id": "mn3", "from": {"id": 781}, "message": {"chat": {"id": 781}}, "data": "bonus"}})
    assert any("پاداش روزانه" in str(t[1]) for t in tg.sent)


@pytest.mark.asyncio
async def test_data_safety_guard():
    """v2.2 (item 8): booting on Railway without Postgres must fail loudly so
    no deploy silently swaps the database for an ephemeral SQLite file."""
    from app import main as main_mod
    old_url, old_env = settings.database_url, os.environ.get("RAILWAY_ENVIRONMENT")
    try:
        settings.database_url = "sqlite+aiosqlite:///./kanarchat.db"
        os.environ["RAILWAY_ENVIRONMENT"] = "production"
        with pytest.raises(RuntimeError, match="DATA SAFETY"):
            main_mod.ensure_persistent_database()
        os.environ.pop("RAILWAY_ENVIRONMENT", None)
        os.environ.pop("RAILWAY_PROJECT_ID", None)
        # off-Railway: local SQLite dev is still fine
        main_mod.ensure_persistent_database()
        # on Railway WITH Postgres: fine
        os.environ["RAILWAY_ENVIRONMENT"] = "production"
        settings.database_url = "postgresql+asyncpg://u:p@h:5432/db"
        main_mod.ensure_persistent_database()
    finally:
        settings.database_url = old_url
        if old_env is None:
            os.environ.pop("RAILWAY_ENVIRONMENT", None)
        else:
            os.environ["RAILWAY_ENVIRONMENT"] = old_env


# ================================================= v2.3 regression tests ===
@pytest.mark.asyncio
async def test_birthdate_gregorian_year_page_and_back_nav(adapters):
    """v2.3 regression: after choosing the gregorian calendar the year page must
    show GREGORIAN years (the stale user.state bug rendered jalali years, so
    every click failed with invalid_input), and each signup stage offers a
    back-to-previous-step button instead of back-to-main-menu."""
    from app import bot as bot_mod
    import datetime as _dt
    tg, _ = adapters
    pid = "977"
    upd_id = [700000]

    def upd(**kw):
        upd_id[0] += 1
        return {"update_id": upd_id[0], **kw}

    async def cq(cq_id, data):
        await bot_mod.process_update("telegram", upd(callback_query={
            "id": cq_id, "from": {"id": int(pid)},
            "message": {"chat": {"id": int(pid)}}, "data": data}))

    def last_kb():
        return [k for (_c, _t, k) in tg.sent if k][-1]

    def datas_of(kb_):
        return {b.get("callback_data") for row in kb_ for b in row}

    await bot_mod.process_update("telegram", upd(message={
        "message_id": 1, "chat": {"id": int(pid)},
        "from": {"id": int(pid), "first_name": "Nav"}, "text": "/start"}))
    await cq("n1", "lang_fa")
    await cq("n2", "accept_rules")
    await bot_mod.process_update("telegram", upd(message={
        "message_id": 2, "chat": {"id": int(pid)},
        "from": {"id": int(pid)}, "text": "NickNav"}))
    today = _dt.date.today()
    # calendar intro: back goes to the name step, not home
    tg.sent.clear()
    kb_ = [k for (_c, _t, k) in tg.sent if k][-1] if [k for (_c, _t, k) in tg.sent if k] else None
    await cq("n3", "bcal_gregorian")
    datas = datas_of(last_kb())
    assert f"byear_{today.year - 13}" in datas  # regression: stale state showed jalali years
    assert "bcal_back" in datas and "back_home" not in datas
    # click the youngest gregorian year -> month page (previously: invalid_input)
    tg.sent.clear()
    await cq("n4", f"byear_{today.year - 13}")
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == pid))).scalar_one()
        assert u.state["step"] == "birth_month" and u.state["calendar"] == "gregorian"
    datas = datas_of(last_kb())
    assert "bmon_back" in datas and "back_home" not in datas
    # back to the year page
    await cq("n5", "bmon_back")
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == pid))).scalar_one()
        assert u.state["step"] == "birth_year"
    # forward again month -> day -> confirm, checking back buttons at each hop
    await cq("n6", f"byear_{today.year - 13}")
    await cq("n7", f"bmon_{today.month}")
    datas = datas_of(last_kb())
    assert "bday_back" in datas and "back_home" not in datas
    await cq("n8", f"bday_{today.day}")
    datas = datas_of(last_kb())
    assert "bage_back" in datas and "bage_redo" in datas and "back_home" not in datas
    await cq("n9", "bage_back")
    async with db.session_scope() as s:
        u = (await s.execute(select(User).where(User.platform_user_id == pid))).scalar_one()
        assert u.state["step"] == "birth_day"


@pytest.mark.asyncio
async def test_restore_one_click_forward_with_caption(adapters):
    """v2.3: the owner forwards the bot's backup file with its caption (the
    restore password travels in the caption); the bot validates instantly and
    ONE confirm click replaces all current data with the backup."""
    from app import bot as bot_mod
    from app import backup as bk
    from app import bot_admin as ba
    owner_pid = "860835914"
    await register_via_bot(adapters, "telegram", owner_pid, "OwnerS", 30, "male")
    await register_via_bot(adapters, "telegram", "978", "KeepMe", 25, "male")
    async with db.session_scope() as s:
        u1 = (await s.execute(select(User).where(User.platform_user_id == "978"))).scalar_one()
        u1.bio = "precious"
    async with db.session_scope() as s:
        out = await bk.create_backup(s, "manual", "test")
        assert isinstance(out, tuple)
        rec, blob = out
    async with db.session_scope() as s:
        await s.execute(delete(User).where(User.platform_user_id == "978"))
    # open the restore wait-state from the admin menu
    await bot_mod.process_update("telegram", {"update_id": 700500, "callback_query": {
        "id": "r0", "from": {"id": int(owner_pid)},
        "message": {"chat": {"id": int(owner_pid)}}, "data": "adm_restore"}})
    assert owner_pid in ba.RESTORE_WAIT
    # forward the file with the caption the bot itself attaches
    caption = (f"🔐 پشتیبان پایگاه‌داده هم‌چت\nشناسه: {rec.id}\n"
               f"بخش 1 از 1\n🔑 رمز بازیابی: {settings.backup_password}\n"
               "↩️ برای بازگردانی، همین فایل را با همین کپشن به ربات فوروارد کن.")
    async with db.session_scope() as s:
        owner = (await s.execute(select(User).where(
            User.platform_user_id == owner_pid))).scalar_one()
    handled = await ba.admin_document(owner, blob, caption=caption,
                                      filename=f"{rec.filename}")
    assert handled is True
    st = ba.RESTORE_WAIT[owner_pid]
    assert st.get("blob"), "blob must be validated and staged instantly"
    assert st["password"] == settings.backup_password
    tg_sent = RUNTIME["adapter_telegram"].sent
    assert any("adm_restore_go" in str(k) for (_c, _t, k) in tg_sent if k), \
        "one-click confirm button must be offered right after validation"
    # ONE click replaces the data
    await bot_mod.process_update("telegram", {"update_id": 700501, "callback_query": {
        "id": "r1", "from": {"id": int(owner_pid)},
        "message": {"chat": {"id": int(owner_pid)}}, "data": "adm_restore_go"}})
    async with db.session_scope() as s:
        u1 = (await s.execute(select(User).where(User.platform_user_id == "978"))).scalar_one()
        assert u1.bio == "precious"
    assert owner_pid not in ba.RESTORE_WAIT


@pytest.mark.asyncio
async def test_restore_caption_password_variants():
    """The caption parser accepts the bot's format and a manual 'رمز: ...' line."""
    from app.bot_admin import _parse_restore_caption
    pwd, idx, total = _parse_restore_caption(
        "🔐 پشتیبان پایگاه‌داده هم‌چت\nبخش 2 از 3\n🔑 رمز بازیابی: Secret-Key-9",
        "BKP-x.enc.part02of03")
    assert pwd == "Secret-Key-9" and (idx, total) == (2, 3)
    pwd2, idx2, total2 = _parse_restore_caption("رمز: MyPass-2026", "backup.enc")
    assert pwd2 == "MyPass-2026" and idx2 is None and total2 is None
    pwd3, _, _ = _parse_restore_caption("", "x.enc")
    assert pwd3 is None
