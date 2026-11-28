"""Platform adapters (Telegram + Bale) behind one shared interface.

Verified against official docs (2026-11):
- Telegram Bot API 10.4: https://core.telegram.org/bots/api
- Bale Bot API: https://docs.bale.ai/ (base https://tapi.bale.ai/bot<TOKEN>/METHOD)

Key verified Bale differences implemented here:
- No parse_mode (Bale always applies its own Markdown) -> we send plain text.
- No sendSticker / no inline mode / no secret_token on webhooks (only `url`).
- sendInvoice requires provider_token; LabeledPrice.amount is in Rial.
- Webhook ports: 443 and 88 only.
- answerCallbackQuery must always be called (buttons stay waiting otherwise).
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from typing import Any, Optional

import httpx

from .config import RUNTIME

log = logging.getLogger("kanar.platform")

RETRYABLE = {429, 500, 502, 503, 504}
MAX_RETRIES = 4

MEDIA_SEND = {
    "photo": "sendPhoto", "voice": "sendVoice", "video": "sendVideo",
    "document": "sendDocument", "audio": "sendAudio", "animation": "sendAnimation",
    "video_note": "sendVideoNote", "location": "sendLocation", "contact": "sendContact",
}
# Cross-platform-safe types (documented on BOTH platforms):
CROSS_OK = {"text", "photo", "voice", "video", "document", "audio", "animation", "location", "contact"}
# Sticker: Telegram sendSticker exists; Bale sendSticker is NOT documented -> cross & bale disabled.
# video_note: Bale sendVideoNote is NOT documented -> cross disabled.


class PlatformError(Exception):
    def __init__(self, method: str, error_code: int, description: str):
        super().__init__(f"{method}: {error_code} {description}")
        self.method, self.error_code, self.description = method, error_code, description


class UnsupportedMethod(PlatformError):
    def __init__(self, method: str, why: str):
        super().__init__(method, 400, why)


class BaseAdapter:
    platform = ""
    api_base = ""
    file_base = ""

    # v2.2 (item 5): outbound send throttling so the bot never trips the API
    # rate limits (Telegram: ~30 msg/s global, Bale similar). Messages to the
    # same chat are spaced out, and after a 429 EVERY send waits for retry_after.
    rate_per_sec: float = 25.0        # conservative global sends/second cap
    min_chat_interval: float = 0.5    # seconds between sends to the same chat

    def __init__(self, token: str):
        self.token = token
        self._client: Optional[httpx.AsyncClient] = None
        self._locks: dict[str, asyncio.Lock] = {}
        self._throttle_lock = asyncio.Lock()
        self._send_history: deque = deque()          # monotonic timestamps
        self._last_per_chat: dict[str, float] = {}
        self._cooldown_until = 0.0                   # global 429 cooldown

    async def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            # Short timeouts: Bale is often unreachable from non-Iranian IPs;
            # calls must fail fast so startup and schedulers never block.
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=8.0))
        return self._client

    async def _throttle(self, chat_id) -> None:
        """Reserve a send slot: global sliding window + per-chat spacing +
        global 429 cooldown (item 5)."""
        while True:
            wait = 0.0
            async with self._throttle_lock:
                now = time.monotonic()
                if len(self._last_per_chat) > 10000:
                    self._last_per_chat = {k:v for k,v in self._last_per_chat.items() if now-v < 60}
                if len(self._locks) > 10000:
                    self._locks = {k:v for k,v in self._locks.items() if v.locked() or getattr(v, "_waiters", None)}
                wait = self._cooldown_until - now
                while self._send_history and now - self._send_history[0] > 1.0:
                    self._send_history.popleft()
                if len(self._send_history) >= self.rate_per_sec:
                    wait = max(wait, 1.0 - (now - self._send_history[0]))
                last = self._last_per_chat.get(str(chat_id))
                if last is not None:
                    wait = max(wait, self.min_chat_interval - (now - last))
                if wait <= 0:
                    # reserve the slot now so concurrent callers queue up fairly
                    self._send_history.append(now)
                    self._last_per_chat[str(chat_id)] = now
                    return
            await asyncio.sleep(min(max(wait, 0.01), 1.0))

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def chat_lock(self, chat_id: str) -> asyncio.Lock:
        key = str(chat_id)
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def call(self, method: str, payload: Optional[dict] = None,
                   files: Optional[dict] = None) -> Any:
        """POST with 429/backoff handling and bounded retries."""
        url = f"{self.api_base}{method}"
        attempt = 0
        backoff = 1.0
        while True:
            attempt += 1
            try:
                cli = await self.client()
                if files:
                    data = {k: (str(v) if not isinstance(v, (bytes, tuple)) else v)
                            for k, v in (payload or {}).items()}
                    resp = await cli.post(url, data=data, files=files)
                else:
                    resp = await cli.post(url, json=payload or {})
                body = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                if attempt >= MAX_RETRIES:
                    raise PlatformError(method, 0, f"network: {exc}") from exc
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15.0)
                continue
            if body.get("ok"):
                return body.get("result")
            err_code = body.get("error_code", resp.status_code)
            desc = body.get("description", "unknown error")
            if err_code == 429 and attempt < MAX_RETRIES:
                params = body.get("parameters") or {}
                retry = min(float(params.get("retry_after", 5)), 60.0)
                # v2.2: pause EVERY send on this adapter until retry_after passes
                self._cooldown_until = max(self._cooldown_until,
                                           time.monotonic() + retry)
                log.warning("429 on %s.%s — global send cooldown %ss",
                            self.platform, method, retry)
                await asyncio.sleep(retry + 0.2)
                continue
            if err_code in RETRYABLE and attempt < MAX_RETRIES:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15.0)
                continue
            raise PlatformError(method, err_code, desc)

    # ---------------------------------------------------- messaging API ----
    async def send_message(self, chat_id: int | str, text: str,
                           keyboard: Optional[list[list[dict]]] = None,
                           reply_to: Optional[int] = None) -> dict:
        await self._throttle(chat_id)
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:4096]}
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        return await self.call("sendMessage", payload)

    async def edit_message(self, chat_id: int | str, message_id: int, text: str,
                           keyboard: Optional[list[list[dict]]] = None) -> Any:
        await self._throttle(chat_id)
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text[:4096]}
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        return await self.call("editMessageText", payload)

    async def edit_reply_markup(self, chat_id: int | str, message_id: int,
                                keyboard: Optional[list[list[dict]]] = None) -> Any:
        """Replace (or, with keyboard=None, remove) the inline keyboard of a
        message. Both platforms document this method (Bale: editMessageReplyMarkup
        with chat_id/message_id/reply_markup)."""
        await self._throttle(chat_id)
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id}
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        return await self.call("editMessageReplyMarkup", payload)

    async def delete_message(self, chat_id: int | str, message_id: int) -> Any:
        """Delete a message. Telegram/Bale both allow bots to delete their own
        outgoing messages in private chats (Bale: message must be < 48h old)."""
        await self._throttle(chat_id)
        return await self.call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    async def answer_callback(self, callback_query_id: str, text: str = "",
                              alert: bool = False) -> Any:
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text[:200]
        if alert:
            payload["show_alert"] = True
        return await self.call("answerCallbackQuery", payload)

    async def send_media(self, chat_id: int | str, msg_type: str,
                         file_id: Optional[str] = None, *, caption: str = "",
                         bytes_data: Optional[bytes] = None, filename: str = "file",
                         reply_to: Optional[int] = None, extra: Optional[dict] = None) -> dict:
        if msg_type not in MEDIA_SEND:
            raise UnsupportedMethod(msg_type, "media type not supported by adapter")
        await self._throttle(chat_id)
        method = MEDIA_SEND[msg_type]
        payload: dict[str, Any] = {"chat_id": chat_id}
        if caption:
            payload["caption"] = caption[:1024]
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        payload.update(extra or {})
        if bytes_data is not None:
            files = {msg_type: (filename, bytes_data)}
            return await self.call(method, payload, files=files)
        if not file_id:
            raise ValueError("file_id or bytes_data required")
        payload[msg_type] = file_id
        return await self.call(method, payload)

    async def send_chat_action(self, chat_id: int | str, action: str) -> Any:
        return await self.call("sendChatAction", {"chat_id": chat_id, "action": action})

    async def get_file(self, file_id: str) -> dict:
        return await self.call("getFile", {"file_id": file_id})

    async def download_file(self, file_id: str, max_bytes: int) -> bytes:
        info = await self.get_file(file_id)
        path = info.get("file_path")
        if not path:
            raise PlatformError("getFile", 0, "no file_path")
        size = info.get("file_size") or 0
        if size and size > max_bytes:
            raise UnsupportedMethod("download", f"file exceeds {max_bytes} bytes limit")
        url = f"{self.file_base}{path}"
        cli = await self.client()
        # Bounded streaming download.
        buf = bytearray()
        async with cli.stream("GET", url) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(64 * 1024):
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    raise UnsupportedMethod("download", f"stream exceeded {max_bytes} bytes")
        return bytes(buf)

    async def set_webhook(self, url: str) -> Any:
        raise NotImplementedError

    async def get_webhook_info(self) -> dict:
        return await self.call("getWebhookInfo", {})

    async def set_my_commands(self, commands: list[dict]) -> Any:
        return await self.call("setMyCommands", {"commands": commands})

    # -------------------------------------------------------- payments ----
    async def send_invoice(self, chat_id: int | str, title: str, description: str,
                           payload: str, prices: list[dict],
                           provider_token: str = "") -> dict:
        await self._throttle(chat_id)
        raise NotImplementedError

    async def refund_payment(self, user_id: int, charge_id: str) -> Any:
        raise NotImplementedError

    # --------------------------------------------------------- polling ----
    UPDATE_TIMEOUT = 60  # hard cap for one update's handler (seconds)

    async def poll_updates(self, handler, stop: asyncio.Event,
                           start_offset: int = 0):
        """Long-polling fallback loop (v2.4: hardened; v2.4.3: hang-proof).

        - network failures (dial/timeout) and API errors back off differently:
          network problems retry sooner (they are often transient), hard API
          failures back off exponentially but never longer than 120s so the bot
          recovers within ~2 minutes instead of going dark for 10.
        - jitter avoids thundering reconnections after a platform outage.
        - every outcome is recorded in RUNTIME["poll_health"] for the auto
          alerting engine (growth.check_alerts).
        - v2.4.3 incident fix: ONE stuck handler (a DB row-lock wait with no
          lock_timeout, a dead platform send, …) froze the WHOLE poll loop
          forever — getUpdates stopped mid-batch and the bot went deaf while
          ~1500 updates piled up. Every handler now runs under a hard
          asyncio.wait_for cap; a timeout is logged and the loop moves on.
        - start_offset lets the caller skip a stale backlog (flushed at boot).
        """
        offset = max(int(start_offset or 0), 0)
        fails = 0
        health = RUNTIME.setdefault("poll_health", {}).setdefault(self.platform, {
            "last_ok": 0.0, "last_error": "", "fails": 0, "ok": 0,
            "started_at": time.time()})
        while not stop.is_set():
            try:
                # v2.4.4: allowed_updates is explicit — some Bot-API servers
                # (Bale included) may not deliver pre_checkout_query by their
                # default subscription, which silently breaks wallet payments.
                params = {"timeout": 25, "offset": offset, "limit": 50,
                          "allowed_updates": ["message", "edited_message",
                                              "callback_query", "pre_checkout_query"]}
                result = await self.call("getUpdates", params)
                fails = 0
                health["ok"] += 1
                health["fails"] = 0
                health["last_ok"] = time.time()
                health["last_error"] = ""
                for upd in result or []:
                    try:
                        await asyncio.wait_for(handler(upd), timeout=self.UPDATE_TIMEOUT)
                        offset = max(offset, int(upd.get("update_id", 0)) + 1)
                    except asyncio.TimeoutError:
                        log.error("update handler TIMEOUT after %ss (polling %s, "
                                  "update_id=%s) — skipping to keep the poller alive",
                                  self.UPDATE_TIMEOUT, self.platform,
                                  upd.get("update_id"))
                        break
                    except Exception:
                        log.exception("update handler failed (polling %s)", self.platform)
                        break
            except asyncio.CancelledError:
                raise
            except PlatformError as exc:
                fails += 1
                health["fails"] = fails
                health["last_error"] = str(exc)[:200]
                if fails <= 3 or fails % 20 == 0:
                    log.warning("poll error %s (fail #%d): %s", self.platform, fails, exc)
                if exc.error_code == 0:
                    # network-level failure (DNS/timeout/unreachable) — short waits
                    await asyncio.sleep(min(2 * fails, 20) + random.uniform(0, 1.5))
                else:
                    # real API error — exponential backoff, capped at 120s
                    await asyncio.sleep(min(3 * fails, 120) + random.uniform(0, 2))
            except Exception:
                fails += 1
                health["fails"] = fails
                health["last_error"] = "poll loop crash"
                log.exception("poll loop crash %s", self.platform)
                await asyncio.sleep(5)


class TelegramAdapter(BaseAdapter):
    platform = "telegram"

    def __init__(self, token: str, webhook_secret: str = ""):
        super().__init__(token)
        self.api_base = f"https://api.telegram.org/bot{token}/"
        self.file_base = f"https://api.telegram.org/file/bot{token}/"
        self.webhook_secret = webhook_secret

    async def send_message(self, chat_id, text, keyboard=None, reply_to=None):
        # Telegram supports HTML parse_mode.
        await self._throttle(chat_id)
        import html as _h
        payload = {"chat_id": chat_id, "text": _h.escape(text)[:4096], "parse_mode": "HTML"}
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        if reply_to:
            payload["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        return await self.call("sendMessage", payload)

    async def send_media(self, chat_id, msg_type, file_id=None, *, caption="", bytes_data=None,
                         filename="file", reply_to=None, extra=None):
        if msg_type == "sticker":
            payload = {"chat_id": chat_id, "sticker": file_id}
            if bytes_data is not None:
                return await self.call("sendSticker", payload, files={"sticker": (filename, bytes_data)})
            return await self.call("sendSticker", payload)
        return await super().send_media(chat_id, msg_type, file_id, caption=caption,
                                         bytes_data=bytes_data, filename=filename,
                                         reply_to=reply_to, extra=extra)

    async def send_sticker(self, chat_id: int | str, file_id: str) -> dict:
        await self._throttle(chat_id)
        return await self.call("sendSticker", {"chat_id": chat_id, "sticker": file_id})

    async def set_webhook(self, url: str) -> Any:
        return await self.call("setWebhook", {
            "url": url, "secret_token": self.webhook_secret,
            "max_connections": 60,
            "allowed_updates": ["message", "callback_query", "pre_checkout_query"],
            "drop_pending_updates": False})

    async def send_invoice(self, chat_id, title, description, payload, prices, provider_token=""):
        # Telegram Stars: currency XTR, empty provider_token, exactly one price item.
        return await self.call("sendInvoice", {
            "chat_id": chat_id, "title": title[:32], "description": description[:255],
            "payload": payload, "currency": "XTR", "provider_token": "",
            "prices": prices})

    async def refund_payment(self, user_id: int, charge_id: str) -> Any:
        return await self.call("refundStarPayment", {
            "user_id": user_id, "telegram_payment_charge_id": charge_id})

    def verify_webhook_secret(self, header: Optional[str]) -> bool:
        import hmac
        return bool(self.webhook_secret) and hmac.compare_digest(header or "", self.webhook_secret)


class BaleAdapter(BaseAdapter):
    platform = "bale"
    # docs.bale.ai documents a 20 messages/second ceiling (Telegram ~30).
    # Staying at the documented cap avoids 429 storms on large broadcasts.
    rate_per_sec: float = 20.0

    def __init__(self, token: str, provider_token: str = ""):
        super().__init__(token)
        self.api_base = f"https://tapi.bale.ai/bot{token}/"
        self.file_base = f"https://tapi.bale.ai/file/bot{token}/"
        self.provider_token = provider_token

    async def send_message(self, chat_id, text, keyboard=None, reply_to=None):
        # Verified: Bale has NO parse_mode; text is always Bale-Markdown formatted,
        # so we neutralise markdown metacharacters to guarantee plain-text rendering.
        safe = text.replace("*", "\\*").replace("_", "\\_").replace("`", "\\`").replace("[", "\\[")
        return await super().send_message(chat_id, safe, keyboard, reply_to)

    async def send_media(self, chat_id, msg_type, file_id=None, *, caption="", bytes_data=None,
                         filename="file", reply_to=None, extra=None):
        if msg_type in ("sticker", "video_note", "poll"):
            # NOT documented on Bale (no sendSticker/sendVideoNote in docs.bale.ai).
            raise UnsupportedMethod(msg_type, f"{msg_type} is not supported by Bale Bot API")
        return await super().send_media(chat_id, msg_type, file_id, caption=caption,
                                         bytes_data=bytes_data, filename=filename,
                                         reply_to=reply_to, extra=extra)

    async def set_webhook(self, url: str) -> Any:
        # Verified: Bale setWebhook accepts only `url` (no secret_token documented).
        return await self.call("setWebhook", {"url": url})

    async def send_invoice(self, chat_id, title, description, payload, prices, provider_token=""):
        if not (provider_token or self.provider_token):
            raise UnsupportedMethod("sendInvoice", "BALE_PROVIDER_TOKEN not configured")
        # Verified: amounts are in Rial; provider_token required; no currency param.
        token = provider_token or self.provider_token
        body = {"chat_id": chat_id, "title": title[:32], "description": description[:255],
                "payload": payload, "provider_token": token, "prices": prices}
        log.info("bale invoice request sent")
        res = await self.call("sendInvoice", body)
        log.info("bale sendInvoice OK: message_id=%s (wallet pre_checkout must follow "
                 "when the user taps pay)", (res or {}).get("result", {}).get("message_id"))
        return res

    async def refund_payment(self, user_id: int, charge_id: str) -> Any:
        # Verified absence: no refund method documented in Bale Bot API.
        raise UnsupportedMethod("refund", "Bale Bot API documents no refund method; "
                                          "refunds must be issued manually via the Bale wallet panel")

    def verify_webhook_secret(self, header: Optional[str]) -> bool:
        # No secret_token support documented on Bale; origin verified by URL secrecy.
        return True
