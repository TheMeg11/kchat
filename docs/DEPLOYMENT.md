# Deployment and verification — 2026-11-28

## Supported topology
One Python service, **one process / one replica**, one persistent PostgreSQL database. No Redis or separate worker is required. Durable inbox workers, schedulers, bot adapters and FastAPI run together. Do not add `--workers` or replicas: cache invalidation, ordering partitions, broadcasts, throttles and restore gates are partly process-local. PostgreSQL row/advisory locks do not remove this restriction.

## Before Railway deployment
1. Back up the existing PostgreSQL service at provider level and validate an encrypted application archive in an isolated database. Review existing duplicate active chats and negative balances before migration.
2. Attach PostgreSQL and set `DATABASE_URL` via Railway's service reference. Never copy a local SQLite URL to Railway. Production startup rejects SQLite (including the former `ALLOW_SQLITE_ON_RAILWAY` escape hatch).
3. Configure three independent secrets (`ENCRYPTION_KEY`, `BACKUP_PASSWORD`, `WEBHOOK_SECRET`, >=24 characters each), bot tokens, explicit platform owner IDs and `BACKUP_OWNER_TELEGRAM_ID`. Keep backup keys outside Railway as well.
4. Set `PUBLIC_URL` to the public HTTPS service URL, or leave it blank to use Railway's automatically supplied `RAILWAY_PUBLIC_DOMAIN` with HTTPS. The latter requires a public domain enabled under Networking. Explicit HTTP/malformed URLs still fail startup; the private service domain is never used as a public origin. Start the product Telegram bot privately as the backup owner. Set a strong one-time `ADMIN_PANEL_USERNAME` / `ADMIN_PANEL_PASSWORD` for bootstrap, or configure the delivery bot for generated one-time credentials. Environment bootstrap values do not reset an existing owner's changed password.
5. Review `.env.example`, resource budgets and pool size; include PostgreSQL's own connections in the budget. Defaults are a 3-connection app pool with 2 overflow connections, two ordinary update lanes plus a payment lane, Uvicorn concurrency 100. Do not tune down purely to chase cost numbers.
6. Build checked-in frontend assets if modified: `npm ci && npm run build:panel`. Docker only needs Python at runtime. `requirements.txt` is hash-locked; direct dependency constraints are in `requirements.in`.
7. Build/deploy the Docker image using `railway.json`. It runs as UID 10001, binds `0.0.0.0:$PORT`, and has `/health` health checking. Local tests do not prove the production Docker image or Railway proxy configuration; run a staging smoke test first.

## Migrations
Startup creates missing tables, applies legacy additive/widening changes and then the versioned migration `20261128_hardening_v1` in `app/migrations.py`. PostgreSQL DDL is serialized by advisory lock `71389203`; the migration marker prevents repeat work. This introduces inbox/orders/source keys, indexes and wallet/chat guards; it is forward-only. The balance check is `NOT VALID` for historical rows (new writes are checked). Startup is **not** a destructive reset. Index creation is not concurrent: schedule a maintenance window for a large database. Versioned migrations supplement the old additive bootstrap; this is not a complete Alembic conversion.

## After deployment
- First owner login: change the initial password. Refresh and perform a harmless mutation; `/auth/me` must return a fresh CSRF header without requiring logout/login. No TOTP enrollment is required.
- Search an old user by `@username`, public/platform/internal ID, name and Persian digits; check pagination and phone-width rendering.
- Verify Telegram/Bale updates, payment pre-checkout latency, matching, stale chat buttons, queue compensation, and outbound rate limits.
- Create a manual backup, confirm **delivered**, download all parts and validate in isolation. A configured channel is not proof of delivery.
- Inspect security events and scheduler errors, inbox retries/dead letters, DB connections and measured process/cgroup memory. Billing/network/disk metrics absent from the Railway API are shown as unknown, not estimated zeros.
- Do not delete/recreate the PostgreSQL service during application redeploys. Monitor post-migration data and keep recovery artifacts until accepted.

## Local development and tests
```sh
python3 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.txt
.venv/bin/pip install pytest==9.2.0 pytest-asyncio==1.5.0
.venv/bin/python -m pytest tests/ -q
npm ci
npm run build:panel
npm run check:panel
npm run test:postgres
```
The default suite uses disposable SQLite and skips the PostgreSQL-only tests. `test:postgres` starts embedded PostgreSQL on port 55432 with a temporary `hamchat_test` database, then stops it. Alternatively set `TEST_PG_URL` for `tests/test_postgres.py`: **the database must end in `_test` and its public schema will be dropped**. Never point tests at a deployment database. PostgreSQL tests include concurrent wallet/payment/matching and restore rollback cases.

`npm run test:panel` requires a separately running, deliberately seeded **local-only** preview on port 8000 with the test `preview` account and 550 synthetic users (old target `@Old_Test_User`, public ID `100000`, display name `علی آزمایشی`). It rejects remote hosts. It is not a general production smoke-test command. Chromium on minimal Linux may require NSS/NSPR runtime libraries; use the distribution libraries or the runtime supplied with `@sparticuz/chromium` and `LD_LIBRARY_PATH`. Screenshots are ignored under `artifacts/`.

## Delivery semantics / recovery
Inbox receipts commit before HTTP acknowledgement, retry with backoff and eventually dead-letter after `UPDATE_MAX_ATTEMPTS`. Successful payloads are cleared. Inspect dead updates before replay, especially payments. Partition count must remain stable until the queue drains; changing `UPDATE_WORKERS` with pending rows changes actor ordering.

External network sends cannot be atomically committed with SQL. A crash after send/before recording may duplicate a message. Broadcasts persist a keyset cursor every 25 targets and may repeat that batch after a hard crash; failed targets are counted, not automatically guaranteed eventual delivery. Broadcast recipients are selected from the current audience on each batch, not a frozen snapshot. Pause/cancel takes effect at a batch boundary. Refunds with ambiguous results stay `refund_pending`; reconcile/retry the same payment rather than manually crediting coins back.

## Startup error: PUBLIC_URL must use HTTPS

A failure in `settings.validate_production()` occurs before database initialization/migration. Set `PUBLIC_URL` on the **application service** to its real `https://` panel address (not the Postgres/private hostname), apply the variable change and deploy again. Do not rotate backup keys or reset the database to fix this error.

The startup hotfix resolves a blank `PUBLIC_URL` from `RAILWAY_PUBLIC_DOMAIN` automatically, strips surrounding whitespace/trailing slashes, and validates the resulting HTTPS URL. If `PUBLIC_URL` is explicitly wrong, correct it or remove it to enable this fallback. If neither value exists, enable a Railway public domain or set the real custom HTTPS URL. The error alone cannot establish whether the original variable was blank or incorrectly formatted.


## Commerce revision

See [MONETIZATION.md](MONETIZATION.md) before enabling/promoting the new catalog. No new environment variables; startup adds eight business tables under `20261128_commerce_v2`. Back up PostgreSQL first; keep one process/replica. Existing VIP/pass entitlements are honored when the catalog's sales toggle is off. Review the initial coin prices and platform top-up rates in the two admin screens; do not present coin spending as cash revenue. Local tests are not evidence that Railway deployed or that a live payment succeeded.
