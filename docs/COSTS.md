# Resource use and costs

This revision makes **no numerical Railway savings claim**. No live billing comparison or production load test has been performed. Check current Railway pricing in the provider console instead of relying on historical trial/pricing numbers in old release notes.

- One process combines web, bots and schedulers; no Redis or extra worker service.
- PostgreSQL app pool defaults to 3 + 2 overflow connections; include the database service and advisory-lock connections when measuring the total.
- Matching reads bounded FIFO batches (500 candidates / up to 20 pairs), uses SQL/batched lookups and indexes. Payment/webhook receipts are durable and retries bounded.
- Config/moderation caches invalidate after local commits. Only a single process is supported; do not trade away consistency by adding replicas without shared invalidation.
- Broadcasts count in SQL and traverse 25-target keyset batches with a durable cursor; no entire-user-table materialization for campaign creation.
- Backups spool rows and bound archive sizes, but JSON validation and authenticated encryption still require bounded in-memory buffers. Plan headroom for simultaneous worker/network operations.
- Frontend assets are built once, locally font-hosted, precompressed and content-versioned. Python deployment needs no Node runtime or separate frontend service.
- Monitoring reports actual process/cgroup measurements and available PostgreSQL data. CPU is process consumption (may exceed 100% on multiple cores); RAM is process RSS, not the entire Railway service charge. History is bounded to 288 samples and resets on restart. Unknown billing/egress/storage/30-day availability stays unknown.

## Verify without reducing service quality
Measure comparable traffic windows before/after staging: peak RSS/cgroup memory, database connections, query latency, webhook acknowledgement and processing lag, payment response latency, match latency/success, retry/dead counts and backup/restore duration. Keep daily backups, moderation and privacy jobs enabled. Change one pool/batch/worker setting at a time, with a rollback plan. Do not promise savings or lower resource limits until these quality indicators remain acceptable.
