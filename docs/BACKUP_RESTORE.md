# Backup & Restore

## Automatic
- Daily at **03:00 Asia/Tehran** (in-process scheduler, DB-locked, restart-safe via `lastRunDate`).
- Content: full logical dump of **all tables** (chat text incl. >30-day history inside snapshots, financial records, users, settings). Excludes secrets/tokens.
- Pipeline: JSON → gzip → AES-256-GCM (scrypt-derived key from `BACKUP_PASSWORD`) → SHA-256 → manifest with schema version → chunked parts ≤40 MB → delivered **by the product Telegram bot** to user `860835914` (product bot used so file_ids stay accessible for restore).
- Failure notifications via admin notifications; backup history in the panel.

## Backup password
Generated randomly, stored only in Railway Variables as `BACKUP_PASSWORD`, sent separately to the owner via the delivery bot. **Without it backups are NOT recoverable** (AES-GCM). Store an external copy.

## Restore (Owner only)
1. Bot panel: /admin → ♻️ بازیابی → upload .enc file(s) (multi-part supported, reassembled in order) → «شروع بازیابی»; or Web panel: Backups → validate/execute with confirmation phrase `RESTORE KANAR DB` + Owner TOTP.
2. Validation: magic header, format, checksum, AES-GCM authentication, schema version compatibility. Malformed/tampered archives are rejected.
3. Execution: maintenance mode ON → emergency rollback snapshot → transactional wipe & load → consistency checks (orphan transactions/chats, wallet integrity) → on any failure automatic rollback to the snapshot → owner notified with rollback ID.
