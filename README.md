# هم‌چت / HamChat

Anonymous chat bots for Telegram and Bale, with a Persian RTL web administration panel, PostgreSQL, matching, moderation, wallet/payments, and encrypted backup/restore.

## Hardening revision (2026-11-28)

- Refresh-safe panel sessions/CSRF, password rotation without forced logout of the current browser, server-side paginated user search (including old users, `@username`, IDs and Persian digits).
- **No TOTP requirement or enrollment.** The existing age policy is unchanged; an age-policy decision is still pending. Do not assume verified ages or separated minor/adult pools.
- Encrypted bounded backups, real file upload/validation and owner-password-confirmed restore; durable PostgreSQL inbox/retry, atomic wallet updates and server-side invoice orders.
- Batched matching/broadcasts, bounded local caches/maps, precompressed versioned assets and local fonts. No measured Railway savings are claimed.

This repository supports **one process / one replica**. See the documented delivery/crash and multi-process limitations before deployment. No change to a deployed service is implied by these source changes.

## Local start

```sh
python3 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.txt
cp .env.example .env
# For local SQLite, remove DATABASE_URL or use sqlite+aiosqlite:///./kanarchat.db.
# Set secrets, owner IDs, bot tokens and one-time admin bootstrap credentials.
# PUBLIC_URL can be empty locally (polling); Railway uses its public domain
# when PUBLIC_URL is blank. Railway still requires HTTPS + PostgreSQL.
.venv/bin/python -m app.main
```

The service binds `0.0.0.0:$PORT` (default 8000), serves the panel and `/health`. `ADMIN_PANEL_USERNAME` / `ADMIN_PANEL_PASSWORD` are used **once**, never to reset a changed password on redeploy. All production secrets and persistent PostgreSQL must be configured explicitly. `.env.example` is the environment reference; do not paste another operator's IDs or secrets.

Bale uses long polling; Telegram uses webhooks when configured, falling back to polling if registration fails. Backup delivery uses the product Telegram bot; its owner must have started that bot.

## Build and test

```sh
.venv/bin/pip install pytest==9.2.0 pytest-asyncio==1.5.0
.venv/bin/python -m pytest tests/ -q
npm ci
npm run build:panel
npm run check:panel
npm run test:postgres
```

The default tests use disposable SQLite; PostgreSQL-only tests skip without an isolated database. The embedded PostgreSQL harness never uses the deployment database. See [test safety and browser prerequisites](docs/DEPLOYMENT.md#local-development-and-tests).

Frontend editable recovered sources live in `frontend/`; checked-in deployable outputs are in `webadmin/`. The original JSX project was not supplied. The build is reproducible, but the recovered bundle is **not a fully decomposed component source tree**; see [frontend notes](frontend/README.md).

`requirements.in` contains direct dependency constraints; `requirements.txt` is the exact transitive hash lock consumed by Docker. Update deliberately with `pip-compile --generate-hashes --strip-extras requirements.in`, audit, and retest. Node is needed for development/tests, not the production image.

## Code map

- `app/main.py`: lifecycle, webhooks, scheduler, static serving.
- `app/inbox.py`, `orders.py`, `limits.py`, `http_limits.py`: durable ingress, payment validation and resource limits.
- `app/db.py`, `migrations.py`, `service.py`: models, versioned forward migrations, atomic economy and matching.
- `app/bot.py`, `bot_admin.py`, `panels.py`, `i18n.py`: bot flows, roles, chat-bound controls, translations.
- `app/backup.py`, `uploads.py`, `security.py`: backup/restore, temporary uploads, password/session cryptography.
- `app/webadmin.py`, `telemetry.py`: authorized panel API and measured monitoring.
- `tests/`: baseline, security/UX/failure regressions and real PostgreSQL concurrency tests.

## Operations

[Deployment/migrations](docs/DEPLOYMENT.md) · [Security](docs/SECURITY.md) · [Backup/restore](docs/BACKUP_RESTORE.md) · [Architecture](docs/ARCHITECTURE.md) · [Privacy/age policy](docs/COMPLIANCE.md) · [Resources/cost measurement](docs/COSTS.md)
