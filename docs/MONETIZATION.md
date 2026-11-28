# Monetization and user-menu revision — 2026-11-28

## Scope and navigation

Implemented for Telegram and Bale with the existing wallet/invoice system; no new gateway, card data, random reward, cash-out, recurring billing or automatic renewal.

- Main menu: primary search/active-chat action first; profile and friends share a row; anonymous links/messages stay together; one **Shop & VIP** entry instead of separate product buttons. Wallet **Buy coins** still goes directly to the existing coin packages/custom invoice.
- Shop is organized around needs: VIP/passes, games/starters, identity/links, gifts. Price/details → free preview → immutable final quote → explicit payment. Ownership is shown; redundant permanent-pack purchases are rejected.
- Active-chat shop screens have a direct return to chat controls/report/block. Games have a separate persistent inline board, so ordinary menu replacement does not destroy the 3×3 game keyboard. Stale moves and old-chat actions are rejected server-side.
- Relevant entry points: activities/gifts inside a chat, paid reconnect below the partner-left notice, topic-link upgrade next to the ordinary anonymous link, and upgrade guidance when a sample/unlock reaches its limit. No unsolicited scheduled sales messages or invented countdowns.
- Persian, English and Russian bot labels; RTL admin screen. Safety/report/block remain free. Request/gift opt-outs are free and visible in the shop settings.

## Configurable defaults (not an agreed market valuation)

Admin panel → **محصولات و اشتراک** (`/monetization`, permission `store.manage`). Operators can change all prices, stop new sales, create quota/expiry-bound codes and disable codes. Mutations require the normal authenticated session, CSRF and audit trail. This does **not** add TOTP.

| Product | Initial price | What it provides |
| --- | ---: | --- |
| VIP | 300 coins | 30 days: starters, hosted games, six topic links and both card themes |
| Message pass | 20 coins | 60 minutes with zero active-chat per-message coin fee; anti-spam/moderation remain |
| Starter pack | 10 coins | Permanent starter questions; first question free |
| Games | 25 coins | Permanent hosting of tic-tac-toe and three-round cooperative clue challenge |
| Ocean / garden card | 15 coins each | Permanent selectable introduction-card frame; plain card remains available |
| Topic links | 20 coins | Up to six independently deletable anonymous topic links |
| Rose / coffee / book | 3 / 5 / 8 coins | One fixed-content virtual gift to the current partner, no redeemable value |
| Reconnect | 1,000 Toman reference | Converted once to `ceil(reference / tomanPerCoin)` wallet coins, final price confirmed before the request |
| First-purchase bundle | 100 base coins + 50% bonus | One immutable invoice: initially 150 coins for the price of 100 |

With the existing initial rates of 200 Toman/coin and 20 coins/Star, reconnect is **5 wallet coins**. This is a store pricing reference, **not** a claim that Stars convert to Toman at the same rate. Coin top-up invoices show their platform's real currency/amount. Default first bundle: Telegram 5 XTR; Bale 200,000 IRR (20,000 Toman), subject to the platform payment toggle/token. No fake scarcity or reduced-price claim is displayed.

VIP does **not** include a message pass, paid visual gifts, queue priority or age-policy changes. Permanent packs survive VIP expiry; VIP-only access expires, including premium topic links/themes until renewed or separately unlocked. A pass/VIP renewal extends from the later of now and its current expiry. Timed gifts activate immediately for the recipient, not the payer. Confirmation identifies the recipient.

## Reconnect consent and accounting

No arbitrary paid “last message” is delivered. Only one neutral reconnect request is allowed per ended conversation:

1. Previous conversation ended by leave/next within ten minutes; both users active/registered, neither blocked the other, no report on the chat, no newer/active conversation, recipient has not disabled reconnect.
2. Requester sees the frozen price and explicitly requests sending. Insufficient balance prevents sending. At most three confirmed requests per requester per ten minutes. Declined/expired requests cannot be retried for that old conversation.
3. Recipient can accept or decline freely within two minutes. No response/decline costs nothing. Requester cannot accept their own request; old keyboards cannot reconnect a different pair.
4. Acceptance revalidates eligibility, debits only the requester, stops their current searches and creates the chat under the same PostgreSQL matching mutex as regular matching.
5. If either chat-control delivery fails, the chat is closed and cost returned once. Interrupted handshakes are recovered/refunded by the scheduler after timeout.

The debit is a transactionally reserved charge immediately after acceptance; it becomes successful when both delivery acknowledgements arrive. This is not atomic with the external messaging APIs. A hard crash after external acknowledgement but before recording can therefore cause conservative compensation, not a second charge. Search queue removal is not automatically reversed after a failed handshake; both users get a fresh search action.

## Gifts, games, quotes and codes

- Wallet quotes last five minutes and store price/discount/recipient server-side. Price changes do not silently change a valid quote. Purchase, entitlement and ledger update commit together. Repeated confirmation cannot charge twice.
- Subscription/pass gifts: same-platform friend or current partner; visual gifts: current partner only. Blocks, opt-outs and relationship/chat state are checked again before payment. Timed gifts remain durably active if their notification fails. Failed visual delivery refunds once. Paid visual sends interrupted for over ten minutes are conservatively refunded by recovery; external sends are not exactly-once.
- Games require partner acceptance. One free **hosted** trial, consumed only on acceptance; guest always free. After the trial, host needs Games or VIP. SQL stores board/clues, player message IDs and version. Both players may stop; games expire after twenty minutes. Expiry/ending a chat makes old boards non-actionable; Telegram may still display an old board until interaction.
- Separate topic links use fixed topics (questions, feedback, friends, work, ideas, other). They preserve the anonymous-thread blocking/reporting path; topic appears on incoming content. Deleting a link or losing access invalidates it, including an already-open compose flow. Existing ordinary anonymous links remain free.
- Codes are real operator-created records: unique ASCII code, future expiry, finite global quota, once per account. Choose **either** direct gift coins **or** 1–90% product discount. Discount is consumed atomically with the successful wallet purchase, not when typed. Final quote displays it; insufficient balance does not consume quota. Codes do not discount Stars/Bale coin invoices. Prices never become negative/free through rounding.
- First bundle is issued only before a recorded coin payment and uses one invoice per payer. Repeated clicks/restarts reuse its frozen terms, not mint new bonus invoices. Pre-checkout rejects it after another completed purchase; already-charged delayed settlement honors its immutable invoice (no loss of paid funds). Refund/replay rules are the existing invoice-order rules.

## Deployment and data lifecycle

- No new required environment variables. Existing persistent PostgreSQL, bot tokens, payment switches and secrets remain required as before.
- Startup migration `20261128_commerce_v2` creates eight separate tables without rewriting legacy users/payments. Migration is idempotent and tested against an already-hardened schema. Run **one process / one replica**, as before.
- Encrypted logical backups automatically include every new business table in FK-safe order. Restore keeps the inbox/excludes authenticated sessions as before. New-table backup/restore/account deletion was tested with real PostgreSQL FKs.
- Account deletion clears topic links, settings and entitlements, cancels unconfirmed purchases, and removes game/reconnect rows before deleting chat content. Financial quote/ledger rows and consumed-code IDs remain for reconciliation/quota integrity against the soft-deleted account, like existing payments; they do not retain gift text or display names.
- Chat retention removes game/reconnect children first. Old unused quotes and expired games are cleaned after seven days. Paid financial history remains; first-bundle invoice is retained so a restart cannot mint a second discounted order.
- Monitor failed/dead inbox updates, payment reconciliation alerts, ledger refunds and scheduler errors. Sales counts in the commerce panel are **wallet coin spending**, not revenue, settlement balance or Railway billing.

## Verification for this revision

Local isolated runs (no production database or live bot sends):

- `python -m pytest tests/ -q`: **179 passed, 13 PostgreSQL-only skipped**.
- `npm run test:postgres`: **13 passed**, including concurrent quotes/payment, last promo slot, immutable starter invoice, versioned game moves, reconnect accept replay, encrypted restore/deletion, and upgrade from old schema.
- `npm run build:panel`, `npm run check:panel`; browser regression on local seeded data: login/session/CSRF, search, password/session rotation, commerce price save/reload, real code creation/revocation, desktop/mobile layout.
- `npm audit --audit-level=high`: zero vulnerabilities; `pip check`: clean.

No production bot interaction, live payment, Railway metric or deployed smoke test is implied by these local checks. TOTP and the age policy are unchanged.
