# Kanar Anonymous Chat — کانار چت ناشناس

> **v2.4** — growth & reliability release: age-group separation removed (matching open to all ages, chats are fully moderated), friends list (chat-options add-friend + main-menu friends list with anonymous DM), admin actions notify the user's PV (coins/ban/unban — also fixed the dead `admub_`/`admuc_` buttons), signup funnel on the dashboard, admin session survives deploys (stateless CSRF + 30-day trusted-device cookie), panel error-boundary overlay, users-list N+1 fixed, polling hardened with auto alerts (polling down / webhook backlog / scheduler crash / backup failure) to the owner's Telegram PV, signup-rescue reminders (3-stage cadence, daily cap) with bot-blocked isolation list + auto-restore, first-match waiting nudge at 60s (user stays queued), referral upgraded (50 coins/party, top-inviter list, share button), stronger anon-link copy + post-signup anon-message landing, segmented broadcasts (incomplete / inactive-7d / low-balance), light gamification (levels, stats, daily-bonus streak).

> **v2.2** — display-name profanity filter (word lists in `app/profanity.py`), report reason step, coin store with changeable rates (500 Toman = 1 coin, 1 Star = 20 coins) + custom amounts + Telegram Stars, birthdate-based registration (Jalali/Gregorian, age confirmation, min age 13), Telegram/Bale send throttling with 429 cooldown, dual invite links, restructured menus, Railway data-persistence guard.

Anonymous stranger-chat platform: Telegram ↔ Telegram, Bale ↔ Bale, and cross-platform Telegram ↔ Bale, with one Python codebase and one shared PostgreSQL database. Coins economy, moderation, in-bot admin panels, a Persian RTL Super Admin web panel, and daily encrypted DB backups delivered to the owner's Telegram.

## Stack
Python 3.12 · asyncio · FastAPI · HTTPX · SQLAlchemy 2 async · PostgreSQL (SQLite for dev/tests) · Argon2id · AES-256-GCM · Docker · Railway.

## Layout
- `app/` — application package
  - `config.py` settings · `db.py` models · `security.py` crypto/TOTP · `moderation.py` filters · `i18n.py` fa/en/ru · `service.py` business logic · `platform.py` Telegram+Bale adapters · `bot.py` user bot · `bot_admin.py` in-bot admin panels · `backup.py` backup/restore engine · `webadmin.py` Super Admin REST API · `main.py` entrypoint (webhooks + schedulers + SPA)
- `webadmin/index.html` — built single-file Super Admin frontend (from admin.zip)
- `tests/test_all.py` — pytest suite (53 tests)
- `docs/` — architecture, security, backup, deployment, compliance, platform capabilities, costs, competitors

## Quick start (local)
```bash
cp .env.example .env   # fill tokens/secrets
pip install -r requirements.txt
python -m app.main     # serves web panel + bots (polling fallback when PUBLIC_URL empty)
```

## Tests
```bash
python -m pytest tests/ -q
```

## Railway deploy
See `docs/DEPLOYMENT.md`. Set the variables from `.env.example` (esp. `DATABASE_URL`, `PUBLIC_URL`, `ENCRYPTION_KEY`, `BACKUP_PASSWORD`, `WEBHOOK_SECRET`, tokens), then deploy the Dockerfile. Health: `/health`.

## Environment variables (empty template)

Copy the block into Railway → Variables (or a local `.env`) and fill in your own values. Railway injects `DATABASE_URL` (when a Postgres service is attached), `PORT` and `RAILWAY_*` automatically — do not set those by hand. Empty values are ignored and fall back to the built-in defaults, so pasting the whole block as-is is safe.

```
DATABASE_URL=
TELEGRAM_BOT_TOKEN=
BALE_BOT_TOKEN=
BALE_PROVIDER_TOKEN=
DELIVERY_BOT_TOKEN=
ENCRYPTION_KEY=
BACKUP_PASSWORD=
WEBHOOK_SECRET=
PUBLIC_URL=
ADMIN_TELEGRAM_IDS=
ADMIN_BALE_IDS=
BACKUP_OWNER_TELEGRAM_ID=
BACKUP_HOUR_TEHRAN=
ADMIN_PANEL_USERNAME=
ADMIN_PANEL_PASSWORD=
CROSS_MEDIA_MAX_BYTES=
TELEGRAM_CHUNK_BYTES=
ALLOW_SQLITE_ON_RAILWAY=
```

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | PostgreSQL URL (Railway injects it when Postgres is attached; SQLite is used for local dev) |
| `TELEGRAM_BOT_TOKEN` / `BALE_BOT_TOKEN` | Platform bot tokens from Telegram @BotFather / Bale @BotFather |
| `BALE_PROVIDER_TOKEN` | Bale wallet payment token ("پرداخت در بازوها") — empty keeps Bale payments disabled |
| `DELIVERY_BOT_TOKEN` | Separate bot used only for one-time credential / backup-password delivery |
| `ENCRYPTION_KEY` | 32-byte key for AES-256-GCM message encryption |
| `BACKUP_PASSWORD` | Passphrase for the daily encrypted DB backup |
| `WEBHOOK_SECRET` | Shared secret between the platform webhook and the app |
| `PUBLIC_URL` | Public HTTPS URL (webhook mode; empty → long polling fallback) |
| `ADMIN_TELEGRAM_IDS` / `ADMIN_BALE_IDS` | Owner user IDs per platform (comma separated) |
| `BACKUP_OWNER_TELEGRAM_ID` | Telegram ID that receives the daily 03:00 (Asia/Tehran) backup |
| `BACKUP_HOUR_TEHRAN` | Backup hour in Tehran time (default 3) |
| `ADMIN_PANEL_USERNAME` / `ADMIN_PANEL_PASSWORD` | Super Admin web panel bootstrap credentials (reconciled at every startup) |
| `CROSS_MEDIA_MAX_BYTES` | Cross-platform media relay cap (default 20 MB) |
| `TELEGRAM_CHUNK_BYTES` | Telegram multipart upload chunk (default 40 MB) |
| `ALLOW_SQLITE_ON_RAILWAY` | Escape hatch: `1` permits SQLite on Railway (normally guarded against) |
