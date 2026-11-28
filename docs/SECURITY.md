# Security — hardening revision 2026-11-28

## Panel access
- Argon2id passwords, with at most two concurrent offloaded password operations.
- **No TOTP or recovery-code enrollment/login requirement.** Legacy enrollment/verification routes are disabled; old schema columns are retained for compatibility, not used for authentication.
- `ADMIN_PANEL_USERNAME` / `ADMIN_PANEL_PASSWORD` bootstrap an owner **once**. Redeploying does not reset the password. Initial login requires a password change. Legacy accounts with the old `!unset!` fallback are quarantined or re-keyed with an explicitly configured bootstrap password.
- Random, revocable, database-backed sessions (default 30 days), HttpOnly / SameSite=Lax; Secure on Railway or with `COOKIE_SECURE=true`. Activity timestamps are coalesced to five-minute intervals. Password rotation revokes old sessions and issues a new session for the current browser.
- Session-bound HMAC CSRF tokens; `/auth/me` rehydrates the browser token on refresh. Mutations require `X-CSRF-Token`; comparison is constant-time. Login/bootstrap are narrowly exempted. A stale token can be refreshed without logging out.
- Login limited to six failed attempts per five minutes per effective IP. Forwarded addresses are trusted only from `TRUSTED_PROXY_IPS`; do not set this to arbitrary public clients. Failed-login security events commit independently of failed business transactions.
- Every privileged API action checks server-side permissions. Conversation history has separate permissions, lazy bounded retrieval, and audit records. Application audit records are not tamper-proof against a database administrator.

## Payments and wallet
- Atomic guarded balance updates and unique ledger idempotency keys; PostgreSQL checks reject new negative wallet balances.
- Server-side expiring opaque invoice orders bind payer, recipient, platform, currency, amount and purchased coins. A charge is credited once; legacy client-derived invoice values are not trusted.
- Refunds reserve purchased coins (from the gift recipient when applicable) before contacting Telegram, outside the database transaction. Ambiguous results stay `refund_pending` for retry/reconciliation; already-refunded gateway responses reconcile without another debit. Insufficient/spent coins and deleted accounts require manual review. Bale refunds remain manual.
- External send and database commit cannot be one atomic operation. Delivery is **not exactly-once**; see [deployment limitations](DEPLOYMENT.md).

## Ingress, secrets and data
- Streaming ASGI request size checks cover declared and chunked bodies. Default webhook body 256 KiB, restore archive 64 MiB (+ multipart envelope), other request bodies 1 MiB. Media download caps and adapter throttling remain in force. Webhook events are structurally validated, persisted before acknowledgement, retried with a finite attempt budget, and deduplicated.
- Telegram checks its webhook header and secret URL; Bale uses the secret URL. Rotate `WEBHOOK_SECRET` only with an intentional webhook/session plan. Treat backup keys, bot tokens, session cookies and private webhook URLs as credentials.
- Railway startup requires PostgreSQL, HTTPS `PUBLIC_URL`, and configured `ENCRYPTION_KEY`, `BACKUP_PASSWORD`, `WEBHOOK_SECRET` (at least 24 characters each). There is no production SQLite escape hatch.
- Encrypted, authenticated bounded backups; owner-only restore with current panel password and explicit confirmation, without TOTP. See [backup operations](BACKUP_RESTORE.md). Archives contain sensitive business data; do not commit them.
- Account deletion scrubs the profile and removes conversations, support and social data, retaining a pseudonymized row for financial ledger integrity. Old encrypted backups can still contain deleted information until off-platform copies are removed. Financial retention and legal obligations need operator/legal review.
- Runtime dependencies are hash-locked; Docker runs as non-root. Static assets/fonts are local, content-versioned and precompressed. Security headers are applied by the service. Neither dependency audits nor automated tests constitute a penetration test.

## Policy boundaries
Age policy was **not changed** in this revision. No claim of a separate minor/adult matching pool or verified age is made. See [compliance](COMPLIANCE.md). Multi-process cache, scheduler and restore coordination is not supported; deploy one process/replica.
