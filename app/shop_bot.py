"""Small, contextual storefront. No automatic spend, renewal or message upsells."""

from html import escape
from sqlalchemy import select, func, delete
from . import db, service, commerce as C
from .config import RUNTIME, settings
from .platform import PlatformError


def L(user, fa, en=None, ru=None):
    return C.local(user, fa, en, ru)


def button(text, data):
    return {"text": text, "callback_data": data}


def back(user):
    return [
        button(L(user, "‹ فروشگاه", "‹ Shop", "‹ Магазин"), "m:home"),
        button(L(user, "🏠 خانه", "🏠 Home", "🏠 Главное"), "back_home"),
    ]


async def say(user, text, rows=None):
    from . import panels

    adp = RUNTIME.get(f"adapter_{user.platform}")
    if adp:
        rows = list(rows or [back(user)])
        async with db.session_scope() as s:
            chatting = await service.active_chat_for(s, user.id)
        if chatting:
            rows.append(
                [
                    button(
                        L(
                            user,
                            "💬 بازگشت به گفتگو و گزارش / بلاک",
                            "💬 Chat controls / report / block",
                            "💬 Чат / жалоба / блокировка",
                        ),
                        "chat_back",
                    )
                ]
            )
        return await panels.send(
            adp, user.platform, user.platform_user_id, text, keyboard=rows
        )


async def error(user, code):
    messages = {
        "BALANCE": (
            "موجودی کافی نیست؛ هنوز هزینه‌ای کسر نشده است.",
            "Insufficient coins. No new charge.",
            "Недостаточно монет. Списания нет.",
        ),
        "LOCKED": (
            "این قابلیت در VIP یا بستهٔ مربوط موجود است؛ نمونه را رایگان ببین.",
            "Available with VIP or the relevant pack; preview it free.",
            "Доступно с VIP или пакетом; пробный просмотр бесплатный.",
        ),
        "OWNED": (
            "همین حالا به این قابلیت دسترسی داری؛ دوباره خرید نکن.",
            "You already have access; no need to buy again.",
            "У вас уже есть доступ; повторная покупка не нужна.",
        ),
        "PROMO_INVALID": (
            "کد معتبر نیست، منقضی شده یا سهمیه‌اش تمام شده است.",
            "Code invalid, expired, used or exhausted.",
            "Код недействителен, истёк или использован.",
        ),
        "FIRST_USED": (
            "بسته شروع فقط پیش از اولین خرید قابل دریافت است.",
            "Welcome pack is only available before your first purchase.",
            "Стартовый пакет доступен до первой покупки.",
        ),
        "LIMIT": (
            "به سقف این کار رسیدی؛ کمی بعد امتحان کن. برای لینک‌ها، سقف شش لینک است.",
            "Action limit reached. Try later; topic links have a maximum of six.",
            "Достигнут лимит. Повторите позже; максимум шесть ссылок.",
        ),
        "MOVE": (
            "نوبت تو نیست، خانه پر است یا جواب درست نیست؛ دوباره بررسی کن.",
            "Wrong turn, occupied cell or incorrect answer. Try again.",
            "Не ваш ход, клетка занята или неверный ответ.",
        ),
    }
    values = messages.get(
        code,
        (
            "این درخواست دیگر قابل اجرا نیست. هزینه‌ای تازه کسر نشد؛ منوی تازه را باز کن.",
            "This action is no longer available. No new charge; open a fresh menu.",
            "Действие недоступно. Нового списания нет; откройте меню.",
        ),
    )
    await say(
        user,
        L(user, *values),
        [
            [
                button(
                    L(
                        user,
                        "🛍 امکانات و قیمت‌ها",
                        "🛍 Products & prices",
                        "🛍 Товары и цены",
                    ),
                    "m:home",
                ),
                button(
                    L(user, "🪙 خرید سکه", "🪙 Buy coins", "🪙 Купить монеты"),
                    "m:coins",
                ),
            ],
            back(user),
        ],
    )


async def home(user):
    async with db.session_scope() as s:
        balance = (await s.get(db.User, user.id)).coin_balance
        vip = (
            await s.execute(
                select(func.max(db.MemberGrant.expires_at)).where(
                    db.MemberGrant.user_id == user.id, db.MemberGrant.sku == "vip30"
                )
            )
        ).scalar_one()
        pass_until = (
            await s.execute(
                select(func.max(db.MemberGrant.expires_at)).where(
                    db.MemberGrant.user_id == user.id, db.MemberGrant.sku == "pass60"
                )
            )
        ).scalar_one()
        cfg = await C.config(s)
        first = not (
            await s.execute(
                select(db.Payment.id).where(db.Payment.user_id == user.id).limit(1)
            )
        ).scalar_one_or_none()
    title = L(
        user,
        "🛍 برای چه کاری خرید می‌کنی؟",
        "🛍 What would you like to do?",
        "🛍 Что вам нужно?",
    )
    text = f"{title}\n🪙 {balance}\n" + L(
        user,
        "هیچ خرید یا تمدیدی خودکار نیست. قیمت نهایی پیش از تأیید نمایش داده می‌شود.",
        "No automatic purchases or renewal. Final price shown before confirmation.",
        "Без автопокупок и продлений. Итоговая цена до подтверждения.",
    )
    if vip and vip > db.utcnow():
        text += "\nVIP: " + vip.strftime("%Y-%m-%d %H:%M UTC")
    if pass_until and pass_until > db.utcnow():
        text += "\n🎫 " + pass_until.strftime("%Y-%m-%d %H:%M UTC")
    rows = [
        [
            button(
                L(user, "⭐ VIP و پاس گفتگو", "⭐ VIP & passes", "⭐ VIP и пропуски"),
                "m:cat:vip",
            )
        ],
        [
            button(
                L(
                    user,
                    "🎮 بازی و شروع مکالمه",
                    "🎮 Games & starters",
                    "🎮 Игры и темы",
                ),
                "m:cat:fun",
            )
        ],
        [
            button(
                L(
                    user,
                    "🎨 کارت معرفی و لینک‌ها",
                    "🎨 Cards & links",
                    "🎨 Карточки и ссылки",
                ),
                "m:cat:identity",
            )
        ],
        [
            button(
                L(user, "🎁 هدیه برای دوست", "🎁 Gift a friend", "🎁 Подарок другу"),
                "m:gifts",
            )
        ],
        [
            button(L(user, "🪙 شارژ سکه", "🪙 Top up", "🪙 Пополнить"), "m:coins"),
            button(
                L(user, "🎟 کد هدیه / تخفیف", "🎟 Redeem code", "🎟 Ввести код"), "m:promo"
            ),
        ],
    ]
    if first and cfg["enabled"]:
        rows.insert(
            0,
            [
                button(
                    L(
                        user,
                        "🌱 بستهٔ شروع اولین خرید",
                        "🌱 First-purchase pack",
                        "🌱 Стартовый пакет",
                    ),
                    "m:first",
                )
            ],
        )
    rows.append(
        [
            button(
                L(
                    user,
                    "⚙️ تنظیم درخواست‌ها و هدیه‌ها",
                    "⚙️ Requests & gifts",
                    "⚙️ Запросы и подарки",
                ),
                "m:preferences",
            )
        ]
    )
    rows.append([button(L(user, "🏠 خانه", "🏠 Home", "🏠 Главное"), "back_home")])
    await say(user, text, rows)


async def catalog(user, category):
    groups = {
        "vip": ["vip30", "pass60"],
        "fun": ["starters", "games"],
        "identity": ["theme_ocean", "theme_garden", "links"],
    }
    skus = groups.get(category, [])
    async with db.session_scope() as s:
        cfg = await C.config(s)
        ownership = {
            sku: await C.owns(s, user.id, sku) for sku in skus if sku not in C.TIMED
        }
    rows = []
    for sku in skus:
        label = C.name(user, sku)
        rows.append(
            [
                button(
                    f"{label} · {cfg[sku]} 🪙" + (" ✓" if ownership.get(sku) else ""),
                    f"m:item:{sku}",
                )
            ]
        )
    if category == "fun":
        rows.append(
            [
                button(
                    L(
                        user,
                        "▶️ سرگرمی در گفتگوی فعلی",
                        "▶️ Play in current chat",
                        "▶️ Играть в текущем чате",
                    ),
                    "m:fun",
                )
            ]
        )
    if category == "identity":
        rows.append(
            [
                button(
                    L(
                        user,
                        "🔗 مدیریت لینک‌ها",
                        "🔗 Manage links",
                        "🔗 Управление ссылками",
                    ),
                    "m:links",
                )
            ]
        )
    await say(
        user,
        L(
            user,
            "هر مورد را برای توضیح و پیش‌نمایش باز کن.",
            "Open an item for details and preview.",
            "Откройте товар для описания и просмотра.",
        ),
        rows + [back(user)],
    )


def description(user, sku):
    if sku == "vip30":
        return L(
            user,
            "۳۰ روز دسترسی به بستهٔ شروع مکالمه، میزبانی بازی‌ها، شش لینک موضوعی و قالب‌های کارت. شامل پیام رایگان، هدیهٔ مصرفی یا اولویت صف نیست. تمدید دستی.",
            "30 days of starters, hosted games, six topic links and card themes. No free messages, consumable gifts or queue priority. Manual renewal.",
            "30 дней: темы, игры, шесть ссылок и шаблоны карточек. Без бесплатных сообщений, подарков и приоритета. Продление вручную.",
        )
    if sku == "pass60":
        return L(
            user,
            "۶۰ دقیقه از لحظهٔ خرید: هزینهٔ پیام‌های ارسالی در گفتگوی فعال صفر می‌شود؛ محدودیت ضداسپم برقرار است. فیلتر اتصال و سایر خریدها جدا هستند.",
            "For 60 minutes from purchase: no per-message coin charge in active chats; anti-spam limits remain. Match filters and other products cost extra.",
            "60 минут с покупки без платы за сообщения в активном чате. Антиспам остаётся. Фильтры и покупки оплачиваются отдельно.",
        )
    return L(
        user,
        "پیش‌نمایش رایگان است. بسته‌ها و قالب‌ها یک‌بار خریدند؛ هدیهٔ نمایشی یک‌بار ارسال می‌شود. امنیت، گزارش و بلاک همیشه رایگان‌اند.",
        "Preview is free. Packs/themes are one-time unlocks; visual gifts are delivered once. Safety/report/block are always free.",
        "Просмотр бесплатный. Пакеты и шаблоны покупаются один раз; подарок отправляется один раз. Безопасность бесплатна.",
    )


async def item(user, sku):
    if sku not in C.PRODUCTS:
        raise C.ShopError("NOT_FOUND")
    async with db.session_scope() as s:
        price = (await C.config(s))[sku]
        owned = (
            sku not in C.TIMED and sku not in C.VISUAL and await C.owns(s, user.id, sku)
        )
    rows = [
        [
            button(
                L(user, "👀 پیش‌نمایش رایگان", "👀 Free preview", "👀 Просмотр"),
                f"m:preview:{sku}",
            )
        ]
    ]
    if not owned and sku not in C.VISUAL:
        rows.append(
            [
                button(
                    L(
                        user,
                        "ادامه و دیدن قیمت نهایی",
                        "Continue to final price",
                        "К итоговой цене",
                    ),
                    f"m:quote:{sku}",
                )
            ]
        )
    if sku in C.TIMED:
        rows.append(
            [
                button(
                    L(user, "🎁 هدیه بده", "🎁 Give as gift", "🎁 Подарить"),
                    f"m:recipients:{sku}",
                )
            ]
        )
    if sku in C.VISUAL:
        rows.append(
            [
                button(
                    L(
                        user,
                        "برای هم‌چت فعلی",
                        "For current partner",
                        "Текущему собеседнику",
                    ),
                    f"m:recipients:{sku}",
                )
            ]
        )
    if owned and sku == "starters":
        rows.append(
            [
                button(
                    L(user, "استفاده از سؤال‌ها", "Use starters", "Использовать темы"),
                    "m:prompts",
                )
            ]
        )
    if owned and sku == "games":
        rows.append(
            [
                button(
                    L(user, "شروع بازی در چت", "Play in current chat", "Играть в чате"),
                    "m:fun",
                )
            ]
        )
    if sku.startswith("theme_"):
        rows.append(
            [
                button(
                    L(
                        user,
                        "کارت ساده (بدون قالب)",
                        "Plain card (no theme)",
                        "Карточка без шаблона",
                    ),
                    "m:theme:plain",
                )
            ]
        )
    rows.append(back(user))
    await say(
        user,
        C.name(user, sku)
        + f"\n{price} 🪙\n"
        + description(user, sku)
        + (
            L(user, "\n✓ دسترسی فعال داری", "\n✓ Already unlocked", "\n✓ Уже доступно")
            if owned
            else ""
        ),
        rows,
    )


PROMPTS = [
    (
        "اگر یک روز کاملاً آزاد داشتی چه کار می‌کردی؟",
        "What would you do with a completely free day?",
        "Как бы вы провели совершенно свободный день?",
    ),
    (
        "کدام فیلم یا کتاب نگاهت را عوض کرد؟",
        "Which book or film changed your perspective?",
        "Какая книга или фильм изменили ваш взгляд?",
    ),
    (
        "کدام مهارت را دوست داری یاد بگیری؟",
        "What skill would you like to learn?",
        "Какому навыку хотите научиться?",
    ),
    (
        "یک خاطرهٔ بامزه بدون اسم و مشخصات تعریف کن.",
        "Share a funny memory without names or personal details.",
        "Расскажите смешную историю без имён и личных данных.",
    ),
    (
        "اگر می‌توانستی یک اختراع بسازی، چه بود؟",
        "What would you invent?",
        "Что бы вы изобрели?",
    ),
    (
        "یک عادت کوچک که حالت را بهتر می‌کند چیست؟",
        "What small habit makes your day better?",
        "Какая маленькая привычка улучшает ваш день?",
    ),
    (
        "دوست داری پایان کدام داستان را عوض کنی؟",
        "Which story ending would you change?",
        "Финал какой истории вы бы изменили?",
    ),
    (
        "با سه کلمه یک سفر خیالی بسازیم.",
        "Let's invent an imaginary trip using three words.",
        "Придумаем путешествие из трёх слов.",
    ),
]


async def preview(user, sku):
    if sku not in C.PRODUCTS:
        raise C.ShopError("NOT_FOUND")
    text = L(
        user,
        "👀 نمونهٔ رایگان — چیزی خریداری نشد.\n",
        "👀 Free preview — no purchase.\n",
        "👀 Бесплатный просмотр — без покупки.\n",
    )
    rows = []
    if sku.startswith("theme_"):
        symbol = "🌊" if sku.endswith("ocean") else "🌿"
        text += f"{symbol} ━━━━━━━━━━ {symbol}\n{escape(user.display_name)}\n" + L(
            user,
            "این یک کارت نمونه است.",
            "This is a sample card.",
            "Это образец карточки.",
        )
        rows.append(
            [
                button(
                    L(
                        user,
                        "استفاده از این قالب",
                        "Use this theme",
                        "Применить шаблон",
                    ),
                    f"m:theme:{sku[6:]}",
                )
            ]
        )
    elif sku == "games":
        text += "▫️ ▫️ ▫️\n▫️ ❌ ▫️\n▫️ ▫️ ⭕\n" + L(
            user,
            "دوز با دکمه‌های ۳×۳ و چالش رمز مشترک؛ مهمان رایگان و یک میزبانی آزمایشی رایگان.",
            "Inline 3×3 tic-tac-toe and a shared-code challenge. Guests free; one free hosted trial.",
            "Игра 3×3 и совместная загадка. Гость бесплатно; одна пробная игра.",
        )
        rows.append(
            [
                button(
                    L(
                        user,
                        "بازی آزمایشی در چت",
                        "Try in current chat",
                        "Попробовать в чате",
                    ),
                    "m:fun",
                )
            ]
        )
    elif sku == "starters":
        text += PROMPTS[0][{"fa": 0, "en": 1, "ru": 2}.get(user.language, 0)]
        rows.append(
            [
                button(
                    L(
                        user,
                        "استفاده در چت فعلی",
                        "Use in current chat",
                        "Использовать в чате",
                    ),
                    "m:prompt:0",
                )
            ]
        )
    else:
        text += C.name(user, sku) + "\n" + description(user, sku)
    await say(user, text, rows + [back(user)])


async def confirm_quote(user, q):
    text = (
        C.name(user, q.sku)
        + f"\n{q.coins} 🪙\n"
        + L(
            user,
            "قیمت نهایی؛ اعتبار تأیید ۵ دقیقه.\n",
            "Final price; confirm within 5 minutes.\n",
            "Итоговая цена; подтвердите за 5 минут.\n",
        )
        + description(user, q.sku)
    )
    if q.recipient_id != user.id:
        async with db.session_scope() as s:
            recipient = await s.get(db.User, q.recipient_id)
        text += (
            "\n🎁 "
            + L(user, "گیرنده: ", "Recipient: ", "Получатель: ")
            + escape(recipient.display_name)
        )
    if q.detail.get("coupon"):
        text += f"\n🎟 {q.detail['percent']}%"
    await say(
        user,
        text,
        [
            [
                button(
                    L(
                        user,
                        "✅ تأیید و پرداخت",
                        "✅ Confirm & pay",
                        "✅ Подтвердить оплату",
                    )
                    + f" {q.coins} 🪙",
                    f"m:pay:{q.id}",
                )
            ],
            back(user),
        ],
    )


async def recipients(user, sku):
    rows = []
    async with db.session_scope() as s:
        chat = await service.active_chat_for(s, user.id)
        ids = []
        if chat:
            ids.append(chat.user_b_id if chat.user_a_id == user.id else chat.user_a_id)
        if sku not in C.VISUAL:
            ids += list(
                (
                    await s.execute(
                        select(db.Friend.friend_id)
                        .where(db.Friend.user_id == user.id)
                        .limit(15)
                    )
                ).scalars()
            )
        for uid in dict.fromkeys(ids):
            target = await s.get(db.User, uid)
            if (
                target
                and await C.allowed_pair(s, user, target)
                and target.platform == user.platform
            ):
                rows.append(
                    [button("🎁 " + target.display_name[:25], f"m:gift:{sku}:{uid}")]
                )
    await say(
        user,
        L(
            user,
            "گیرنده را از هم‌چت فعلی یا دوستان همین پلتفرم انتخاب کن.",
            "Choose your current partner or a friend on this platform.",
            "Выберите собеседника или друга на этой платформе.",
        ),
        rows + [back(user)],
    )


async def deliver_purchase(user, q, fresh):
    if not fresh:
        await say(
            user,
            L(
                user,
                "این خرید قبلاً ثبت شده؛ دوباره کسر نشد.",
                "Already processed; no second charge.",
                "Уже обработано; повторного списания нет.",
            ),
        )
        return
    if q.sku in C.VISUAL or q.recipient_id != user.id:
        async with db.session_scope() as s:
            target = await s.get(db.User, q.recipient_id)
        adp = RUNTIME.get(f"adapter_{target.platform}")
        try:
            if not adp:
                raise PlatformError("sendMessage", 503, "unavailable")
            await adp.send_message(
                target.platform_user_id,
                "🎁 "
                + C.name(target, q.sku)
                + "\n"
                + L(
                    target,
                    "یک هدیه از هم‌چت یا دوستت دریافت کردی.\nهدیهٔ نمایشی ارزش نقدی ندارد؛ اشتراک و پاس از همین لحظه فعال‌اند.",
                    "A gift from your partner or friend. Visual gifts have no cash value; memberships/passes start now.",
                    "Подарок от собеседника или друга. Без денежной стоимости; подписки и пропуска уже активны.",
                ),
            )
            async with db.session_scope() as s:
                current = (
                    await s.execute(
                        select(db.ShopQuote)
                        .where(db.ShopQuote.id == q.id)
                        .with_for_update()
                    )
                ).scalar_one()
                if current.status == "paid":
                    current.status = "delivered"
        except PlatformError:
            if q.sku in C.VISUAL:
                await C.refund_visual(q.id)
                await say(
                    user,
                    L(
                        user,
                        "هدیه تحویل نشد؛ سکه‌ها برگشت.",
                        "Gift not delivered; coins refunded.",
                        "Подарок не доставлен; монеты возвращены.",
                    ),
                )
                return
            # Timed gift is durably active even if its notification fails.
    await say(
        user,
        L(
            user,
            "✅ خرید ثبت و فعال شد. برای استفاده از دکمه‌های زیر برو.",
            "✅ Purchase active. Choose how to use it below.",
            "✅ Покупка активна. Выберите действие.",
        ),
        [
            [
                button(
                    L(user, "🎮 سرگرمی در چت", "🎮 Chat activities", "🎮 Развлечения"),
                    "m:fun",
                ),
                button(L(user, "🔗 لینک‌ها", "🔗 Links", "🔗 Ссылки"), "m:links"),
            ],
            back(user),
        ],
    )


async def fun(user):
    rows = [
        [
            button(
                L(
                    user,
                    "❌⭕ دعوت به دوز",
                    "❌⭕ Invite to tic-tac-toe",
                    "❌⭕ Пригласить в игру",
                ),
                "m:game:ttt",
            )
        ],
        [
            button(
                L(
                    user,
                    "🧩 چالش همکاری",
                    "🧩 Co-op challenge",
                    "🧩 Совместная загадка",
                ),
                "m:game:coop",
            )
        ],
        [
            button(
                L(user, "💡 سؤال رایگان", "💡 Free starter", "💡 Бесплатный вопрос"),
                "m:prompt:0",
            ),
            button(
                L(user, "💬 سؤال‌های بیشتر", "💬 More starters", "💬 Больше вопросов"),
                "m:prompts",
            ),
        ],
        [
            button(
                L(user, "🌹 هدیه نمایشی", "🌹 Visual gifts", "🌹 Виртуальные подарки"),
                "m:visual",
            )
        ],
        back(user),
    ]
    await say(
        user,
        L(
            user,
            "🎮 سرگرمی داخل گفتگو\nبازی فقط با پذیرش طرف مقابل شروع می‌شود. مهمان هزینه‌ای ندارد. یک بازی میزبانی رایگان است.",
            "🎮 Chat activities. Games start with consent; guests free. One free hosted trial.",
            "🎮 Развлечения. Игра только после согласия; гостю бесплатно. Одна пробная игра.",
        ),
        rows,
    )


TOPICS = {
    "questions": ("پرسش‌ها", "Questions", "Вопросы"),
    "feedback": ("بازخورد", "Feedback", "Отзывы"),
    "friends": ("دوستان", "Friends", "Друзья"),
    "work": ("کار", "Work", "Работа"),
    "ideas": ("ایده‌ها", "Ideas", "Идеи"),
    "other": ("سایر", "Other", "Другое"),
}


async def links(user):
    from . import bot

    async with db.session_scope() as s:
        rows = list(
            (
                await s.execute(
                    select(db.TopicLink)
                    .where(db.TopicLink.owner_id == user.id)
                    .limit(6)
                )
            ).scalars()
        )
        unlocked = await C.owns(s, user.id, "links")
    text = L(
        user,
        "🔗 لینک‌های جدا با موضوع مشخص؛ فرستنده ناشناس می‌ماند. حذف لینک فوراً آن را غیرفعال می‌کند.\n",
        "🔗 Separate topic links; senders remain anonymous. Deletion disables a link immediately.\n",
        "🔗 Тематические ссылки; отправители анонимны. Удаление сразу отключает ссылку.\n",
    )
    keyboard = []
    for row in rows:
        text += (
            "\n"
            + L(user, *TOPICS[row.label])
            + ":\n"
            + bot.deep_link(user, "t" + row.id)
            + "\n"
        )
        keyboard.append(
            [
                button(
                    L(user, "حذف ", "Delete ", "Удалить ")
                    + L(user, *TOPICS[row.label]),
                    f"m:unlink:{row.id}",
                )
            ]
        )
    if unlocked:
        keyboard += [
            [button("＋ " + L(user, *label), f"m:newlink:{key}")]
            for key, label in TOPICS.items()
        ]
    else:
        keyboard.append(
            [
                button(
                    L(
                        user,
                        "بازکردن شش لینک موضوعی",
                        "Unlock six topic links",
                        "Открыть шесть ссылок",
                    ),
                    "m:item:links",
                )
            ]
        )
    await say(user, text, keyboard + [back(user)])


async def handle(user, data):
    from . import bot, reconnect, social_games

    try:
        if data.startswith("g:"):
            _, gid, version, action = data.split(":")
            await social_games.act(user.id, gid, int(version), action)
            await social_games.publish(gid)
            return
        parts = data.split(":")
        action = parts[1]
        arg = parts[2] if len(parts) > 2 else ""
        if action == "home":
            await home(user)
        elif action == "coins":
            await bot.show_coin_store(user)
        elif action == "cat":
            await catalog(user, arg)
        elif action == "item":
            await item(user, arg)
        elif action == "preview":
            await preview(user, arg)
        elif action == "quote":
            await confirm_quote(user, await C.quote(user.id, arg))
        elif action == "gift":
            await confirm_quote(user, await C.quote(user.id, arg, int(parts[3])))
        elif action == "recipients":
            await recipients(user, arg)
        elif action == "pay":
            q, fresh = await C.purchase(user.id, arg)
            await deliver_purchase(user, q, fresh)
        elif action == "gifts":
            await say(
                user,
                L(
                    user,
                    "هدیه را انتخاب کن؛ قیمت و گیرنده پیش از پرداخت تأیید می‌شوند.",
                    "Choose a gift; confirm recipient and price before payment.",
                    "Выберите подарок; получатель и цена до оплаты.",
                ),
                [[button(C.name(user, sku), f"m:recipients:{sku}")] for sku in C.TIMED]
                + [back(user)],
            )
        elif action == "visual":
            await say(
                user,
                L(
                    user,
                    "هدیهٔ نمایشی با قیمت ثابت؛ قابل تبدیل به پول یا سکه نیست.",
                    "Fixed-price visual gifts; no cash-out or coin value.",
                    "Подарки по фиксированной цене; без обмена на деньги.",
                ),
                [
                    [button(C.name(user, sku), f"m:item:{sku}")]
                    for sku in ("rose", "coffee", "book")
                ]
                + [back(user)],
            )
        elif action == "fun":
            await fun(user)
        elif action == "game":
            gid = await social_games.invite(user.id, arg)
            await social_games.publish(gid)
        elif action == "prompts":
            await say(
                user,
                L(
                    user,
                    "سؤال را انتخاب کن؛ برای هر دو نفر ارسال می‌شود.",
                    "Choose a starter to send to both players.",
                    "Выберите вопрос для обоих.",
                ),
                [[button(f"💡 {i+1}", f"m:prompt:{i}")] for i in range(len(PROMPTS))]
                + [back(user)],
            )
        elif action == "prompt":
            idx = int(arg)
            if not 0 <= idx < len(PROMPTS):
                raise C.ShopError("NOT_FOUND")
            from .limits import message_limiter

            if not message_limiter.allow(f"starter:{user.id}", 6, 60):
                raise C.ShopError("LIMIT")
            async with db.session_scope() as s:
                chat = await service.active_chat_for(s, user.id)
                if not chat:
                    raise C.ShopError("STALE")
                if idx and not await C.owns(s, user.id, "starters"):
                    raise C.ShopError("LOCKED")
                people = [
                    await s.get(db.User, uid)
                    for uid in (chat.user_a_id, chat.user_b_id)
                ]
            for person in people:
                adp = bot.adapter(person.platform)
                if adp:
                    await adp.send_message(
                        person.platform_user_id, "💡 " + L(person, *PROMPTS[idx])
                    )
        elif action == "theme":
            async with db.session_scope() as s:
                await C.lock_users(s, [user.id])
                if arg not in {"plain", "ocean", "garden"}:
                    raise C.ShopError("NOT_FOUND")
                if arg != "plain" and not await C.owns(s, user.id, "theme_" + arg):
                    raise C.ShopError("LOCKED")
                (await C.profile(s, user.id)).theme = arg
            await say(
                user,
                L(
                    user,
                    "✅ قالب کارت ذخیره شد.",
                    "✅ Card theme saved.",
                    "✅ Шаблон сохранён.",
                ),
            )
        elif action == "links":
            await links(user)
        elif action == "newlink":
            await C.create_topic(user.id, arg)
            await links(user)
        elif action == "unlink":
            async with db.session_scope() as s:
                await s.execute(
                    delete(db.TopicLink).where(
                        db.TopicLink.id == arg, db.TopicLink.owner_id == user.id
                    )
                )
            await links(user)
        elif action == "promo":
            async with db.session_scope() as s:
                (await s.get(db.User, user.id)).state = {"step": "m_promo"}
            await say(
                user,
                L(
                    user,
                    "کد را بفرست. برای انصراف «لغو» یا /cancel را بزن.",
                    "Send your code. /cancel to stop.",
                    "Отправьте код. /cancel для отмены.",
                ),
                [[button(L(user, "لغو", "Cancel", "Отмена"), "m:cancel")]],
            )
        elif action == "cancel":
            async with db.session_scope() as s:
                (await s.get(db.User, user.id)).state = None
            await home(user)
        elif action == "first":
            async with db.session_scope() as s:
                cfg = await C.config(s)
            await say(
                user,
                L(
                    user,
                    "🌱 بستهٔ شروع اولین خرید\n",
                    "🌱 First-purchase pack\n",
                    "🌱 Стартовый пакет\n",
                )
                + f"{cfg['firstBaseCoins']} 🪙 + {cfg['firstBonusPct']}%\n"
                + L(
                    user,
                    "قیمت واقعی در فاکتور بعدی نمایش داده می‌شود؛ ارسال فاکتور به معنی پرداخت نیست.",
                    "The invoice shows the real price; creating it does not charge you.",
                    "Точная цена в счёте; создание счёта не списывает средства.",
                ),
                [
                    [
                        button(
                            L(user, "دیدن فاکتور", "View invoice", "Посмотреть счёт"),
                            "m:firstinvoice",
                        )
                    ],
                    back(user),
                ],
            )
        elif action == "firstinvoice":
            async with db.session_scope() as s:
                app = await service.get_setting(s, "app")
                gate = (
                    (
                        app.get("paymentsTelegramEnabled", True)
                        and bool(settings.telegram_bot_token)
                    )
                    if user.platform == "telegram"
                    else (
                        app.get("paymentsBaleEnabled", False)
                        and bool(settings.bale_provider_token)
                    )
                )
            if not gate:
                raise C.ShopError("UNAVAILABLE")
            order = await C.first_order(user.id)
            await bot.adapter(user.platform).send_invoice(
                user.platform_user_id,
                L(user, "بسته شروع", "Welcome pack", "Стартовый пакет"),
                f"{order.coins} coins",
                "order:" + order.id,
                [{"label": f"{order.coins} coins", "amount": order.amount}],
            )
        elif action == "preferences":
            async with db.session_scope() as s:
                await C.lock_users(s, [user.id])
                p = await C.profile(s, user.id)
                text = f"↩️ {'✅' if p.allow_reconnect else '❌'}\n🎁 {'✅' if p.allow_gifts else '❌'}"
            await say(
                user,
                text,
                [
                    [
                        button(
                            L(
                                user,
                                "تغییر اجازهٔ درخواست اتصال مجدد",
                                "Toggle reconnect requests",
                                "Запросы соединения вкл/выкл",
                            ),
                            "m:toggle:reconnect",
                        )
                    ],
                    [
                        button(
                            L(
                                user,
                                "تغییر اجازهٔ هدیه",
                                "Toggle gifts",
                                "Подарки вкл/выкл",
                            ),
                            "m:toggle:gifts",
                        )
                    ],
                    back(user),
                ],
            )
        elif action == "toggle":
            async with db.session_scope() as s:
                await C.lock_users(s, [user.id])
                p = await C.profile(s, user.id)
                if arg == "reconnect":
                    p.allow_reconnect = not p.allow_reconnect
                elif arg == "gifts":
                    p.allow_gifts = not p.allow_gifts
            await handle(user, "m:preferences")
        elif action == "recon":
            r = await reconnect.prepare(user.id, int(arg))
            await say(
                user,
                L(
                    user,
                    "↩️ درخواست اتصال مجدد\nفقط یک درخواست بدون متن برای هم‌چت قبلی ارسال می‌شود. پذیرش برای او اختیاری و رایگان است. هزینه فقط پس از پذیرش و برقراری اتصال کسر می‌شود. اعتبار درخواست: ۲ دقیقه.\n",
                    "↩️ One neutral reconnect request. Your partner can accept freely. Only you pay after acceptance and connection. Expires in 2 minutes.\n",
                    "↩️ Один запрос без сообщения. Собеседник может отказаться. Платите только вы после соединения. Срок: 2 минуты.\n",
                )
                + f"{r.coins} 🪙"
                + L(
                    user,
                    f"\nارزش بر مبنای نرخ ریالی فروشگاه: {r.toman_equivalent:,} تومان؛ پرداخت از کیف سکه است.",
                    "\nPaid from your coin wallet; Stars top-ups have their own displayed price.",
                    "\nОплата из кошелька монет; цена Stars указана при пополнении.",
                ),
                [
                    [
                        button(
                            L(
                                user,
                                "ارسال درخواست با این هزینه",
                                "Send request at this price",
                                "Отправить запрос по этой цене",
                            ),
                            f"m:rc:{r.id}",
                        )
                    ],
                    back(user),
                ],
            )
        elif action == "rc":
            await reconnect.confirm(user.id, arg)
            await say(
                user,
                L(
                    user,
                    "درخواست ارسال شد. فعلاً چیزی کسر نشد؛ رد یا بی‌پاسخ‌ماندن رایگان است.",
                    "Request sent. No charge yet; declining or no response is free.",
                    "Запрос отправлен. Пока списания нет; отказ бесплатен.",
                ),
            )
        elif action in {"ra", "rd"}:
            cid = await reconnect.decide(user.id, arg, action == "ra")
            if not cid:
                await say(
                    user,
                    L(user, "درخواست بسته شد.", "Request closed.", "Запрос закрыт."),
                )
        else:
            raise C.ShopError("NOT_FOUND")
    except (C.ShopError, ValueError, IndexError) as exc:
        await error(user, str(exc))
    except PlatformError:
        await error(user, "UNAVAILABLE")


async def text_flow(user, text):
    if text.strip().lower() in {"لغو", "cancel", "/cancel"}:
        await handle(user, "m:cancel")
        return
    from .limits import message_limiter

    if not message_limiter.allow(f"promo:{user.id}", 5, 300):
        await error(user, "PROMO_INVALID")
        return
    try:
        kind, value = await C.redeem(user.id, text)
        async with db.session_scope() as s:
            (await s.get(db.User, user.id)).state = None
        await say(
            user,
            (
                L(
                    user,
                    "✅ سکهٔ هدیه واریز شد: ",
                    "✅ Gift coins credited: ",
                    "✅ Монеты начислены: ",
                )
                if kind == "gift"
                else L(
                    user,
                    "✅ تخفیف خرید بعدی فعال شد: ",
                    "✅ Next purchase discount: ",
                    "✅ Скидка на следующую покупку: ",
                )
            )
            + str(value)
            + (" 🪙" if kind == "gift" else "%"),
        )
    except C.ShopError as exc:
        await error(user, str(exc))
