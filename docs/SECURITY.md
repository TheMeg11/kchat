# Security

- Argon2id password hashing (web panel); TOTP (RFC 6238, SHA1/30s/6 digits) generated and verified in-app; 8 one-time recovery codes (SHA-256 hashed).
- Sessions: 256-bit random IDs, HttpOnly + Secure (on Railway) + SameSite=Lax cookies, 8h expiry, revocable; CSRF double-submit token issued on login and enforced by middleware on all mutating admin endpoints (login/TOTP steps exempt).
- Login rate limiting: 6 failures / 5 min / IP → 429.
- Secrets only via environment variables; tokens never logged; backup archives exclude secrets; masked tokens in API responses; webhook secret header verification (Telegram) + secret URL path (both platforms).
- Sensitive fields (TOTP secrets) encrypted at rest with AES-256-GCM under `ENCRYPTION_KEY`.
- Server-side authorization on every privileged action + immutable audit log (bot, web and system actors).
- Webhook update deduplication, SQL via SQLAlchemy bound parameters, idempotent payment crediting (unique charge IDs), no negative balances.
- Backups: AES-256-GCM (scrypt N=2^14) + SHA-256 checksums; wrong password or tampering fails authentication (GCM tag) — rejected before any DB write.
- Known limits: Bale webhooks have no secret token (documented absence) — mitigated by secret URL path; Railway trial volumes are ephemeral (backups offloaded to Telegram for this reason).
