"""Trilingual UI strings (fa default, en, ru) — v2, user-friendly."""
from __future__ import annotations

S = {
    # ------------------------------------------------------------ intro ----
    "welcome": {"fa": "👋 سلام! به چت ناشناس کانار خوش آمدید.\nاینجا می‌توانید بدون افشای هویت خود، با افراد جدید گفتگو کنید.\n\nبرای شروع، زبان مورد نظر خود را انتخاب کنید:",
                "en": "👋 Hi! Welcome to Kanar Anonymous Chat.\nHere you can talk to new people without revealing your identity.\n\nTo get started, please choose your language:",
                "ru": "👋 Привет! Добро пожаловать в анонимный чат Kanar.\nЗдесь вы можете общаться с новыми людьми, не раскрывая личность.\n\nДля начала выберите язык:"},
    "choose_lang": {"fa": "🌐 زبان خود را انتخاب کنید:", "en": "🌐 Choose your language:", "ru": "🌐 Выберите язык:"},
    "rules_title": {"fa": "📜 قوانین ربات چت ناشناس", "en": "📜 Anonymous Chat Rules", "ru": "📜 Правила анонимного чата"},

    # ------------------------------------------------------------ rules ----
    "rules_body": {
        "fa": "📜 قوانین استفاده از ربات چت ناشناس\n\n۱. احترام متقابل الزامی است؛ توهین، تهدید و آزار ممنوع است.\n۲. اشتراک‌گذاری اطلاعات تماس (شماره تلفن، لینک، آیدی شبکه‌های اجتماعی) ممنوع است.\n۳. محتوای غیراخلاقی، خشونت‌آمیز یا غیرقانونی ممنوع است.\n۴. ارسال اسپم، تبلیغات ناخواسته و پیام‌های تکراری ممنوع است؛ همچنین افشای اطلاعات شخصی دیگران بدون رضایت آن‌ها مجاز نیست.\n۵. گزارش تخلف: در صورت مشاهده هرگونه آزار، تهدید یا نقض قوانین، از دکمه «گزارش» یا دستور /report استفاده کنید.\n۶. نقض قوانین به اخطار، محدودیت موقت و سپس مسدودی تدریجی منجر می‌شود.\n\nبا ادامه استفاده از ربات، این قوانین را می‌پذیرید.",
        "en": "📜 Rules for using the Anonymous Chat bot\n\n1. Mutual respect is mandatory; insults, threats and harassment are forbidden.\n2. Sharing contact information (phone numbers, links, social media IDs) is forbidden.\n3. Immoral, violent or illegal content is forbidden.\n4. Spam, unwanted advertising and repeated messages are forbidden; disclosing other people's personal information without their consent is also not allowed.\n5. Reporting violations: if you see any harassment, threats or rule-breaking, use the \"Report\" button or the /report command.\n6. Breaking the rules leads to a warning, temporary restriction and then progressive bans.\n\nBy continuing to use the bot, you accept these rules.",
        "ru": "📜 Правила использования бота анонимного чата\n\n1. Взаимное уважение обязательно; оскорбления, угрозы и домогательства запрещены.\n2. Передача контактных данных (телефоны, ссылки, соцсети) запрещена.\n3. Непристойный, жестокий или незаконный контент запрещён.\n4. Спам, нежелательная реклама и повторяющиеся сообщения запрещены; раскрытие личных данных других без согласия также не допускается.\n5. Сообщить о нарушении: при домогательствах, угрозах или нарушении правил используйте кнопку «Жалоба» или /report.\n6. Нарушения ведут к предупреждению, ограничению и затем блокировке.\n\nПродолжая пользоваться ботом, вы принимаете эти правила."},
    "accept_rules": {"fa": "لطفاً قوانین بالا را با دقت بخوانید. برای ادامه باید آن‌ها را بپذیرید:", "en": "Please read the rules above carefully. You must accept them to continue:", "ru": "Внимательно прочитайте правила выше. Для продолжения их нужно принять:"},
    "accept": {"fa": "✅ قوانین را می‌پذیرم", "en": "✅ I accept the rules", "ru": "✅ Принимаю правила"},

    # ------------------------------------------------------ registration ----
    "ask_name": {"fa": "😊 قدم اولِ آشنایی!\nنام نمایشی خود را وارد کنید (۳ تا ۲۰ کاراکتر).\nاین نام برای هم‌چت‌های ناشناس شما نمایش داده می‌شود:",
                 "en": "😊 First step!\nEnter your display name (3-20 characters).\nThis name will be shown to your anonymous chat partners:",
                 "ru": "😊 Первый шаг!\nВведите отображаемое имя (3–20 символов).\nЭто имя увидят ваши анонимные собеседники:"},
    "ask_age": {"fa": "🎂 عالی! حالا سن خود را به عدد وارد کنید (بین ۱۳ تا ۶۰):",
                "en": "🎂 Great! Now enter your age as a number (between 13 and 60):",
                "ru": "🎂 Отлично! Теперь введите возраст числом (от 13 до 60):"},
    "ask_gender": {"fa": "جنسیت خود را انتخاب کنید:", "en": "Select your gender:", "ru": "Выберите пол:"},
    "girl": {"fa": "👧 دختر", "en": "👧 Girl", "ru": "👧 Девочка"},
    "boy": {"fa": "👦 پسر", "en": "👦 Boy", "ru": "👦 Мальчик"},
    "reg_done": {"fa": "🎉 ثبت‌نام شما کامل شد!\n\n🎁 {coins} سکه خوش‌آمدگویی به کیف شما اضافه شد.\nحالا می‌توانید اولین هم‌چت ناشناس خود را پیدا کنید. موفق باشید!",
                 "en": "🎉 Registration complete!\n\n🎁 You received {coins} welcome coins in your wallet.\nNow you can find your first anonymous chat partner. Good luck!",
                 "ru": "🎉 Регистрация завершена!\n\n🎁 Вы получили {coins} приветственных монет.\nТеперь можно найти первого анонимного собеседника. Удачи!"},

    # ----------------------------------------------------------- main ----
    "main_menu": {"fa": "🏠 منوی اصلی کانار\nاز دکمه‌های زیر استفاده کنید:",
                  "en": "🏠 Kanar main menu\nUse the buttons below:",
                  "ru": "🏠 Главное меню Kanar\nИспользуйте кнопки ниже:"},
    "btn_search": {"fa": "🔍 جستجوی هم‌چت", "en": "🔍 Find partner", "ru": "🔍 Поиск собеседника"},
    "btn_profile": {"fa": "👤 پروفایل و تنظیمات", "en": "👤 Profile & settings", "ru": "👤 Профиль и настройки"},
    "btn_coins": {"fa": "🪙 سکه‌ها و کیف پول", "en": "🪙 Coins & wallet", "ru": "🪙 Монеты и кошелёк"},
    "btn_store": {"fa": "🛒 فروشگاه سکه", "en": "🛒 Coin store", "ru": "🛒 Магазин монет"},
    "btn_bonus": {"fa": "🎁 پاداش روزانه", "en": "🎁 Daily bonus", "ru": "🎁 Ежедневный бонус"},
    "btn_myref": {"fa": "👥 دعوت دوستان", "en": "👥 Invite friends", "ru": "👥 Приглашения"},
    "btn_language": {"fa": "🌐 تغییر زبان", "en": "🌐 Change language", "ru": "🌐 Сменить язык"},
    "btn_help": {"fa": "❓ راهنمای کامل", "en": "❓ Full guide", "ru": "❓ Полное руководство"},
    "btn_admin": {"fa": "🛡 پنل مدیریت", "en": "🛡 Admin panel", "ru": "🛡 Панель админа"},
    "btn_back": {"fa": "🏠 بازگشت به منوی اصلی", "en": "🏠 Back to main menu", "ru": "🏠 В главное меню"},
    "btn_gift": {"fa": "🎁 هدیه به هم‌چت", "en": "🎁 Gift to partner", "ru": "🎁 Подарок собеседнику"},

    # ----------------------------------------------------------- chat ----
    "searching": {"fa": "🔍 در حال جستجوی هم‌چت ناشناس...\n\n⏳ این کار ممکن است تا ۵ دقیقه طول بکشد.\n🌍 هم‌چت شما می‌تواند از تلگرام یا بله باشد.\n\n💡 اگر پشیمان شدید، دکمه «توقف» را بزنید.",
                  "en": "🔍 Searching for an anonymous partner...\n\n⏳ This may take up to 5 minutes.\n🌍 Your partner may be on Telegram or Bale.\n\n💡 Changed your mind? Tap \"Stop\".",
                  "ru": "🔍 Идёт поиск анонимного собеседника...\n\n⏳ Это может занять до 5 минут.\n🌍 Собеседник может быть из Telegram или Bale.\n\n💡 Передумали? Нажмите «Стоп»."},
    "btn_stop_search": {"fa": "⏹ توقف جستجو", "en": "⏹ Stop search", "ru": "⏹ Остановить поиск"},
    "search_cancel": {"fa": "✅ جستجو لغو شد. هر وقت آماده بودید دوباره تلاش کنید.", "en": "✅ Search cancelled. Try again whenever you're ready.", "ru": "✅ Поиск отменён. Попробуйте снова, когда будете готовы."},
    "search_timeout": {"fa": "⌛ متأسفانه در این لحظه هم‌چت مناسبی پیدا نشد.\nشاید کمی بعد کاربران بیشتری آنلاین شوند؛ دوباره تلاش کنید!",
                       "en": "⌛ Unfortunately no suitable partner was found right now.\nMore users may come online soon — try again!",
                       "ru": "⌛ К сожалению, подходящий собеседник не найден.\nСкоро может быть больше пользователей — попробуйте ещё раз!"},
    "matched": {"fa": "✅ اتصال برقرار شد!\n\n👤 نام: {name}\n🎂 سن: {age}\n🚻 {gender}\n📝 {bio}\n\n💬 هر چیزی که بفرستید (متن، عکس، ویس، ویدیو و...) به‌صورت کاملاً ناشناس برای طرف مقابل ارسال می‌شود.\n🎯 برای رفتن به کاربر بعدی، دکمه «کاربر بعدی» را بزنید.",
                "en": "✅ Connected!\n\n👤 Name: {name}\n🎂 Age: {age}\n🚻 {gender}\n📝 {bio}\n\n💬 Everything you send (text, photos, voice, video...) is relayed completely anonymously.\n🎯 Use the \"Next\" button to move on to another user.",
                "ru": "✅ Соединение установлено!\n\n👤 Имя: {name}\n🎂 Возраст: {age}\n🚻 {gender}\n📝 {bio}\n\n💬 Всё, что вы отправите, пересылается полностью анонимно.\n🎯 Кнопка «Следующий» — сменить собеседника."},
    "btn_next": {"fa": "⏭ کاربر بعدی", "en": "⏭ Next", "ru": "⏭ Следующий"},
    "btn_end": {"fa": "🚪 پایان گفتگو", "en": "🚪 End chat", "ru": "🚪 Завершить"},
    "btn_report": {"fa": "🚩 گزارش", "en": "🚩 Report", "ru": "🚩 Жалоба"},
    "btn_block": {"fa": "⛔ مسدود", "en": "⛔ Block", "ru": "⛔ Заблокировать"},
    "partner_left": {"fa": "🔚 طرف مقابل گفتگو را ترک کرد.\nبرای پیدا کردن هم‌چت جدید، دکمه زیر را بزنید:", "en": "🔚 Your partner left the chat.\nTap below to find a new partner:", "ru": "🔚 Собеседник покинул чат.\nНажмите ниже, чтобы найти нового:"},
    "chat_ended": {"fa": "🔚 گفتگو پایان یافت. امیدواریم لحظات خوبی داشته باشید!", "en": "🔚 Chat ended. Hope you had a good time!", "ru": "🔚 Чат завершён. Надеемся, было хорошо!"},
    "chat_idle_timeout": {"fa": "⌛ گفتگو به دلیل ۱۵ دقیقه بی‌فعالیت، به‌صورت خودکار پایان یافت.\nهر وقت خواستید، هم‌چت جدیدی پیدا کنید:", "en": "⌛ The chat ended automatically after 15 minutes of inactivity.\nFind a new partner whenever you want:", "ru": "⌛ Чат завершён после 15 минут бездействия.\nНайдите нового собеседника, когда захотите:"},
    "not_in_chat": {"fa": "🤔 در حال حاضر در هیچ گفتگویی نیستید.\nبرای شروع یک گفتگوی ناشناس جدید، دکمه زیر را بزنید:",
                    "en": "🤔 You are not in any chat right now.\nTap below to start a new anonymous chat:",
                    "ru": "🤔 Сейчас вы не в чате.\nНажмите ниже, чтобы начать новый анонимный чат:"},
    "gender_filter_q": {"fa": "🎯 دنبال چه کسی باشید؟\n\n▪️ گزینه «همه» رایگان است.\n▪️ فیلترهای جنسیت {cost} سکه هزینه دارند که فقط در صورت اتصال موفق پرداخت می‌شود (جستجوی ناموفق رایگان است).",
                        "en": "🎯 Who should we look for?\n\n▪️ \"Everyone\" is free.\n▪️ Gender filters cost {cost} coins, charged only on a successful match (failed searches are free).",
                        "ru": "🎯 Кого искать?\n\n▪️ «Все» — бесплатно.\n▪️ Фильтры пола — {cost} монет, только при успешном соединении."},
    "gf_everyone": {"fa": "👥 همه (رایگان)", "en": "👥 Everyone (free)", "ru": "👥 Все (бесплатно)"},
    "gf_girls": {"fa": "👧 فقط دختر", "en": "👧 Girls only", "ru": "👧 Только девочки"},
    "gf_boys": {"fa": "👦 فقط پسر", "en": "👦 Boys only", "ru": "👦 Только мальчики"},

    # ------------------------------------------------------ rejections ----
    "msg_rejected_profanity": {"fa": "🚫 پیام شما به دلیل استفاده از الفاظ نامناسب ارسال نشد.\n⚠️ تکرار این رفتار باعث محدودیت موقت و سپس مسدود شدن حساب می‌شود. لطفاً با احترام گفتگو کنید.",
                               "en": "🚫 Your message was not delivered due to inappropriate language.\n⚠️ Repeating this will lead to restrictions and then a ban. Please chat respectfully.",
                               "ru": "🚫 Сообщение не отправлено из-за нецензурной лексики.\n⚠️ Повтор ведёт к ограничениям и блокировке."},
    "msg_rejected_contact": {"fa": "🚫 اشتراک‌گذاری اطلاعات تماس (لینک، شماره تلفن، ایمیل، آیدی شبکه‌های اجتماعی) طبق قوانین ممنوع است.\n💡 امنیت شما برای ما مهم است؛ گفتگو فقط داخل همین ربات انجام می‌شود.",
                             "en": "🚫 Sharing contact information (links, phone numbers, emails, social IDs) is against the rules.\n💡 Your safety matters; conversations stay inside this bot.",
                             "ru": "🚫 Передача контактных данных запрещена правилами.\n💡 Общение происходит только внутри бота."},
    "msg_rejected_media": {"fa": "🚫 ارسال این نوع رسانه در حال حاضر توسط مدیریت غیرفعال شده است. لطفاً بعداً دوباره تلاش کنید.",
                           "en": "🚫 This media type is currently disabled by the administration. Please try again later.",
                           "ru": "🚫 Этот тип медиа сейчас отключён администрацией."},
    "msg_rejected_cross": {"fa": "🚫 این نوع رسانه بین تلگرام و بله پشتیبانی نمی‌شود.\n💡 هم‌چت شما در پلتفرم دیگری است؛ متن، عکس، ویس، ویدیو و فایل قابل ارسال هستند.",
                           "en": "🚫 This media type is not supported between Telegram and Bale.\n💡 Your partner is on another platform; text, photos, voice, video and files work.",
                           "ru": "🚫 Этот тип медиа не поддерживается между платформами.\n💡 Текст, фото, голос, видео и файлы работают."},
    "msg_rejected_media_big": {"fa": "🚫 حجم فایل برای انتقال بین‌پلتفرمی بیش از حد مجاز است (حداکثر ۲۰ مگابایت).",
                               "en": "🚫 File too large for cross-platform transfer (max 20 MB).",
                               "ru": "🚫 Файл слишком велик (макс. 20 МБ)."},
    "no_coins": {"fa": "🪙 سکه کافی برای این کار ندارید.\n\nراه‌های دریافت سکه:\n🎁 پاداش روزانه (منوی اصلی)\n👥 دعوت دوستان (۲۰ سکه برای هر دوست)\n🎂 ثبت تاریخ تولد\n🛒 خرید از فروشگاه", "en": "🪙 Not enough coins.\n\nWays to get coins:\n🎁 Daily bonus\n👥 Invite friends (20 each)\n🎂 Set your birthday\n🛒 Buy from the store", "ru": "🪙 Недостаточно монет.\n\n🎁 Ежедневный бонус\n👥 Приглашения\n🎂 День рождения\n🛒 Магазин"},

    # ---------------------------------------------------------- economy ----
    "coins_balance": {"fa": "🪙 موجودی کیف پول شما: {coins} سکه\n\nبا سکه‌ها می‌توانید:\n🎯 در جستجو فیلتر جنسیت فعال کنید\n🎁 به هم‌چت خود هدیه بدهید", "en": "🪙 Your balance: {coins} coins\n\nWith coins you can:\n🎯 Use gender filters\n🎁 Gift your partner", "ru": "🪙 Баланс: {coins} монет\n\nЗа монеты можно:\n🎯 Фильтры пола\n🎁 Подарки"},
    "bonus_ok": {"fa": "🎁 پاداش روزانه شما: {coins} سکه دریافت شد!\n⏰ فردا می‌توانید دوباره پاداش بگیرید.", "en": "🎁 Daily bonus received: {coins} coins!\n⏰ Come back tomorrow for more.", "ru": "🎁 Ежедневный бонус: {coins} монет!\n⏰ Возвращайтесь завтра."},
    "bonus_already": {"fa": "😊 شما پاداش امروز را قبلاً دریافت کرده‌اید.\n⏰ پاداش فردا به وقت تهران فعال می‌شود.\nفعلاً می‌توانید با جستجوی هم‌چت یا دعوت دوستان سکه کسب کنید!",
                      "en": "😊 You already claimed today's bonus.\n⏰ Tomorrow's bonus unlocks at midnight (Tehran time).\nMeanwhile, earn coins by chatting or inviting friends!",
                      "ru": "😊 Вы уже получили сегодняшний бонус.\n⏰ Завтрашний откроется в полночь по Тегерану."},
    "referral_info": {"fa": "👥 دعوت دوستان، سکه بی‌نهایت!\n\n🔗 لینک اختصاصی شما:\n{link}\n\n✨ نحوه کار:\n۱. لینک بالا را برای دوستان خود بفرستید.\n۲. دوست شما با این لینک ربات را شروع و ثبت‌نام را کامل می‌کند.\n۳. پس از اولین گفتگوی موفق او، هر دو نفر {coins} سکه جایزه می‌گیرید!",
                      "en": "👥 Invite friends, earn endless coins!\n\n🔗 Your personal link:\n{link}\n\n✨ How it works:\n1. Share the link with your friends.\n2. Your friend starts the bot with it and completes registration.\n3. After their first successful chat, you BOTH get {coins} coins!",
                      "ru": "👥 Приглашайте друзей!\n\n🔗 Ваша ссылка:\n{link}\n\n✨ Как это работает:\n1. Отправьте ссылку друзьям.\n2. Друг регистрируется по ней.\n3. После его первого чата вы ОБА получите {coins} монет!"},
    "referral_count": {"fa": "🏆 تعداد دعوت‌های موفق شما: {count} نفر", "en": "🏆 Successful referrals: {count}", "ru": "🏆 Успешных приглашений: {count}"},
    "store_title": {"fa": "🛒 فروشگاه سکه کانار\n\nسکه‌ها برای فیلتر جنسیت، هدیه دادن به هم‌چت‌ها و... استفاده می‌شوند.\nبسته مورد نظر خود را انتخاب کنید:", "en": "🛒 Kanar coin store\n\nCoins are used for gender filters, gifting partners and more.\nChoose a package:", "ru": "🛒 Магазин монет Kanar\n\nВыберите пакет:"},
    "pay_stars": {"fa": "⭐ {stars} ستاره = {coins} سکه", "en": "⭐ {stars} Stars = {coins} coins", "ru": "⭐ {stars} звёзд = {coins} монет"},
    "pay_bale": {"fa": "💳 {toman} تومان = {coins} سکه", "en": "💳 {toman} Toman = {coins} coins", "ru": "💳 {toman} томанов = {coins} монет"},
    "pay_unavailable": {"fa": "🔌 درگاه پرداخت این پلتفرم در حال حاضر فعال نیست.\nلطفاً بعداً تلاش کنید یا با پشتیبانی تماس بگیرید.", "en": "🔌 The payment gateway on this platform is currently unavailable.\nPlease try later or contact support.", "ru": "🔌 Платёжный шлюз недоступен.\nПопробуйте позже."},
    "pay_ok": {"fa": "✅ پرداخت با موفقیت انجام شد و {coins} سکه به کیف پول شما اضافه شد.\nاز خرید شما سپاسگزاریم! 🎉", "en": "✅ Payment successful — {coins} coins added to your wallet.\nThank you! 🎉", "ru": "✅ Оплата успешна — {coins} монет зачислено. Спасибо! 🎉"},
    "paysupport": {"fa": "💳 پشتیبانی پرداخت\n\nاگر پرداخت انجام شده اما سکه‌ها به حساب شما اضافه نشده‌اند:\n۱. چند دقیقه صبر کنید و موجودی را با /coins بررسی کنید.\n۲. اگر مشکل ادامه داشت، با دکمه زیر یک تیکت پشتیبانی بسازید تا کارشناسان ما در سریع‌ترین زمان رسیدگی کنند.",
                   "en": "💳 Payment support\n\nIf your payment went through but coins were not added:\n1. Wait a few minutes and check /coins.\n2. If the problem persists, create a support ticket below — our team will handle it ASAP.",
                   "ru": "💳 Поддержка оплат\n\nЕсли оплата прошла, но монеты не зачислены:\n1. Подождите и проверьте /coins.\n2. Если проблема осталась, создайте тикет ниже."},
    "pay_support_btn": {"fa": "📩 ساخت تیکت پشتیبانی پرداخت", "en": "📩 Create payment support ticket", "ru": "📩 Создать тикет"},

    # --------------------------------------------------------- profile ----
    "profile_card": {"fa": "👤 پروفایل شما\n\n📛 نام: {name}\n🎂 سن: {age}\n🚻 جنسیت: {gender}\n📝 بیوگرافی: {bio}\n🪙 موجودی: {coins} سکه\n{birthday}",
                     "en": "👤 Your profile\n\n📛 Name: {name}\n🎂 Age: {age}\n🚻 Gender: {gender}\n📝 Bio: {bio}\n🪙 Balance: {coins} coins\n{birthday}",
                     "ru": "👤 Ваш профиль\n\n📛 Имя: {name}\n🎂 Возраст: {age}\n🚻 Пол: {gender}\n📝 Био: {bio}\n🪙 Баланс: {coins} монет\n{birthday}"},
    "birthday_set": {"fa": "🎂 تولد: {date}", "en": "🎂 Birthday: {date}", "ru": "🎂 День рождения: {date}"},
    "birthday_not_set": {"fa": "🎂 تولد: ثبت نشده — با ثبت آن سکه جایزه بگیرید! 🎁", "en": "🎂 Birthday: not set — set it for a reward! 🎁", "ru": "🎂 День рождения: не задан — задайте за награду! 🎁"},
    "profile_menu_title": {"fa": "⚙️ مدیریت پروفایل\nاز دکمه‌های زیر انتخاب کنید:", "en": "⚙️ Profile management\nChoose an option:", "ru": "⚙️ Управление профилем:"},
    "btn_edit_name": {"fa": "📛 تغییر نام (۱۰ سکه)", "en": "📛 Change name (10 coins)", "ru": "📛 Имя (10 монет)"},
    "btn_edit_age": {"fa": "🎂 تغییر سن (۱۰ سکه)", "en": "🎂 Change age (10 coins)", "ru": "🎂 Возраст (10 монет)"},
    "btn_edit_bio": {"fa": "📝 تغییر بیوگرافی", "en": "📝 Change bio", "ru": "📝 Био"},
    "btn_edit_photo": {"fa": "📷 تغییر عکس پروفایل", "en": "📷 Change photo", "ru": "📷 Фото"},
    "btn_birthday": {"fa": "🎁 ثبت تاریخ تولد (پاداش)", "en": "🎁 Set birthday (reward)", "ru": "🎁 День рождения (награда)"},
    "btn_anon_link": {"fa": "🔗 لینک ناشناس من", "en": "🔗 My anonymous link", "ru": "🔗 Анонимная ссылка"},
    "btn_link_account": {"fa": "🔗 اتصال حساب تلگرام/بله (+۵۰ سکه)", "en": "🔗 Link Telegram/Bale (+50 coins)", "ru": "🔗 Связать аккаунты (+50 монет)"},
    "btn_direct_msg": {"fa": "💌 پیام ناشناس به کاربر", "en": "💌 Anonymous message to user", "ru": "💌 Анонимное сообщение"},
    "ask_bio": {"fa": "📝 بیوگرافی جدید خود را بفرستید (حداکثر ۱۰۰ کاراکتر).\nاین متن برای هم‌چت‌های شما نمایش داده می‌شود. برای رد کردن /skip بفرستید:",
                "en": "📝 Send your new bio (max 100 chars).\nIt is shown to your chat partners. /skip to skip:",
                "ru": "📝 Новая биография (макс. 100 символов). /skip — пропустить:"},
    "ask_photo": {"fa": "📷 عکس پروفایل جدید را بفرستید.\n⚠️ این عکس فقط در چت‌های همین پلتفرم نمایش داده می‌شود و هرگز به پلتفرم دیگر منتقل نمی‌شود. /skip برای رد کردن:",
                  "en": "📷 Send your new profile photo.\n⚠️ Shown only on your platform, never transferred. /skip to skip:",
                  "ru": "📷 Новое фото. Показывается только на вашей платформе. /skip — пропустить:"},
    "profile_updated": {"fa": "✅ پروفایل شما با موفقیت به‌روزرسانی شد.", "en": "✅ Profile updated.", "ru": "✅ Профиль обновлён."},
    "ask_new_name": {"fa": "📛 نام نمایشی جدید خود را وارد کنید (۳ تا ۲۰ کاراکتر):\n💰 هزینه این تغییر: {cost} سکه",
                     "en": "📛 New display name (3-20 chars):\n💰 Cost: {cost} coins",
                     "ru": "📛 Новое имя (3–20):\n💰 Стоимость: {cost} монет"},
    "ask_new_age": {"fa": "🎂 سن جدید خود را وارد کنید (۱۳ تا ۶۰):\n💰 هزینه این تغییر: {cost} سکه",
                    "en": "🎂 New age (13-60):\n💰 Cost: {cost} coins",
                    "ru": "🎂 Новый возраст (13–60):\n💰 Стоимость: {cost} монет"},
    "name_age_changed": {"fa": "✅ تغییر با موفقیت انجام شد و {cost} سکه از کیف شما کسر شد.", "en": "✅ Changed successfully; {cost} coins were deducted.", "ru": "✅ Изменено; списано {cost} монет."},

    # --------------------------------------------------------- birthday ----
    "birthday_intro": {"fa": "🎂 تاریخ تولد خود را ثبت کنید!\n\n✨ مزایای ثبت تولد:\n🎁 یک‌بار {coins} سکه جایزه می‌گیرید.\n🎈 در روز تولدتان پیام تبریک دریافت می‌کنید.\n📊 سن شما به‌صورت خودکار و دقیق به‌روز می‌شود.\n\n📅 تقویم شما: شمسی\nماه تولد خود را انتخاب کنید:",
                       "en": "🎂 Register your birthday!\n\n✨ Benefits:\n🎁 One-time {coins} coin reward.\n🎈 Birthday congratulations from us.\n📊 Your age updates automatically and precisely.\n\n📅 Your calendar: Gregorian\nSelect your birth month:",
                       "ru": "🎂 Зарегистрируйте день рождения!\n\n✨ Преимущества:\n🎁 Разовая награда {coins} монет.\n🎈 Поздравление в ваш день.\n📊 Возраст обновляется автоматически.\n\n📅 Календарь: григорианский\nВыберите месяц:"},
    "birthday_ask_day": {"fa": "روز تولد خود در ماه {month} را به عدد وارد کنید (۱ تا {maxday}):",
                         "en": "Enter your birth day in {month} (1 to {maxday}):",
                         "ru": "Введите день рождения в {month} (1–{maxday}):"},
    "birthday_saved": {"fa": "🎉 عالی! تاریخ تولد شما ثبت شد.\n🎁 {coins} سکه جایزه به کیف شما اضافه شد. سپاس!",
                       "en": "🎉 Great! Birthday saved.\n🎁 {coins} reward coins added. Thanks!",
                       "ru": "🎉 Отлично! День рождения сохранён.\n🎁 {coins} монет добавлено."},
    "birthday_greet": {"fa": "🎂🎉 {name} عزیز، تولدت مبارک!\nآروزومندیم سالی پر از شادی و سکه داشته باشید! 🎁", "en": "🎂🎉 Happy birthday, {name}!\nWishing you a year full of joy and coins! 🎁", "ru": "🎂🎉 С днём рождения, {name}!\nЖелаем года радости и монет! 🎁"},

    # ----------------------------------------------------- anon link (4) ----
    "anon_link_info": {"fa": "🔗 لینک ناشناس شما آماده است!\n\n{link}\n\n✨ این لینک چطور کار می‌کند؟\nاین لینک را در استوری، بیوگرافی یا گروه‌ها به اشتراک بگذارید. هر کسی با این لینک وارد ربات شود، می‌تواند برای شما پیام ناشناس بفرستد.\n\n🎭 شما هرگز نمی‌فهمید پیام از چه کسی آمده، اما فرستنده می‌داند پیامش به شما رسیده — همین رمز و رازِ آن است!\n\n💡 هر وقت بخواهید می‌توانید از بخش پروفایل، لینک را دوباره دریافت کنید.",
                       "en": "🔗 Your anonymous link is ready!\n\n{link}\n\n✨ How does it work?\nShare this link in your story, bio or groups. Anyone who opens the bot with it can send you an anonymous message.\n\n🎭 You'll never know who sent it, but the sender knows it reached you — that's the mystery!\n\n💡 You can retrieve the link from your profile anytime.",
                       "ru": "🔗 Ваша анонимная ссылка готова!\n\n{link}\n\n✨ Как это работает?\nПоделитесь ссылкой в сторис, био или группах. Любой, кто откроет бота по ней, сможет отправить вам анонимное сообщение.\n\n🎭 Вы никогда не узнаете отправителя, но он знает, что сообщение дошло — в этом интрига!\n\n💡 Ссылку можно получить в профиле в любой момент."},
    "anon_msg_intro": {"fa": "💌 شما از طریق لینک ناشناس وارد شده‌اید.\n\nپیام خود را بنویسید تا به‌صورت کاملاً ناشناس برای صاحب این لینک ارسال شود.\n🎭 صاحب لینک هرگز متوجه هویت شما نخواهد شد.",
                       "en": "💌 You entered via an anonymous link.\n\nWrite your message and it will be delivered completely anonymously to the link owner.\n🎭 The owner will never know your identity.",
                       "ru": "💌 Вы вошли по анонимной ссылке.\n\nНапишите сообщение — оно будет доставлено владельцу ссылки полностью анонимно.\n🎭 Владелец никогда не узнает, кто вы."},
    "anon_msg_sent": {"fa": "✅ پیام ناشناس شما با موفقیت ارسال شد!\n🎭 صاحب لینک آن را دریافت کرد، اما هرگز نمی‌داند از چه کسی آمده.",
                      "en": "✅ Your anonymous message was delivered!\n🎭 The owner received it, but will never know who sent it.",
                      "ru": "✅ Ваше сообщение доставлено!\n🎭 Владелец получил его, но не узнает отправителя."},
    "anon_msg_received": {"fa": "📬 شما یک پیام ناشناس جدید دریافت کردید!\n\n____________________\n{text}\n____________________\n\n🎭 هویت فرستنده کاملاً محفوظ است. اگر پیام آزاردهنده بود، آن را گزارش کنید.",
                          "en": "📬 You received a new anonymous message!\n\n____________________\n{text}\n____________________\n\n🎭 The sender's identity is fully protected. If it's abusive, please report it.",
                          "ru": "📬 Вам пришло анонимное сообщение!\n\n____________________\n{text}\n____________________\n\n🎭 Личность отправителя скрыта. Если это оскорбительно — сообщите."},

    # ---------------------------------------------------- direct msg (14) ----
    "direct_msg_intro": {"fa": "💌 پیام ناشناس به کاربر\n\nآیدی عددی یا یوزرنیم کاربر مورد نظر را بفرستید:\n▪️ مثال یوزرنیم: @username\n▪️ مثال آیدی عددی: 123456789\n\n🎭 ربات تلاش می‌کند پیام شما را به‌صورت کاملاً ناشناس به آن کاربر برساند. نتیجه ارسال به شما اعلام نمی‌شود تا هیجان ماجرا حفظ شود!",
                        "en": "💌 Anonymous message to a user\n\nSend the target's numeric ID or username:\n▪️ @username\n▪️ 123456789\n\n🎭 The bot will try to deliver your message anonymously. The result is NOT shown to you — to keep the suspense!",
                        "ru": "💌 Анонимное сообщение пользователю\n\nОтправьте ID или @username:\n▪️ @username\n▪️ 123456789\n\n🎭 Бот попытается доставить сообщение анонимно. Результат скрыт — для интриги!"},
    "direct_msg_neutral": {"fa": "🎭 پیام شما ثبت شد!\nاگر کاربر مورد نظر عضو ربات باشد، پیام شما به‌صورت کاملاً ناشناس به او می‌رسد.\nنتیجه ارسال (موفق یا ناموفق) طبق طراحی به شما نمایش داده نمی‌شود تا هیجان حفظ شود. 🤫",
                          "en": "🎭 Your message has been recorded!\nIf the target is a member of the bot, it will be delivered anonymously.\nThe delivery result is intentionally hidden — suspense! 🤫",
                          "ru": "🎭 Сообщение записано!\nЕсли пользователь — участник бота, оно будет доставлено анонимно.\nРезультат намеренно скрыт. 🤫"},
    "ask_msg_text": {"fa": "✍️ حالا متن پیام ناشناس خود را بنویسید:", "en": "✍️ Now write your anonymous message:", "ru": "✍️ Теперь напишите анонимное сообщение:"},
    "direct_msg_self": {"fa": "🙂 نمی‌توانید به خودتان پیام ناشناس بفرستید!", "en": "🙂 You cannot send an anonymous message to yourself!", "ru": "🙂 Нельзя отправить сообщение себе!"},

    # -------------------------------------------------- linking accounts ----
    "link_intro": {"fa": "🔗 اتصال حساب تلگرام و بله\n\nبا اتصال حساب‌ها:\n🎁 هر دو حساب شما {coins} سکه پاداش می‌گیرند (مجموعاً {total} سکه).\n⚙️ امکانات جدید فعال می‌شود.\n\nبرای شروع، روی دکمه زیر بزنید تا لینک اتصال اختصاصی شما ساخته شود:",
                   "en": "🔗 Link your Telegram and Bale accounts\n\nBy linking:\n🎁 Both accounts get {coins} coins each ({total} total).\n⚙️ New features unlock.\n\nTap below to generate your personal linking link:",
                   "ru": "🔗 Свяжите аккаунты Telegram и Bale\n\nПри связывании:\n🎁 Каждый аккаунт получит {coins} монет ({total} всего).\n\nНажмите ниже, чтобы создать ссылку:"},
    "link_gen": {"fa": "🔗 ساخت لینک اتصال", "en": "🔗 Generate linking link", "ru": "🔗 Создать ссылку"},
    "link_show": {"fa": "✅ لینک اتصال شما ساخته شد!\n\n{link}\n\n📋 مراحل بعدی:\n۱. این لینک را در پلتفرم دیگر خود باز کنید (اگر الان در تلگرام هستید، آن را در بله باز کنید و برعکس).\n۲. در آن پلتفرم، دکمه «تأیید اتصال» را بزنید.\n۳. هر دو حساب {coins} سکه جایزه می‌گیرند! 🎉\n\n⚠️ این لینک فقط یک‌بار قابل استفاده است و ۱۰ دقیقه اعتبار دارد.",
                  "en": "✅ Your linking link was created!\n\n{link}\n\n📋 Next steps:\n1. Open this link on your OTHER platform (on Bale if you're on Telegram now, and vice versa).\n2. There, tap \"Confirm link\".\n3. Both accounts receive {coins} coins! 🎉\n\n⚠️ Single-use, valid for 10 minutes.",
                  "ru": "✅ Ссылка создана!\n\n{link}\n\n📋 Дальше:\n1. Откройте ссылку на ДРУГОЙ платформе.\n2. Нажмите «Подтвердить связь».\n3. Оба аккаунта получат {coins} монет! 🎉\n\n⚠️ Одноразовая, действует 10 минут."},
    "link_confirm_q": {"fa": "🔗 یک درخواست اتصال حساب از {platform} دریافت شده است.\n\nاگر این درخواست از خود شماست (لینک را خودتان ساخته‌اید)، اتصال را تأیید کنید. پس از تأیید، هر دو حساب شما {coins} سکه جایزه می‌گیرند.",
                       "en": "🔗 An account-linking request from {platform} was received.\n\nIf this is your own request (you generated the link), confirm it. Both accounts will receive {coins} coins.",
                       "ru": "🔗 Получен запрос на связь с {platform}.\n\nЕсли это ваш запрос (вы создавали ссылку), подтвердите его. Оба аккаунта получат {coins} монет."},
    "link_confirm_btn": {"fa": "✅ تأیید اتصال", "en": "✅ Confirm link", "ru": "✅ Подтвердить"},
    "link_done": {"fa": "🎉 اتصال حساب‌ها با موفقیت انجام شد!\n🎁 {coins} سکه به هر دو حساب شما اضافه شد.\n\nحساب {other_platform} شما به این حساب متصل است.",
                  "en": "🎉 Accounts linked!\n🎁 {coins} coins added to both accounts.\n\nYour {other_platform} account is connected to this one.",
                  "ru": "🎉 Аккаунты связаны!\n🎁 {coins} монет на оба аккаунта.\n\nВаш аккаунт {other_platform} связан с этим."},
    "link_already": {"fa": "⚠️ این حساب قبلاً به یک حساب دیگر متصل شده است.\nبرای اتصال جدید، با پشتیبانی تماس بگیرید.", "en": "⚠️ This account is already linked to another.\nContact support to relink.", "ru": "⚠️ Аккаунт уже связан с другим."},
    "link_same_platform": {"fa": "⚠️ اتصال فقط بین دو پلتفرم مختلف (تلگرام ↔ بله) امکان‌پذیر است.\nلینک را در پلتفرم دیگر خود باز کنید.", "en": "⚠️ Linking works only between different platforms (Telegram ↔ Bale).\nOpen the link on your other platform.", "ru": "⚠️ Связка только между разными платформами (Telegram ↔ Bale)."},
    "link_expired": {"fa": "⌛ لینک اتصال منقضی شده است. از پروفایل، لینک جدیدی بسازید.", "en": "⌛ The linking link expired. Generate a new one from your profile.", "ru": "⌛ Ссылка истекла. Создайте новую в профиле."},

    # ----------------------------------------------------------- gifts ----
    "gift_title": {"fa": "🎁 هدیه به هم‌چت\n\nمی‌توانید با پرداخت، سکه هدیه به هم‌چت خود اهدا کنید!\nنمونه: با پرداخت {price}، {coins} سکه به هم‌چت شما هدیه داده می‌شود.\n\nبسته هدیه خود را انتخاب کنید:",
                   "en": "🎁 Gift to your partner\n\nPay to gift coins to your chat partner!\nExample: pay {price} to gift {coins} coins.\n\nChoose a gift package:",
                   "ru": "🎁 Подарок собеседнику\n\nОплатите и подарите монеты!\nПример: {price} = {coins} монет.\n\nВыберите пакет:"},
    "gift_sent_payer": {"fa": "🎁 هدیه شما با موفقیت اهدا شد!\nهم‌چت شما {coins} سکه کادو گرفت. چه کار قشنگی! ✨", "en": "🎁 Your gift was delivered!\nYour partner received {coins} coins. How sweet! ✨", "ru": "🎁 Подарок доставлен!\nСобеседник получил {coins} монет. ✨"},
    "gift_received": {"fa": "🎉 هدیه دریافت کردید!\nهم‌چت ناشناس شما برایتان {coins} سکه هدیه فرستاد. چقدر مهربان! 🥰", "en": "🎉 You received a gift!\nYour anonymous partner gifted you {coins} coins. 🥰", "ru": "🎉 Вам подарок!\nАнонимный собеседник подарил {coins} монет. 🥰"},

    # -------------------------------------------------------- invites ----
    "invite_msg": {"fa": "🔔 یک کاربر ناشناس در انتظار اتصال است!\n\nآیا آماده یک گفتگوی ناشناس جدید هستید؟ با یک کلیک به صف جستجو بپیوندید:",
                   "en": "🔔 An anonymous user is waiting to connect!\n\nReady for a new anonymous chat? Join the search queue with one tap:",
                   "ru": "🔔 Анонимный пользователь ждёт связи!\n\nГотовы к новому чату? Присоединяйтесь одним нажатием:"},
    "invite_btn": {"fa": "⚡ اتصال سریع", "en": "⚡ Quick connect", "ru": "⚡ Быстрое соединение"},
    "invite_joined": {"fa": "✅ به صف جستجو اضافه شدید. در حال یافتن هم‌چت...", "en": "✅ You joined the search queue. Finding a partner...", "ru": "✅ Вы в очереди. Ищем собеседника..."},

    # --------------------------------------------------------- banning ----
    "banned_until": {"fa": "⛔ حساب شما موقتاً مسدود شده است.\n\n📌 دلیل: {reason}\n⏰ تا زمان: {until}\n\nاگر فکر می‌کنید اشتباهی رخ داده، پس از پایان مهلت با پشتیبانی تماس بگیرید.",
                     "en": "⛔ Your account is temporarily banned.\n\n📌 Reason: {reason}\n⏰ Until: {until}\n\nIf you believe this is a mistake, contact support after the deadline.",
                     "ru": "⛔ Аккаунт временно заблокирован.\n\n📌 Причина: {reason}\n⏰ До: {until}"},
    "banned_perm": {"fa": "⛔ حساب شما به‌صورت دائم مسدود شده است.\n\n📌 دلیل: {reason}\n\nبرای اعتراض به این تصمیم، دستور /appeal را بفرستید و متن اعتراض خود را بنویسید.",
                    "en": "⛔ Your account is permanently banned.\n\n📌 Reason: {reason}\n\nTo appeal, send /appeal with your appeal text.",
                    "ru": "⛔ Аккаунт заблокирован навсегда.\n\n📌 Причина: {reason}\n\nДля апелляции отправьте /appeal."},
    "appeal_sent": {"fa": "✅ اعتراض شما ثبت شد.\nتیم بررسی، پرونده شما را مطالعه می‌کند و نتیجه از طریق همین ربات اعلام می‌شود. از صبر شما متشکریم.", "en": "✅ Appeal recorded.\nThe team will review your case and announce the result via this bot. Thank you.", "ru": "✅ Апелляция записана.\nРезультат сообщат через бота."},
    "no_active_ban": {"fa": "✅ شما هیچ مسدودی دائمی فعالی ندارید که نیاز به اعتراض داشته باشد.", "en": "✅ You have no active permanent ban to appeal.", "ru": "✅ Нет активной перманентной блокировки."},
    "ask_appeal": {"fa": "✍️ متن اعتراض خود را بنویسید:\n(توضیح دهید چرا فکر می‌کنید مسدودی شما اشتباه بوده است)", "en": "✍️ Write your appeal:\n(Explain why you believe the ban was a mistake)", "ru": "✍️ Напишите текст апелляции:"},

    # --------------------------------------------------------- reports ----
    "report_done": {"fa": "🚩 گزارش شما با موفقیت ثبت شد.\n\nتیم بررسی به‌سرعت آن را بررسی می‌کند. همچنین اگر ۳ کاربر مختلف در ۲۴ ساعت از یک نفر گزارش بدهند، آن کاربر به‌طور خودکار مسدود می‌شود.\n🙏 از کمک شما به پاک‌ماندن فضای گفتگو سپاسگزاریم.",
                    "en": "🚩 Report recorded.\nThe team will check it soon. Also, 3 different reports on one user within 24h trigger an automatic ban.\n🙏 Thanks for keeping the community clean.",
                    "ru": "🚩 Жалоба записана.\nЕсли 3 разных пользователя пожалуются за 24 часа — автоблокировка.\n🙏 Спасибо за помощь!"},
    "report_dup": {"fa": "ℹ️ شما قبلاً این کاربر را در همین گفتگو گزارش کرده‌اید.\nنگران نباشید؛ گزارش شما در صف بررسی است.", "en": "ℹ️ You already reported this user in this chat.\nYour report is in the review queue.", "ru": "ℹ️ Вы уже жаловались на этого пользователя."},
    "block_done": {"fa": "⛔ کاربر مسدود شد.\nاز این پس هرگز — تحت هیچ شرایطی — با این کاربر هم‌چت نخواهید شد.", "en": "⛔ User blocked.\nYou will never be matched with them again.", "ru": "⛔ Пользователь заблокирован навсегда."},
    "blocked_by_partner": {"fa": "⛔ طرف مقابل شما را مسدود کرد و گفتگو پایان یافت.", "en": "⛔ Your partner blocked you and the chat ended.", "ru": "⛔ Собеседник заблокировал вас."},

    # ----------------------------------------------------------- misc ----
    "maintenance": {"fa": "🔧 در حال حاضر ربات در حالت تعمیر و نگهداری است.\nکمی بعد دوباره سر بزنید — به‌زودی برمی‌گردیم! 🙏", "en": "🔧 The bot is under maintenance.\nPlease check back soon! 🙏", "ru": "🔧 Бот на обслуживании. Скоро вернёмся! 🙏"},
    "support_sent": {"fa": "✅ تیکت پشتیبانی شما با شماره {ticket} ثبت شد.\nکارشناسان ما در اسرع وقت پاسخ می‌دهند. پاسخ در همین گفتگو ارسال می‌شود.", "en": "✅ Support ticket {ticket} created.\nWe'll reply ASAP right here.", "ru": "✅ Тикет {ticket} создан. Ответ придёт сюда."},
    "invalid_input": {"fa": "❌ ورودی نامعتبر است. لطفاً دوباره و طبق راهنما تلاش کنید.", "en": "❌ Invalid input. Please try again following the guide.", "ru": "❌ Неверный ввод. Попробуйте снова."},
    "language_set": {"fa": "✅ زبان به فارسی تغییر یافت.", "en": "✅ Language changed to English.", "ru": "✅ Язык изменён на русский."},
    "default_avatar": {"fa": "🖼 (عکس پروفایل بین تلگرام و بله منتقل نمی‌شود؛ آواتار پیش‌فرض نمایش داده می‌شود)", "en": "🖼 (profile photos are never transferred between platforms; default avatar shown)", "ru": "🖼 (фото не передаются между платформами)"},
    "yes": {"fa": "بله", "en": "Yes", "ru": "Да"},
    "no": {"fa": "لغو", "en": "Cancel", "ru": "Отмена"},

    # ------------------------------------------------------------ help ----
    "help_body": {"fa": "❓ راهنمای کامل ربات چت ناشناس کانار\n\n🎯 چطور کار می‌کند؟\nروی «جستجوی هم‌چت» بزنید تا ربات شما را با یک کاربر تصادفی — از تلگرام یا بله — به‌صورت کاملاً ناشناس وصل کند. هویت شما محفوظ می‌ماند؛ فقط نام، سن، جنسیت و بیوگرافی‌ای که خودتان وارد کرده‌اید نمایش داده می‌شود.\n\n🪙 سکه‌ها به چه دردی می‌خورند؟\n• ارسال هر پیام ۱ سکه هزینه دارد (دستورات و دکمه‌ها رایگان‌اند).\n• فیلتر جنسیت در جستجو ۵ سکه (فقط در صورت اتصال موفق).\n• سکه رایگان: پاداش روزانه، دعوت دوستان، ثبت تاریخ تولد و اتصال حساب‌ها!\n\n💌 امکانات ویژه:\n• لینک ناشناس: لینک خود را به اشتراک بگذارید تا دیگران برایتان پیام ناشناس بفرستند (/link).\n• پیام ناشناس به کاربر: با یوزرنیم یا آیدی برای هر عضوی پیام بفرستید (/msg).\n• هدیه: در حین گفتگو به هم‌چت خود سکه هدیه دهید (/gift)!\n• اتصال حساب: حساب تلگرام و بله خود را متصل کنید و ۵۰ سکه بگیرید.\n\n📋 دستورات:\n/search — جستجوی هم‌چت\n/next — کاربر بعدی\n/stop — توقف جستجو یا گفتگو\n/profile — پروفایل و تنظیمات\n/coins — موجودی سکه\n/store — فروشگاه\n/bonus — پاداش روزانه\n/referral — دعوت دوستان\n/link — لینک ناشناس\n/msg — پیام ناشناس به کاربر\n/gift — هدیه به هم‌چت\n/language — تغییر زبان\n/rules — قوانین\n/privacy — حریم خصوصی\n/paysupport — پشتیبانی پرداخت\n/report — گزارش تخلف\n/block — مسدودسازی\n/appeal — اعتراض به مسدودی دائمی\n/admin — پنل مدیریت (مدیران)\n\n💡 همه این کارها با دکمه‌های شیشه‌ای داخل منوها هم قابل انجام‌اند. شروع کنید!",
                  "en": "❓ Complete guide to Kanar Anonymous Chat\n\n🎯 How does it work?\nTap \"Find partner\" and the bot connects you with a random user — from Telegram or Bale — completely anonymously. Only the name, age, gender and bio you provided are shown.\n\n🪙 What are coins for?\n• Each message costs 1 coin (commands and buttons are free).\n• Gender filter costs 5 coins (only on successful match).\n• Free coins: daily bonus, inviting friends, setting your birthday and linking accounts!\n\n💌 Special features:\n• Anonymous link: share your link to receive anonymous messages (/link).\n• Direct anonymous message: message any member by @username or ID (/msg).\n• Gifts: send coins to your partner during a chat (/gift)!\n• Account linking: connect Telegram & Bale and get 50 coins.\n\n📋 Commands:\n/search /next /stop /profile /coins /store /bonus /referral /link /msg /gift /language /rules /privacy /paysupport /report /block /appeal /admin\n\n💡 Everything is also doable via the inline buttons in the menus. Enjoy!",
                  "ru": "❓ Полное руководство\n\n🎯 Как это работает?\nНажмите «Поиск» — бот соединит вас со случайным пользователем из Telegram или Bale анонимно. Видны только имя, возраст, пол и био.\n\n🪙 Монеты:\n• Сообщение — 1 монета (команды бесплатны).\n• Фильтр пола — 5 монет (при успехе).\n• Бесплатные: бонус, приглашения, день рождения, связка аккаунтов!\n\n💌 Возможности:\n• Анонимная ссылка (/link).\n• Прямое сообщение по @username или ID (/msg).\n• Подарки собеседнику (/gift)!\n• Связка Telegram и Bale — 50 монет.\n\n📋 Команды: /search /next /stop /profile /coins /store /bonus /referral /link /msg /gift /language /rules /privacy /paysupport /report /block /appeal /admin\n\n💡 Всё доступно через кнопки в меню."},
    "privacy_title": {"fa": "🔒 سیاست حریم خصوصی", "en": "🔒 Privacy Policy", "ru": "🔒 Политика конфиденциальности"},
    "privacy_body": {"fa": "• متن پیام‌ها حداکثر ۳۰ روز نگهداری و سپس به‌صورت خودکار حذف می‌شوند.\n• شناسه رسانه‌ها (نوع، file_id، زمان) ثبت می‌شود؛ محتوای فایل‌ها ذخیره نمی‌شود.\n• عکس پروفایل بین تلگرام و بله منتقل نمی‌شود؛ در چت بین‌پلتفرمی آواتار پیش‌فرض نمایش داده می‌شود.\n• آرشیو پشتیبان رمزنگاری‌شده ممکن است متن پیام‌های قدیمی‌تر از ۳۰ روز را در نسخه‌های قبلی خود حفظ کند.\n• دسترسی به تاریخچه فقط برای مالک (همه) و مدیران (گزارش‌شده‌ها) با ثبت رسید عملیات.",
                     "en": "• Message texts are kept for at most 30 days and then automatically deleted.\n• Media identifiers (type, file_id, time) are stored; file contents are not.\n• Profile photos are never transferred between Telegram and Bale.\n• Encrypted backup archives may retain text older than 30 days inside previous snapshots.\n• History access is Owner-only (all) and admins (reported chats only), always audited.",
                     "ru": "• Тексты хранятся максимум 30 суток, затем удаляются.\n• Хранятся только идентификаторы медиа.\n• Фото профиля не передаются между платформами.\n• Бэкапы могут содержать тексты старше 30 суток.\n• Доступ к истории: владелец (все) и админы (жалобы), с аудитом."},
}


def tr(lang: str, key: str, **kw) -> str:
    entry = S.get(key) or {}
    text = entry.get(lang) or entry.get("fa") or key
    if kw:
        for k, v in kw.items():
            text = text.replace("{" + k + "}", str(v))
    return text


LANG_NAMES = {"fa": "🇮🇷 فارسی", "en": "🇬🇧 English", "ru": "🇷🇺 Русский"}
GENDER_LABEL = {"male": {"fa": "👦 پسر", "en": "👦 Boy", "ru": "👦 Boy"},
                "female": {"fa": "👧 دختر", "en": "👧 Girl", "ru": "👧 Girl"}}
PLATFORM_LABEL = {"telegram": {"fa": "تلگرام", "en": "Telegram", "ru": "Telegram"},
                  "bale": {"fa": "بله", "en": "Bale", "ru": "Bale"}}
JALALI_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
                 "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
GREGORIAN_MONTHS = {"fa": ["ژانویه", "فوریه", "مارس", "آوریل", "مه", "ژوئن", "ژوئیه", "اوت",
                           "سپتامبر", "اکتبر", "نوامبر", "دسامبر"],
                    "en": ["January", "February", "March", "April", "May", "June", "July",
                           "August", "September", "October", "November", "December"],
                    "ru": ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль",
                           "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]}
