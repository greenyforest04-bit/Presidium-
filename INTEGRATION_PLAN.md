# Corrected frontend integration (current direction)

The original Argon application is the primary frontend. The earlier test-shell expansion below is historical and does not define the current UI.

| Function | Previous local implementation | Verified Argon source | Chosen integration | Verification |
|---|---|---|---|---|
| Main frontend | Vite src/main.tsx -> App.tsx | app/layout.tsx -> app/page.tsx, original components/hooks/lib/public | Next App Router serves / on 5173; preserve the original screen composition and CSS | matching desktop/mobile screenshots, root HTML, production build |
| Identity | working Matrix SDK password/token/device | separate ChatGPT identity and Matrix wrapper | original AccountManager with a Matrix login/registration adapter; same Matrix token forwarded to existing FastAPI | real login, registration, same-browser switching, cookie Origin regression |
| Conversation | existing SDK transport and Rust crypto DB | original main conversation/inbox markup | lib/local-matrix.ts projects existing SDK events into original UI; no second persistent chat store | real two-way Megolm delivery, raw encrypted server event, reload, reconnect, no duplicates |
| Groups | explicit encrypted group marker and membership checks | original SpaceForm and MatrixInbox | create/join groups through the same existing transport | group invitation and cross-user delivery; direct/group guards |
| Feed/Stories | existing Python/TS domain services | original PresidiumFeed, PostComposer, StoryStudio | retain original UI and its real server publication contract; remove fake seed posts/stories | navigation, backend idempotency/permissions/revision tests; original local-only interactions remain labelled |
| Commissionka/entitlements | existing SQLite/TS service adapter | original Commissionka components and SubscriptionPanel | cookie proxy to the existing service; no Cloudflare bindings required | screen loads, backend checkout/dispute/retry tests; sandbox payments are explicitly simulated |
| Capsule/AI | backend private notes/AI provider gateway | original CapsuleUniverse, CapsuleNotebook, CapsuleAI | original account-scoped local notebook and agent UI; same backend AI contract; Matrix private messages excluded from local audits | notebook reload/account isolation, privacy regression, missing-provider tests |
| Styles/resources | test-shell layout | all original app CSS and public resources | exact original files, no whole-design rewrite | 34/34 CSS/resources byte-identical to verified canonical source |

Original source is preserved at presidium-argon-68/.integration/canonical. Temporary unmodified comparison copy: .integration/frontend-reference, port 5180. The supplied source uses Next App Router (its Cloudflare runner is not required for the frontend); therefore adopting Next retains the original framework rather than translating its screens to Vite.

The former Argon Matrix implementation is replaced with a type-only declaration contract. Existing src/core/messaging/MatrixTransport.ts is the single live transport. Server messages and device keys are not copied to product SQLite or the original local content IndexedDB. Old browser sessions and crypto prefixes remain intact.

Launch from presidium: `.\.venv\Scripts\python.exe scripts/run-local.py` after `docker compose up -d tuwunel`. Frontend development: `cd web; npm run dev`; production: `npm run build; npm start`. URL: http://127.0.0.1:5173/. FastAPI remains on 8000; Matrix on 8008.

# Earlier server integration record

# Argon 68 integration

Branch: integration/argon-68. Baseline preserved in presidium/.cache/integration-baseline-20261016-025126 (binary patches, index copy, worktree-full.zip). Existing staged work is not committed or reset.

## Verified baseline

Vite/React/TypeScript frontend: `cd presidium/web; npm run dev -- --host 127.0.0.1 --port 5173 --strictPort`. Matrix Tuwunel: `cd presidium; docker compose up -d tuwunel`, port 8008. Python FastAPI gateway: port 8000. Desktop Python/Qt is a separate client, not a second web transport. Browser authentication uses Matrix password login/token and device ID. Matrix server volume stores events; Rust SDK IndexedDB stores browser crypto; scoped localStorage stores sessions. Desktop history is encrypted SQLite, with keys derived from the unlocked identity.

Baseline browser test on the live homeserver passed (2026-10-16): Alice/Bob delivery, raw server event m.room.encrypted without plaintext, history after reload, conversation/account isolation, refusal to reuse plaintext rooms. Receipt in a second browser context is asserted.

## Approach

Keep the existing chat and Matrix SDK lifecycle. Reuse Argon's TypeScript domain services rather than rewrite their financial and entitlement logic. FastAPI remains the only application HTTP backend. A local Node 24 module adapter runs the recovered services using real SQLite with a D1-compatible interface; it has no listening port, authentication service, or independent deployment. Build the module with the existing npm workspace. Replace Cloudflare bindings with explicit local filesystem/SQLite adapters. Matrix whoami remains the identity authority; Argon device records mirror this authenticated session and never introduce a second login/token in the browser. A separate product database is justified: the existing desktop DB is private encrypted message history, not the web product domain. No Matrix messages/keys are copied into product tables.

| Function | Current | Argon code | Decision | Verification |
|---|---|---|---|---|
| Accounts/devices | Matrix identity/device | account-service, device-security-service | Matrix-authenticated profile/session mirror; existing login plus registration UI | token isolation, revocation, revision conflicts |
| Direct chat/E2EE | live SDK Megolm | separate SDK wrapper/outbox | keep current direct guards and SDK; add group-specific marker and encryption checks in the same client | live two-browser regression before/after |
| Groups/channels | absent | SpacesService with roles/invites | reuse original Spaces service; explicit Matrix group chats through existing client | owner/member restrictions, invite replay/revoke, live encrypted group delivery |
| Posts/Stories | absent | SocialService + revision/TTL/media schemas | reuse server services and local media adapter | idempotency, wrong owner, revisions, TTL |
| Comments/subscriptions | absent | local-only in Argon UI | explicit persistent domain extension, no fake network status | cross-user persistence and ownership |
| Commissionka | absent | MarketplaceService, schemas, integer pricing | preserve original logic, filesystem photos, configured sandbox only | checkout races, request replay, disputes, no real payment claims |
| Plans/permissions | absent | PLANS, subscription-service, billing webhook | reuse server authorization; no client plan escalation | expired plan restrictions, owner deletion, signed billing |
| Capsule/AI | absent | notebooks/local storage + agent quotas | account-scoped persistent notes and original provider contract | isolation, persistence, missing configuration, budget/retry |
| SQL migrations | desktop schema only | D1 SQLite 0000..0009 | versioned independent product schema, unchanged original SQL plus 0010 domain extension and 0011 sandbox payment evidence | empty install and populated upgrade copies |
| Assets | test-shell identity (superseded) | original frontend and editorial files in Git | full original frontend/assets now primary; see corrected direction above | production asset loading and visual comparison |

## Required financial repair first

Preserve buyer_total_kopecks. Infer exactly one missing component by subtracting every known component from the locked total; ambiguous/negative/non-integer values are diagnostic conflicts. Never automatically cancel duplicate active/paid orders or dismiss disputes. Migration must stop until an authorized resolution exists. Regression checks use disposable SQLite copies; no --apply on user databases.

## Acceptance

Typechecks; Python tests; npm unit tests; production build; real SQLite domain integration tests; live Matrix browser regression; integrated browser workflows (profiles, social, spaces, checkout, Capsule); restart persistence. External billing/provider success must be distinguished from sandbox or mock-provider checks.

## Implemented boundaries

Local Node 24 is an execution dependency of FastAPI, not a network service. Cloudflare D1/R2 and gateway account headers are not required or trusted. The product database is presidium/.data/product/product.sqlite3; media is stored alongside it. In Docker, use the product_data volume. The original Argon chat wrapper, browser-selected plan state, and gateway password authorization are deliberately not activated alongside Matrix.

Source services are adapted where real tests found defects: channel media visibility, post attachment linkage, moderator queue access. Argon local-only comments, subscriptions to authors, and Capsule were replaced with authenticated server operations, not claimed to have been server features in the source. Advanced local-only Argon UI widgets (Story layer editor/polls, extra notebook views) are not copied wholesale; completed server workflows are exposed through the existing visual identity. No claim of live AI or real money processing is made.
