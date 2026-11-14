# Deployment (Railway)

Target project: `satisfied-fulfillment`, service name: `kanarchat`.

1. Create service `kanarchat` (Dockerfile builder) and add the Railway **PostgreSQL** service; `DATABASE_URL` is injected automatically.
2. Set variables (see `.env.example`): `TELEGRAM_BOT_TOKEN`, `BALE_BOT_TOKEN`, `BALE_PROVIDER_TOKEN` (optional; empty keeps Bale payments disabled), `DELIVERY_BOT_TOKEN`, `ENCRYPTION_KEY`, `BACKUP_PASSWORD`, `WEBHOOK_SECRET`, `ADMIN_TELEGRAM_IDS=860835914`, `ADMIN_BALE_IDS=133609405`, `PUBLIC_URL=https://<service>.up.railway.app`.
3. Generate a public domain on the service; the app listens on `$PORT`.
4. `railway.json` sets healthcheck `/health` (timeout 300s), restart ON_FAILURE (10 retries).
5. On boot the app runs migrations via `create_all`, seeds defaults (store packages, roles, media types, moderation dictionary), sets bot commands, registers webhooks (`/webhook/telegram/<secret>`, `/webhook/bale/<secret>`), and starts schedulers. First boot also creates the initial web-admin Owner account and DMs the credentials via the delivery bot (change password + TOTP enrollment are enforced).
6. Free/Trial plan limits (verified 2026-11): $5 one-time credit / 30 days, 2 vCPU / 1 GB RAM per service, 1 GB ephemeral disk, Postgres volume 0.5 GB, 1 custom domain/service, no ALWAYS restart policy. Botted traffic must stay within these; upgrade to Hobby ($5/mo) to persist volumes beyond trial.

## Data persistence across deploys (v2.2)

Railway recycles the container filesystem on every deploy, so data must live in
PostgreSQL — never in a local file.

1. **Attach the Railway PostgreSQL service** to the `kanarchat` service. Railway
   injects `DATABASE_URL` automatically; the app normalizes it for asyncpg.
   The database is NOT dropped or recreated on deploys: boot runs
   `create_all` (missing tables only) plus additive seeds guarded by
   emptiness/version markers. Nothing is ever `drop_all`-ed.
2. **Boot guard:** if `RAILWAY_ENVIRONMENT` / `RAILWAY_PROJECT_ID` is present
   and `DATABASE_URL` is not a Postgres URL, startup FAILS with a clear error
   instead of silently booting on an ephemeral SQLite file (which would wipe
   all user data on the next deploy). Escape hatch for throwaway test deploys:
   `ALLOW_SQLITE_ON_RAILWAY=1` (logs a loud warning; data is still ephemeral).
3. **Startup summary:** each boot logs the engine type and user/chat row counts
   so a wiped database is immediately visible in the deploy logs.
4. **Backups:** the daily 03:00 Tehran encrypted backup (delivered to the
   owner's Telegram) plus the instant backup button in the admin panel remain
   the restore path of last resort; see `docs/BACKUP_RESTORE.md`.
