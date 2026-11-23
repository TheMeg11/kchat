# Platform capabilities (verified 2026-11-13, official docs only)

## Telegram (Bot API 10.4 — core.telegram.org/bots/api)
- Digital goods must be paid in **Stars (XTR)** with empty provider_token and exactly one LabeledPrice; pre-checkout must be answered ≤10s; `successful_payment` carries `telegram_payment_charge_id`; refunds via `refundStarPayment`. No documented cap for one-time XTR invoices.
- Webhooks: HTTPS, ports 443/80/88/8443, `secret_token` header, retries on non-2xx → handler must be idempotent. Rate limits: 1 msg/s per chat, ~30/s broadcast; 429 with `retry_after`.
- Files: multipart upload 50 MB (photos 10 MB), URL 20 MB, `getFile` download 20 MB, link valid 1h, `file_id` persistent per bot, NOT transferable between bots.
- `callback_data` 1-64 bytes; HTML/MarkdownV2 parse modes; all media types receivable.

## Bale (docs.bale.ai)
- Base `https://tapi.bale.ai/bot<token>/<method>`; based on Telegram Bot API with changes.
- **No** `parse_mode` (Bale Markdown always applied), **no `sendSticker`**, **no inline mode**, **no webhook `secret_token`** (only `url`; ports 443/88 only).
- Uploads: 10 MB photos / 50 MB files (multipart), 5/20 MB by URL, `getFile` 20 MB, link 1h. `file_unique_id` exists.
- Payments: wallet payments only; provider token from Bale @BotFather ("پرداخت در بازوها"); `sendInvoice` with `provider_token` and Rial amounts; `pre_checkout_query` (answer ≤10s); `successful_payment` with `telegram_payment_charge_id`; `inquireTransaction` for status; **no refund API documented** → refunds manual via wallet panel (implemented as disabled with explanatory error).
- Rate limits: ~2/s per chat guidance, 20/s & 120/min caps documented; 429 with `retry_after`.
- Message editing: `editMessageText`, `editMessageCaption`, **`editMessageReplyMarkup`** (chat_id, message_id, reply_markup) and **`deleteMessage`** (own outgoing messages in private/groups, message must be < 48h old) are documented. The bot uses `editMessageReplyMarkup` to retire stale inline keyboards — never `deleteMessage`, so the user's chat history is never touched.

## Cross-platform relay policy
Allowed only where BOTH platforms document support: text, photo, voice, video, document, audio, animation, location, contact. Sticker/video-note/poll are Telegram-only (Bale methods undocumented) → blocked in cross-platform chats. Media transferred by bounded streaming download (≤20 MB, the lower documented getFile cap) then multipart upload; never stored at rest; file_ids never reused across platforms.
