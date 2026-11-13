# Compliance

## Minors (13-17 pool)
- Telegram ToS (verified): only an 18+ sign-up clause for EU/UK/Australia exists; no bot-specific minors policy found in ToS / Bot Developer Terms / Stars ToS.
- Bale terms (bale.ai/terms, verified): no general minimum age; financial transactions require 18 (solar) years; prohibited-content rules apply to bots; bots get base profile data only.
- GDPR Art. 8: 16 default consent age (member states may lower to 13); COPPA: under-13. Our floor is 13 with hard 13-17/18-60 pool separation.
- Safeguards implemented: mandatory rules+privacy acceptance, profanity/contact filters, per-media admin switches, reporting with 3-strike auto-ban, progressive bans, moderation of reported content, account deletion, 30-day retention, no cross-platform photo transfer, no purchases required to chat.
- **Unresolved**: neither platform documents age-verification requirements for anonymous-chat bots; Iranian law (e-commerce law arts. 58-59, Supreme Cyberspace Council messenger regulations) adds data-localization/consent duties for processors — non-binding legal advice; consult counsel before enabling minors at scale. Documented decision: registration 13-60 implemented with strict pools; an `allowNewRegistrations` kill-switch exists for emergency disable.

## Payments
- Telegram: Stars only for digital goods (platform rule) — implemented.
- Bale: wallet payments implemented per docs; refunds manual (no API) — disclosed in product.

## Privacy disclosures
30-day retention, media metadata only, backup archives may retain older text (disclosed in-bot in all 3 languages), audited history access (Owner: all; others: reported only).
