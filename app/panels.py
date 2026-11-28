"""Single-active-keyboard bookkeeping (v2.5).

Problem (user report): every relayed chat message carried a full inline
keyboard, and after the chat ended those stale keyboards still offered
«next / end / report / block / gift». Users pressed them and got confusing
results — `next` silently re-queued them into the search queue.

Solution implemented here:
- Every message that carries a keyboard is registered as the chat's *panel*.
- Sending a new panel strips the keyboard of the previous panel message of the
  same user, so at most ONE message in the conversation has buttons.
- Chat-control panels additionally remember which chat they belong to, so when
  that chat ends the keyboard is replaced by a «find a new partner» CTA.
- Everything is best-effort: a panel may be too old to edit (Bale/Telegram
  limit edits to 48h), deleted by the user, or lost on restart. Failures are
  swallowed — the bot never crashes or blocks on a cosmetic edit.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Optional

from .config import RUNTIME

log = logging.getLogger("kanar.panels")

# RUNTIME["panels"] = { "<platform>:<user_id>": (message_id, kind, chat_id) }
_PANELS = "panels"


def _key(platform: str, platform_user_id) -> str:
    return f"{platform}:{platform_user_id}"


def _map() -> dict:
    panels = RUNTIME.setdefault(_PANELS, OrderedDict())
    while len(panels) > 10000:
        panels.pop(next(iter(panels)))
    return panels


def get(platform: str, platform_user_id):
    return _map().get(_key(platform, platform_user_id))


async def strip(adp, platform: str, platform_user_id, message_id,
                keyboard: Optional[list] = None) -> bool:
    """Remove/replace the keyboard of a previously sent panel (best effort)."""
    if not adp or not message_id:
        return False
    try:
        await adp.edit_reply_markup(platform_user_id, message_id, keyboard)
        return True
    except Exception as exc:  # too old, deleted, unsupported, network…
        log.debug("panel strip skipped (%s/%s #%s): %s", platform, platform_user_id,
                  message_id, exc)
        return False


async def send(adp, platform: str, platform_user_id, text: str,
               keyboard: Optional[list] = None, *, kind: str = "panel",
               chat_id: Optional[int] = None, reply_to: Optional[int] = None) -> dict:
    """Send a message and register it as the active panel.

    The previous panel of the same user loses its keyboard right after the new
    message is on its way, so there is never more than one live keyboard.
    """
    if kind == "chat" and chat_id and keyboard:
        keyboard = [[{**button, "callback_data": f"c:{chat_id}:" + button["callback_data"]}
            if button.get("callback_data", "").startswith("chat_") else dict(button)
            for button in row] for row in keyboard]
    res = await adp.send_message(platform_user_id, text, keyboard=keyboard,
                                 reply_to=reply_to)
    if keyboard:
        prev = _map().get(_key(platform, platform_user_id))
        if prev and prev[0] and prev[0] != (res or {}).get("message_id"):
            await strip(adp, platform, platform_user_id, prev[0])
        _map()[_key(platform, platform_user_id)] = (
            (res or {}).get("message_id"), kind, chat_id)
    return res


async def send_media(adp, platform: str, platform_user_id, msg_type: str,
                     file_id: Optional[str] = None, *, caption: str = "",
                     bytes_data: Optional[bytes] = None, filename: str = "file",
                     keyboard: Optional[list] = None, kind: str = "panel",
                     chat_id: Optional[int] = None) -> dict:
    """Same as send() but for media messages (photo cards at match time)."""
    res = await adp.send_media(platform_user_id, msg_type, file_id, caption=caption,
                               bytes_data=bytes_data, filename=filename)
    if keyboard:
        prev = _map().get(_key(platform, platform_user_id))
        if prev and prev[0] and prev[0] != (res or {}).get("message_id"):
            await strip(adp, platform, platform_user_id, prev[0])
        _map()[_key(platform, platform_user_id)] = (
            (res or {}).get("message_id"), kind, chat_id)
    return res


async def retire_chat(platform: str, platform_user_id, chat_id: int,
                      cta: Optional[list]) -> bool:
    """Turn the chat-control panel of a finished chat into a search CTA."""
    rec = _map().get(_key(platform, platform_user_id))
    if not rec or rec[1] != "chat" or rec[2] != chat_id:
        return False
    _map().pop(_key(platform, platform_user_id), None)
    adp = RUNTIME.get(f"adapter_{platform}")
    return await strip(adp, platform, platform_user_id, rec[0], cta)


def forget(platform: str, platform_user_id) -> None:
    _map().pop(_key(platform, platform_user_id), None)
