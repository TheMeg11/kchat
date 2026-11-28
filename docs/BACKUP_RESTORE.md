# Backup & restore — 2026-11-28

## Export and storage
- Daily default 03:00 Asia/Tehran; schedule and enablement are configurable. Failed daily attempts back off for five minutes. Manual backups use the same pipeline.
- Logical business-table dump, including personal data, chat content, financial records, configuration and admin password hashes. Operational update inbox, browser sessions and login challenges are excluded. External environment secrets are not exported; treat the whole archive as highly sensitive anyway.
- Rows stream in batches of 250 through a 1 MiB spool into gzip; PostgreSQL uses a repeatable-read snapshot. Encryption is AES-256-GCM with a scrypt-derived key (`BACKUP_PASSWORD`), authenticated format `KNRB1`, filename `.json.gz.enc` and SHA-256 digest. Encryption and validation run off the event loop where appropriate.
- Default compressed/export budget: 64 MiB; default **expanded restore budget: 32 MiB**. Export also enforces this expanded budget, failing explicitly rather than silently omitting rows. Check restore validation routinely and raise budgets only after measuring memory headroom. This is bounded-memory, not constant-memory: encryption, decryption and validated JSON still occupy bounded buffers.
- Product Telegram bot delivers parts of at most **18 MiB**, so they can be downloaded again via getFile. Owner must have started that bot. Set `BACKUP_OWNER_TELEGRAM_ID` explicitly. Delivery network calls happen outside DB transactions. PostgreSQL advisory lock `71389201` prevents overlapping backup/restore jobs.
- `BACKUP_PASSWORD` is operator-provided (16+ characters locally, 24+ on Railway), not generated or delivered automatically. Keep an independent secure copy and record which key belongs to which archive. Keys are not included in generated captions. Loss of the key means loss of recovery access.
- The displayed SHA is recorded export metadata, **not proof that a remote Telegram copy was re-downloaded and verified**. Delivery and export statuses are separate. No automatic deletion of Telegram copies is promised by the retention setting.

## Restore: web (Owner only)
1. Backups → upload the actual archive (concatenate multipart pieces in order first). The server stores a restricted temporary file bound to the current session, with a ten-minute TTL and a bounded number of uploads.
2. Validate. New encrypted archives use the configured backup key; legacy plaintext gzip imports remain supported for authenticated owners. Wrong key, corrupt/truncated input, incompatible schema and excessive expanded size are rejected before writes.
3. Enter the **current panel password** and exact confirmation `RESTORE HAMCHAT DB`. **No TOTP.** CSRF and owner authorization remain required.
4. Restore enables the mutation/worker gate, creates an emergency encrypted snapshot, performs a transactional replacement and checks consistency. On Railway, emergency snapshot delivery to the owner must succeed before destructive writes proceed. Failure aborts restore. Existing sessions are invalidated; sign in again after success.
5. Check users, wallet totals, payments and bot operation, and retain the emergency snapshot until operational verification is complete. The UI shows returned operation results, not simulated percentage progress.

## Restore: bot (Owner only)
`/admin` → restore → forward all parts with their original captions in order → confirm. Current `BACKUP_PASSWORD` is used by default. If using an old key, supply it only in the private owner conversation as prompted; remove that message afterwards. The key then exists in that chat's history until removed—prefer web/operator recovery when possible.

Bot state is platform-scoped, capped at four pending restores, expires after ten minutes, and permits at most eight parts and the configured archive byte budget. Download limits still apply to each part. Reopen restore if it expires or the process restarts. Validation alone does not alter the database.

## Recovery and limitations
- Test restore against an isolated PostgreSQL database before deploying this revision or changing keys/budgets. Automated tests cover round trips and rollback, not access to a live Telegram vault.
- Only one process/replica is supported. Local temporary uploads, mutation gates and in-flight bot restore state are not shared across replicas. An SQL lock alone does not make distributed restores safe.
- Inbox receipts are deliberately retained across restore. New business state can differ from the state in which an old event originated; inspect pending/dead updates after disaster recovery.
- Do not downgrade the code against a migrated database as a substitute for restore. Take a provider-level PostgreSQL snapshot and verified encrypted export before deployment. Keep the previous image plus recovery material.
- `RESTORE_ON_BOOT_FILE` / `RESTORE_ON_BOOT_PASSWORD` are exceptional operator recovery controls, not routine deployment configuration. Remove them after a successful recovery. Do not bake archives into Docker or Git.
