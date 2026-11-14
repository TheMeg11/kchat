#!/usr/bin/env python3
"""v2.4 panel bundle patcher — surgical string replacements in webadmin/index.html.

1. Error Boundary overlay (global window error/unhandledrejection + white-screen
   watchdog) injected before the module bundle.
2. Signup-funnel card on the dashboard (reads signupFunnel from /dashboard/metrics).
3. "بلاک‌کرده ربات" option in the users status filter.
4. Segmented-broadcast audiences (ثبت‌نام ناتمام / غایب ۷+ روز) in the composer map.
5. matching settings flags: age-group separation now reports unlocked (removed).
"""
import hashlib
import io
import re
import sys

PATH = "webadmin/index.html"
src = io.open(PATH, encoding="utf-8").read()
orig_hash = hashlib.sha256(src.encode()).hexdigest()[:12]
print(f"bundle loaded ({len(src)/1e6:.2f} MB, sha256:{orig_hash})")


def must_replace(s, old, new, count=1, tag=""):
    if old not in s:
        print(f"FATAL: anchor not found for {tag or old[:60]!r}")
        sys.exit(1)
    if s.count(old) < count:
        print(f"FATAL: anchor appears {s.count(old)}x (<{count}) for {tag or old[:60]!r}")
        sys.exit(1)
    print(f"ok: {tag or old[:48]!r}")
    return s.replace(old, new, count)


# ---------------------------------------------------------------- 1) EB ----
EB = """<script>
/* v2.4 Error Boundary overlay — catches runtime errors, unhandled promise
   rejections and white-screen renders in the prebuilt panel bundle and shows
   a recoverable Persian overlay instead of dying silently. */
(function () {
  'use strict';
  var shown = false;
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function show(detail) {
    if (shown) {
      var t = document.getElementById('eb-detail');
      if (t) t.textContent = String(detail || '').slice(0, 600);
      return;
    }
    shown = true;
    var d = document.createElement('div');
    d.id = 'eb-overlay';
    d.dir = 'rtl';
    d.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:rgba(10,12,20,.96);' +
      'display:flex;align-items:center;justify-content:center;font-family:Vazirmatn,Tahoma,sans-serif;';
    d.innerHTML =
      '<div style="max-width:560px;margin:24px;padding:28px;border-radius:18px;background:#161a26;' +
      'border:1px solid #2a3042;color:#e7eaf3;text-align:center;box-shadow:0 18px 60px rgba(0,0,0,.5)">' +
      '<div style="font-size:40px;line-height:1">&#9888;&#65039;</div>' +
      '<h2 style="margin:12px 0 6px;font-size:20px;color:#97a4fb">خطایی در پنل رخ داد</h2>' +
      '<p style="margin:0 0 6px;color:#9aa3b5;font-size:13px">مشکلی در اجرای رابط مدیریت پیش آمد. ' +
      'معمولاً یک بارگذاری مجدد آن را برطرف می‌کند.</p>' +
      '<pre id="eb-detail" dir="ltr" style="text-align:left;white-space:pre-wrap;word-break:break-word;' +
      'max-height:140px;overflow:auto;margin:12px 0;padding:10px;border-radius:10px;background:#0e111a;' +
      'color:#f19db5;font-size:11px"></pre>' +
      '<button id="eb-reload" type="button" style="cursor:pointer;border:0;border-radius:12px;' +
      'padding:10px 26px;font-size:14px;font-family:inherit;background:linear-gradient(135deg,#97a4fb,#b4a5f8);' +
      'color:#12141c;font-weight:700">&#128260; بارگذاری مجدد</button>' +
      '</div>';
    document.body ? document.body.appendChild(d) : document.documentElement.appendChild(d);
    var t = document.getElementById('eb-detail');
    if (t) t.textContent = String(detail || '').slice(0, 600);
    var b = document.getElementById('eb-reload');
    if (b) b.addEventListener('click', function () { location.reload(); });
  }
  window.addEventListener('error', function (e) {
    if (e && e.message && /Script error/i.test(e.message)) return;
    show(e && (e.message + (e.filename ? (' @ ' + e.filename + ':' + e.lineno) : '')) || 'unknown error');
  });
  window.addEventListener('unhandledrejection', function (e) {
    var r = e && e.reason;
    show(r && (r.message || r) || 'unhandled rejection');
  });
  document.addEventListener('DOMContentLoaded', function () {
    setTimeout(function () {
      var root = document.getElementById('root');
      if (root && root.childElementCount === 0) show('White screen: #root is empty after load.');
    }, 6000);
  });
})();
</script>
"""
# inject right before the module bundle script tag
src = must_replace(src, '<script type="module" crossorigin>', EB + '<script type="module" crossorigin>',
                   tag="error-boundary injection")

# ------------------------------------------------------- 2) funnel card ----
# anchor: the incompleteSignups card added in v2.3.3 (scope var = l)
anchor = 'value:l.incompleteSignups||0,subtext:"کاربرانی که ثبت‌نام را کامل نکرده‌اند (بدون تاریخ تولد)"'
funnel_card = (',u.jsx(He,{label:"قیف ثبت‌نام",'
               'value:l.signupFunnel?l.signupFunnel.completedPct||0:0,'
               'subtext:l.signupFunnel?("شروع "+l.signupFunnel.started+" ← قوانین '
               '"+l.signupFunnel.acceptedRules+" ← تولد "+l.signupFunnel.birthdateSet+" ← کامل "'
               '+l.signupFunnel.completed):"—",'
               'trend:{text:"درصد تکمیل ثبت‌نام",positive:!0},'
               'icon:u.jsx(Pp,{className:"size-5"}),tone:"violet"})')
# find the END of that card object: after the anchor the card continues to its `}),` closing
idx = src.find(anchor)
if idx < 0:
    print("FATAL: funnel anchor missing"); sys.exit(1)
# locate the next '}),u.jsx(He' boundary after the anchor (card end)
end_marker = '}),u.jsx(He'
end = src.find(end_marker, idx)
if end < 0:
    print("FATAL: card end boundary not found"); sys.exit(1)
insert_at = end + len('}),')
src = src[:insert_at] + funnel_card + src[insert_at:]
print("ok: signup funnel card inserted")

# ------------------------------------------- 3) blocked_bot status option ----
src = must_replace(src,
                   ',u.jsx("option",{value:"perm_banned",children:"مسدود دائم"}),u.jsx("option",{value:"deleted",children:"حذف‌شده"})',
                   ',u.jsx("option",{value:"perm_banned",children:"مسدود دائم"}),'
                   'u.jsx("option",{value:"blocked_bot",children:"بلاک‌کرده ربات"}),'
                   'u.jsx("option",{value:"deleted",children:"حذف‌شده"})',
                   tag="blocked_bot status option")

# ------------------------------------- 4) broadcast audiences (f3 map) ----
src = must_replace(src,
                   ',low_balance:{label:"سگمنت: موجودی کمتر از ۱۰ سکه",count:29840}}',
                   ',low_balance:{label:"سگمنت: موجودی کمتر از ۱۰ سکه",count:29840},'
                   'incomplete:{label:"سگمنت: ثبت‌نام‌های ناتمام",count:0},'
                   'inactive_7d:{label:"سگمنت: غایب بیش از ۷ روز",count:0}}',
                   tag="broadcast segment audiences")

# --------------------------------- 5) age-group flags now unlocked ----
n = src.count('strictAgeGroupMinorLocked:!0') + src.count('strictAgeGroupAdultLocked:!0')
src = src.replace('strictAgeGroupMinorLocked:!0', 'strictAgeGroupMinorLocked:!1')
src = src.replace('strictAgeGroupAdultLocked:!0', 'strictAgeGroupAdultLocked:!1')
print(f"ok: age-group flags unlocked ({n} occurrences)")

io.open(PATH, "w", encoding="utf-8").write(src)
print(f"bundle written ({len(src)/1e6:.2f} MB)")
