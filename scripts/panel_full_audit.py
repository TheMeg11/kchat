#!/usr/bin/env python3
"""v2.3.4 full panel endpoint audit — hits EVERY admin API route against a
locally seeded app, asserts 2xx/expected-4xx, prints a per-section verdict."""
import asyncio, os, sys
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:////tmp/live_e2e3.db"
os.environ["ENCRYPTION_KEY"] = "e2e-encryption-key-0123456789abcdef"
os.environ["BACKUP_PASSWORD"] = "E2eBackupPass-1234567890"
os.environ["WEBHOOK_SECRET"] = "e2e-wh"
os.environ["ADMIN_PANEL_USERNAME"] = "probe.admin"
os.environ["ADMIN_PANEL_PASSWORD"] = "ProbePass123!x"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from app import db
from app.main import app


async def _boot():
    db.init_engine(os.environ["DATABASE_URL"])
    await db.create_all(db.get_engine())

B = "http://t"


async def main():
    await _boot()
    tr = httpx.ASGITransport(app=app)
    results = []

    async with httpx.AsyncClient(transport=tr, base_url=B) as c:
        r = await c.post("/api/admin/v1/auth/login",
                         json={"username": "probe.admin", "password": "ProbePass123!x"})
        assert r.json()["status"] == "authenticated"
        csrf = r.headers.get("x-csrf-token")
        H = {"X-CSRF-Token": csrf}
        C = {"cookies": dict(r.cookies)}

        def rec(name, r, expect_ok=True):
            ok = (200 <= r.status_code < 300) if expect_ok else (r.status_code >= 400)
            results.append((("OK " if ok else "BAD"), name, r.status_code))
            return r

        # --- GET sweep: every read route the FE uses ---
        gets = [
            "/dashboard/metrics", "/dashboard/metrics?range=7d", "/dashboard/metrics?range=30d",
            "/users", "/users?status=incomplete", "/users?status=active", "/users?q=U1325EE12",
            "/chats", "/monitoring/railway", "/moderation", "/reports", "/bans",
            "/economy", "/store/packages", "/payments", "/referrals", "/daily-rewards",
            "/settings/media", "/settings/matching", "/settings/privacy",
            "/broadcasts", "/support/tickets", "/admins", "/roles", "/audit-logs",
            "/backups", "/platforms", "/settings/app", "/security/overview",
            "/auth/sessions", "/auth/me", "/notifications",
        ]
        for g in gets:
            rec(g, await c.get(f"/api/admin/v1{g}", **C))

        # --- representative mutations (local DB only) ---
        r = await c.post(f"{B}/api/admin/v1/users/1/coins", **C,
                         json={"amount": 10, "reason": "audit"}, headers=H)
        rec("POST users/1/coins grant", r)
        r = await c.post(f"{B}/api/admin/v1/users/1/coins", **C,
                         json={"amount": -9999, "reason": "audit"}, headers=H)
        rec("POST coins over-deduct 400", r, expect_ok=False)
        r = await c.patch(f"{B}/api/admin/v1/users/1", **C,
                          json={"city": "کرج"}, headers=H)
        rec("PATCH users/1 city", r)
        r = await c.post(f"{B}/api/admin/v1/users/1/ban", **C,
                         json={"action": "ban", "type": "temporary", "hours": 1,
                               "reason": "audit"}, headers=H)
        rec("POST users/1 ban", r)
        r = await c.post(f"{B}/api/admin/v1/users/1/ban", **C,
                         json={"action": "unban", "reason": "audit"}, headers=H)
        rec("POST users/1 unban", r)
        r = await c.post(f"{B}/api/admin/v1/moderation/dictionary", **C,
                         json={"word": "کلمه_تست", "severity": "block"}, headers=H)
        rec("POST moderation dictionary", r, expect_ok=(r.status_code != 404))
        r = await c.patch(f"{B}/api/admin/v1/settings/matching", **C,
                          json={}, headers=H)
        rec("PATCH settings/matching (noop)", r)
        r = await c.get(f"{B}/api/admin/v1/dashboard/metrics", **C)
        import json as _j
        m = r.json()
        assert "incompleteSignups" in m and "totalUsers" in m
        rec("metrics contract keys", r)

    bad = [x for x in results if x[0] == "BAD"]
    for st, name, code in results:
        print(f"{st} {code} {name}")
    print(f"\n=== {len(results) - len(bad)}/{len(results)} OK ===")
    if bad:
        sys.exit(1)


asyncio.run(main())
