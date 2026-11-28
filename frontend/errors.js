// Recoverable request errors are notifications, NOT fatal full-page overlays.
function notifyError(message) {
  let box = document.getElementById("network-notice");
  if (!box) {
    box = document.createElement("div");
    box.id = "network-notice";
    box.setAttribute("role", "alert");
    box.style.cssText = "position:fixed;bottom:20px;right:20px;left:20px;z-index:99999;background:#33212b;color:#fff;padding:16px;border:1px solid #955;border-radius:12px;font:14px Tahoma;cursor:pointer";
    box.onclick = () => box.remove();
    document.body.appendChild(box);
  }
  box.textContent = message + " — برای بستن لمس کنید";
  clearTimeout(box.timer);
  box.timer = setTimeout(() => box.remove(), 10000);
}
window.addEventListener("unhandledrejection", e => {
  if (e.reason?.name === "AbortError") { e.preventDefault(); return; }
  notifyError(e.reason?.name === "ApiError" ? e.reason.message : "خطایی رخ داد؛ دوباره تلاش کنید. نیازی به خروج از حساب نیست.");
  e.preventDefault();
});
window.addEventListener("error", () => {
  notifyError("بخشی از پنل با خطا مواجه شد. اگر ادامه دارد صفحه را تازه‌سازی کنید.");
});
