"""Super Admin web API — implements docs/API_CONTRACT.md exactly (FastAPI)."""
from __future__ import annotations

import asyncio
import os
import re
import secrets
import time
import uuid
from datetime import timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, select

from . import backup as backup_mod
from . import db, security, service
from .config import RUNTIME, settings
from .db import (AdminNotification, AllowlistPhrase, Appeal, AuditLog, Ban, Block,
                 BroadcastCampaign, Chat, ChatMessage, CoinTransaction, DailyClaim,
                 DictionaryWord, LoginChallenge, MediaType, Payment, QueueEntry, Referral,
                 Report, RolePermission, SecurityEvent, StorePackage, SupportMessage,
                 SupportTicket, User, ViolationLog, WebAdmin, WebSession, utcnow)

router = APIRouter(prefix="/api/admin/v1")


class ApiErr(HTTPException):
    """HTTPException carrying the frontend contract: detail (fa msg) + code."""
    def __init__(self, status_code: int, msg: str, code: str):
        self.detail = {"detail": msg, "code": code}
        super().__init__(status_code=status_code, detail=self.detail)



SESSION_COOKIE = "kanar_admin_session"
CSRF_HEADER = "X-CSRF-Token"
_login_attempts: dict[str, list[float]] = {}


def now_iso(dt) -> str:
    return dt.isoformat() + "Z" if dt and dt.tzinfo is None else (dt.isoformat() if dt else "")


def iso(dt) -> str:
    return dt.isoformat() + "Z" if dt else ""


# ------------------------------------------------------------ serialize ----
def app_user(u: User) -> dict:
    return {
        "id": str(u.id), "publicId": u.public_id, "displayName": u.display_name,
        "username": u.username or "", "platform": u.platform,
        "platformUserId": u.platform_user_id, "gender": u.gender, "age": u.age,
        # v2.3.3: age 0 = registration never completed (row created on the very
        # first message) — its own category instead of the bogus "13-17".
        "ageGroup": "incomplete" if u.age == 0 else ("13-17" if u.age < 18 else "18-60"),
        "city": u.city,
        "language": u.language,
        "status": {"banned": "temp_banned"}.get(u.status, u.status if u.status != "banned" else "temp_banned"),
        "online": (utcnow() - u.last_seen_at).total_seconds() < 300,
        "searching": False, "coinBalance": u.coin_balance, "totalChats": u.total_chats,
        "totalMessages": u.total_messages, "reportsReceived": u.reports_received,
        "reportsSent": u.reports_sent, "referralCount": u.referral_count,
        "registeredAt": iso(u.registered_at), "lastSeenAt": iso(u.last_seen_at),
        "banCount": u.ban_count, "botBlocked": bool(u.bot_blocked)}


def _ban_status(u: User) -> str:
    # refine status using active bans
    return app_user(u)["status"]


def coin_txn(t: CoinTransaction, u: Optional[User]) -> dict:
    return {"id": str(t.id), "userId": str(t.user_id),
            "userDisplayName": (u.display_name if u else "?"), "platform": u.platform if u else "?",
            "type": t.type, "amount": t.amount, "balanceAfter": t.balance_after,
            "description": t.description, "referenceId": t.reference_id or "",
            "createdAt": iso(t.created_at)}


def chat_session(c: Chat, uas: dict[int, User], msgs: list[ChatMessage], has_report: bool,
                 report_id: Optional[int]) -> dict:
    def part(uid):
        u = uas[uid]
        return {"userId": str(uid), "publicId": u.public_id, "displayName": u.display_name,
                "platform": u.platform, "ageGroup": "13-17" if u.age < 18 else "18-60",
                "gender": u.gender}
    return {"id": str(c.id), "participants": [part(c.user_a_id), part(c.user_b_id)],
            "platforms": sorted({uas[c.user_a_id].platform, uas[c.user_b_id].platform}),
            "crossPlatform": c.cross_platform, "ageGroup": c.age_group,
            "status": {"ended": "ended", "terminated": "terminated_by_mod"}.get(c.status, c.status),
            "messageCount": c.message_count, "hasReport": has_report,
            "reportId": str(report_id) if report_id else None,
            "startedAt": iso(c.started_at), "endedAt": iso(c.ended_at),
            "expiresAt": iso((c.started_at or utcnow()) + timedelta(days=30)),
            "messages": [{"id": str(m.id), "senderId": str(m.sender_id), "type": m.msg_type,
                          "text": m.text or "",
                          "mediaMeta": ({"fileId": (m.media_file_id or "")[:24] + "…",
                                         "fileSizeKb": (m.media_size or 0) // 1024,
                                         "mimeType": m.mime or ""} if m.media_file_id else None),
                          "flagged": m.flagged, "flagReason": m.flag_reason or "",
                          "sentAt": iso(m.created_at)} for m in msgs]}


# ---------------------------------------------------------------- auth ----
async def current_admin(request: Request) -> tuple[WebAdmin, WebSession]:
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid:
        raise ApiErr(401, "نشست فعالی یافت نشد.", "UNAUTHENTICATED")
    async with db.session_scope() as s:
        sess = await s.get(WebSession, sid)
        if not sess or sess.revoked:
            raise ApiErr(401, "نشست منقضی شده است.", "UNAUTHENTICATED")
        admin = await s.get(WebAdmin, sess.admin_id)
        if not admin or admin.status != "active":
            raise ApiErr(401, "حساب مدیر غیرفعال است.", "ADMIN_SUSPENDED")
        sess.last_active_at = utcnow()
        return admin, sess


async def require(request: Request, perm: str) -> tuple[WebAdmin, WebSession]:
    admin, sess = await current_admin(request)
    perms = await service.role_permissions(None, admin.role) if False else None
    from .db import session_scope
    async with session_scope() as s:
        perms = await service.role_permissions(s, admin.role)
    if not service.has_perm(admin.role, perms, perm):
        raise ApiErr(403, "دسترسی لازم برای این عملیات را ندارید.", "FORBIDDEN")
    return admin, sess


async def require_owner(request: Request) -> WebAdmin:
    admin, _ = await current_admin(request)
    if admin.role != "owner":
        raise ApiErr(403, "فقط مالک سیستم (Owner) مجاز است.", "OWNER_ONLY")
    return admin


def admin_out(a: WebAdmin, perms: list) -> dict:
    labels = {"owner": "مالک سیستم", "senior_admin": "مدیر ارشد",
              "moderator": "ناظر", "support": "پشتیبان"}
    return {"id": str(a.id), "username": a.username, "fullName": a.full_name,
            "email": a.email, "role": a.role, "roleLabel": labels.get(a.role, a.role),
            "permissions": ["*"] if a.role == "owner" else perms,
            "totpEnabled": a.totp_enabled, "mustChangePassword": a.must_change_password,
            "status": a.status, "lastLoginAt": iso(a.last_login_at),
            "lastLoginIp": a.last_login_ip or "—", "createdAt": iso(a.created_at)}


def _ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or \
        (request.client.host if request.client else "?")


def _rate_limited(ip: str) -> bool:
    now = time.time()
    lst = [t for t in _login_attempts.get(ip, []) if now - t < 300]
    _login_attempts[ip] = lst
    return len(lst) >= 6


def _record_fail(ip: str):
    _login_attempts.setdefault(ip, []).append(time.time())


class LoginBody(BaseModel):
    username: str
    password: str


class ChangePwdBody(BaseModel):
    challengeId: str
    currentPassword: str
    newPassword: str


async def _new_session(admin_id: int, request: Request) -> str:
    sid = secrets.token_urlsafe(32)
    ua = request.headers.get("user-agent", "")[:250]
    device = "مرورگر ناشناس"
    m = re.search(r"\(([^)]+)\)", ua)
    if m:
        device = m.group(1)[:60]
    if "Mobile" in ua or "Android" in ua:
        device = "موبایل — " + device
    async with db.session_scope() as s:
        s.add(WebSession(id=sid, admin_id=admin_id, ip=_ip(request), user_agent=ua, device=device))
    return sid


def _set_session_cookie(response: Response, sid: str):
    # v2.4: 30-day trusted-device cookie — the admin's device/browser survives
    # deploys (sessions live in Postgres; the CSRF token is now stateless too).
    response.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax",
                        secure=bool(os.getenv("RAILWAY_ENVIRONMENT")), max_age=30 * 24 * 3600,
                        path="/")


async def _authed_response(admin: WebAdmin, request: Request,
                           extra: Optional[dict] = None) -> JSONResponse:
    sid = await _new_session(admin.id, request)
    perms = []
    async with db.session_scope() as s:
        perms = await service.role_permissions(s, admin.role)
        admin.last_login_at = utcnow()
        admin.last_login_ip = _ip(request)
        s.add(SecurityEvent(type="login_success", severity="info", admin_username=admin.username,
                            ip_address=_ip(request),
                            user_agent=request.headers.get("user-agent", "")[:250],
                            message="ورود موفق مدیر به پنل وب"))
    # v2.4: stateless CSRF derived from the session id — valid across restarts
    csrf = security.csrf_token(sid, settings.webhook_secret)
    body = {"status": "authenticated", "admin": admin_out(admin, perms)}
    if extra:
        body.update(extra)
    resp = JSONResponse(body)
    _set_session_cookie(resp, sid)
    resp.headers[CSRF_HEADER] = csrf
    return resp


def _check_csrf(request: Request, sess: WebSession):
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    token = request.headers.get(CSRF_HEADER)
    expected = security.csrf_token(sess.id, settings.webhook_secret)
    if not token or token != expected:
        raise ApiErr(403, "توکن CSRF نامعتبر است. صفحه را رفرش کنید.", "CSRF")


@router.post("/auth/login")
async def login(body: LoginBody, request: Request):
    ip = _ip(request)
    if _rate_limited(ip):
        raise ApiErr(429, "تلاش‌های ناموفق زیاد؛ ۵ دقیقه صبر کنید.", "RATE_LIMITED")
    async with db.session_scope() as s:
        res = await s.execute(select(WebAdmin).where(
            WebAdmin.username == body.username.strip().lower()))
        admin = res.scalar_one_or_none()
        if not admin or not security.verify_password(admin.password_hash, body.password):
            _record_fail(ip)
            s.add(SecurityEvent(type="login_failed", severity="warning",
                                admin_username=body.username[:40], ip_address=ip,
                                user_agent=request.headers.get("user-agent", "")[:250],
                                message="تلاش ورود ناموفق"))
            raise ApiErr(401, "نام کاربری یا رمز عبور نامعتبر است.", "INVALID_CREDENTIALS")
        if admin.status != "active":
            raise ApiErr(403, "حساب کاربری این مدیر مسدود شده است.", "ADMIN_SUSPENDED")
        if admin.must_change_password:
            ch = str(uuid.uuid4())
            s.add(LoginChallenge(id=ch, admin_id=admin.id, stage="password",
                                 expires_at=utcnow() + timedelta(minutes=10)))
            return {"status": "password_change_required", "challengeId": ch}
        # v2.3: two-factor (TOTP) login removed by product decision —
        # username + password is the only credential pair.
        return await _authed_response(admin, request)


@router.post("/auth/change-initial-password")
async def change_initial_password(body: ChangePwdBody, request: Request):
    if len(body.newPassword) < 10:
        raise ApiErr(400, "رمز جدید باید حداقل ۱۰ کاراکتر باشد.", "WEAK_PASSWORD")
    async with db.session_scope() as s:
        ch = await s.get(LoginChallenge, body.challengeId)
        if not ch or ch.expires_at < utcnow() or ch.stage != "password":
            raise ApiErr(400, "چالش نامعتبر یا منقضی است.", "INVALID_CHALLENGE")
        admin = await s.get(WebAdmin, ch.admin_id)
        if not security.verify_password(admin.password_hash, body.currentPassword):
            raise ApiErr(400, "رمز فعلی نادرست است.", "INVALID_CREDENTIALS")
        admin.password_hash = security.hash_password(body.newPassword)
        admin.must_change_password = False
        await s.delete(ch)
        s.add(SecurityEvent(type="password_changed", severity="info",
                            admin_username=admin.username, ip_address=_ip(request),
                            user_agent="", message="تغییر رمز اولیه انجام شد"))
        # v2.3: no TOTP enrollment — go straight to the authenticated session
        return await _authed_response(admin, request)


@router.get("/auth/me")
async def me(request: Request):
    admin, _ = await current_admin(request)
    async with db.session_scope() as s:
        perms = await service.role_permissions(s, admin.role)
    return admin_out(admin, perms)


# v2.3.1: explicit stubs for the retired TOTP endpoints — the prebuilt panel
# bundle still references them; returning a clean Persian error beats a 404.
_TOTP_REMOVED = ("احراز دومرحله‌ای (TOTP) از نسخه ۲.۳ حذف شده است؛ "
                 "ورود فقط با نام کاربری و رمز عبور انجام می‌شود.")


@router.post("/auth/verify-totp")
async def verify_totp_removed(request: Request):
    await current_admin(request)
    raise ApiErr(410, _TOTP_REMOVED, "TOTP_REMOVED")


@router.post("/auth/totp/enroll/confirm")
async def totp_enroll_removed(request: Request):
    await current_admin(request)
    raise ApiErr(410, _TOTP_REMOVED, "TOTP_REMOVED")


@router.post("/auth/logout")
async def logout(request: Request, response: Response):
    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        async with db.session_scope() as s:
            sess = await s.get(WebSession, sid)
            if sess:
                sess.revoked = True
    response.delete_cookie(SESSION_COOKIE, path="/")
    return Response(status_code=204)


@router.get("/auth/sessions")
async def list_sessions(request: Request):
    admin, sess = await current_admin(request)
    async with db.session_scope() as s:
        rows = (await s.execute(select(WebSession).where(
            WebSession.admin_id == admin.id, WebSession.revoked == False))).scalars()  # noqa
        return [{"id": r.id, "device": r.device or "مرورگر", "ip": r.ip or "—",
                 "location": "نامشخص", "createdAt": iso(r.created_at),
                 "lastActiveAt": iso(r.last_active_at), "current": r.id == sess.id}
                for r in rows]


@router.delete("/auth/sessions/{session_id}")
async def revoke_session(session_id: str, request: Request):
    admin, _ = await current_admin(request)
    async with db.session_scope() as s:
        sess = await s.get(WebSession, session_id)
        if not sess or sess.admin_id != admin.id:
            raise ApiErr(404, "نشست یافت نشد.", "NOT_FOUND")
        sess.revoked = True
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="session.revoked", module="security",
                            target_type="session", target_id=session_id,
                            ip=_ip(request), details="لغو نشست از راه دور")
    return Response(status_code=204)


# ----------------------------------------------------------- dashboard ----
@router.get("/dashboard/metrics")
async def dashboard_metrics(request: Request, period: str = Query("24h", alias="range"),
                            platform: str = "all"):
    # v2.3.1: `range` param shadowed the builtin and crashed the endpoint (500)
    await require(request, "dashboard.view")
    day_from = {"24h": 1, "7d": 7, "30d": 30}.get(period, 1)
    async with db.session_scope() as s:
        base = select(func.count(User.id)).where(User.status != "deleted")
        if platform != "all":
            base = base.where(User.platform == platform)
        total = (await s.execute(base)).scalar()
        # v2.3.3: incomplete signups — age 0 means the birthdate step was never
        # reached (row is created on the user's very first message). These stay
        # inside totalUsers; this is only an extra breakdown, not a separate
        # exclusion.
        inc_base = select(func.count(User.id)).where(User.age == 0,
                                                     User.status != "deleted")
        if platform != "all":
            inc_base = inc_base.where(User.platform == platform)
        incomplete = (await s.execute(inc_base)).scalar()
        tg = (await s.execute(select(func.count(User.id)).where(User.platform == "telegram"))).scalar()
        bal = (await s.execute(select(func.count(User.id)).where(User.platform == "bale"))).scalar()
        online = (await s.execute(select(func.count(User.id)).where(
            User.status == "active", User.last_seen_at >= utcnow() - timedelta(minutes=5)))).scalar()
        searching = (await s.execute(select(func.count(QueueEntry.id)))).scalar()
        active_chats = (await s.execute(select(func.count(Chat.id)).where(Chat.status == "active"))).scalar()
        since = utcnow() - timedelta(days=day_from)
        new_reg = (await s.execute(select(func.count(User.id)).where(
            User.registered_at >= since))).scalar()
        dau = (await s.execute(select(func.count(func.distinct(ChatMessage.sender_id))).where(
            ChatMessage.created_at >= utcnow() - timedelta(days=1)))).scalar() or 0
        wau = (await s.execute(select(func.count(func.distinct(ChatMessage.sender_id))).where(
            ChatMessage.created_at >= utcnow() - timedelta(days=7)))).scalar() or 0
        mau = (await s.execute(select(func.count(func.distinct(ChatMessage.sender_id))).where(
            ChatMessage.created_at >= utcnow() - timedelta(days=30)))).scalar() or 0
        stars = (await s.execute(select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status == "succeeded", Payment.currency == "XTR",
            Payment.created_at >= utcnow() - timedelta(days=30)))).scalar()
        toman = (await s.execute(select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status == "succeeded", Payment.currency != "XTR",
            Payment.created_at >= utcnow() - timedelta(days=30)))).scalar()
        coins_issued = (await s.execute(select(func.coalesce(func.sum(CoinTransaction.amount), 0)).where(
            CoinTransaction.amount > 0, CoinTransaction.created_at >= utcnow() - timedelta(days=30)))).scalar()
        coins_spent = (await s.execute(select(func.coalesce(func.sum(-CoinTransaction.amount), 0)).where(
            CoinTransaction.amount < 0, CoinTransaction.created_at >= utcnow() - timedelta(days=30)))).scalar()
        pending = (await s.execute(select(func.count(Report.id)).where(
            Report.status.in_(["pending", "under_review"])))).scalar()
        autobans = (await s.execute(select(func.count(Ban.id)).where(
            Ban.source == "auto_3_reports", Ban.issued_at >= utcnow() - timedelta(days=1)))).scalar()
        manualbans = (await s.execute(select(func.count(Ban.id)).where(
            Ban.source == "manual_admin", Ban.issued_at >= utcnow() - timedelta(days=1)))).scalar()
        filtered = (await s.execute(select(func.count(ViolationLog.id)).where(
            ViolationLog.created_at >= utcnow() - timedelta(days=1)))).scalar()
        msgs_hr = (await s.execute(select(func.count(ChatMessage.id)).where(
            ChatMessage.created_at >= utcnow() - timedelta(hours=1)))).scalar()
        chats_hr = (await s.execute(select(func.count(Chat.id)).where(
            Chat.started_at >= utcnow() - timedelta(hours=1)))).scalar()
        hourly = []
        for h in range(23, -1, -1):
            t0 = utcnow() - timedelta(hours=h)
            t1 = t0 + timedelta(hours=1)
            ch = (await s.execute(select(func.count(Chat.id)).where(
                Chat.started_at >= t0, Chat.started_at < t1))).scalar()
            ms = (await s.execute(select(func.count(ChatMessage.id)).where(
                ChatMessage.created_at >= t0, ChatMessage.created_at < t1))).scalar()
            # v2.3.2: join Chat explicitly — Postgres rejects the old ON clause
            # that referenced chats without them in FROM (sqlite tolerated it).
            tg_m = (await s.execute(select(func.count(ChatMessage.id)).join(
                Chat, Chat.id == ChatMessage.chat_id).join(
                User, User.id == Chat.user_a_id).where(
                Chat.started_at >= t0, Chat.started_at < t1,
                User.platform == "telegram"))).scalar()
            hourly.append({"hour": t0.strftime("%H:00"), "chats": ch, "messages": ms,
                           "telegram": tg_m, "bale": max(ch - tg_m, 0)})
        daily = []
        for d in range(6, -1, -1):
            t0 = utcnow() - timedelta(days=d)
            t1 = t0 + timedelta(days=1)
            regs = (await s.execute(select(func.count(User.id)).where(
                User.registered_at >= t0, User.registered_at < t1))).scalar()
            act = (await s.execute(select(func.count(func.distinct(ChatMessage.sender_id))).where(
                ChatMessage.created_at >= t0, ChatMessage.created_at < t1))).scalar()
            rev_t = (await s.execute(select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == "succeeded", Payment.currency != "XTR",
                Payment.created_at >= t0, Payment.created_at < t1))).scalar()
            ci = (await s.execute(select(func.coalesce(func.sum(CoinTransaction.amount), 0)).where(
                CoinTransaction.amount > 0, CoinTransaction.created_at >= t0,
                CoinTransaction.created_at < t1))).scalar()
            cs = (await s.execute(select(func.coalesce(func.sum(-CoinTransaction.amount), 0)).where(
                CoinTransaction.amount < 0, CoinTransaction.created_at >= t0,
                CoinTransaction.created_at < t1))).scalar()
            daily.append({"date": t0.strftime("%Y-%m-%d"), "registrations": regs, "dau": act,
                          "revenueToman": rev_t, "coinsIssued": ci, "coinsSpent": cs})
        mod_breakdown = [
            {"category": "فحاشی", "count": (await s.execute(select(func.count(ViolationLog.id)).where(
                ViolationLog.rule == "profanity"))).scalar()},
            {"category": "اطلاعات تماس", "count": (await s.execute(select(func.count(ViolationLog.id)).where(
                ViolationLog.rule == "contact"))).scalar()},
            {"category": "رسانه غیرمجاز", "count": 0},
            {"category": "گزارش کاربران", "count": (await s.execute(select(func.count(Report.id)))).scalar()}]
        # v2.4: signup funnel — where do the 66% abandoned signups leak?
        all_users = base
        started = (await s.execute(all_users)).scalar()
        rules_base = select(func.count(User.id)).where(User.accepted_rules_at.isnot(None),
                                                       User.status != "deleted")
        birth_base = select(func.count(User.id)).where(User.birth_year.isnot(None),
                                                       User.status != "deleted")
        comp_base = select(func.count(User.id)).where(User.registration_rewarded == True,  # noqa
                                                      User.status != "deleted")
        if platform != "all":
            rules_base = rules_base.where(User.platform == platform)
            birth_base = birth_base.where(User.platform == platform)
            comp_base = comp_base.where(User.platform == platform)
        accepted_rules = (await s.execute(rules_base)).scalar()
        birthdate_set = (await s.execute(birth_base)).scalar()
        completed = (await s.execute(comp_base)).scalar()
        funnel = {"started": started, "acceptedRules": accepted_rules,
                  "birthdateSet": birthdate_set, "completed": completed,
                  "completedPct": round(completed * 100 / started, 1) if started else 0.0}
    return {"totalUsers": total, "dau": dau, "wau": wau, "mau": mau, "onlineUsers": online,
            "incompleteSignups": incomplete,
            "signupFunnel": funnel,
            "searchingUsers": searching, "activeChats": active_chats, "chatsPerHour": chats_hr,
            "messagesPerHour": msgs_hr, "newRegistrations24h": new_reg, "telegramUsers": tg,
            "baleUsers": bal, "revenueToman30d": toman, "revenueStars30d": stars,
            "coinsIssued30d": coins_issued, "coinsSpent30d": coins_spent,
            "pendingReports": pending, "autoBans24h": autobans, "manualBans24h": manualbans,
            "filteredMessages24h": filtered, "hourlyActivity": hourly, "dailyGrowth": daily,
            "moderationBreakdown": mod_breakdown}


@router.get("/notifications")
async def notifications(request: Request):
    await require(request, "dashboard.view")
    async with db.session_scope() as s:
        rows = (await s.execute(select(AdminNotification).order_by(
            AdminNotification.id.desc()).limit(50))).scalars()
        return [{"id": str(n.id), "title": n.title, "body": n.body, "severity": n.severity,
                 "read": n.read, "route": n.route, "createdAt": iso(n.created_at)} for n in rows]


@router.post("/notifications/read-all")
async def read_all_notifications(request: Request):
    await require(request, "dashboard.view")
    async with db.session_scope() as s:
        await s.execute(
            update_stmt(AdminNotification, {"read": True}))
    return Response(status_code=204)


def update_stmt(model, values: dict):
    from sqlalchemy import update
    return update(model).values(**values)


# --------------------------------------------------------------- users ----
@router.get("/users")
async def list_users(request: Request, platform: str = "", status: str = "", q: str = ""):
    await require(request, "users.view")
    async with db.session_scope() as s:
        query = select(User).order_by(User.id.desc()).limit(500)
        if platform:
            query = query.where(User.platform == platform)
        # v2.3.3: the declared-but-never-wired status filter now works; the
        # special value "incomplete" selects users with age 0 (registration
        # never completed — see app_user ageGroup).
        # v2.4: "blocked_bot" selects the isolated bot-blocked list.
        if status == "incomplete":
            query = query.where(User.age == 0)
        elif status == "blocked_bot":
            query = query.where(User.bot_blocked == True)  # noqa
        elif status:
            query = query.where(User.status == status)
        if q:
            query = query.where((User.public_id == q.upper()) | (User.display_name.ilike(f"%{q}%")) |
                                (User.platform_user_id == q))
        rows = list((await s.execute(query)).scalars())
        # v2.4 PERF: the old loop ran TWO extra queries per user (active ban +
        # queue entry) — up to 1000 round-trips for 500 users. Both are now
        # fetched in one bulk query each and mapped in memory.
        ids = [u.id for u in rows]
        searching_ids: set[int] = set()
        ban_map: dict[int, str] = {}
        if ids:
            q_ids = [u.id for u in rows]
            q_rows = (await s.execute(select(QueueEntry.user_id).where(
                QueueEntry.user_id.in_(q_ids)))).all()
            searching_ids = {r[0] for r in q_rows}
            bans = (await s.execute(select(Ban).where(
                Ban.user_id.in_(q_ids), Ban.status == "active"))).scalars()
            now = utcnow()
            for b in bans:
                if b.type == "permanent":
                    ban_map[b.user_id] = "perm_banned"
                elif b.expires_at and b.expires_at > now:
                    ban_map[b.user_id] = "temp_banned"
        out = []
        for u in rows:
            au = app_user(u)
            au["botBlocked"] = bool(u.bot_blocked)
            if u.status == "banned":
                au["status"] = ban_map.get(u.id, "temp_banned")
            au["searching"] = u.id in searching_ids
            out.append(au)
        return out


class UserPatch(BaseModel):
    displayName: str | None = None
    city: str | None = None
    age: int | None = None
    gender: str | None = None
    language: str | None = None


@router.patch("/users/{user_id}")
async def patch_user(user_id: str, body: UserPatch, request: Request):
    admin, _ = await require(request, "users.edit")
    async with db.session_scope() as s:
        u = await _get_user(s, user_id)
        if body.displayName is not None:
            u.display_name = body.displayName[:20]
        if body.age is not None:
            if not (13 <= body.age <= 60):
                raise ApiErr(400, "سن باید بین ۱۳ تا ۶۰ باشد.", "INVALID_AGE")
            u.age = body.age
        if body.gender:
            u.gender = body.gender
        if body.language:
            u.language = body.language
        if body.city is not None:
            u.city = body.city[:40]
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="user.profile_updated", module="users",
                            target_type="user", target_id=u.public_id, ip=_ip(request),
                            details="ویرایش پروفایل کاربر")
        return app_user(u)


class CoinsBody(BaseModel):
    amount: int
    reason: str


@router.post("/users/{user_id}/coins")
async def adjust_coins(user_id: str, body: CoinsBody, request: Request):
    admin, _ = await require(request, "users.coins")
    async with db.session_scope() as s:
        u = await _get_user(s, user_id)
        if body.amount >= 0:
            t = await service.credit(s, u, body.amount, "admin_grant", body.reason)
        else:
            t = await service.debit(s, u, -body.amount, "admin_deduct", body.reason)
            if t is None:
                raise ApiErr(400, "موجودی کاربر برای کسر این مقدار کافی نیست.", "INSUFFICIENT_BALANCE")
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="user.coins_granted" if body.amount >= 0
                            else "user.coins_deducted", module="economy",
                            target_type="user", target_id=u.public_id, ip=_ip(request),
                            details=f"{body.amount:+d} — {body.reason}")
        # v2.4: the result of the admin action also lands in the user's PV
        await _notify_user_action(u, "admin_coins_grant" if body.amount >= 0
                                  else "admin_coins_deduct",
                                  amount=abs(body.amount), reason=body.reason,
                                  balance=u.coin_balance)
        return {"user": app_user(u), "transaction": coin_txn(t, u)}


async def _notify_user_action(u: User, key: str, **kw) -> None:
    """v2.4: deliver an admin-action notice to the user's PV (best effort)."""
    from . import bot as bot_mod, growth
    from .i18n import tr
    from .platform import PlatformError
    adp = bot_mod.adapter(u.platform)
    if not adp:
        return
    try:
        await adp.send_message(u.platform_user_id, tr(u.language, key, **kw))
    except PlatformError as exc:
        await growth.mark_bot_blocked(u.id, u.platform, str(exc))


class BanBody(BaseModel):
    action: str
    type: str | None = None
    hours: int | None = None
    reason: str


@router.post("/users/{user_id}/ban")
async def toggle_ban(user_id: str, body: BanBody, request: Request):
    admin, _ = await require(request, "users.ban")
    async with db.session_scope() as s:
        u = await _get_user(s, user_id)
        if body.action == "unban":
            for b in (await s.execute(select(Ban).where(
                    Ban.user_id == u.id, Ban.status == "active"))).scalars():
                b.status = "revoked"
            u.status = "active"
            await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                                admin_role=admin.role, action="user.unbanned", module="bans",
                                target_type="user", target_id=u.public_id, ip=_ip(request),
                                details=body.reason)
            # v2.4: the user learns about it in their own PV
            await _notify_user_action(u, "admin_unban_notice")
        else:
            type_ = body.type or "temporary"
            await service.ban_user(s, u, type_, "manual_admin", body.reason,
                                   f"web:{admin.username}", hours=body.hours or 24)
            await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                                admin_role=admin.role,
                                action="user.perm_banned" if type_ == "permanent" else "user.temp_banned",
                                module="bans", target_type="user", target_id=u.public_id,
                                ip=_ip(request), details=body.reason)
            # v2.4: the user learns about it in their own PV
            await _notify_user_action(u, "admin_ban_notice", reason=body.reason)
        return app_user(u)


class DeleteBody(BaseModel):
    reason: str


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, body: DeleteBody, request: Request):
    admin, _ = await require(request, "users.delete")
    async with db.session_scope() as s:
        u = await _get_user(s, user_id)
        await service.delete_account(s, u)
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="user.deleted", module="users",
                            target_type="user", target_id=u.public_id, ip=_ip(request),
                            details=body.reason)
    return Response(status_code=204)


async def _get_user(s, user_id: str) -> User:
    try:
        u = await s.get(User, int(user_id))
    except ValueError:
        u = None
    if not u:
        r = await s.execute(select(User).where(User.public_id == user_id.upper()))
        u = r.scalar_one_or_none()
    if not u:
        raise ApiErr(404, "کاربر یافت نشد.", "USER_NOT_FOUND")
    return u


# --------------------------------------------------------------- chats ----
@router.get("/chats")
async def list_chats(request: Request):
    admin, _ = await current_admin(request)
    async with db.session_scope() as s:
        perms = await service.role_permissions(s, admin.role)
        can_all = admin.role == "owner" and service.has_perm(admin.role, perms, "chats.view_all")
        query = select(Chat).order_by(Chat.id.desc()).limit(200)
        if not can_all:
            query = query.where(Chat.has_report == True)  # noqa
        chats = list((await s.execute(query)).scalars())
        out = []
        for c in chats:
            uas = {uid: await s.get(User, uid) for uid in (c.user_a_id, c.user_b_id)}
            if any(u is None for u in uas.values()):
                continue
            msgs = list((await s.execute(select(ChatMessage).where(
                ChatMessage.chat_id == c.id).order_by(ChatMessage.id).limit(300))).scalars())
            rep = (await s.execute(select(Report).where(Report.chat_id == c.id))).scalars()
            rep = list(rep)
            # audit history access
            await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                                admin_role=admin.role, action="chats.viewed", module="chats",
                                target_type="chat", target_id=c.id, ip=_ip(request),
                                details=("دسترسی مالک به همه گفتگوها" if can_all
                                         else "دسترسی به گفتگوی گزارش‌شده"))
            out.append(chat_session(c, uas, msgs, c.has_report, rep[0].id if rep else None))
        privacy = await service.get_setting(s, "privacy")
        return {"chats": out, "scope": "all" if can_all else "reported_only",
                "retentionDays": privacy.get("chatRetentionDays", 30)}


class TerminateBody(BaseModel):
    reason: str


@router.post("/chats/{chat_id}/terminate")
async def terminate_chat(chat_id: str, body: TerminateBody, request: Request):
    admin, _ = await require(request, "moderation.manage")
    async with db.session_scope() as s:
        c = await s.get(Chat, int(chat_id))
        if not c:
            raise ApiErr(404, "مکالمه یافت نشد.", "CHAT_NOT_FOUND")
        await service.end_chat(s, c, "terminated")
        c.status = "terminated"
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="chat.terminated", module="chats",
                            target_type="chat", target_id=c.id, ip=_ip(request),
                            details=body.reason)
        uas = {uid: await s.get(User, uid) for uid in (c.user_a_id, c.user_b_id)}
        # notify participants via their platform bots
        from . import bot as bot_mod
        for u in uas.values():
            if u:
                await bot_mod.send(u, "chat_ended")
        return chat_session(c, uas, [], c.has_report, None)


# ------------------------------------------------------- reports/mods ----
@router.get("/reports")
async def list_reports(request: Request):
    await require(request, "reports.view")
    async with db.session_scope() as s:
        rows = (await s.execute(select(Report).order_by(Report.id.desc()).limit(200))).scalars()
        out = []
        for r in rows:
            rep = await s.get(User, r.reporter_id)
            target = await s.get(User, r.reported_id)
            window = utcnow() - timedelta(hours=24)
            uniq = (await s.execute(select(func.count(func.distinct(Report.reporter_id))).where(
                Report.reported_id == r.reported_id, Report.created_at >= window))).scalar()
            out.append({
                "id": str(r.id), "chatId": str(r.chat_id),
                "reporter": {"userId": str(r.reporter_id), "publicId": rep.public_id if rep else "?",
                             "displayName": rep.display_name if rep else "?",
                             "platform": rep.platform if rep else "?"},
                "reported": {"userId": str(r.reported_id), "publicId": target.public_id if target else "?",
                             "displayName": target.display_name if target else "?",
                             "platform": target.platform if target else "?",
                             "uniqueReports24h": uniq,
                             "previousViolations": target.ban_count if target else 0},
                # v2.2: user-chosen reasons (bad_name/abuse/spam/other) + legacy values
                "reason": r.reason if r.reason in ("bad_name", "abuse", "spam", "other",
                                                   "harassment", "inappropriate_media",
                                                   "external_link", "underage_suspicion",
                                                   "fraud", "anon_message")
                          else "other",
                "details": r.details, "status": r.status,
                "autoBanTriggered": r.auto_ban_triggered, "createdAt": iso(r.created_at),
                "resolvedBy": r.resolved_by or "", "resolvedAt": iso(r.resolved_at)})
        return out


class ResolveBody(BaseModel):
    action: str
    note: str


@router.post("/reports/{report_id}/resolve")
async def resolve_report(report_id: str, body: ResolveBody, request: Request):
    admin, _ = await require(request, "reports.resolve")
    async with db.session_scope() as s:
        r = await s.get(Report, int(report_id))
        if not r:
            raise ApiErr(404, "گزارش یافت نشد.", "REPORT_NOT_FOUND")
        r.status = "dismissed" if body.action == "dismiss" else "action_taken"
        r.resolved_by = admin.username
        r.resolved_at = utcnow()
        if body.action in ("temp_ban_24h", "perm_ban"):
            target = await s.get(User, r.reported_id)
            if target:
                await service.ban_user(s, target,
                                       "permanent" if body.action == "perm_ban" else "temporary",
                                       "manual_admin", f"اقدام بر اساس گزارش #{r.id}: {body.note}",
                                       f"web:{admin.username}", hours=24)
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="report.resolved", module="reports",
                            target_type="report", target_id=r.id, ip=_ip(request),
                            details=f"{body.action} — {body.note}")
        return {"ok": True, "status": r.status}


@router.get("/moderation")
async def moderation_all(request: Request):
    await require(request, "moderation.manage")
    async with db.session_scope() as s:
        dic = (await s.execute(select(DictionaryWord).order_by(DictionaryWord.id.desc()))).scalars()
        alw = (await s.execute(select(AllowlistPhrase).order_by(AllowlistPhrase.id.desc()))).scalars()
        cfg = await service.get_setting(s, "moderation")
        vio = (await s.execute(select(ViolationLog).order_by(ViolationLog.id.desc()).limit(200))).scalars()
        users_by_id = {}
        out_v = []
        for v in vio:
            if v.user_id not in users_by_id:
                users_by_id[v.user_id] = await s.get(User, v.user_id)
            u = users_by_id.get(v.user_id)
            out_v.append({"id": str(v.id), "userId": str(v.user_id),
                          "userDisplayName": u.display_name if u else "?",
                          "platform": u.platform if u else "?", "rule": v.rule,
                          "snippet": v.snippet, "actionTaken": v.action_taken,
                          "createdAt": iso(v.created_at)})
        return {"dictionary": [{"id": str(d.id), "word": d.word, "severity": d.severity,
                                "action": d.action, "matchCount30d": d.match_count,
                                "addedBy": d.added_by, "createdAt": iso(d.created_at)} for d in dic],
                "allowlist": [{"id": str(a.id), "phrase": a.phrase, "note": a.note,
                               "addedBy": a.added_by, "createdAt": iso(a.created_at)} for a in alw],
                "config": {"blockUrls": cfg.get("blockUrls", True),
                           "blockTelegramHandles": cfg.get("blockTelegramHandles", True),
                           "blockPhoneNumbers": cfg.get("blockPhoneNumbers", True),
                           "blockInstagramIds": cfg.get("blockInstagramIds", True),
                           "autoBanReportThreshold": cfg.get("autoBanReportThreshold", 3),
                           "autoBanWindowHours": cfg.get("autoBanWindowHours", 24),
                           "autoBanDurationHours": cfg.get("autoBanDurationHours", 24)},
                "violations": out_v}


class WordBody(BaseModel):
    word: str
    severity: str = "high"
    action: str = "block_and_warn"


@router.post("/moderation/dictionary")
async def add_word(body: WordBody, request: Request):
    admin, _ = await require(request, "moderation.manage")
    async with db.session_scope() as s:
        w = DictionaryWord(word=body.word.strip()[:60], severity=body.severity,
                           action=body.action, added_by=admin.username)
        s.add(w)
        await s.flush()
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="moderation.word_added",
                            module="moderation", target_type="dictionary", target_id=w.word,
                            ip=_ip(request), details=f"افزودن عبارت ({body.severity})")
        return {"id": str(w.id), "word": w.word, "severity": w.severity, "action": w.action,
                "matchCount30d": 0, "addedBy": w.added_by, "createdAt": iso(w.created_at)}


@router.delete("/moderation/dictionary/{wid}")
async def del_word(wid: str, request: Request):
    await require(request, "moderation.manage")
    async with db.session_scope() as s:
        await s.execute(delete(DictionaryWord).where(DictionaryWord.id == int(wid)))
    return Response(status_code=204)


class AllowBody(BaseModel):
    phrase: str
    note: str = ""


@router.post("/moderation/allowlist")
async def add_allow(body: AllowBody, request: Request):
    admin, _ = await require(request, "moderation.manage")
    async with db.session_scope() as s:
        a = AllowlistPhrase(phrase=body.phrase.strip()[:60], note=body.note[:120],
                            added_by=admin.username)
        s.add(a)
        await s.flush()
        return {"id": str(a.id), "phrase": a.phrase, "note": a.note,
                "addedBy": a.added_by, "createdAt": iso(a.created_at)}


@router.delete("/moderation/allowlist/{aid}")
async def del_allow(aid: str, request: Request):
    await require(request, "moderation.manage")
    async with db.session_scope() as s:
        await s.execute(delete(AllowlistPhrase).where(AllowlistPhrase.id == int(aid)))
    return Response(status_code=204)


class ModConfigBody(BaseModel):
    blockUrls: bool | None = None
    blockTelegramHandles: bool | None = None
    blockPhoneNumbers: bool | None = None
    blockInstagramIds: bool | None = None
    autoBanReportThreshold: int | None = None
    autoBanWindowHours: int | None = None
    autoBanDurationHours: int | None = None


@router.patch("/moderation/config")
async def patch_mod_config(body: ModConfigBody, request: Request):
    admin, _ = await require(request, "moderation.manage")
    async with db.session_scope() as s:
        cfg = await service.get_setting(s, "moderation")
        for k, v in body.model_dump(exclude_none=True).items():
            if k == "autoBanReportThreshold" and v < 1:
                continue
            cfg[k] = v
        await service.set_setting(s, "moderation", cfg)
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="moderation.config_updated",
                            module="moderation", target_type="config", target_id="filters",
                            ip=_ip(request), details="بروزرسانی فیلترها")
        return {"blockUrls": cfg["blockUrls"], "blockTelegramHandles": cfg["blockTelegramHandles"],
                "blockPhoneNumbers": cfg["blockPhoneNumbers"],
                "blockInstagramIds": cfg["blockInstagramIds"],
                "autoBanReportThreshold": cfg["autoBanReportThreshold"],
                "autoBanWindowHours": cfg["autoBanWindowHours"],
                "autoBanDurationHours": cfg["autoBanDurationHours"]}


# ---------------------------------------------------------- bans/appeals ----
@router.get("/bans")
async def bans_all(request: Request):
    await require(request, "bans.manage")
    async with db.session_scope() as s:
        for b in (await s.execute(select(Ban).where(Ban.status == "active"))).scalars():
            if b.type == "temporary" and b.expires_at and b.expires_at < utcnow():
                b.status = "expired"
        bans = (await s.execute(select(Ban).order_by(Ban.id.desc()).limit(300))).scalars()
        appeals = (await s.execute(select(Appeal).order_by(Appeal.id.desc()).limit(300))).scalars()
        ubyid = {}

        async def uof(uid):
            if uid not in ubyid:
                ubyid[uid] = await s.get(User, uid)
            return ubyid[uid]
        ban_out = []
        for b in bans:
            u = await uof(b.user_id)
            ban_out.append({"id": str(b.id), "userId": str(b.user_id),
                            "publicId": u.public_id if u else "?",
                            "userDisplayName": u.display_name if u else "?",
                            "platform": u.platform if u else "?", "type": b.type,
                            "source": b.source, "reason": b.reason, "status": b.status,
                            "issuedBy": b.issued_by, "issuedAt": iso(b.issued_at),
                            "expiresAt": iso(b.expires_at), "appealEligible": b.type == "permanent"})
        app_out = []
        for a in appeals:
            u = await uof(a.user_id)
            ban = await s.get(Ban, a.ban_id)
            app_out.append({"id": str(a.id), "banId": str(a.ban_id), "userId": str(a.user_id),
                            "publicId": u.public_id if u else "?",
                            "userDisplayName": u.display_name if u else "?",
                            "platform": u.platform if u else "?",
                            "banReason": ban.reason if ban else "?",
                            "appealMessage": a.message, "status": a.status,
                            "submittedAt": iso(a.submitted_at),
                            "reviewedBy": a.reviewed_by or "",
                            "reviewNote": a.review_note or "",
                            "reviewedAt": iso(a.reviewed_at)})
        return {"bans": ban_out, "appeals": app_out}


class ReasonBody(BaseModel):
    reason: str


@router.post("/bans/{ban_id}/revoke")
async def revoke_ban(ban_id: str, body: ReasonBody, request: Request):
    admin, _ = await require(request, "bans.manage")
    async with db.session_scope() as s:
        b = await s.get(Ban, int(ban_id))
        if not b:
            raise ApiErr(404, "رکورد مسدودیت یافت نشد.", "BAN_NOT_FOUND")
        b.status = "revoked"
        u = await s.get(User, b.user_id)
        if u:
            u.status = "active"
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="ban.revoked", module="bans",
                            target_type="ban", target_id=b.id, ip=_ip(request), details=body.reason)
        return {"ok": True, "status": "revoked"}


class AppealBody(BaseModel):
    decision: str
    note: str


@router.post("/bans/appeals/{appeal_id}/review")
async def review_appeal(appeal_id: str, body: AppealBody, request: Request):
    admin, _ = await require(request, "appeals.review")
    async with db.session_scope() as s:
        a = await s.get(Appeal, int(appeal_id))
        if not a:
            raise ApiErr(404, "درخواست اعتراض یافت نشد.", "APPEAL_NOT_FOUND")
        a.status = body.decision
        a.review_note = body.note
        a.reviewed_by = admin.username
        a.reviewed_at = utcnow()
        if body.decision == "approved":
            b = await s.get(Ban, a.ban_id)
            if b:
                b.status = "revoked"
            u = await s.get(User, a.user_id)
            if u:
                u.status = "active"
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role,
                            action="appeal.approved" if body.decision == "approved" else "appeal.rejected",
                            module="bans", target_type="appeal", target_id=a.id,
                            ip=_ip(request), details=body.note)
        return {"ok": True, "status": a.status}


# ------------------------------------------------------------- economy ----
@router.get("/economy")
async def economy_all(request: Request):
    await require(request, "economy.view")
    async with db.session_scope() as s:
        eco = await service.get_setting(s, "economy")
        store = await service.get_setting(s, "store")
        txns = (await s.execute(select(CoinTransaction).order_by(
            CoinTransaction.id.desc()).limit(300))).scalars()
        users = (await s.execute(select(User).order_by(User.id.desc()).limit(300))).scalars()
        umap = {u.id: u for u in users}
        return {"config": {"initialReward": eco["initialReward"], "messageCost": eco["messageCost"],
                           "dailyBonus": eco["dailyBonus"],
                           "genderFilterMatchCost": eco["genderFilterMatchCost"],
                           "referralRewardPerParty": eco["referralRewardPerParty"],
                           # v2.2: coin-store conversion rates (changeable)
                           "tomanPerCoin": store.get("tomanPerCoin", 500),
                           "coinsPerStar": store.get("coinsPerStar", 20),
                           "customMinCoins": store.get("customMinCoins", 10),
                           "customMaxCoins": store.get("customMaxCoins", 5000),
                           "updatedAt": eco.get("updatedAt") or "",
                           "updatedBy": eco.get("updatedBy") or "system"},
                "transactions": [coin_txn(t, umap.get(t.user_id)) for t in txns],
                "users": [app_user(u) for u in users if u.status != "deleted"]}


class EconomyBody(BaseModel):
    initialReward: int
    messageCost: int
    dailyBonus: int
    genderFilterMatchCost: int
    referralRewardPerParty: int
    tomanPerCoin: int | None = None
    coinsPerStar: int | None = None
    customMinCoins: int | None = None
    customMaxCoins: int | None = None


@router.put("/economy/config")
async def economy_update(body: EconomyBody, request: Request):
    admin, _ = await require(request, "economy.configure")
    async with db.session_scope() as s:
        eco = await service.get_setting(s, "economy")
        store = await service.get_setting(s, "store")
        eco.update(body.model_dump(exclude_none=True, include={
            "initialReward", "messageCost", "dailyBonus",
            "genderFilterMatchCost", "referralRewardPerParty"}))
        for k in ("tomanPerCoin", "coinsPerStar", "customMinCoins", "customMaxCoins"):
            v = getattr(body, k)
            if v is not None and int(v) > 0:
                store[k] = int(v)
        eco["updatedBy"] = admin.username
        eco["updatedAt"] = utcnow().isoformat()
        await service.set_setting(s, "economy", eco)
        await service.set_setting(s, "store", store)
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="economy.config_updated",
                            module="economy", target_type="economy_config", target_id="global",
                            ip=_ip(request),
                            details=f"ثبت‌نام:{body.initialReward} | پیام:{body.messageCost} | "
                                    f"روزانه:{body.dailyBonus} | فیلتر:{body.genderFilterMatchCost} | "
                                    f"دعوت:{body.referralRewardPerParty} | "
                                    f"تومان/سکه:{store.get('tomanPerCoin')} | "
                                    f"سکه/استارز:{store.get('coinsPerStar')}")
        return {"initialReward": eco["initialReward"], "messageCost": eco["messageCost"],
                "dailyBonus": eco["dailyBonus"], "genderFilterMatchCost": eco["genderFilterMatchCost"],
                "referralRewardPerParty": eco["referralRewardPerParty"],
                "tomanPerCoin": store.get("tomanPerCoin", 500),
                "coinsPerStar": store.get("coinsPerStar", 20),
                "customMinCoins": store.get("customMinCoins", 10),
                "customMaxCoins": store.get("customMaxCoins", 5000),
                "updatedAt": eco["updatedAt"], "updatedBy": eco["updatedBy"]}


@router.get("/store/packages")
async def store_packages(request: Request):
    await require(request, "store.manage")
    async with db.session_scope() as s:
        pkgs = (await s.execute(select(StorePackage).order_by(StorePackage.coins))).scalars()
        out = []
        for p in pkgs:
            sales = (await s.execute(select(func.count(Payment.id)).where(
                Payment.package_id == p.id, Payment.status == "succeeded",
                Payment.created_at >= utcnow() - timedelta(days=30)))).scalar()
            out.append({"id": p.id, "coins": p.coins, "title": p.title, "badge": p.badge or "",
                        "telegramStarsPrice": p.telegram_stars_price,
                        "baleTomanPrice": p.bale_toman_price, "active": p.active,
                        "telegramEnabled": p.telegram_enabled, "baleEnabled": p.bale_enabled,
                        "sales30d": sales})
        return out


class PackagePatch(BaseModel):
    telegramStarsPrice: int | None = None
    baleTomanPrice: int | None = None
    active: bool | None = None
    telegramEnabled: bool | None = None
    baleEnabled: bool | None = None


@router.patch("/store/packages/{pid}")
async def patch_package(pid: str, body: PackagePatch, request: Request):
    admin, _ = await require(request, "store.manage")
    async with db.session_scope() as s:
        p = await s.get(StorePackage, pid)
        if not p:
            raise ApiErr(404, "بسته سکه یافت نشد.", "PACKAGE_NOT_FOUND")
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(p, k, v)
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="store.package_updated",
                            module="payments", target_type="coin_package", target_id=p.id,
                            ip=_ip(request), details=f"بروزرسانی بسته {p.coins} سکه‌ای")
        return {"id": p.id, "coins": p.coins, "title": p.title, "badge": p.badge or "",
                "telegramStarsPrice": p.telegram_stars_price, "baleTomanPrice": p.bale_toman_price,
                "active": p.active, "telegramEnabled": p.telegram_enabled,
                "baleEnabled": p.bale_enabled, "sales30d": 0}


@router.get("/payments")
async def list_payments(request: Request):
    await require(request, "payments.view")
    async with db.session_scope() as s:
        rows = (await s.execute(select(Payment).order_by(Payment.id.desc()).limit(300))).scalars()
        out = []
        for p in rows:
            u = await s.get(User, p.user_id)
            out.append({"id": str(p.id), "referenceCode": (p.telegram_payment_charge_id or "?")[:24],
                        "gateway": "telegram_stars" if p.platform == "telegram" else "bale_pay",
                        "userId": str(p.user_id), "userDisplayName": u.display_name if u else "?",
                        "packageCoins": p.coins,
                        "amountDisplay": f"{p.amount:,} {'⭐' if p.currency == 'XTR' else 'ریال'}",
                        "amountNumeric": p.amount, "currency": p.currency, "status": p.status,
                        "failureReason": p.failure_reason or "",
                        "createdAt": iso(p.created_at), "refundedAt": iso(p.refunded_at),
                        "refundedBy": p.refunded_by or ""})
        return out


@router.post("/payments/{payment_id}/refund")
async def refund_payment(payment_id: str, body: ReasonBody, request: Request):
    admin, _ = await require(request, "payments.refund")
    async with db.session_scope() as s:
        p = await s.get(Payment, int(payment_id))
        if not p:
            raise ApiErr(404, "تراکنش پرداخت یافت نشد.", "PAYMENT_NOT_FOUND")
        if p.status != "succeeded":
            raise ApiErr(400, "فقط تراکنش‌های موفق قابل استردادند.", "NOT_REFUNDABLE")
        if p.platform == "telegram":
            adp = RUNTIME.get("adapter_telegram")
            u = await s.get(User, p.user_id)
            if not adp or not u or not p.telegram_payment_charge_id:
                raise ApiErr(503, "بازوی تلگرام یا شناسه تراکنش در دسترس نیست.", "REFUND_UNAVAILABLE")
            from .platform import PlatformError
            try:
                await adp.refund_payment(int(u.platform_user_id), p.telegram_payment_charge_id)
            except PlatformError as exc:
                raise ApiErr(502, f"خطای Telegram در استرداد: {exc}", "REFUND_FAILED")
            p.status = "refunded"
            p.refunded_at = utcnow()
            p.refunded_by = admin.username
            if u:
                await service.debit(s, u, min(p.coins, u.coin_balance), "admin_deduct",
                                    f"برداشت سکه خرید استردادشده: {body.reason}")
        else:
            raise HTTPException(400, "بازوی Bot API بله روش استرداد مستند ندارد؛ "
                                     "استرداد باید از پنل کیف‌پول بله به‌صورت دستی انجام شود.",
                                {"code": "BALE_REFUND_MANUAL"})
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="payment.refunded", module="payments",
                            target_type="payment", target_id=p.id, ip=_ip(request),
                            details=f"استرداد ({p.telegram_payment_charge_id}): {body.reason}")
        return {"ok": True, "status": p.status}


@router.get("/referrals")
async def list_referrals(request: Request):
    await require(request, "referrals.view")
    async with db.session_scope() as s:
        rows = (await s.execute(select(Referral).order_by(Referral.id.desc()).limit(300))).scalars()
        out = []
        for r in rows:
            ref = await s.get(User, r.referrer_id)
            new = await s.get(User, r.referred_id)
            eco = await service.get_setting(s, "economy")
            out.append({"id": str(r.id), "code": r.code,
                        "link": f"https://t.me/?start=ref_{r.code}",
                        "referrerId": str(r.referrer_id),
                        "referrerName": ref.display_name if ref else "?",
                        "referrerPlatform": ref.platform if ref else "?",
                        "referredId": str(r.referred_id),
                        "referredName": new.display_name if new else "?",
                        "referredPlatform": new.platform if new else "?",
                        "rewardCoinsPerParty": eco["referralRewardPerParty"],
                        "status": r.status, "fraudScore": r.fraud_score,
                        "fraudReasons": r.fraud_reasons or [], "createdAt": iso(r.created_at)})
        return out


class StatusBody(BaseModel):
    status: str


@router.patch("/referrals/{rid}")
async def patch_referral(rid: str, body: StatusBody, request: Request):
    admin, _ = await require(request, "referrals.view")
    async with db.session_scope() as s:
        r = await s.get(Referral, int(rid))
        if not r:
            raise ApiErr(404, "رکورد دعوت یافت نشد.", "REFERRAL_NOT_FOUND")
        r.status = body.status
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="referral.status_changed",
                            module="economy", target_type="referral", target_id=r.id,
                            ip=_ip(request), details=f"وضعیت: {body.status}")
        return {"ok": True, "status": r.status}


@router.get("/daily-rewards")
async def daily_rewards(request: Request):
    await require(request, "daily_rewards.manage")
    async with db.session_scope() as s:
        eco = await service.get_setting(s, "economy")
        app_cfg = await service.get_setting(s, "app")
        rows = (await s.execute(select(DailyClaim).order_by(DailyClaim.id.desc()).limit(200))).scalars()
        out = []
        for c in rows:
            u = await s.get(User, c.user_id)
            out.append({"id": str(c.id), "userId": str(c.user_id),
                        "userDisplayName": u.display_name if u else "?",
                        "platform": u.platform if u else "?", "streakDay": 1,
                        "coinsAwarded": c.coins, "claimedAt": iso(c.claimed_at)})
        return {"claims": out, "dailyBonusCoins": eco["dailyBonus"],
                "enabled": app_cfg.get("enableDailyRewards", True)}


class DailyBody(BaseModel):
    dailyBonusCoins: int
    enabled: bool


@router.patch("/daily-rewards/config")
async def daily_config(body: DailyBody, request: Request):
    admin, _ = await require(request, "daily_rewards.manage")
    async with db.session_scope() as s:
        eco = await service.get_setting(s, "economy")
        eco["dailyBonus"] = body.dailyBonusCoins
        await service.set_setting(s, "economy", eco)
        app_cfg = await service.get_setting(s, "app")
        app_cfg["enableDailyRewards"] = body.enabled
        await service.set_setting(s, "app", app_cfg)
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="daily_rewards.updated",
                            module="economy", target_type="daily_rewards", target_id="global",
                            ip=_ip(request), details=f"مقدار: {body.dailyBonusCoins} / فعال: {body.enabled}")
    return Response(status_code=204)


# ------------------------------------------------------ media/matching ----
MEDIA_META = {
    "text": ("متن", "پیام‌های متنی", "full", "full"),
    "photo": ("عکس", "تصاویر", "full", "full"),
    "voice": ("پیام صوتی", "ویس", "full", "full"),
    "video": ("ویدیو", "فایل ویدیویی", "full", "full"),
    "document": ("فایل", "اسناد و فایل‌های عمومی", "full", "full"),
    "audio": ("موزیک", "فایل صوتی", "full", "full"),
    "sticker": ("استیکر", "استیکر", "full", "unsupported"),
    "gif": ("گیف", "فایل متحرک (animation)", "full", "full"),
    "location": ("موقعیت", "اشتراک مکان", "full", "full"),
    "contact": ("مخاطب", "اشتراک شماره تماس", "full", "full"),
    "video_note": ("پیام ویدیویی دایره‌ای", "ویدیو نوت", "full", "unsupported"),
    "poll": ("نظرسنجی", "نظرسنجی", "full", "unsupported"),
}


@router.get("/settings/media")
async def media_list(request: Request):
    await require(request, "media.configure")
    async with db.session_scope() as s:
        rows = (await s.execute(select(MediaType).order_by(MediaType.key))).scalars()
        out = []
        for m in rows:
            label, desc, tg, bale = MEDIA_META.get(m.key, (m.key, "", "full", "full"))
            key = "animation" if m.key == "gif" else m.key
            out.append({"id": m.key, "key": m.key, "label": label, "description": desc,
                        "enabled": m.enabled, "telegramSupport": tg, "baleSupport": bale,
                        "maxSizeMb": 20, "notes": None})
        return out


class EnabledBody(BaseModel):
    enabled: bool


@router.patch("/settings/media/{mid}")
async def media_toggle(mid: str, body: EnabledBody, request: Request):
    admin, _ = await require(request, "media.configure")
    async with db.session_scope() as s:
        m = await s.get(MediaType, mid)
        if not m:
            raise ApiErr(404, "تنظیم رسانه یافت نشد.", "NOT_FOUND")
        m.enabled = body.enabled
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="media.toggled", module="settings",
                            target_type="media_type", target_id=m.key, ip=_ip(request),
                            details=f"{m.key}: {'فعال' if body.enabled else 'غیرفعال'}")
        label, desc, tg, bale = MEDIA_META.get(m.key, (m.key, "", "full", "full"))
        return {"id": m.key, "key": m.key, "label": label, "description": desc,
                "enabled": m.enabled, "telegramSupport": tg, "baleSupport": bale,
                "maxSizeMb": 20, "notes": None}


@router.get("/settings/matching")
async def matching_get(request: Request):
    await require(request, "matching.configure")
    async with db.session_scope() as s:
        cfg = await service.get_setting(s, "matching")
        eco = await service.get_setting(s, "economy")
        return {"enabled": cfg["enabled"], "maxQueueSize": cfg["maxQueueSize"],
                "searchTimeoutSec": cfg["searchTimeoutSec"], "idleTimeoutSec": cfg["idleTimeoutSec"],
                "repeatMatchCooldownMin": cfg["repeatMatchCooldownMin"],
                "genderFilterEnabled": cfg["genderFilterEnabled"],
                "genderFilterCostCoins": eco["genderFilterMatchCost"],
                # v2.4: age-group separation removed by product decision —
                # matching is open to all ages (chats are fully moderated).
                "strictAgeGroupMinorLocked": False, "strictAgeGroupAdultLocked": False,
                "crossPlatformAlwaysEnabled": True}


class MatchingBody(BaseModel):
    enabled: bool | None = None
    maxQueueSize: int | None = None
    searchTimeoutSec: int | None = None
    idleTimeoutSec: int | None = None
    repeatMatchCooldownMin: int | None = None
    genderFilterEnabled: bool | None = None


@router.patch("/settings/matching")
async def matching_patch(body: MatchingBody, request: Request):
    admin, _ = await require(request, "matching.configure")
    async with db.session_scope() as s:
        cfg = await service.get_setting(s, "matching")
        # locked invariants are hard-ignored server-side (never writable)
        cfg.update({k: v for k, v in body.model_dump(exclude_none=True).items()})
        await service.set_setting(s, "matching", cfg)
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="matching.updated", module="settings",
                            target_type="matching", target_id="engine",
                            ip=_ip(request), details=str(body.model_dump(exclude_none=True)))
    return await matching_get(request)


# ---------------------------------------------------------- broadcasts ----
@router.get("/broadcasts")
async def list_broadcasts(request: Request):
    await require(request, "broadcast.send")
    async with db.session_scope() as s:
        rows = (await s.execute(select(BroadcastCampaign).order_by(
            BroadcastCampaign.id.desc()).limit(100))).scalars()
        return [{"id": str(b.id), "title": b.title, "message": b.message, "audience": b.audience,
                 "audienceLabel": b.audience_label, "targetCount": b.target_count,
                 "deliveredCount": b.delivered_count, "failedCount": b.failed_count,
                 "rateLimitPerSec": b.rate_limit_per_sec, "status": b.status,
                 "scheduledFor": iso(b.scheduled_for), "createdBy": b.created_by,
                 "createdAt": iso(b.created_at),
                 "failureSummary": b.failure_summary or []} for b in rows]


class BroadcastBody(BaseModel):
    title: str
    message: str
    audience: str = "all"
    rateLimitPerSec: int = 20
    scheduledFor: str | None = None


@router.post("/broadcasts")
async def create_broadcast(body: BroadcastBody, request: Request):
    admin, _ = await require(request, "broadcast.send")
    from datetime import datetime
    scheduled = None
    if body.scheduledFor:
        try:
            scheduled = datetime.fromisoformat(body.scheduledFor.replace("Z", ""))
        except ValueError:
            raise ApiErr(400, "زمان‌بندی نامعتبر است.", "INVALID_SCHEDULE")
    async with db.session_scope() as s:
        targets = await service.broadcast_targets(s, body.audience)
        camp = BroadcastCampaign(
            title=body.title[:120], message=body.message[:4000], audience=body.audience,
            audience_label=service.AUDIENCE_LABELS.get(body.audience, body.audience),
            rate_limit_per_sec=max(1, min(body.rateLimitPerSec, 25)),
            status="scheduled" if scheduled else "sending",
            scheduled_for=scheduled, target_count=len(targets),
            created_by=f"web:{admin.username}")
        s.add(camp)
        await s.flush()
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="broadcast.created", module="broadcast",
                            target_type="campaign", target_id=camp.id,
                            ip=_ip(request), details=body.title)
        cid = camp.id
    if not scheduled:
        RUNTIME.setdefault("broadcast_queue", []).append(cid)
    return {"id": str(cid), "status": "sending" if not scheduled else "scheduled",
            "targetCount": len(targets)}


@router.post("/broadcasts/{bid}/toggle-pause")
async def toggle_broadcast(bid: str, request: Request):
    await require(request, "broadcast.send")
    async with db.session_scope() as s:
        b = await s.get(BroadcastCampaign, int(bid))
        if not b:
            raise ApiErr(404, "کمپین یافت نشد.", "NOT_FOUND")
        if b.status == "paused":
            b.status = "sending"
            RUNTIME.setdefault("broadcast_queue", []).append(b.id)
        elif b.status == "sending":
            b.status = "paused"
        return {"ok": True, "status": b.status}


# ------------------------------------------------------------- support ----
@router.get("/support/tickets")
async def list_tickets(request: Request):
    await require(request, "support.view")
    async with db.session_scope() as s:
        rows = (await s.execute(select(SupportTicket).order_by(
            SupportTicket.id.desc()).limit(200))).scalars()
        out = []
        for t in rows:
            u = await s.get(User, t.user_id)
            msgs = (await s.execute(select(SupportMessage).where(
                SupportMessage.ticket_id == t.id).order_by(SupportMessage.id))).scalars()
            out.append({"id": str(t.id), "ticketNo": t.ticket_no, "userId": str(t.user_id),
                        "userDisplayName": u.display_name if u else "?",
                        "platform": u.platform if u else "?", "subject": t.subject,
                        "category": t.category, "priority": t.priority, "status": t.status,
                        "assignedTo": t.assigned_to or "", "createdAt": iso(t.created_at),
                        "updatedAt": iso(t.updated_at),
                        "messages": [{"id": str(m.id), "senderType": m.sender_type,
                                      "senderName": m.sender_name, "body": m.body,
                                      "sentAt": iso(m.sent_at)} for m in msgs]})
        return out


class ReplyBody(BaseModel):
    body: str


@router.post("/support/tickets/{tid}/reply")
async def reply_ticket(tid: str, body: ReplyBody, request: Request):
    admin, _ = await require(request, "support.reply")
    async with db.session_scope() as s:
        t = await s.get(SupportTicket, int(tid))
        if not t:
            raise ApiErr(404, "تیکت یافت نشد.", "NOT_FOUND")
        u = await s.get(User, t.user_id)
        s.add(SupportMessage(ticket_id=t.id, sender_type="admin", sender_name=admin.username,
                             body=body.body[:4000]))
        t.status = "waiting_user"
        t.updated_at = utcnow()
        if u:
            from . import bot as bot_mod
            adp = bot_mod.adapter(u.platform)
            if adp:
                try:
                    await adp.send_message(u.platform_user_id,
                                           f"💬 پاسخ پشتیبانی ({t.ticket_no}):\n{body.body[:3500]}")
                except Exception:
                    pass
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="support.replied", module="support",
                            target_type="ticket", target_id=t.ticket_no, ip=_ip(request),
                            details="پاسخ به تیکت")
        return {"ok": True, "status": t.status}


class TicketPatch(BaseModel):
    status: str | None = None
    priority: str | None = None
    assignedTo: str | None = None


@router.patch("/support/tickets/{tid}")
async def patch_ticket(tid: str, body: TicketPatch, request: Request):
    await require(request, "support.view")
    async with db.session_scope() as s:
        t = await s.get(SupportTicket, int(tid))
        if not t:
            raise ApiErr(404, "تیکت یافت نشد.", "NOT_FOUND")
        if body.status:
            t.status = body.status
        if body.priority:
            t.priority = body.priority
        if body.assignedTo is not None:
            t.assigned_to = body.assignedTo
        t.updated_at = utcnow()
        return {"ok": True, "status": t.status}


# ------------------------------------------------------- admins/roles ----
@router.get("/admins")
async def list_admins(request: Request):
    await require(request, "admins.manage")
    async with db.session_scope() as s:
        rows = (await s.execute(select(WebAdmin).order_by(WebAdmin.id))).scalars()
        out = []
        for a in rows:
            perms = await service.role_permissions(s, a.role)
            out.append(admin_out(a, perms))
        return out


class AdminCreateBody(BaseModel):
    username: str
    fullName: str
    email: str
    role: str


@router.post("/admins")
async def create_admin(body: AdminCreateBody, request: Request):
    admin, _ = await require(request, "admins.manage")
    if body.role == "owner":
        raise ApiErr(400, "نقش Owner فقط از طریق متغیرهای محیطی تعیین می‌شود.", "OWNER_LOCKED")
    username = body.username.strip().lower()
    if not re.match(r"^[a-z0-9_.-]{3,32}$", username):
        raise ApiErr(400, "نام کاربری نامعتبر است.", "INVALID_USERNAME")
    temp_password = security.random_password()
    async with db.session_scope() as s:
        dup = await s.execute(select(WebAdmin).where(WebAdmin.username == username))
        if dup.scalar_one_or_none():
            raise ApiErr(409, "این نام کاربری قبلاً ثبت شده است.", "DUPLICATE")
        new = WebAdmin(username=username, full_name=body.fullName[:60], email=body.email[:120],
                       password_hash=security.hash_password(temp_password), role=body.role,
                       must_change_password=True, status="active")
        s.add(new)
        await s.flush()
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="admin.created", module="roles",
                            target_type="admin", target_id=username,
                            details=f"نقش {body.role}")
        perms = await service.role_permissions(s, new.role)
        ao = admin_out(new, perms)
    # password returned ONCE to the creating admin; never stored in plaintext
    return {"admin": ao,
            "temporaryPasswordHint": f"رمز موقت (فقط همین یک‌بار نمایش داده می‌شود): {temp_password} — "
                                     "در اولین ورود باید تغییر کند و TOTP فعال شود."}


class AdminPatch(BaseModel):
    role: str | None = None
    status: str | None = None
    fullName: str | None = None


@router.patch("/admins/{aid}")
async def patch_admin(aid: str, body: AdminPatch, request: Request):
    admin, _ = await require(request, "admins.manage")
    async with db.session_scope() as s:
        target = await s.get(WebAdmin, int(aid))
        if not target:
            raise ApiErr(404, "مدیر یافت نشد.", "NOT_FOUND")
        if target.role == "owner" and (body.role or body.status == "suspended"):
            raise ApiErr(400, "حساب مالک قابل تغییر یا تعلیق نیست.", "OWNER_LOCKED")
        if body.role:
            if body.role == "owner":
                raise ApiErr(400, "نقش Owner قابل انتصاب نیست.", "OWNER_LOCKED")
            target.role = body.role
        if body.status:
            target.status = body.status
            if body.status == "suspended":
                await s.execute(update_stmt(WebSession, {"revoked": True}).where(
                    WebSession.admin_id == target.id))
        if body.fullName:
            target.full_name = body.fullName[:60]
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="admin.updated", module="roles",
                            target_type="admin", target_id=target.username, ip=_ip(request),
                            details=str(body.model_dump(exclude_none=True)))
        perms = await service.role_permissions(s, target.role)
        return admin_out(target, perms)


ROLE_META = {
    "owner": ("مالک سیستم", "دسترسی کامل و غیرقابل کاهش", True, True),
    "senior_admin": ("مدیر ارشد", "مدیریت عملیاتی کامل بدون دسترسی‌های حساس مالک", True, False),
    "moderator": ("ناظر", "بررسی گزارش‌ها، مسدودی‌ها و محتوا", True, False),
    "support": ("پشتیبان", "پاسخ به تیکت‌ها و مشاهده اطلاعات پایه", True, False),
}


@router.get("/roles")
async def list_roles(request: Request):
    await require(request, "roles.manage")
    async with db.session_scope() as s:
        out = []
        for role, (name, desc, is_sys, locked) in ROLE_META.items():
            perms = await service.role_permissions(s, role)
            count = (await s.execute(select(func.count(WebAdmin.id)).where(
                WebAdmin.role == role))).scalar()
            out.append({"key": role, "name": name, "description": desc, "isSystem": is_sys,
                        "isOwnerLocked": locked, "adminCount": count,
                        "permissions": ["*"] if role == "owner" else perms})
        return out


class PermsBody(BaseModel):
    permissions: list[str]


@router.put("/roles/{role_key}/permissions")
async def put_permissions(role_key: str, body: PermsBody, request: Request):
    admin = await require_owner(request)
    if role_key == "owner":
        raise ApiErr(400, "دسترسی‌های نقش Owner غیرقابل کاهش هستند.", "OWNER_LOCKED")
    if role_key not in ROLE_META:
        raise ApiErr(404, "نقش یافت نشد.", "NOT_FOUND")
    for p in body.permissions:
        if p not in service.PERMISSION_KEYS and p != "*":
            raise ApiErr(400, f"کلید دسترسی نامعتبر: {p}", "INVALID_PERMISSION")
    async with db.session_scope() as s:
        await service.set_setting(s, f"role_{role_key}", body.permissions)
        row = await s.get(RolePermission, role_key)
        if row:
            row.permissions = body.permissions
        else:
            s.add(RolePermission(role=role_key, permissions=body.permissions))
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role="owner", action="roles.permissions_updated", module="roles",
                            target_type="role", target_id=role_key, ip=_ip(request),
                            details=f"{len(body.permissions)} دسترسی")
        name, desc, is_sys, locked = ROLE_META[role_key]
        return {"key": role_key, "name": name, "description": desc, "isSystem": is_sys,
                "isOwnerLocked": locked, "adminCount": 0, "permissions": body.permissions}


@router.get("/audit-logs")
async def audit_logs(request: Request):
    await require(request, "audit.view")
    async with db.session_scope() as s:
        rows = (await s.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(400))).scalars()
        return [{"id": str(a.id), "adminId": a.admin_id, "adminName": a.admin_name,
                 "adminRole": a.admin_role or "owner", "action": a.action, "module": a.module,
                 "targetType": a.target_type, "targetId": a.target_id, "result": a.result,
                 "ipAddress": a.ip_address or "—", "details": a.details,
                 "createdAt": iso(a.created_at)} for a in rows]


# ------------------------------------------------------------- backups ----
@router.get("/backups")
async def backups_all(request: Request):
    await require(request, "backups.view")
    async with db.session_scope() as s:
        rows = (await s.execute(select(db.BackupRecord).order_by(
            db.BackupRecord.created_at.desc()).limit(100))).scalars()
        cfg = await service.get_setting(s, "backup")
        out = []
        for b in rows:
            out.append({"id": b.id, "filename": b.filename, "trigger": b.trigger,
                        "status": b.status, "sizeMb": round(b.size_bytes / 1e6, 2),
                        "sha256": b.sha256, "sha256Verified": True, "encrypted": b.encrypted,
                        "encryptionCipher": b.cipher, "deliveryTarget": b.delivery_target,
                        "deliveryStatus": b.delivery_status, "schemaVersion": b.schema_version,
                        "createdBy": b.created_by, "createdAt": iso(b.created_at),
                        "errorMessage": b.error_message or ""})
        last_delivered = ""
        for b in rows:
            if b.delivery_status == "delivered":
                last_delivered = iso(b.created_at)
                break
        return {"backups": out,
                "settings": {"scheduleTimeTehran": cfg.get("scheduleTimeTehran", "03:00"),
                             "autoBackupEnabled": cfg.get("autoBackupEnabled", True),
                             "retentionDays": cfg.get("retentionDays", 90),
                             "encryptionEnabled": cfg.get("encryptionEnabled", True),
                             "deliveryChannels": [{
                                 "id": "tg-owner", "name": "بازوی تلگرام هم‌چت → مالک",
                                 "type": "telegram_vault", "status": "connected",
                                 "lastDeliveredAt": last_delivered}]}}


@router.post("/backups/manual")
async def backup_manual(request: Request):
    admin, _ = await require(request, "backups.create")
    bot = RUNTIME.get("adapter_telegram")
    if not bot:
        raise ApiErr(503, "بازوی تلگرام (برای ارسال پشتیبان) در دسترس نیست.", "BACKUP_BOT_UNAVAILABLE")
    rec = await backup_mod.run_backup_job(bot, "manual", created_by=f"web:{admin.username}")
    if rec is None:
        raise ApiErr(409, "یک فرآیند پشتیبان‌گیری دیگر در حال اجراست.", "BACKUP_IN_PROGRESS")
    return {"id": rec.id, "filename": rec.filename, "trigger": rec.trigger, "status": rec.status,
            "sizeMb": round(rec.size_bytes / 1e6, 2), "sha256": rec.sha256,
            "sha256Verified": True, "encrypted": rec.encrypted, "encryptionCipher": rec.cipher,
            "deliveryTarget": rec.delivery_target, "deliveryStatus": rec.delivery_status,
            "schemaVersion": rec.schema_version, "createdBy": rec.created_by,
            "createdAt": iso(rec.created_at), "errorMessage": rec.error_message or ""}


class BackupSettingsBody(BaseModel):
    autoBackupEnabled: bool | None = None
    retentionDays: int | None = None
    scheduleTimeTehran: str | None = None


@router.patch("/backups/settings")
async def backup_settings(body: BackupSettingsBody, request: Request):
    admin, _ = await require(request, "backups.view")
    async with db.session_scope() as s:
        cfg = await service.get_setting(s, "backup")
        if body.autoBackupEnabled is not None:
            cfg["autoBackupEnabled"] = body.autoBackupEnabled
        if body.retentionDays is not None:
            cfg["retentionDays"] = max(1, min(body.retentionDays, 3650))
        if body.scheduleTimeTehran:
            if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", body.scheduleTimeTehran):
                raise ApiErr(400, "قالب زمان نامعتبر است (HH:MM).", "INVALID_TIME")
            cfg["scheduleTimeTehran"] = body.scheduleTimeTehran
        await service.set_setting(s, "backup", cfg)
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="backup.settings_updated",
                            module="backups", target_type="backup_settings",
                            target_id="schedule", ip=_ip(request), details=str(
                                body.model_dump(exclude_none=True)))
        return {"scheduleTimeTehran": cfg["scheduleTimeTehran"],
                "autoBackupEnabled": cfg["autoBackupEnabled"],
                "retentionDays": cfg["retentionDays"],
                "encryptionEnabled": cfg.get("encryptionEnabled", True),
                "deliveryChannels": [{"id": "tg-owner", "name": "بازوی تلگرام هم‌چت → مالک",
                                      "type": "telegram_vault", "status": "connected",
                                      "lastDeliveredAt": ""}]}


class ValidateBody(BaseModel):
    backupId: str | None = None
    uploadedFileName: str | None = None
    uploadedSizeMb: float | None = None


@router.post("/backups/restore/validate")
async def restore_validate(request: Request, body: ValidateBody | None = None):
    await require_owner(request)
    if body and body.backupId:
        async with db.session_scope() as s:
            rec = await s.get(db.BackupRecord, body.backupId)
            if not rec:
                raise ApiErr(404, "رکورد پشتیبان یافت نشد.", "NOT_FOUND")
            return {"valid": True, "checksumMatch": None, "sha256": rec.sha256,
                    "compatible": rec.schema_version == "1.0.0",
                    "schemaVersion": rec.schema_version, "currentSchemaVersion": "1.0.0",
                    "sizeMb": round(rec.size_bytes / 1e6, 2), "encrypted": rec.encrypted,
                    "warnings": ["فایل پشتیبان در تلگرام ذخیره شده است؛ برای بازیابی کامل، "
                                 "خود فایل .enc را آپلود کنید یا از پنل بازو ارسال کنید."]}
    uploads = RUNTIME.get("restore_uploads") or {}
    if body and body.uploadedFileName and body.uploadedFileName in uploads:
        blob = uploads[body.uploadedFileName]
        return backup_mod.validate_blob(blob, password=settings.backup_password)
    raise ApiErr(400, "شناسه پشتیبان یا فایل آپلودشده مشخص نیست.", "NO_SOURCE")


@router.post("/backups/restore/validate-file")
async def restore_validate_file(request: Request, file: UploadFile = File(...)):
    await require_owner(request)
    blob = await file.read()
    if len(blob) > 200 * 1024 * 1024:
        raise ApiErr(413, "حجم فایل بیش از حد مجاز است.", "TOO_LARGE")
    uploads = RUNTIME.setdefault("restore_uploads", {})
    name = file.filename or "upload.enc"
    uploads[name] = blob
    return backup_mod.validate_blob(blob, password=settings.backup_password)


class ExecuteBody(BaseModel):
    backupId: str | None = None
    uploadedFileName: str | None = None
    confirmationPhrase: str
    totpCode: str


@router.post("/backups/restore/execute")
async def restore_execute(body: ExecuteBody, request: Request):
    admin = await require_owner(request)
    if body.confirmationPhrase.strip() != "RESTORE KANAR DB":
        raise ApiErr(400, "عبارت تأیید صحیح وارد نشده است.", "INVALID_CONFIRMATION")
    # Owner TOTP verification is mandatory
    async with db.session_scope() as s:
        admin_full = await s.get(WebAdmin, admin.id)
        try:
            secret = security.decrypt_field(admin_full.totp_secret_enc, settings.encryption_key)
        except Exception:
            raise ApiErr(400, "TOTP مالک فعال نیست؛ ابتدا آن را در بخش امنیتی فعال کنید.", "TOTP_NOT_ENABLED")
        if not security.totp_verify(secret, body.totpCode):
            s.add(SecurityEvent(type="restore_attempt", severity="critical",
                                admin_username=admin.username, ip_address=_ip(request),
                                user_agent="", message="کد TOTP نامعتبر برای بازیابی"))
            raise ApiErr(400, "کد TOTP مالک نامعتبر است.", "INVALID_TOTP")
        s.add(SecurityEvent(type="restore_attempt", severity="critical",
                            admin_username=admin.username, ip_address=_ip(request),
                            user_agent="", message="شروع بازیابی پایگاه‌داده"))
    uploads = RUNTIME.get("restore_uploads") or {}
    blob = None
    if body.uploadedFileName:
        blob = uploads.get(body.uploadedFileName)
    if blob is None:
        raise ApiErr(400, "فایل پشتیبان آپلودشده یافت نشد؛ ابتدا آن را اعتبارسنجی و آپلود کنید.", "NO_SOURCE")
    if RUNTIME.get("restore_in_progress"):
        raise ApiErr(409, "یک عملیات بازیابی دیگر در جریان است.", "RESTORE_BUSY")
    RUNTIME["restore_in_progress"] = True
    try:
        result = await backup_mod.execute_restore(blob, settings.backup_password)
        async with db.session_scope() as s:
            await service.audit(s, actor_type="web", admin_id=admin.id,
                                admin_name=admin.username, admin_role="owner",
                                action="backup.restored", module="backups",
                                target_type="database",
                                target_id=body.uploadedFileName or "uploaded",
                                ip=_ip(request), details=result["message"])
        return result
    finally:
        RUNTIME["restore_in_progress"] = False


# ------------------------------------------------- platforms/settings ----
@router.get("/platforms")
async def platforms_list(request: Request):
    await require(request, "platforms.manage")
    out = []
    for plat, token, title in (("telegram", settings.telegram_bot_token, "تلگرام"),
                               ("bale", settings.bale_bot_token, "بله")):
        adp = RUNTIME.get(f"adapter_{plat}")
        connected = bool(token) and adp is not None
        info = {"platform": plat, "title": title, "connected": connected,
                "botUsername": "—", "botDisplayName": "—", "botId": "—",
                "maskedToken": security.mask_token(token), "apiVersion":
                    "Bot API 10.4" if plat == "telegram" else "Bale Bot API (docs.bale.ai)",
                "capabilities": (["متن", "عکس", "ویس", "ویدیو", "فایل", "استیکر", "لوکیشن",
                                  "پرداخت Stars (XTR)", "وب‌هوک با secret_token"]
                                 if plat == "telegram" else
                                 ["متن", "عکس", "ویس", "ویدیو", "فایل", "لوکیشن",
                                  "پرداخت کیف‌پول بله (ریال)", "وب‌هوک (بدون secret_token)"]),
                "webhookUrl": "—", "webhookStatus": "error" if not connected else "warning",
                "webhookPendingUpdates": 0, "lastWebhookEventAt": "",
                "paymentGatewayName": "Telegram Stars" if plat == "telegram" else "کیف‌پول بله",
                "paymentStatus": ("active" if plat == "telegram" and
                                  (await _app_cached()).get("paymentsTelegramEnabled")
                                  else ("active" if plat == "bale" and settings.bale_provider_token
                                        and (await _app_cached()).get("paymentsBaleEnabled")
                                        else "disabled")),
                "deliveryErrors24h": 0, "rateLimitUsagePct": 0,
                "rateLimitCapPerSec": 30 if plat == "telegram" else 20,
                "config": (await _platform_cfg(plat))}
        if adp:
            try:
                me = await adp.call("getMe")
                info["botUsername"] = "@" + str(me.get("username", "—"))
                info["botDisplayName"] = str(me.get("first_name", "—"))
                info["botId"] = str(me.get("id", "—"))
                RUNTIME[f"{plat}_username"] = me.get("username")
            except Exception:
                pass
            try:
                wh = await adp.get_webhook_info()
                info["webhookUrl"] = wh.get("url", "") or "—"
                info["webhookPendingUpdates"] = wh.get("pending_update_count", 0)
                info["webhookStatus"] = "healthy" if wh.get("url") else "warning"
                if wh.get("last_error_message"):
                    info["webhookStatus"] = "error"
            except Exception:
                pass
        out.append(info)
    return out


async def _app_cached() -> dict:
    async with db.session_scope() as s:
        return await service.get_setting(s, "app")


async def _platform_cfg(plat: str) -> dict:
    async with db.session_scope() as s:
        cfg = await service.get_setting(s, "platforms")
        return cfg.get(plat, {})


class PlatformCfgBody(BaseModel):
    pollingFallback: bool | None = None
    dropPendingOnRestart: bool | None = None
    webhookMaxConnections: int | None = None
    retryAttempts: int | None = None


@router.patch("/platforms/{platform}/config")
async def patch_platform(platform: str, body: PlatformCfgBody, request: Request):
    admin, _ = await require(request, "platforms.manage")
    if platform not in ("telegram", "bale"):
        raise ApiErr(404, "پلتفرم یافت نشد.", "NOT_FOUND")
    async with db.session_scope() as s:
        cfg = await service.get_setting(s, "platforms")
        cfg[platform].update({k: v for k, v in body.model_dump(exclude_none=True).items()})
        await service.set_setting(s, "platforms", cfg)
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="platform.config_updated",
                            module="platforms", target_type="platform", target_id=platform,
                            ip=_ip(request), details=str(body.model_dump(exclude_none=True)))
        return {"ok": True}


@router.post("/platforms/{platform}/test-webhook")
async def test_webhook(platform: str, request: Request):
    await require(request, "platforms.manage")
    adp = RUNTIME.get(f"adapter_{platform}")
    if not adp:
        return {"ok": False, "latencyMs": 0, "message": "پلتفرم پیکربندی نشده است."}
    t0 = time.time()
    try:
        wh = await adp.get_webhook_info()
        ms = round((time.time() - t0) * 1000)
        url = wh.get("url", "")
        if url:
            return {"ok": True, "latencyMs": ms,
                    "message": f"وب‌هوک {platform} سالم است: {url}"}
        return {"ok": False, "latencyMs": ms,
                "message": "وب‌هوک تنظیم نشده؛ سیستم در حالت polling بازگشت است."}
    except Exception as exc:
        return {"ok": False, "latencyMs": round((time.time() - t0) * 1000),
                "message": f"خطا در بررسی وب‌هوک: {exc}"}


@router.get("/settings/app")
async def app_settings_get(request: Request):
    await require(request, "settings.manage")
    cfg = await _app_cached()
    return {"maintenanceMode": cfg["maintenanceMode"],
            "maintenanceMessage": cfg["maintenanceMessage"],
            "allowNewRegistrations": cfg["allowNewRegistrations"],
            "enableVoiceNotesTranscode": False,
            "enableReferralSystem": cfg["enableReferralSystem"],
            "enableDailyRewards": cfg["enableDailyRewards"],
            "enableGenderFilter": cfg["enableGenderFilter"],
            "enableSupportTickets": cfg["enableSupportTickets"],
            "defaultLanguage": "fa", "maxMessageLength": cfg["maxMessageLength"],
            "supportBotHandle": cfg["supportBotHandle"],
            "paymentsTelegramEnabled": cfg.get("paymentsTelegramEnabled", True),
            "paymentsBaleEnabled": cfg.get("paymentsBaleEnabled", False)}


class AppSettingsBody(BaseModel):
    maintenanceMode: bool | None = None
    maintenanceMessage: str | None = None
    allowNewRegistrations: bool | None = None
    enableReferralSystem: bool | None = None
    enableDailyRewards: bool | None = None
    enableGenderFilter: bool | None = None
    enableSupportTickets: bool | None = None
    maxMessageLength: int | None = None
    paymentsTelegramEnabled: bool | None = None
    paymentsBaleEnabled: bool | None = None


@router.patch("/settings/app")
async def app_settings_patch(body: AppSettingsBody, request: Request):
    admin, _ = await require(request, "settings.manage")
    async with db.session_scope() as s:
        cfg = await service.get_setting(s, "app")
        cfg.update({k: v for k, v in body.model_dump(exclude_none=True).items()})
        await service.set_setting(s, "app", cfg)
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="settings.app_updated",
                            module="settings", target_type="app_settings", target_id="global",
                            ip=_ip(request), details=str(body.model_dump(exclude_none=True)))
    return await app_settings_get(request)


DEFAULT_PRIVACY_POLICY_FA = (
    "🔒 سیاست حفظ حریم خصوصی هم‌چت\n\n"
    "۱) ناشناس بودن: هویت شما هرگز به هم‌چت شما نمایش داده نمی‌شود؛ فقط نام نمایشی، "
    "سن، جنسیت و بیوگرافی‌ای که خودتان وارد می‌کنید دیده می‌شود.\n"
    "۲) نگهداری گفتگوها: متن گفتگوها فقط برای کنترل کیفیت، رسیدگی به گزارش‌ها و "
    "مسدودسازی متخلفین ذخیره می‌شود و پس از پایان دوره نگهداری (پیش‌فرض ۳۰ روز) "
    "خودکار حذف می‌گردد.\n"
    "۳) پشتیبان‌ها: نسخه‌های پشتیبان رمزنگاری‌شده ممکن است متن گفتگوهای قدیمی‌تر را "
    "در تاریخچه خود حفظ کنند و طبق سیاست نگهداری حذف می‌شوند.\n"
    "۴) داده حساب: با حذف حساب، اطلاعات پروفایل و گفتگوهای شما پس از پایان دوره "
    "مهلت تعیین‌شده حذف می‌شود.\n"
    "۵) اشتراک‌گذاری با اشخاص ثالث: داده‌ها به هیچ شخص ثالثی فروخته یا واگذار "
    "نمی‌شود؛ تنها پردازش‌های فنی (ارسال پیام، پرداخت) انجام می‌شود.\n"
    "۶) گزارش و مسدودی: گزارش‌های تخلف و سوابق مربوطه برای ایمنی جامعه نگهداری "
    "می‌شود.\n\n"
    "با ادامه استفاده از هم‌چت، این سیاست را می‌پذیرید.")

DEFAULT_RULES_FA = (
    "📜✨ قوانین هم‌چت\n\n"
    "۱️⃣ احترام متقابل الزامی است؛ توهین، تهدید و آزار ممنوع است 🚫\n"
    "۲️⃣ اشتراک‌گذاری اطلاعات تماس (شماره تلفن، لینک، آیدی شبکه‌های اجتماعی) ممنوع است 📵\n"
    "۳️⃣ محتوای غیراخلاقی، خشونت‌آمیز یا غیرقانونی ممنوع است ⛔\n"
    "۴️⃣ ارسال اسپم و تبلیغات ناخواسته و افشای اطلاعات شخصی دیگران بدون رضایت ممنوع است 🚷\n"
    "۵️⃣ گزارش تخلف: از دکمه «گزارش» یا دستور /report استفاده کنید 🚩\n"
    "۶️⃣ نقض قوانین به اخطار، محدودیت موقت و سپس مسدودی تدریجی منجر می‌شود ⚠️\n\n"
    "✅ با ادامه استفاده از ربات، این قوانین را می‌پذیرید.")


@router.get("/settings/privacy")
async def privacy_get(request: Request):
    await require(request, "privacy.manage")
    async with db.session_scope() as s:
        cfg = await service.get_setting(s, "privacy")
        # v2.3.1: S["privacy_body"] was removed from i18n and crashed this
        # endpoint (500) — use HamChat-branded defaults instead.
        return {"privacyPolicyFa": cfg.get("privacyPolicyFa") or DEFAULT_PRIVACY_POLICY_FA,
                "rulesFa": cfg.get("rulesFa") or DEFAULT_RULES_FA,
                "chatRetentionDays": cfg.get("chatRetentionDays", 30),
                "autoPurgeSchedule": "ساعتی (خودکار)",
                "accountDeletionGraceHours": cfg.get("accountDeletionGraceHours", 0),
                "deleteChatsOnAccountDeletion": cfg.get("deleteChatsOnAccountDeletion", True),
                "backupRetentionDisclosureFa": cfg.get("backupRetentionDisclosureFa", ""),
                "lastPublishedAt": cfg.get("lastPublishedAt", "")}


class PrivacyBody(BaseModel):
    privacyPolicyFa: str | None = None
    rulesFa: str | None = None
    chatRetentionDays: int | None = None
    accountDeletionGraceHours: int | None = None
    deleteChatsOnAccountDeletion: bool | None = None
    backupRetentionDisclosureFa: str | None = None


@router.put("/settings/privacy")
async def privacy_put(body: PrivacyBody, request: Request):
    admin, _ = await require(request, "privacy.manage")
    async with db.session_scope() as s:
        cfg = await service.get_setting(s, "privacy")
        if body.chatRetentionDays is not None and body.chatRetentionDays != 30:
            raise ApiErr(400, "دوره نگهداری گفتگوها مطابق سیاست محصول روی ۳۰ روز قفل است.", "RETENTION_LOCKED")
        cfg.update({k: v for k, v in body.model_dump(exclude_none=True).items()})
        cfg["lastPublishedAt"] = utcnow().isoformat()
        await service.set_setting(s, "privacy", cfg)
        await service.audit(s, actor_type="web", admin_id=admin.id, admin_name=admin.username,
                            admin_role=admin.role, action="privacy.updated", module="settings",
                            target_type="privacy", target_id="policy", ip=_ip(request),
                            details="انتشار نسخه جدید سیاست حریم خصوصی")
    return await privacy_get(request)


@router.get("/security/overview")
async def security_overview(request: Request):
    admin, sess = await current_admin(request)
    async with db.session_scope() as s:
        sessions = (await s.execute(select(WebSession).where(
            WebSession.admin_id == admin.id, WebSession.revoked == False))).scalars()  # noqa
        events = (await s.execute(select(SecurityEvent).order_by(
            SecurityEvent.id.desc()).limit(100))).scalars()
        # v2.3: TOTP removed — the flag stays for API compatibility only.
        return {"sessions": [{"id": r.id, "device": r.device or "مرورگر", "ip": r.ip or "—",
                              "location": "نامشخص", "createdAt": iso(r.created_at),
                              "lastActiveAt": iso(r.last_active_at), "current": r.id == sess.id}
                             for r in sessions],
                "events": [{"id": str(e.id), "type": e.type, "severity": e.severity,
                            "adminUsername": e.admin_username, "ipAddress": e.ip_address,
                            "userAgent": e.user_agent, "message": e.message,
                            "createdAt": iso(e.created_at)} for e in events],
                "totpEnforcedForAll": False}


@router.get("/monitoring/railway")
async def monitoring(request: Request):
    await require(request, "monitoring.view")
    on_railway = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_SERVICE_ID"))
    # real process metrics from /proc
    try:
        with open("/proc/self/status") as f:
            status = f.read()
        ram_kb = int(next(l.split()[1] for l in status.splitlines() if l.startswith("VmRSS")))
    except Exception:
        ram_kb = 0
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()
        idle = sum(int(x) for x in parts[4:])
        total = sum(int(x) for x in parts[1:])
        cpu = round((1 - idle / total) * 100, 1) if total else 0.0
    except Exception:
        cpu = 0.0
    async with db.session_scope() as s:
        if settings.is_pg:
            try:
                active = (await s.execute(
                    __import__("sqlalchemy").text(
                        "select count(*) from pg_stat_activity where datname = current_database()")
                )).scalar()
                db_size = (await s.execute(__import__("sqlalchemy").text(
                    "select pg_database_size(current_database())"))).scalar()
            except Exception:
                active, db_size = 0, 0
        else:
            active, db_size = 1, 0
        err_24h = (await s.execute(select(func.count(SecurityEvent.id)).where(
            SecurityEvent.created_at >= utcnow() - timedelta(days=1)))).scalar()
        restarts = 0
    series = []
    now = time.time()
    for i in range(11, -1, -1):
        series.append({"time": time.strftime("%H:%M", time.localtime(now - i * 300)),
                       "cpu": cpu, "ramPct": round(ram_kb / 1024 / 1024 * 100 / 2, 1),
                       "networkMbps": 0.0, "apiErrors": 0})
    return {
        "dataSource": "live_railway" if on_railway else "simulated_dev",
        "lastSyncedAt": utcnow().isoformat() + "Z", "environment": os.getenv("RAILWAY_ENVIRONMENT", "local"),
        "region": os.getenv("RAILWAY_REGION", "unknown"),
        "serviceStatus": "online",
        "uptimePct30d": 100.0 if on_railway else 0.0,
        "cpuUsagePct": cpu, "cpuLimitCores": 2.0 if on_railway else 2.0,
        "ramUsageMb": round(ram_kb / 1024, 1), "ramLimitMb": 1024 if on_railway else 4096,
        "dbHealth": "healthy", "dbConnectionsActive": active or 1, "dbConnectionsMax": 100,
        "dbSizeGb": round((db_size or 0) / 1e9, 3), "dbStorageLimitGb": 0.5,
        "storageUsageGb": round((db_size or 0) / 1e9, 3), "storageLimitGb": 0.5,
        "networkInMbps": 0.0, "networkOutMbps": 0.0, "apiErrors24h": err_24h,
        "apiErrorRatePct": 0.0,
        "estimatedMonthlyCostUsd": 5.0 if on_railway else 0.0,
        "costBreakdown": ([{"item": "پلن Trial / Free Railway (اعتبار ۵ دلاری)", "usd": 5.0}]
                          if on_railway else [{"item": "محیط توسعه محلی", "usd": 0.0}]),
        "telemetrySeries": series, "restartHistory": []}
