# Compliance and policy boundaries — 2026-11-28

This is an implementation note, not legal advice or a fresh verification of platform terms.

## Age policy: decision pending
The hardening revision does **not** change registration or matching age policy. Current code retains the existing 13–60 registration range and no enforced minor/adult matching-pool separation. Previous documentation claiming strict 13–17 / 18–60 pools was inaccurate. Age is self-reported, not verified. A future age-policy change requires the owner's explicit choice and an appropriate safety/legal review. The registration kill switch can disable new signups but is not an age-verification mechanism.

Do not assume platform access makes an anonymous-chat service compliant for minors, particular jurisdictions or data-residency obligations. Review current Telegram/Bale terms, local law, consent and safeguarding requirements before expanding access.

## Privacy
- Registration/rules/privacy flows and three-language bot text remain; this revision does not constitute an exhaustive translation or accessibility audit.
- Runtime chat retention defaults to 30 days, configurable by the operator. Media handling stores metadata/file references; profile photos are not transferred cross-platform.
- Conversation-history access is authorized separately and audited. Ordinary support permissions must not imply unrestricted chat access.
- Deletion scrubs personal profile identifiers and removes conversation/support/social data while preserving pseudonymized financial history. It is not a promise to erase statutory transaction records or data in already-delivered backups.
- Encrypted backup copies may contain older and deleted personal data. The operator controls Telegram-copy retention and access; changing a panel retention value does not remotely delete those copies.
- Admin/security audit logs and pending/dead event payloads can also contain personal information. Restrict access and review retention/replay operationally.

## Payments
Telegram digital goods use Stars; Bale payments use the configured wallet provider. Bale refunds are manual. Telegram refunds with uncertain gateway outcomes remain pending for reconciliation rather than silently returning coins. Spent/gifted/deleted-account cases can require human review.

## Release boundaries
No TOTP was added or re-enabled. No production Railway billing savings, distributed-processing safety, end-to-end exactly-once delivery, full WCAG certification or legal compliance certification is claimed. See security, deployment and backup guides for the enforced controls and residual risks.
