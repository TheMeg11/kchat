# Costs

- Railway Trial (verified 2026-11): $5 one-time credit, 30 days; usage rates RAM $10/GB/mo, CPU $20/vCPU/mo, egress $0.05/GB. One service + one Postgres fits the trial if traffic is modest; Hobby $5/mo includes $5 usage and retains volumes.
- Single process (web + bots + schedulers) → one service, no Redis, no cron service, no separate worker.
- Telegram Stars: bot receives XTR; refunds free via API. Bale wallet: no documented bot-side fee schedule in the Bot API docs (unverified).
- Efficiency: pooled DB connections (5+5), indexes on hot paths, hourly purge, dedup table trimmed at 48h, bounded media streaming (no persistent storage), single-file admin frontend served from disk.
