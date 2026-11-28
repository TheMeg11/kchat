# Approved hardening scope: implementation and verification

Date: 2026-11-28. Source changes only; **no production Railway/database deployment** was performed. Work stays on the session branch. S7/TOTP is intentionally excluded; S12/age policy is unchanged pending the owner's decision.

## Implemented controls and flows

| Scope | Implementation |
| --- | --- |
| S1 / P6 | Authenticated encrypted backup export, bounded batched/spooled processing, PostgreSQL backup/restore advisory lock and emergency snapshot delivery gate. |
| S2 | Bootstrap once, strong production configuration checks, no public fallback-password login; quarantine/re-key unsafe legacy accounts. |
| S3 | Server-side conversation permissions, lazy bounded history and audit records. |
| S4–S5 | Atomic guarded wallets/idempotency, server-side invoice orders and locked settlement; recipient-aware reserved refunds with pending reconciliation. |
| S6 / U1 | Revocable default 30-day sessions, refreshable session-bound CSRF, rate-limited login, current-browser-preserving password rotation. |
| S8 | Streaming body budgets, bounded upload slots (including concurrent receives), bot restore TTL/part/archive limits, media limits and bounded ingress/retry. |
| S9 | Pseudonymized financial retention with profile/content/social/support erasure; privacy/backup limitations documented. |
| S10–S11 | Non-root Docker, exact hashed runtime lock, security headers, local assets, independently persisted failed-login events. |
| P1 / P3–P5 | Batched/SQL matching, short critical wallet/match transactions, send compensation, commit-aware config invalidation and server-side user search/pagination. Not every legacy bot send has been moved out of its surrounding session. |
| P2 / P7 | Durable PostgreSQL inbox with leases/retry/dead state; SQL-counted/keyset broadcast batches and restart cursor. External delivery is not exactly-once. |
| P8–P9 | Hot-path indexes, smaller configurable connection pool, isolated scheduler job failures, bounded histories/maps, gzip/versioned assets and local font. Single-process operational restriction remains. |
| U2 | Real session-bound upload/validation, owner-password + confirmation phrase restore, real result feedback, no simulated progress or TOTP barrier. |
| U3 | Resume collected birthday/gender registration steps, Persian/Arabic numeric input, Gregorian/Jalali month/leap validation; registration age policy unchanged. |
| U4–U5 | Queue/cost messaging, connection-failure compensation, chat-ID-bound controls and persisted stale-control checks. |
| U6 | Recoverable browser API errors and measured/null monitoring instead of invented billing/resource history; backup export/delivery/validation labels separated. |
| U7 | Shared input label associations, modal focus/keyboard/Escape support, reduced-motion/mobile layout and local font. **Not an exhaustive WCAG or three-language translation audit.** |
| Q1 | SQLite regressions, real PostgreSQL concurrency/restore tests, browser core-flow checks and explicit failure tests. Not a production load/soak or live payment-gateway test. |
| Q2 | Reproducible editable recovered frontend entry, separate error source and extracted password component. **Partial modularization only**: original JSX is missing; most legacy screens/vendor code remain in the recovered entry. |
| Q3 | Versioned forward migration on top of legacy additive startup, current environment/operations docs and CI workflow. Not a full Alembic conversion. |

## Local verification

Commands and final counts are recorded in the delivery message. Coverage includes:
- Old-user search outside the former 500-user window, literal normalized search and pagination.
- Refresh + CSRF mutation, session revocation/rotation, unauthorized chat access, login failure persistence and forwarded-IP spoof resistance.
- Payment amount/payer/currency validation and idempotency, gift-recipient refunds and ambiguous-refund reconciliation.
- Inbox persistence/dedup/retry, archive authentication/tamper/rollback, session-bound uploads and concurrent upload-slot budgets.
- Broadcast creation/count/cursor resume and a failed gateway target, bounded unknown telemetry, bot restore expiry, resumable signup and calendar/digit checks.
- Browser login, refresh, mutation, old-user/Persian-digit search, Escape modal handling, offline recovery, phone viewport, monitoring/backups/security render, password + session rotation, with zero page errors in that run.
- Exact frontend rebuild checksums, dependency audits and whitespace checks.

## Still requires staging / follow-up

1. Docker image build and GitHub-hosted CI passed before PR #3 merged. Production container startup/health still needs verification; a subsequent Railway log exposed missing/invalid public-origin configuration, covered by the startup-origin hotfix and regression tests.
2. Railway proxy/cookie/IP configuration, live Telegram/Bale webhook/polling/payment/backup delivery, load/soak comparison and measured billing. No numerical cost savings are asserted.
3. Full screen-by-screen frontend decomposition, assistive-technology testing, exhaustive fa/en/ru translation/UX review and broader failure-injection coverage.
4. Any multi-process/replica support. Keep one process/replica; SQL locks alone do not coordinate local caches, restore gates, throttles or broadcast ownership.
5. Owner's age-policy decision. Existing unrestricted age-pool behavior was not silently changed.

See [deployment](DEPLOYMENT.md), [security](SECURITY.md), [backup/restore](BACKUP_RESTORE.md), and [frontend provenance](../frontend/README.md) before accepting a production rollout.
