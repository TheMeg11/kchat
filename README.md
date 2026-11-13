# Kanar Anonymous Chat — کانار چت ناشناس

Anonymous stranger-chat platform: Telegram ↔ Telegram, Bale ↔ Bale, and cross-platform Telegram ↔ Bale, with one Python codebase and one shared PostgreSQL database. Coins economy, moderation, in-bot admin panels, a Persian RTL Super Admin web panel, and daily encrypted DB backups delivered to the owner's Telegram.

## Stack
Python 3.12 · asyncio · FastAPI · HTTPX · SQLAlchemy 2 async · PostgreSQL (SQLite for dev/tests) · Argon2id · AES-256-GCM · Docker · Railway.

## Layout
- `app/` — application package
  - `config.py` settings · `db.py` models · `security.py` crypto/TOTP · `moderation.py` filters · `i18n.py` fa/en/ru · `service.py` business logic · `platform.py` Telegram+Bale adapters · `bot.py` user bot · `bot_admin.py` in-bot admin panels · `backup.py` backup/restore engine · `webadmin.py` Super Admin REST API · `main.py` entrypoint (webhooks + schedulers + SPA)
- `webadmin/index.html` — built single-file Super Admin frontend (from admin.zip)
- `tests/test_all.py` — pytest suite (30 tests)
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
