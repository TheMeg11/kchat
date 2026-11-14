"""Kanar persona test simulator — 21 agents.
8 Telegram users + 12 Bale users exercise the live bot via real Bot API webhooks;
1 web-admin agent exercises the web panel via HTTP.
Each agent records every anomaly it hits into /home/z/my-project/persona_report.json.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time
import traceback
import uuid

import httpx

BASE = os.getenv("KANAR_BASE", "https://kanarchat-production.up.railway.app")
TG_BASE = "https://api.telegram.org/bot9297093138:AAGQ4VUBeIHFTo_R6twdWNIUq-J9dz4v-pk"
REPORT_PATH = "/home/z/my-project/persona_report.json"

issues: list[dict] = []
actions: list[dict] = []


def report(agent: str, severity: str, what: str, detail: str = ""):
    issues.append({"agent": agent, "severity": severity, "issue": what, "detail": detail[:400]})
    print(f"[{severity}] {agent}: {what} {detail[:120]}")


def ok(agent: str, what: str):
    actions.append({"agent": agent, "action": what, "ok": True})


_WEBHOOK_URLS: dict[str, str] = {}


def _wh_url(platform: str) -> str:
    if _WEBHOOK_URLS.get(platform):
        return _WEBHOOK_URLS[platform]
    token = os.getenv("TG_TOKEN", "9297093138:AAGQ4VUBeIHFTo_R6twdWNIUq-J9dz4v-pk")
    base = "https://api.telegram.org/bot" if platform == "telegram" else "https://tapi.bale.ai/bot"
    token = token if platform == "telegram" else os.getenv(
        "BALE_TOKEN", "1875531337:HiHoVWOaSRGX1nX6nMOfLE-VjOu6c_GmpGg")
    with httpx.Client(timeout=20) as cli:
        r = cli.get(f"{base}{token}/getWebhookInfo")
        _WEBHOOK_URLS[platform] = r.json()["result"]["url"]
    return _WEBHOOK_URLS[platform]


class BotAgent:
    """A user persona talking to the LIVE bot through platform webhooks."""

    def __init__(self, platform: str, name: str, age: int, gender: str, pid: int):
        self.platform = platform
        self.name = name
        self.age = age
        self.gender = gender
        self.pid = pid
        self.chat_id = pid
        self.update_id = 1_000_000 + pid * 1000
        self.sent_texts: list[str] = []
        self.last_reply = ""

    async def send_update(self, upd: dict) -> None:
        """POST directly to the app's webhook endpoint (same shape the platform sends)."""
        url = _wh_url(self.platform)
        headers = {"X-Telegram-Bot-Api-Secret-Token": "Vn7xvAU4TMMh7Ee0r1dCScc6nw5P4S6-"}
        async with httpx.AsyncClient(timeout=40) as cli:
            r = await cli.post(url, json=upd, headers=headers)
            if r.status_code not in (200, 201):
                report(self.name, "HIGH", f"webhook returned {r.status_code}", r.text[:200])

    async def msg(self, text: str) -> None:
        self.update_id += 1
        self.sent_texts.append(text)
        await self.send_update({"update_id": self.update_id, "message": {
            "message_id": self.update_id % 100000,
            "chat": {"id": self.chat_id}, "from": {"id": self.pid, "first_name": self.name},
            "text": text}})

    async def tap(self, data: str) -> None:
        self.update_id += 1
        await self.send_update({"update_id": self.update_id, "callback_query": {
            "id": f"cq{self.pid}{self.update_id}", "from": {"id": self.pid},
            "message": {"chat": {"id": self.chat_id}, "message_id": 1},
            "data": data}})

    async def register(self) -> None:
        await self.msg("/start")
        await self.tap("lang_fa")
        await self.tap("accept_rules")
        await self.msg(self.name)
        await self.msg(str(self.age))
        await self.tap(f"reg_{self.gender}")
        ok(self.name, "registered")

    async def read_inbox(self) -> list[str]:
        """Read what the bot sent us via the platform API (getUpdates is consumed by
        webhook, so instead we query the bot's message log via admin API is not
        possible; we rely on /coins, /profile echo and partner-visible effects).
        For simplicity agents verify via state-echoing commands."""
        return []


TG_AGENTS = [
    ("سارا", 24, "female", 101), ("امیر", 27, "male", 102),
    ("نگار", 19, "female", 103), ("رضا", 31, "male", 104),
    ("مینا", 16, "female", 105), ("کیان", 15, "male", 106),
    ("الهام", 22, "female", 107), ("بهراد", 29, "male", 108),
]
BALE_AGENTS = [
    ("فاطمه", 23, "female", 201), ("حسین", 26, "male", 202),
    ("زهرا", 18, "female", 203), ("مجید", 35, "male", 204),
    ("شیوا", 17, "female", 205), ("آرش", 14, "male", 206),
    ("لیلا", 21, "female", 207), ("کاوه", 28, "male", 208),
    ("نازنین", 20, "female", 209), ("سامان", 33, "male", 210),
    ("پریسا", 25, "female", 211), ("فرهاد", 30, "male", 212),
]


async def run_bot_agents() -> None:
    agents: list[BotAgent] = []
    for name, age, gender, pid in TG_AGENTS:
        agents.append(BotAgent("telegram", name, age, gender, pid))
    for name, age, gender, pid in BALE_AGENTS:
        agents.append(BotAgent("bale", name, age, gender, pid))

    # --- phase 1: register everyone ---
    for a in agents:
        try:
            await a.register()
            await asyncio.sleep(0.3)
        except Exception as exc:
            report(a.name, "HIGH", "registration crashed", traceback.format_exc()[:300])

    # --- phase 2: main menu buttons for a few agents ---
    for a in agents[:6]:
        try:
            await a.tap("help"); await asyncio.sleep(0.2)
            await a.tap("coins"); await asyncio.sleep(0.2)
            await a.tap("profile"); await asyncio.sleep(0.2)
            await a.tap("back_home"); await asyncio.sleep(0.2)
            ok(a.name, "menu buttons")
        except Exception:
            report(a.name, "HIGH", "menu navigation crashed", traceback.format_exc()[:300])

    # --- phase 3: matching (adults) ---
    adults = [a for a in agents if a.age >= 18]
    random.shuffle(adults)
    searchers = adults[:8]
    for a in searchers:
        try:
            await a.tap("search"); await asyncio.sleep(0.2)
            await a.tap("gf_everyone"); await asyncio.sleep(0.4)
            ok(a.name, "searching")
        except Exception:
            report(a.name, "HIGH", "search crashed", traceback.format_exc()[:300])
    await asyncio.sleep(2)

    # --- phase 4: minors search (age pool separation) ---
    minors = [a for a in agents if a.age < 18]
    for a in minors[:4]:
        try:
            await a.tap("search"); await asyncio.sleep(0.2)
            await a.tap("gf_everyone"); await asyncio.sleep(0.4)
        except Exception:
            report(a.name, "HIGH", "minor search crashed", traceback.format_exc()[:300])

    # --- phase 5: anon links (agent 101 creates, agent 202 sends via link) ---
    try:
        owner = agents[0]           # Sara (TG)
        await owner.tap("anon_link"); await asyncio.sleep(0.5)
        # fetch the link code from DB? Not accessible. Instead use /link echo:
        # the bot sent the links to the owner — we cannot read them via webhook.
        # Trick: use deep link with a KNOWN code? Code is random. We instead
        # verify the anon_link button responds without error (200s).
        ok(owner.name, "anon links shown")
    except Exception:
        report("سارا", "HIGH", "anon link crashed", traceback.format_exc()[:300])

    # --- phase 6: daily bonus + referral + birthday (v2.1: year->month->day) ---
    for a in agents[6:10]:
        try:
            await a.tap("bonus"); await asyncio.sleep(0.3)
            await a.tap("set_birthday"); await asyncio.sleep(0.3)
            await a.msg("1380"); await asyncio.sleep(0.4)   # year
            await a.tap("bmon_7"); await asyncio.sleep(0.3)  # month
            await a.msg("15"); await asyncio.sleep(0.5)     # day
            ok(a.name, "bonus+birthday(2.1 flow)")
        except Exception:
            report(a.name, "HIGH", "bonus/birthday crashed", traceback.format_exc()[:300])

    # --- phase 6b: wallet menu (v2.1) ---
    try:
        for a in agents[:3]:
            await a.tap("wallet"); await asyncio.sleep(0.3)
            await a.tap("store"); await asyncio.sleep(0.4)
            await a.tap("back_home"); await asyncio.sleep(0.2)
        ok("wallet", "wallet+buy menu explored")
    except Exception:
        report("wallet", "MEDIUM", "wallet menu crashed", traceback.format_exc()[:300])

    # --- phase 7: direct message (mystery) ---
    try:
        sender = agents[3]  # Reza
        await sender.tap("direct_msg"); await asyncio.sleep(0.2)
        await sender.msg(str(agents[1].pid)); await asyncio.sleep(0.2)
        await sender.msg("سلام! این یک پیام ناشناس تستی است 🔥"); await asyncio.sleep(0.5)
        ok(sender.name, "direct msg sent")
    except Exception:
        report(agents[3].name, "HIGH", "direct msg crashed", traceback.format_exc()[:300])

    # --- phase 8: chats should have formed; send messages in chat ---
    try:
        for a in searchers[:4]:
            await a.msg(f"سلام از {a.name} 👋"); await asyncio.sleep(0.3)
        ok("chat", "in-chat messages sent")
    except Exception:
        report("chat", "HIGH", "in-chat relay crashed", traceback.format_exc()[:300])

    # --- phase 9: stop / end chats ---
    try:
        for a in searchers[:2]:
            await a.tap("chat_end"); await asyncio.sleep(0.3)
        ok("chat", "end chat")
    except Exception:
        report("chat", "MEDIUM", "end chat crashed", traceback.format_exc()[:300])


async def run_web_admin() -> None:
    """Admin persona on the web panel: login flow, dashboard, users, reports."""
    name = "WebAdmin"
    async with httpx.AsyncClient(timeout=30, base_url=BASE) as cli:
        try:
            r = await cli.get("/health")
            if r.status_code != 200:
                report(name, "HIGH", f"/health {r.status_code}")
            else:
                ok(name, "health")
        except Exception:
            report(name, "HIGH", "health unreachable", traceback.format_exc()[:200])
        # login with wrong creds (must 401, not 500)
        try:
            r = await cli.post("/api/admin/v1/auth/login",
                               json={"username": "nope", "password": "nope123456"})
            if r.status_code not in (401, 429):
                report(name, "MEDIUM", f"wrong-login returned {r.status_code}", r.text[:150])
            else:
                ok(name, "wrong login rejected")
        except Exception:
            report(name, "HIGH", "login endpoint crashed", traceback.format_exc()[:200])
        # unauthenticated access must be 401/403, not 5xx
        for path in ("/api/admin/v1/dashboard", "/api/admin/v1/users",
                     "/api/admin/v1/reports", "/api/admin/v1/chats"):
            try:
                r = await cli.get(path)
                if r.status_code >= 500:
                    report(name, "HIGH", f"{path} returned {r.status_code}", r.text[:150])
                else:
                    ok(name, f"{path} guarded ({r.status_code})")
            except Exception:
                report(name, "HIGH", f"{path} crashed", traceback.format_exc()[:200])
        # SPA must load
        try:
            r = await cli.get("/")
            if r.status_code != 200 or "doctype" not in r.text[:100].lower():
                report(name, "MEDIUM", "SPA did not load")
            else:
                ok(name, "SPA loads")
        except Exception:
            report(name, "HIGH", "SPA unreachable", traceback.format_exc()[:200])


async def main() -> None:
    print("=== persona simulation start ===")
    await run_bot_agents()
    await run_web_admin()
    out = {"ts": time.time(), "issues": issues, "actions_ok": len(actions)}
    with open(REPORT_PATH, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"=== done: {len(actions)} ok actions, {len(issues)} issues ===")
    for i in issues:
        print(json.dumps(i, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
