// Incremental modular screen: real API data, no mock revenue or automatic discounts.
export function createMonetizationScreen({ b, u, Te, Ke, Ae, ln, Bt }) {
  const labels = {
    vip30: "اشتراک VIP سی‌روزه",
    pass60: "پاس پیام ۶۰ دقیقه‌ای",
    starters: "بسته شروع مکالمه",
    games: "دوز و چالش همکاری",
    theme_ocean: "قالب اقیانوس",
    theme_garden: "قالب باغ",
    links: "شش لینک موضوعی",
    rose: "هدیه گل",
    coffee: "هدیه قهوه",
    book: "هدیه کتاب",
    reconnectToman: "قیمت پایه اتصال مجدد (تومان)",
    firstBaseCoins: "سکه پایه اولین خرید",
    firstBonusPct: "درصد سکه اضافه اولین خرید",
  };
  const el = (type, props, ...children) =>
    u.jsx(type, {
      ...props,
      ...(children.length
        ? { children: children.length === 1 ? children[0] : children }
        : {}),
    });
  return function MonetizationScreen() {
    const { hasPermission } = Bt(),
      notice = ln();
    const [data, setData] = b.useState(null),
      [cfg, setCfg] = b.useState(null),
      [error, setError] = b.useState(""),
      [busy, setBusy] = b.useState(false);
    const [promo, setPromo] = b.useState({
      code: "",
      percent: 10,
      gift_coins: 0,
      max_uses: 10,
      expires_at: "",
    });
    async function load() {
      try {
        const d = await Te("/monetization");
        setData(d);
        setCfg(d.settings);
        setError("");
      } catch (e) {
        setError(e.message);
      }
    }
    b.useEffect(() => {
      if (hasPermission("store.manage")) load();
    }, [hasPermission]);
    async function act(action) {
      if (busy) return;
      setBusy(true);
      setError("");
      try {
        await action();
        await load();
        notice.success("ذخیره شد");
      } catch (e) {
        setError(e.message);
      } finally {
        setBusy(false);
      }
    }
    const input = (label, value, onChange, props = {}) =>
      el(Ke, {
        label,
        value,
        onChange: (e) => onChange(e.target.value),
        ...props,
      });
    const section = (title, ...children) =>
      el(
        "section",
        { className: "kanar-card rounded-2xl p-5 space-y-4" },
        el("h2", { className: "text-base font-bold" }, title),
        ...children,
      );
    if (!hasPermission("store.manage"))
      return el("p", { role: "alert" }, "دسترسی مدیریت فروشگاه لازم است.");
    return el(
      "div",
      { className: "space-y-5" },
      el("h1", { className: "text-xl font-bold" }, "محصولات، اشتراک و کدها"),
      el(
        "p",
        { className: "text-sm leading-7" },
        "همه قیمت‌ها سکه‌اند، جز پایهٔ اتصال مجدد که تومان است و با نرخ فروشگاه به سکه تبدیل و گرد می‌شود. VIP شامل بازی، شروع مکالمه، لینک و قالب است؛ پیام رایگان فقط با پاس جدا. تمدید خودکار نداریم.",
      ),
      error && el("p", { role: "alert", className: "text-rose-300" }, error),
      cfg &&
        section(
          "قیمت‌گذاری و توقف فروش",
          el(
            "label",
            { className: "flex gap-2" },
            el("input", {
              type: "checkbox",
              checked: cfg.enabled,
              onChange: (e) => setCfg({ ...cfg, enabled: e.target.checked }),
            }),
            "فروش محصولات و اتصال مجدد فعال باشد (حق خریدهای قبلی حفظ می‌شود)",
          ),
          el(
            "div",
            { className: "grid gap-4 sm:grid-cols-2" },
            ...Object.entries(labels).map(([key, label]) =>
              el(Ke, {
                key,
                label:
                  label +
                  (key === "reconnectToman" || key === "firstBonusPct"
                    ? ""
                    : " (سکه)"),
                type: "number",
                min: 1,
                value: cfg[key],
                onChange: (e) =>
                  setCfg({ ...cfg, [key]: Number(e.target.value) }),
              }),
            ),
          ),
          el(
            Ae,
            {
              loading: busy,
              disabled: busy,
              onClick: () =>
                act(() => Te("/monetization", { method: "PUT", body: cfg })),
            },
            "ذخیره قیمت‌ها",
          ),
        ),
      section(
        "ساخت کد واقعی با تاریخ و سهمیه",
        el(
          "p",
          { className: "text-xs leading-6" },
          "یکی را انتخاب کنید: تخفیف درصدی خرید امکانات با سکه یا هدیه مستقیم سکه. هر کد فقط یک بار برای هر حساب؛ تخفیف در تأیید نهایی نشان داده می‌شود و روی فاکتور خرید سکه اعمال نمی‌شود.",
        ),
        el(
          "form",
          {
            className: "grid gap-4 sm:grid-cols-2",
            onSubmit: (e) => {
              e.preventDefault();
              act(() =>
                Te("/monetization/promos", {
                  method: "POST",
                  body: {
                    ...promo,
                    expires_at: new Date(promo.expires_at).toISOString(),
                  },
                }),
              );
            },
          },
          input(
            "کد (حروف لاتین، عدد و خط تیره)",
            promo.code,
            (v) => setPromo({ ...promo, code: v }),
            {
              required: true,
              maxLength: 32,
              minLength: 3,
              pattern: "[A-Za-z0-9-]+",
            },
          ),
          input(
            "درصد تخفیف (برای هدیه سکه صفر)",
            promo.percent,
            (v) => setPromo({ ...promo, percent: Number(v) }),
            { type: "number", min: 0, max: 90, required: true },
          ),
          input(
            "هدیه سکه (برای تخفیف صفر)",
            promo.gift_coins,
            (v) => setPromo({ ...promo, gift_coins: Number(v) }),
            { type: "number", min: 0, max: 10000, required: true },
          ),
          input(
            "سقف کل استفاده",
            promo.max_uses,
            (v) => setPromo({ ...promo, max_uses: Number(v) }),
            { type: "number", min: 1, max: 10000, required: true },
          ),
          input(
            "انقضا (به وقت دستگاه شما)",
            promo.expires_at,
            (v) => setPromo({ ...promo, expires_at: v }),
            { type: "datetime-local", required: true },
          ),
          el(Ae, { type: "submit", disabled: busy, loading: busy }, "ساخت کد"),
        ),
      ),
      data &&
        section(
          "کدهای اخیر",
          ...data.promos.map((p) =>
            el(
              "div",
              {
                key: p.code,
                className:
                  "flex flex-wrap items-center justify-between gap-3 border-b border-white/10 py-3",
              },
              el(
                "p",
                {},
                p.code +
                  " · " +
                  (p.percent ? p.percent + "%" : p.giftCoins + " سکه") +
                  " · " +
                  p.used +
                  "/" +
                  p.maxUses +
                  " · " +
                  p.expiresAt +
                  " · " +
                  (p.active ? "فعال" : "غیرفعال"),
              ),
              p.active &&
                el(
                  Ae,
                  {
                    size: "sm",
                    disabled: busy,
                    onClick: () =>
                      act(() =>
                        Te(
                          "/monetization/promos/" + encodeURIComponent(p.code),
                          { method: "DELETE" },
                        ),
                      ),
                  },
                  "غیرفعال کردن",
                ),
            ),
          ),
        ),
      data &&
        section(
          "خریدهای ثبت‌شده امکانات (نه درآمد نقدی)",
          el(
            "p",
            { className: "text-xs" },
            "فقط خریدهای پرداخت‌شده با سکه؛ موارد برگشت‌خورده حذف شده‌اند. این اعداد با فروش استارز یا تسویه ریالی یکسان نیستند.",
          ),
          ...data.sales.map((row) =>
            el(
              "p",
              { key: row.sku },
              (labels[row.sku] || row.sku) +
                " · " +
                row.purchases +
                " خرید · " +
                row.coinsSpent +
                " سکه",
            ),
          ),
        ),
    );
  };
}
