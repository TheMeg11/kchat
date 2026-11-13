# Deployment (Railway)

Target project: `satisfied-fulfillment`, service name: `kanarchat`.

1. Create service `kanarchat` (Dockerfile builder) and add the Railway **PostgreSQL** service; `DATABASE_URL` is injected automatically.
2. Set variables (see `.env.example`): `TELEGRAM_BOT_TOKEN`, `BALE_BOT_TOKEN`, `BALE_PROVIDER_TOKEN` (optional; empty keeps Bale payments disabled), `DELIVERY_BOT_TOKEN`, `ENCRYPTION_KEY`, `BACKUP_PASSWORD`, `WEBHOOK_SECRET`, `ADMIN_TELEGRAM_IDS=860835914`, `ADMIN_BALE_IDS=133609405`, `PUBLIC_URL=https://<service>.up.railway.app`.
3. Generate a public domain on the service; the app listens on `$PORT`.
4. `railway.json` sets healthcheck `/health` (timeout 300s), restart ON_FAILURE (10 retries).
5. On boot the app runs migrations via `create_all`, seeds defaults (store packages, roles, media types, moderation dictionary), sets bot commands, registers webhooks (`/webhook/telegram/<secret>`, `/webhook/bale/<secret>`), and starts schedulers. First boot also creates the initial web-admin Owner account and DMs the credentials via the delivery bot (change password + TOTP enrollment are enforced).
6. Free/Trial plan limits (verified 2026-11): $5 one-time credit / 30 days, 2 vCPU / 1 GB RAM per service, 1 GB ephemeral disk, Postgres volume 0.5 GB, 1 custom domain/service, no ALWAYS restart policy. Botted traffic must stay within these; upgrade to Hobby ($5/mo) to persist volumes beyond trial.
