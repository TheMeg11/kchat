"""Consent-based, durable two-player games with stale-move protection."""

from datetime import timedelta
import secrets
from sqlalchemy import select
from . import db, service, commerce
from .config import RUNTIME
from .platform import PlatformError

LINES = (
    (0, 1, 2),
    (3, 4, 5),
    (6, 7, 8),
    (0, 3, 6),
    (1, 4, 7),
    (2, 5, 8),
    (0, 4, 8),
    (2, 4, 6),
)
PUZZLES = (("۲ + ۳", "2 + 3", 5), ("۳ × ۲", "3 × 2", 6), ("۸ − ۴", "8 - 4", 4))


async def invite(uid, kind):
    if kind not in {"ttt", "coop"}:
        raise commerce.ShopError("NOT_FOUND")
    async with db.session_scope() as s:
        chat = await service.active_chat_for(s, uid)
        if not chat:
            raise commerce.ShopError("STALE")
        users = await commerce.lock_users(s, [chat.user_a_id, chat.user_b_id])
        if not await commerce.allowed_pair(
            s, users[chat.user_a_id], users[chat.user_b_id]
        ):
            raise commerce.ShopError("UNAVAILABLE")
        existing = (
            await s.execute(
                select(db.SocialGame)
                .where(
                    db.SocialGame.chat_id == chat.id,
                    db.SocialGame.status.in_(["invited", "playing"]),
                    db.SocialGame.expires_at > db.utcnow(),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing:
            return existing.id
        p = await commerce.profile(s, uid)
        if p.game_trial_used and not await commerce.owns(s, uid, "games"):
            raise commerce.ShopError("LOCKED")
        game = db.SocialGame(
            id=secrets.token_hex(6),
            chat_id=chat.id,
            host_id=uid,
            kind=kind,
            expires_at=db.utcnow() + timedelta(minutes=20),
            data={"board": ".........", "round": 0},
        )
        s.add(game)
        await s.flush()
        return game.id


async def act(uid, gid, version, action):
    async with db.session_scope() as s:
        g = (
            await s.execute(
                select(db.SocialGame).where(db.SocialGame.id == gid).with_for_update()
            )
        ).scalar_one_or_none()
        if not g or g.version != version or g.expires_at <= db.utcnow():
            raise commerce.ShopError("STALE")
        chat = await s.get(db.Chat, g.chat_id)
        if (
            not chat
            or chat.status != "active"
            or uid not in (chat.user_a_id, chat.user_b_id)
        ):
            raise commerce.ShopError("STALE")
        users = await commerce.lock_users(s, [chat.user_a_id, chat.user_b_id])
        if not await commerce.allowed_pair(
            s, users[chat.user_a_id], users[chat.user_b_id]
        ):
            raise commerce.ShopError("UNAVAILABLE")
        if action == "stop":
            g.status = "cancelled"
        elif g.status == "invited":
            if uid == g.host_id or action != "accept":
                raise commerce.ShopError("STALE")
            p = await commerce.profile(s, g.host_id)
            if p.game_trial_used and not await commerce.owns(s, g.host_id, "games"):
                raise commerce.ShopError("LOCKED")
            p.game_trial_used = True
            g.status = "playing"
        elif g.status == "playing":
            data = dict(g.data)
            if g.kind == "ttt":
                board = list(data["board"])
                expected = (
                    chat.user_a_id
                    if board.count("X") == board.count("O")
                    else chat.user_b_id
                )
                if (
                    uid != expected
                    or not action.isdigit()
                    or not 0 <= int(action) < 9
                    or board[int(action)] != "."
                ):
                    raise commerce.ShopError("MOVE")
                board[int(action)] = "X" if uid == chat.user_a_id else "O"
                data["board"] = "".join(board)
                if any(
                    board[a] != "." and board[a] == board[b] == board[c]
                    for a, b, c in LINES
                ):
                    g.status = "won"
                    data["winner"] = uid
                elif "." not in board:
                    g.status = "draw"
            else:
                round_no = data["round"]
                answerer = chat.user_b_id if round_no % 2 == 0 else chat.user_a_id
                if uid != answerer or action != str(PUZZLES[round_no][2]):
                    raise commerce.ShopError("MOVE")
                data["round"] += 1
                if data["round"] == len(PUZZLES):
                    g.status = "won"
            g.data = data
        else:
            raise commerce.ShopError("STALE")
        g.version += 1
        return g.id


def render(g, chat, user):
    L = lambda fa, en, ru=None: commerce.local(user, fa, en, ru)
    base = f"g:{g.id}:{g.version}:"
    B = lambda label, action: {"text": label, "callback_data": base + action}
    end = [B(L("⏹ پایان بازی", "⏹ End game", "⏹ Завершить"), "stop")]
    title = (
        L("🎮 دوز", "🎮 Tic-tac-toe", "🎮 Крестики-нолики")
        if g.kind == "ttt"
        else L("🧩 رمز مشترک", "🧩 Shared code", "🧩 Общий код")
    )
    if g.status == "invited":
        rows = (
            [end]
            if user.id == g.host_id
            else [[B(L("✅ قبول دعوت", "✅ Accept", "✅ Принять"), "accept")], end]
        )
        return (
            title
            + "\n"
            + L(
                "دعوت بازی؛ شروع فقط با پذیرش هم‌چت. یک بازی آزمایشی رایگان، مهمان همیشه رایگان.",
                "Game invitation. Starts with partner consent. One free hosted trial; guests always free.",
                "Приглашение. Игра после согласия. Одна пробная игра; гость играет бесплатно.",
            ),
            rows,
        )
    if g.status not in {"playing"}:
        if g.status == "won" and g.kind == "ttt":
            result = (
                L("🏆 تو برنده شدی!", "🏆 You won!", "🏆 Вы выиграли!")
                if g.data.get("winner") == user.id
                else L(
                    "بازی تمام شد؛ هم‌چت برنده شد.",
                    "Your partner won.",
                    "Собеседник выиграл.",
                )
            )
        else:
            result = (
                L(
                    "✅ چالش با همکاری شما حل شد!",
                    "✅ Solved together!",
                    "✅ Решено вместе!",
                )
                if g.status == "won"
                else L(
                    "بازی پایان یافت؛ گفتگو ادامه دارد.",
                    "Game ended; the chat continues.",
                    "Игра окончена; чат продолжается.",
                )
            )
        return title + "\n" + result, []
    if g.kind == "ttt":
        board = g.data["board"]
        expected = (
            chat.user_a_id if board.count("X") == board.count("O") else chat.user_b_id
        )
        text = (
            L("نوبت توست", "Your turn", "Ваш ход")
            if expected == user.id
            else L("منتظر حرکت هم‌چت…", "Waiting for partner…", "Ход собеседника…")
        )
        text += " · " + ("❌" if user.id == chat.user_a_id else "⭕")
        rows = [
            [
                B({"X": "❌", "O": "⭕", ".": "▫️"}[board[i]], str(i))
                for i in range(j, j + 3)
            ]
            for j in (0, 3, 6)
        ]
        return title + "\n" + text, rows + [end]
    round_no = g.data["round"]
    answerer = chat.user_b_id if round_no % 2 == 0 else chat.user_a_id
    if user.id != answerer:
        clue = PUZZLES[round_no][0 if user.language == "fa" else 1]
        return title + f" · {round_no+1}/3\n" + L(
            "سرنخ را در گفتگو برای هم‌چت توضیح بده: ",
            "Explain this clue to your partner in the chat: ",
            "Объясните подсказку собеседнику: ",
        ) + clue, [end]
    return title + f" · {round_no+1}/3\n" + L(
        "سرنخ دست هم‌چت است؛ با هم جواب را پیدا کنید.",
        "Your partner has the clue. Solve it together.",
        "Подсказка у собеседника. Решите вместе.",
    ), [[B(str(i), str(i)) for i in range(j, j + 3)] for j in (1, 4, 7)] + [end]


import asyncio
import weakref

_render_locks = weakref.WeakValueDictionary()


async def publish(gid):
    lock = _render_locks.get(gid)
    if lock is None:
        lock = asyncio.Lock()
        _render_locks[gid] = lock
    async with lock:
        await _publish(gid)


async def _publish(gid):
    # Persist per-player message IDs; edit the same inline 3x3 board on each move.
    async with db.session_scope() as s:
        g = await s.get(db.SocialGame, gid)
        chat = await s.get(db.Chat, g.chat_id)
        if chat.status != "active" or g.expires_at <= db.utcnow():
            return
        users = [
            await s.get(db.User, chat.user_a_id),
            await s.get(db.User, chat.user_b_id),
        ]
        snapshots = [
            (u, *render(g, chat, u), g.message_a if i == 0 else g.message_b)
            for i, u in enumerate(users)
        ]
    ids = []
    for user, text, keyboard, mid in snapshots:
        adp = RUNTIME.get(f"adapter_{user.platform}")
        if not adp:
            continue
        try:
            if mid:
                await adp.edit_message(
                    user.platform_user_id, mid, text, keyboard=keyboard
                )
                if not keyboard:
                    await adp.edit_reply_markup(user.platform_user_id, mid, [])
            else:
                res = await adp.send_message(
                    user.platform_user_id, text, keyboard=keyboard
                )
                ids.append((user.id, (res or {}).get("message_id")))
        except PlatformError:
            continue
    async with db.session_scope() as s:
        current = await s.get(db.SocialGame, gid)
        for uid, mid in ids:
            if uid == chat.user_a_id:
                current.message_a = mid
            else:
                current.message_b = mid
        # Per-game render lock keeps both boards ordered; every move is version-checked.
