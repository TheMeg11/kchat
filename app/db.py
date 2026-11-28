"""Database engine, session helper and all ORM models (Postgres / SQLite)."""
from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (JSON, BigInteger, Boolean, DateTime, ForeignKey,
                        Index, Integer, String, Text, UniqueConstraint, CheckConstraint)
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_public_id() -> str:
    return "U" + secrets.token_hex(4).upper()


def new_referral_code() -> str:
    return secrets.token_hex(5)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------- users ----
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(16))            # telegram|bale
    platform_user_id: Mapped[str] = mapped_column(String(32))
    public_id: Mapped[str] = mapped_column(String(16), default=new_public_id, unique=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    display_name: Mapped[str] = mapped_column(String(40))
    age: Mapped[int] = mapped_column(Integer)
    gender: Mapped[str] = mapped_column(String(8))               # male|female
    bio: Mapped[Optional[str]] = mapped_column(String(120), default=None)
    photo_file_id: Mapped[Optional[str]] = mapped_column(String(256), default=None)
    language: Mapped[str] = mapped_column(String(4), default="fa")
    city: Mapped[str] = mapped_column(String(40), default="")
    coin_balance: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|banned|deleted
    state: Mapped[Optional[dict]] = mapped_column(JSON, default=None)  # bot FSM
    accepted_rules_at: Mapped[Optional[datetime]] = mapped_column(DateTime(), default=None)
    registered_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    referral_code: Mapped[str] = mapped_column(String(16), default=new_referral_code, unique=True)
    referred_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), default=None)
    referral_count: Mapped[int] = mapped_column(Integer, default=0)
    total_chats: Mapped[int] = mapped_column(Integer, default=0)
    total_messages: Mapped[int] = mapped_column(Integer, default=0)
    reports_received: Mapped[int] = mapped_column(Integer, default=0)
    reports_sent: Mapped[int] = mapped_column(Integer, default=0)
    ban_count: Mapped[int] = mapped_column(Integer, default=0)
    profanity_count: Mapped[int] = mapped_column(Integer, default=0)
    contact_violation_count: Mapped[int] = mapped_column(Integer, default=0)
    first_chat_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(), default=None)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(), default=None)
    registration_rewarded: Mapped[bool] = mapped_column(Boolean, default=False)
    # --- birthday / dynamic age (v2) ---
    birth_year: Mapped[Optional[int]] = mapped_column(Integer, default=None)      # gregorian year
    birth_month: Mapped[Optional[int]] = mapped_column(Integer, default=None)     # 1-12
    birth_day: Mapped[Optional[int]] = mapped_column(Integer, default=None)       # 1-31
    birth_calendar: Mapped[Optional[str]] = mapped_column(String(16), default=None)  # jalali|gregorian
    birthday_rewarded: Mapped[bool] = mapped_column(Boolean, default=False)
    # --- anonymous link & account linking (v2) ---
    anon_link_code: Mapped[Optional[str]] = mapped_column(String(16), default=None, unique=True)
    link_code: Mapped[Optional[str]] = mapped_column(String(16), default=None)
    link_code_expires: Mapped[Optional[datetime]] = mapped_column(DateTime(), default=None)
    linked_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), default=None)
    link_rewarded: Mapped[bool] = mapped_column(Boolean, default=False)
    # --- online invitations (v2) ---
    last_invite_at: Mapped[Optional[datetime]] = mapped_column(DateTime(), default=None)
    # --- v2.4 growth: bot-blocked isolation list + signup-rescue cadence ---
    bot_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_stage: Mapped[int] = mapped_column(Integer, default=0)
    last_reminder_at: Mapped[Optional[datetime]] = mapped_column(DateTime(), default=None)
    __table_args__ = (UniqueConstraint("platform", "platform_user_id", name="uq_user_platform"),
                      CheckConstraint("coin_balance >= 0", name="ck_user_nonnegative_balance"),
                      Index("ix_users_status_seen", "status", "last_seen_at"))


class QueueEntry(Base):
    __tablename__ = "queue_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    age_group: Mapped[str] = mapped_column(String(8))                # 13-17 | 18-60
    gender: Mapped[str] = mapped_column(String(8))
    gender_filter: Mapped[str] = mapped_column(String(12), default="everyone")
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    # v2.4 (first-match UX): set when the 60-second "still waiting" nudge fired
    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(), default=None)


class Chat(Base):
    __tablename__ = "chats"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_a_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    user_b_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    cross_platform: Mapped[bool] = mapped_column(Boolean, default=False)
    age_group: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|ended|terminated
    end_reason: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    started_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(), default=None)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    has_report: Mapped[bool] = mapped_column(Boolean, default=False)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    source_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True, default=None)
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id"), index=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    msg_type: Mapped[str] = mapped_column(String(16))
    text: Mapped[Optional[str]] = mapped_column(Text(), default=None)
    media_file_id: Mapped[Optional[str]] = mapped_column(String(256), default=None)
    media_file_unique_id: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    media_size: Mapped[Optional[int]] = mapped_column(BigInteger(), default=None)
    mime: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    flag_reason: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow, index=True)


class Block(Base):
    __tablename__ = "blocks"
    id: Mapped[int] = mapped_column(primary_key=True)
    blocker_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    blocked_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    __table_args__ = (UniqueConstraint("blocker_id", "blocked_id", name="uq_block_pair"),)


class RecentPair(Base):
    __tablename__ = "recent_pairs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_a_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    user_b_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    ended_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    __table_args__ = (Index("ix_recent_pair", "user_a_id", "user_b_id", "ended_at"),)


class Friend(Base):
    """v2.4: one-sided friends list — user_id saved friend_id from a chat (or
    an anonymous thread) and can send them an anonymous direct message later."""
    __tablename__ = "friends"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    friend_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    __table_args__ = (UniqueConstraint("user_id", "friend_id", name="uq_friend_pair"),)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    # v2.1: anonymous-message reports have no chat — nullable FK (was chat_id=0, which
    # violates the FK constraint on Postgres).
    chat_id: Mapped[Optional[int]] = mapped_column(ForeignKey("chats.id"), index=True,
                                                   default=None)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reported_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    reason: Mapped[str] = mapped_column(String(24))
    details: Mapped[str] = mapped_column(Text(), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    auto_ban_triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(), default=None)
    __table_args__ = (UniqueConstraint("chat_id", "reporter_id", "reported_id", name="uq_report_dedup"),)


class Ban(Base):
    __tablename__ = "bans"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(12))               # temporary|permanent
    source: Mapped[str] = mapped_column(String(20))             # auto_3_reports|manual_admin|content_filter
    reason: Mapped[str] = mapped_column(Text(), default="")
    status: Mapped[str] = mapped_column(String(12), default="active")  # active|expired|revoked
    issued_by: Mapped[str] = mapped_column(String(64), default="system")
    issued_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(), default=None)
    appeal_eligible: Mapped[bool] = mapped_column(Boolean, default=False)


class Appeal(Base):
    __tablename__ = "appeals"
    id: Mapped[int] = mapped_column(primary_key=True)
    ban_id: Mapped[int] = mapped_column(ForeignKey("bans.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    message: Mapped[str] = mapped_column(Text(), default="")
    status: Mapped[str] = mapped_column(String(12), default="pending")
    submitted_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    review_note: Mapped[Optional[str]] = mapped_column(Text(), default=None)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(), default=None)


# --------------------------------------------------------------- economy ----
class CoinTransaction(Base):
    __tablename__ = "coin_transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)                # signed
    balance_after: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(24))
    description: Mapped[str] = mapped_column(Text(), default="")
    reference_id: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow, index=True)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(16))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    package_id: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    coins: Mapped[int] = mapped_column(Integer, default=0)
    amount: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(12), default="XTR")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    telegram_payment_charge_id: Mapped[Optional[str]] = mapped_column(String(128), unique=True)
    provider_payment_charge_id: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    invoice_payload: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text(), default=None)
    refunded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(), default=None)
    refunded_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow, index=True)


class StorePackage(Base):
    __tablename__ = "store_packages"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    coins: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(64))
    badge: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    telegram_stars_price: Mapped[int] = mapped_column(Integer, default=0)
    bale_toman_price: Mapped[int] = mapped_column(BigInteger(), default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    bale_enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class Referral(Base):
    __tablename__ = "referrals"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16))
    referrer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    referred_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending_verification")
    fraud_score: Mapped[int] = mapped_column(Integer, default=0)
    fraud_reasons: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    __table_args__ = (UniqueConstraint("referrer_id", "referred_id", name="uq_referral_pair"),)


class DailyClaim(Base):
    __tablename__ = "daily_claims"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[str] = mapped_column(String(12))                # Tehran calendar date
    coins: Mapped[int] = mapped_column(Integer)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_daily_claim"),)


# ------------------------------------------------------------- settings ----
class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(48), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class MediaType(Base):
    __tablename__ = "media_types"
    key: Mapped[str] = mapped_column(String(16), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role: Mapped[str] = mapped_column(String(16), primary_key=True)
    permissions: Mapped[list] = mapped_column(JSON, default=list)


class BotAdmin(Base):
    __tablename__ = "bot_admins"
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(16))
    platform_user_id: Mapped[str] = mapped_column(String(32))
    role: Mapped[str] = mapped_column(String(16), default="support")
    added_by: Mapped[str] = mapped_column(String(64), default="owner")
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    __table_args__ = (UniqueConstraint("platform", "platform_user_id", name="uq_bot_admin"),)


# ---------------------------------------------------------- web security ----
class WebAdmin(Base):
    __tablename__ = "web_admins"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True)
    full_name: Mapped[str] = mapped_column(String(64), default="")
    email: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16), default="support")
    status: Mapped[str] = mapped_column(String(12), default="active")
    totp_secret_enc: Mapped[Optional[str]] = mapped_column(Text(), default=None)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(), default=None)
    last_login_ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)


class WebSession(Base):
    __tablename__ = "web_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("web_admins.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(256), default="")
    device: Mapped[str] = mapped_column(String(64), default="")
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class LoginChallenge(Base):
    __tablename__ = "login_challenges"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("web_admins.id"))
    stage: Mapped[str] = mapped_column(String(24))  # totp|enroll|password
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime())


class RecoveryCode(Base):
    __tablename__ = "recovery_codes"
    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("web_admins.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(128))
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_type: Mapped[str] = mapped_column(String(8), default="web")   # web|bot|system
    admin_id: Mapped[str] = mapped_column(String(32), default="")
    admin_name: Mapped[str] = mapped_column(String(64), default="")
    admin_role: Mapped[str] = mapped_column(String(16), default="")
    action: Mapped[str] = mapped_column(String(48))
    module: Mapped[str] = mapped_column(String(16), default="settings")
    target_type: Mapped[str] = mapped_column(String(24), default="")
    target_id: Mapped[str] = mapped_column(String(64), default="")
    result: Mapped[str] = mapped_column(String(8), default="success")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    details: Mapped[str] = mapped_column(Text(), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow, index=True)


class SecurityEvent(Base):
    __tablename__ = "security_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(24))
    severity: Mapped[str] = mapped_column(String(12), default="info")
    admin_username: Mapped[str] = mapped_column(String(64), default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(256), default="")
    message: Mapped[str] = mapped_column(Text(), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow, index=True)


class AdminNotification(Base):
    __tablename__ = "admin_notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    body: Mapped[str] = mapped_column(Text(), default="")
    severity: Mapped[str] = mapped_column(String(12), default="info")
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    route: Mapped[str] = mapped_column(String(64), default="/")
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)


# ------------------------------------------------------- ops / support ----
class BroadcastCampaign(Base):
    __tablename__ = "broadcasts"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    message: Mapped[str] = mapped_column(Text())
    audience: Mapped[str] = mapped_column(String(16), default="all")
    audience_label: Mapped[str] = mapped_column(String(64), default="")
    rate_limit_per_sec: Mapped[int] = mapped_column(Integer, default=20)
    status: Mapped[str] = mapped_column(String(16), default="sending")
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(DateTime(), default=None)
    target_count: Mapped[int] = mapped_column(Integer, default=0)
    last_user_id: Mapped[int] = mapped_column(Integer, default=0)
    delivered_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_summary: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_no: Mapped[str] = mapped_column(String(16), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    subject: Mapped[str] = mapped_column(String(128), default="")
    category: Mapped[str] = mapped_column(String(16), default="other")
    priority: Mapped[str] = mapped_column(String(8), default="medium")
    status: Mapped[str] = mapped_column(String(16), default="open")
    assigned_to: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)


class SupportMessage(Base):
    __tablename__ = "support_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id"), index=True)
    sender_type: Mapped[str] = mapped_column(String(8), default="user")
    sender_name: Mapped[str] = mapped_column(String(64), default="")
    body: Mapped[str] = mapped_column(Text())
    sent_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)


class BackupRecord(Base):
    __tablename__ = "backups"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    filename: Mapped[str] = mapped_column(String(128))
    trigger: Mapped[str] = mapped_column(String(32), default="manual")
    status: Mapped[str] = mapped_column(String(12), default="success")
    size_bytes: Mapped[int] = mapped_column(BigInteger(), default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    encrypted: Mapped[bool] = mapped_column(Boolean, default=True)
    cipher: Mapped[str] = mapped_column(String(32), default="AES-256-GCM + scrypt")
    delivery_status: Mapped[str] = mapped_column(String(12), default="pending")
    delivery_target: Mapped[str] = mapped_column(String(128), default="Telegram 860835914")
    schema_version: Mapped[str] = mapped_column(String(16), default="")
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    error_message: Mapped[Optional[str]] = mapped_column(Text(), default=None)
    parts: Mapped[list] = mapped_column(JSON, default=list)


class ProcessedUpdate(Base):
    __tablename__ = "processed_updates"
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(16))
    update_id: Mapped[int] = mapped_column(BigInteger())
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    __table_args__ = (UniqueConstraint("platform", "update_id", name="uq_processed_update"),)


class ViolationLog(Base):
    __tablename__ = "violations"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    rule: Mapped[str] = mapped_column(String(24))
    snippet: Mapped[str] = mapped_column(Text(), default="")
    action_taken: Mapped[str] = mapped_column(String(32), default="warn")
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow, index=True)


class AnonThread(Base):
    """One anonymous thread = one sender → one link owner (v3).
    Lets the owner reply and report/block a persistent anonymous sender."""
    __tablename__ = "anon_threads"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|blocked
    last_text: Mapped[Optional[str]] = mapped_column(Text(), default=None)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    __table_args__ = (UniqueConstraint("owner_id", "sender_id", name="uq_anon_thread"),)


class DictionaryWord(Base):
    __tablename__ = "moderation_dictionary"
    id: Mapped[int] = mapped_column(primary_key=True)
    word: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(8), default="high")
    action: Mapped[str] = mapped_column(String(16), default="block_and_warn")
    match_count: Mapped[int] = mapped_column(Integer, default=0)
    added_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)


class AllowlistPhrase(Base):
    __tablename__ = "moderation_allowlist"
    id: Mapped[int] = mapped_column(primary_key=True)
    phrase: Mapped[str] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(String(128), default="")
    added_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)


class InvoiceOrder(Base):
    """Immutable price snapshot, committed BEFORE sending an invoice."""
    __tablename__ = "invoice_orders"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(16))
    payer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    recipient_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    package_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    coins: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime())
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(), default=None)
    __table_args__ = (CheckConstraint("coins > 0 AND amount > 0", name="ck_order_positive"),)


class UpdateInbox(Base):
    __tablename__ = "update_inbox"
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(16))
    update_id: Mapped[int] = mapped_column(BigInteger())
    actor: Mapped[str] = mapped_column(String(64))
    partition: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    error: Mapped[str] = mapped_column(String(64), default="")
    __table_args__ = (UniqueConstraint("platform", "update_id", name="uq_inbox_update"),
                     Index("ix_inbox_ready", "partition", "status", "available_at", "id"),
                     Index("ix_inbox_actor", "platform", "actor", "status", "id"))


# ------------------------------------------------------------- engine ----
_engine = None
SessionLocal: Optional[async_sessionmaker] = None


def init_engine(url: str):
    global _engine, SessionLocal
    from .service import invalidate_cache
    invalidate_cache()
    # Railway provides postgres:// / postgresql:// URLs; normalize for asyncpg.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres"):
        # v2.4.3: hard server-side timeouts. Incident 2026-11-14: a stuck query
        # (row-lock wait without lock_timeout) froze the Bale polling handler
        # FOREVER — getUpdates stopped mid-loop, 1500+ updates piled up and the
        # bot went completely deaf. No query may hang longer than these caps;
        # the timeouts turn a silent infinite hang into a logged, retried error.
        _engine = create_async_engine(
            url, pool_size=__import__("app.config", fromlist=["settings"]).settings.db_pool_size,
            max_overflow=__import__("app.config", fromlist=["settings"]).settings.db_max_overflow, pool_pre_ping=True,
            connect_args={"server_settings": {
                "statement_timeout": "30000",                 # ms
                "lock_timeout": "10000",                      # ms
                "idle_in_transaction_session_timeout": "60000",  # ms
            }}, pool_timeout=30)
    else:
        _engine = create_async_engine(url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


def get_engine():
    assert _engine is not None, "init_engine() must be called first"
    return _engine


async def dispose_engine():
    global _engine, SessionLocal
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    SessionLocal = None


@asynccontextmanager
async def session_scope():
    assert SessionLocal is not None, "init_engine() must be called first"
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_all(engine) -> None:
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if engine.dialect.name == "postgresql":
            # Defence in depth for "one active chat per user" (service layer also enforces).
            await conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_active_chat_a ON chats (user_a_id) WHERE status='active'"))
            await conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_active_chat_b ON chats (user_b_id) WHERE status='active'"))


# ---- v2.4 additive column migration ----------------------------------------
# create_all only creates MISSING TABLES; columns added to existing tables are
# handled here (idempotent, additive, safe on both Postgres and SQLite).
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "users": {
        "bot_blocked": {"postgresql": "BOOLEAN NOT NULL DEFAULT FALSE",
                        "sqlite": "INTEGER NOT NULL DEFAULT 0"},
        "reminder_stage": {"postgresql": "INTEGER NOT NULL DEFAULT 0",
                           "sqlite": "INTEGER NOT NULL DEFAULT 0"},
        "last_reminder_at": {"postgresql": "TIMESTAMP", "sqlite": "DATETIME"},
    },
    "chat_messages": {"source_key": {"postgresql":"VARCHAR(128)", "sqlite":"VARCHAR(128)"}},
    "broadcasts": {"last_user_id": {"postgresql":"INTEGER NOT NULL DEFAULT 0", "sqlite":"INTEGER NOT NULL DEFAULT 0"}},
    "queue_entries": {
        "notified_at": {"postgresql": "TIMESTAMP", "sqlite": "DATETIME"},
    },
}


async def ensure_added_columns(engine) -> list[str]:
    """Add columns introduced after the first deployment (v2.4). Idempotent."""
    from sqlalchemy import text
    added: list[str] = []
    dialect = engine.dialect.name
    async with engine.begin() as conn:
        for table, cols in _ADDED_COLUMNS.items():
            existing = await conn.run_sync(_sync_columns, table)
            for col, ddl in cols.items():
                if col in existing:
                    continue
                stmt = ddl.get(dialect) or ddl["sqlite"]
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {stmt}"))
                added.append(f"{table}.{col}")
    return added


def _sync_columns(sync_conn, table: str) -> set:
    from sqlalchemy import inspect as _inspect
    insp = _inspect(sync_conn)
    return {c["name"] for c in insp.get_columns(table)}


# ---- v2.4.1 column widening (Postgres-only, idempotent) ---------------------
# SQLite does not enforce VARCHAR lengths, so overflows only surface on
# production Postgres: "gregorian" (9 chars) did not fit VARCHAR(8) and every
# Gregorian-calendar registration crashed mid-transaction with
# StringDataRightTruncationError, leaving the signup FSM stuck on the
# birth-confirmation step (each retry re-prompted — the "message flood").
_WIDEN_COLUMNS: list[tuple[str, str, int]] = [
    ("users", "birth_calendar", 16),
    # v2.4.3 FIX: execute_restore writes trigger="restore_rollback_snapshot"
    # (25 chars) into this column — varchar(16) made EVERY restore fail with
    # StringDataRightTruncationError at the emergency-snapshot step.
    ("backups", "trigger", 32),
]


async def ensure_widened_columns(engine) -> list[str]:
    """Widen VARCHAR columns created too narrow by older versions (v2.4.1)."""
    from sqlalchemy import text
    widened: list[str] = []
    if engine.dialect.name != "postgresql":
        return widened
    async with engine.begin() as conn:
        for table, col, width in _WIDEN_COLUMNS:
            row = (await conn.execute(text(
                "SELECT character_maximum_length FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"),
                {"t": table, "c": col})).first()
            if row and row[0] is not None and row[0] < width:
                await conn.execute(text(
                    f"ALTER TABLE {table} ALTER COLUMN {col} TYPE VARCHAR({width})"))
                widened.append(f"{table}.{col}: varchar({row[0]}) -> varchar({width})")
    return widened


# Capture changes BEFORE autoflush empties new/dirty/deleted. This also covers
# explicit commit() calls inside bot flows, not only session_scope's final commit.
from sqlalchemy import event as _event
from sqlalchemy.orm import Session as _SyncSession


@_event.listens_for(_SyncSession, "before_flush")
def _remember_config_changes(session, *_):
    if any(isinstance(obj, (AppSetting, DictionaryWord, AllowlistPhrase))
           for obj in [*session.new, *session.dirty, *session.deleted]):
        session.info["invalidate_config"] = True


@_event.listens_for(_SyncSession, "after_commit")
@_event.listens_for(_SyncSession, "after_rollback")
def _invalidate_committed_config(session):
    changed = session.info.pop("invalidate_config", False)
    changed = session.info.pop("settings_changed", False) or changed
    if changed:
        from .service import invalidate_cache
        invalidate_cache()


# Monetization v2: separate tables keep the legacy user/payment schema intact.
class MemberProfile(Base):
    __tablename__ = "member_profiles"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    theme: Mapped[str] = mapped_column(String(24), default="plain")
    active_coupon: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    first_order: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    game_trial_used: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_reconnect: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_gifts: Mapped[bool] = mapped_column(Boolean, default=True)


class ShopQuote(Base):
    __tablename__ = "shop_quotes"
    __table_args__ = (Index("ix_shop_recovery", "status", "sku", "created_at"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    payer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    recipient_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    sku: Mapped[str] = mapped_column(String(32))
    coins: Mapped[int] = mapped_column(Integer)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="quoted")
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class MemberGrant(Base):
    __tablename__ = "member_grants"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    sku: Mapped[str] = mapped_column(String(32), index=True)
    purchase_id: Mapped[str] = mapped_column(String(32), unique=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PromoCode(Base):
    __tablename__ = "promo_codes"
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    percent: Mapped[int] = mapped_column(Integer, default=0)
    gift_coins: Mapped[int] = mapped_column(Integer, default=0)
    max_uses: Mapped[int] = mapped_column(Integer)
    used: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class PromoUse(Base):
    __tablename__ = "promo_uses"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(ForeignKey("promo_codes.code"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("code", "user_id", name="uq_promo_user"),)


class TopicLink(Base):
    __tablename__ = "topic_links"
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    label: Mapped[str] = mapped_column(String(40))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ReconnectRequest(Base):
    __tablename__ = "reconnect_requests"
    __table_args__ = (Index("ix_reconnect_recovery", "status", "expires_at"),)
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    toman_equivalent: Mapped[int] = mapped_column(Integer, default=0)
    old_chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id"), unique=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    target_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    new_chat_id: Mapped[Optional[int]] = mapped_column(ForeignKey("chats.id"), default=None)
    coins: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SocialGame(Base):
    __tablename__ = "social_games"
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id"), index=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default="invited")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=0)
    message_a: Mapped[Optional[int]] = mapped_column(BigInteger, default=None)
    message_b: Mapped[Optional[int]] = mapped_column(BigInteger, default=None)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
