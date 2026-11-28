"""Server-side immutable invoice orders and idempotent settlement."""
import secrets
from datetime import timedelta
from sqlalchemy import select
from . import db, service


async def create(session, payer, coins, amount, recipient=None, package_id=None):
    if not 1 <= coins <= 100000 or not 1 <= amount <= 2**31 - 1:
        raise ValueError("INVALID_ORDER")
    order = db.InvoiceOrder(id=secrets.token_urlsafe(24), platform=payer.platform,
        payer_id=payer.id, recipient_id=(recipient or payer).id, package_id=package_id,
        coins=coins, amount=amount, currency="XTR" if payer.platform == "telegram" else "IRR",
        expires_at=db.utcnow() + timedelta(minutes=30))
    session.add(order)
    await session.flush()
    return "order:" + order.id


async def find(session, platform, event, check_expiry=True):
    payload = event.get("invoice_payload") or ""
    if not isinstance(payload, str) or not payload.startswith("order:"):
        return None
    order = (await session.execute(select(db.InvoiceOrder).where(
        db.InvoiceOrder.id == payload[6:]).with_for_update())).scalar_one_or_none()
    if not order or order.platform != platform:
        return None
    payer = await session.get(db.User, order.payer_id)
    if not payer or payer.status != "active" or payer.platform != platform:
        return None
    if str((event.get("from") or {}).get("id", "")) != payer.platform_user_id:
        return None
    currency = event.get("currency") or ("IRR" if platform == "bale" else "")
    if type(event.get("total_amount")) is not int or order.amount != event["total_amount"] or currency != order.currency:
        return None
    if check_expiry and (order.paid_at or order.expires_at < db.utcnow()):
        return None
    recipient = await session.get(db.User, order.recipient_id)
    if not recipient or recipient.status != "active":
        return None
    return order


async def settle(platform, msg):
    event = dict(msg.get("successful_payment") or {}, **{"from": msg.get("from") or {}})
    charge = event.get("telegram_payment_charge_id") or event.get("provider_payment_charge_id")
    if not isinstance(charge, str) or not 1 <= len(charge) <= 128:
        raise ValueError("INVALID_PAYMENT_CHARGE")
    async with db.session_scope() as s:
        # Settlement may arrive after order expiry; only PRE-checkout enforces expiry.
        order = await find(s, platform, event, check_expiry=False)
        if not order:
            raise ValueError("PAYMENT_REQUIRES_RECONCILIATION")
        existing = (await s.execute(select(db.Payment).where(
            db.Payment.telegram_payment_charge_id == charge))).scalar_one_or_none()
        if existing:
            if existing.invoice_payload != event["invoice_payload"]:
                raise ValueError("PAYMENT_CHARGE_CONFLICT")
            return None
        if order.paid_at:
            raise ValueError("ORDER_ALREADY_PAID")
        target = await s.get(db.User, order.recipient_id)
        order.paid_at = db.utcnow()
        s.add(db.Payment(platform=platform, user_id=order.payer_id,
            package_id=order.package_id, coins=order.coins, amount=order.amount,
            currency=order.currency, status="succeeded", telegram_payment_charge_id=charge,
            provider_payment_charge_id=event.get("provider_payment_charge_id"),
            invoice_payload=event["invoice_payload"]))
        await service.credit(s, target, order.coins,
            "gift_received" if order.recipient_id != order.payer_id else "store_purchase",
            "خرید سکه" if order.recipient_id == order.payer_id else "هدیه ناشناس",
            reference_id=charge, idempotency_key=f"pay:{platform}:{charge}")
        return target, order.coins


class RefundError(Exception):
    def __init__(self, code, message):
        self.code=code
        super().__init__(message)


async def refund(payment_id, actor):
    """Reserve coins atomically, release DB locks before network, reconcile safely.

    Ambiguous gateway failures stay refund_pending. Never grant the reserved
    coins back automatically after a timeout (the gateway may have refunded).
    Retrying the same charge is safe; already-refunded responses are reconciled.
    """
    from .config import RUNTIME
    from .platform import PlatformError
    adp=RUNTIME.get("adapter_telegram")
    if not adp:
        raise RefundError("REFUND_UNAVAILABLE","بازوی تلگرام در دسترس نیست.")
    async with db.session_scope() as s:
        payment=(await s.execute(select(db.Payment).where(db.Payment.id==payment_id).with_for_update())).scalar_one_or_none()
        if not payment:
            raise RefundError("NOT_FOUND","پرداخت یافت نشد.")
        if payment.status=="refunded":
            return
        if payment.platform!="telegram":
            raise RefundError("BALE_REFUND_MANUAL","استرداد بله باید از کیف‌پول بله انجام شود.")
        if payment.status not in ("succeeded","refund_pending"):
            raise RefundError("NOT_REFUNDABLE","این پرداخت قابل استرداد نیست.")
        payer=await s.get(db.User,payment.user_id)
        if not payer or not payer.platform_user_id.isascii() or not payer.platform_user_id.isdigit():
            raise RefundError("MANUAL_REVIEW","حساب حذف شده است؛ استرداد به بررسی دستی نیاز دارد.")
        if payment.status=="succeeded":
            order=await s.get(db.InvoiceOrder,(payment.invoice_payload or "")[6:]) if (payment.invoice_payload or "").startswith("order:") else None
            recipient=await s.get(db.User,order.recipient_id if order else payer.id)
            if payment.coins > 0:
                txn=await service.debit(s,recipient,payment.coins,"purchase_refund","رزرو سکه برای استرداد خرید",
                    idempotency_key=f"refund-payment:{payment.id}")
                if txn is None:
                    raise RefundError("MANUAL_REVIEW","سکه‌ها مصرف شده‌اند؛ استرداد نیازمند بررسی دستی است.")
            payment.status="refund_pending"
        payer_id,charge=payer.platform_user_id,payment.telegram_payment_charge_id
    try:
        await adp.refund_payment(int(payer_id),charge)
    except PlatformError as exc:
        if "CHARGE_ALREADY_REFUNDED" not in exc.description.upper():
            async with db.session_scope() as s:
                await service.notify(s,"استرداد نیازمند بررسی",f"پرداخت #{payment_id} در وضعیت refund_pending است؛ نتیجه درگاه را تطبیق دهید.","critical","/payments")
            raise RefundError("REFUND_PENDING","نتیجهٔ درگاه قطعی نیست؛ وضعیت پرداخت را بررسی و همین استرداد را دوباره پیگیری کنید.") from exc
    async with db.session_scope() as s:
        payment=await s.get(db.Payment,payment_id)
        payment.status,payment.refunded_at,payment.refunded_by="refunded",db.utcnow(),actor
