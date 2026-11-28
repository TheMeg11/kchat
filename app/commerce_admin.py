"""Authorized commerce configuration, limited promos and measured sales totals."""

import re
from datetime import datetime, timezone
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, func
from . import db, service, commerce
from .webadmin import require, ApiErr, _ip

router = APIRouter(prefix="/api/admin/v1/monetization")


class Prices(BaseModel):
    enabled: bool
    vip30: int = Field(ge=1, le=100000)
    pass60: int = Field(ge=1, le=100000)
    starters: int = Field(ge=1, le=100000)
    games: int = Field(ge=1, le=100000)
    theme_ocean: int = Field(ge=1, le=100000)
    theme_garden: int = Field(ge=1, le=100000)
    links: int = Field(ge=1, le=100000)
    rose: int = Field(ge=1, le=100000)
    coffee: int = Field(ge=1, le=100000)
    book: int = Field(ge=1, le=100000)
    reconnectToman: int = Field(ge=1, le=1000000)
    firstBaseCoins: int = Field(ge=20, le=20000)
    firstBonusPct: int = Field(ge=1, le=100)


class PromoBody(BaseModel):
    code: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9-]+$")
    percent: int = Field(default=0, ge=0, le=90)
    gift_coins: int = Field(default=0, ge=0, le=10000)
    max_uses: int = Field(ge=1, le=10000)
    expires_at: datetime

    @model_validator(mode="after")
    def exclusive_reward(self):
        if bool(self.percent) == bool(self.gift_coins):
            raise ValueError("Choose one: percent discount OR gift coins")
        dt = self.expires_at
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        if dt <= db.utcnow():
            raise ValueError("Expiry must be in the future")
        self.expires_at = dt
        return self


@router.get("")
async def overview(request: Request):
    await require(request, "store.manage")
    async with db.session_scope() as s:
        cfg = await commerce.config(s)
        promos = list(
            (
                await s.execute(
                    select(db.PromoCode)
                    .order_by(db.PromoCode.expires_at.desc())
                    .limit(100)
                )
            ).scalars()
        )
        sales = (
            await s.execute(
                select(db.ShopQuote.sku, func.count(), func.sum(db.ShopQuote.coins))
                .where(db.ShopQuote.status.in_(["paid", "delivered"]))
                .group_by(db.ShopQuote.sku)
            )
        ).all()
        return {
            "settings": cfg,
            "rates": await service.get_setting(s, "store"),
            "sales": [
                {"sku": sku, "purchases": count, "coinsSpent": coins}
                for sku, count, coins in sales
            ],
            "promos": [
                {
                    "code": p.code,
                    "percent": p.percent,
                    "giftCoins": p.gift_coins,
                    "maxUses": p.max_uses,
                    "used": p.used,
                    "active": p.active,
                    "expiresAt": p.expires_at.isoformat() + "Z",
                }
                for p in promos
            ],
        }


@router.put("")
async def prices(body: Prices, request: Request):
    admin, _ = await require(request, "store.manage")
    async with db.session_scope() as s:
        await service.set_setting(s, "monetization", body.model_dump())
        await service.audit(
            s,
            admin_id=admin.id,
            admin_name=admin.username,
            admin_role=admin.role,
            action="monetization.prices",
            module="payments",
            target_type="settings",
            target_id="monetization",
            ip=_ip(request),
            details=str(body.model_dump()),
        )
    return {"ok": True}


@router.post("/promos")
async def create_promo(body: PromoBody, request: Request):
    admin, _ = await require(request, "store.manage")
    code = body.code.upper()
    async with db.session_scope() as s:
        # Serialize creation to turn duplicate-code races into a normal conflict.
        if s.bind.dialect.name == "postgresql":
            from sqlalchemy import text

            await s.execute(text("SELECT pg_advisory_xact_lock(71389205)"))
        if await s.get(db.PromoCode, code):
            raise ApiErr(409, "این کد قبلاً ساخته شده است.", "CODE_EXISTS")
        s.add(
            db.PromoCode(
                code=code,
                percent=body.percent,
                gift_coins=body.gift_coins,
                max_uses=body.max_uses,
                expires_at=body.expires_at,
            )
        )
        await service.audit(
            s,
            admin_id=admin.id,
            admin_name=admin.username,
            admin_role=admin.role,
            action="monetization.promo_created",
            module="payments",
            target_type="promo",
            target_id=code,
            ip=_ip(request),
            details=f"quota={body.max_uses}, expires={body.expires_at}",
        )
    return {"ok": True}


@router.delete("/promos/{code}")
async def disable_promo(code: str, request: Request):
    admin, _ = await require(request, "store.manage")
    if not re.fullmatch(r"[A-Za-z0-9-]{3,32}", code):
        raise ApiErr(400, "کد نامعتبر است.", "INVALID_CODE")
    async with db.session_scope() as s:
        p = (
            await s.execute(
                select(db.PromoCode)
                .where(db.PromoCode.code == code.upper())
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not p:
            raise ApiErr(404, "کد یافت نشد.", "NOT_FOUND")
        p.active = False
        await service.audit(
            s,
            admin_id=admin.id,
            admin_name=admin.username,
            admin_role=admin.role,
            action="monetization.promo_disabled",
            module="payments",
            target_type="promo",
            target_id=p.code,
            ip=_ip(request),
        )
    return {"ok": True}
