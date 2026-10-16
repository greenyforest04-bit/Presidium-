## Авторизация завершена — 16 октября 2026

Ветка `feature/matrix-account-sessions`. Точка восстановления до auth:
`presidium/.cache/auth-baseline-20261016-214849`. Главная показывает вход и
регистрацию; оригинальный frontend сохранён. Реальные Matrix-аккаунты, HttpOnly
сессии с истечением, выход/отзыв устройств, парольная UIA, точный поиск и DM,
изолированные заметки/медиа/ключи. Исправлены гонка регистрации и получение
новых сообщений после logout/login. Старый неполный отзыв сеанса профиля закрыт.

Production build/typecheck, 10 unit + 1 financial + 17 server + 4 browser — PASS.
HTTPS 172.20.10.6:5443 проверен с доверенным локальным CA на уровне HTTP-клиента.
Физический телефон и восстановление E2EE на новом origin/устройстве — ограничения.
Подробности запуска, проверок и восстановления: `AUTH_IMPLEMENTATION.md`.
Пользовательский staging сохранён; архивы/ключи/node_modules не добавлялись.

# Frontend correction — completed, 2026-10-16

Branch: integration/argon-68. The original Argon frontend is now the main application on http://127.0.0.1:5173/. Next production server, FastAPI8000 and Matrix8008 are running. Temporary reference5180 has been stopped; its source remains available for comparison.

## Preserved state and entrypoint

- Restore point: presidium/.cache/frontend-correction-20261016-035156/ contains full worktree ZIP, staged/unstaged binary patches, Git index, online SQLite backup and media copy. No reset/clean or user data deletion. Original Git index remains byte-identical after all work.
- Earlier main route was Vite src/main.tsx -> App.tsx. Current route is Next app/layout.tsx -> app/page.tsx. The old shell remains as source history and is not served by dev/start/preview.
- Original components/hooks/lib/public and all app CSS are copied from verified canonical source at presidium-argon-68/.integration/canonical. Import graph is saved in presidium/.cache/frontend-source-graph.json (137 reachable modules). 34/34 CSS/resource files are byte-identical. Original reference is in .integration/frontend-reference, separate from the main product.
- npm dependencies merged, no package/lock overwrite. Next 16.3.8 installed as a compatible security patch; npm audit after installation reported 0 vulnerabilities. Cloudflare Workers/D1/R2 are not assumed locally.

## Connected functionality

- Original navigation, main feed/tabs, Stories editor/viewer, Commissionka, Capsule, profile/settings and creation sheet are retained. No replacement demo page or new simplified shell.
- lib/local-matrix.ts and lib/matrix-connection.ts connect original main conversation/inbox/SpaceForm to existing src/core/messaging/MatrixTransport.ts. Same Matrix sessions, device IDs, direct/group guards, server history and Rust crypto prefixes. Matrix SDK is loaded only in the browser. argon-matrix-contract.d.ts is declarations only; the original alternate transport implementation is not shipped.
- Messages are an in-memory UI projection of SDK events, keyed by server event ID. Local pending events are not marked accepted. No second persistent Matrix history/outbox. Existing Python/TS domain backend integration is preserved.
- Original AccountManager uses real Matrix login/registration and account switching. One Matrix identity: Next forwards the same verified token to FastAPI using an HttpOnly SameSite cookie. Origin checking accounts for Next internal localhost versus browser 127.0.0.1 and rejects foreign origins.
- Original server API contracts are proxied explicitly, including Capsule /api/capsule-agent/task -> existing /api/capsule-agent. Profile PATCH and session revocation map to existing API operations.
- Original local content, notebook, agent memory and marketplace drafts are scoped per Matrix account. Main state update/backup paths exclude Matrix messages. Local agent audits explicitly exclude network messages. Demo posts/stories are not automatically substituted for user data.
- Fixed direct-invite/open race: only group invites use group-join logic; existing direct acceptance remains with the existing transport. Notifications moved above the composer to avoid blocking Send.

## Verification performed on the actual product

- Production build: npm run build PASS, including original frontend and domain module. npm run typecheck PASS.
- npm test: 9/9 PASS (direct/group isolation, encryption guards, session scoping, pending-event confirmation, cookie Origin, private Matrix exclusion from Capsule, API mapping).
- npm run test:argon: 1/1 financial regression PASS. Existing paid totals are preserved; ambiguous obligations remain conflicts. No --apply on user database.
- Python relevant regression/integration suite: 13/13 PASS on separate test databases. Clean install/populated upgrade, rights, revisions, retries, checkout/disputes/restart and existing Matrix/desktop regressions included. Used a dedicated workspace temp directory after Windows pytest temp cleanup permissions failed; successful rerun exit0.
- Playwright on production: 3/3 PASS in 49.7s. New real Matrix accounts, original UI login, encrypted two-way delivery, raw server m.room.encrypted without plaintext, group invitation/join/delivery, reconnection, history after reload, no duplicates, room/account isolation, account switching and original device preservation. Browser registration, original Capsule notebook save/reload and isolation across accounts also passed. These Matrix checks used the live local homeserver, not mocked delivery.
- UI checks: desktop1440x1000/mobile390x844. Main screen, chat list, conversation, feed, Commissionka, Capsule and creation menu captured for both original reference and main product: 28 comparison screenshots. Profile/settings additionally checked and captured at both widths. No page JavaScript errors or failed style/resource requests in the checked routes.
- HTTP checks: primary root127.0.0.1 and localhost5173 HTTP200, API /health200, Matrix /_matrix/client/versions200. git diff --check passes; existing artifact line-ending warning does not change staged work.
- Lint exits0 with warnings in the imported original UI; it is not warning-free.

Evidence: presidium/.cache/frontend-correction-browser/comparison.html, playwright-production.json, live-chat-evidence.json, assets-styles-verification.json, reference-screen-report.json and lint.log. Reference contains original sample content; main screenshots show real test dialogues or an empty account. Differences in data and availability/delivery labels are intentional. Screenshot verification is separate from the raw network-event evidence.

## Launch

From the Presidium repository root:

```powershell
cd presidium
docker compose up -d tuwunel
.\.venv\Scripts\python.exe scripts/run-local.py
```

Frontend alone from presidium/web: npm run dev. Production: npm run build then npm start. Main URL: http://127.0.0.1:5173/. Sign in/register: Сообщения -> Сетевые чаты. Backend remains8000; Matrix homeserver8008. The launcher requires existing Node24/npm and Python environment; .env.local is optional and ignored, .env.example contains setting names without secrets.

## Remaining limits

- Matrix attachments, voice/video messages, message editing/deletion, device trust UI and key recovery are not connected to the existing wrapper. They are disabled or return an explicit unavailable error. No fake send/read/payment result.
- The original SpaceForm channel editor is not connected; real server channels remain available in Account -> Groups/channels. Some original feed interactions (comments/likes/follows) and Capsule notebooks are local and labelled accordingly; preserved server APIs remain available.
- Cloud AI is not configured locally: CAPSULE_AGENT_URL and CAPSULE_AGENT_TOKEN are required. GET configuration now reaches the real gateway; local deterministic audits remain available and do not include Matrix messages. External connectors/voice services remain unconfigured.
- Real billing/payment provider is absent. Existing sandbox orders are explicit simulations; backend checkout/dispute/retry tests use isolated fixtures, not real charges.
- The user's preexisting browser database was not exported/reset. Preservation of its naming/session migration paths is confirmed by code; live same-device reload/switch tests used separate test accounts.

# Earlier server integration record (historical)

Branch: integration/argon-68. Integrated local implementation running; acceptance checks passed (see verification below).

- Baseline saved: binary staged/unstaged patches, index backup, 275 working files in presidium/.cache/integration-baseline-20261016-025126/worktree-full.zip.
- Baseline Vite 5173 and Matrix 8008 answer HTTP 200. Live encrypted delivery/isolation/reload regression passed in 29.3 seconds.
- AGENTS.md not found in project/parent directories or supplied code.
- Supplied archives recovered only under presidium-argon-68/.integration after SHA-256, size, path, symlink and ZIP CRC checks. Superseded archives with mismatching hashes are rejected, not used.
- Binary history parts concatenated in order. Bundle verifies; git fsck --full passes; 68 commits; HEAD 5ad57e23788b5add024b48591ba65d98d600b557.
- Source comparison uses Git blobs (Windows checkout changes line endings). Full original assets recovered from Git. Detailed inventory: presidium-argon-68/.integration/verification.json.
- Reports read as claims; actual code confirms Cloudflare adapters, separate gateway identity, local-only social interactions, missing live Matrix evidence in Argon, unsafe financial remediation. Current live Matrix is the baseline to preserve.
- Implemented FastAPI -> local Node 24 module -> SQLite/filesystem adapter. No additional HTTP server or login provider. Original TypeScript business services retain provenance; current Matrix transport and crypto stores remain in use.
- Integrated navigation: profiles/device access revocation, server-checked subscriptions, posts/media/comments/follows/24-hour Stories, spaces/channels/roles/invites, encrypted Matrix group conversations, Commissionka moderation/photos/favorites/orders/disputes, private persistent Capsule notes/tasks and original cloud AI contract.
- Financial repair now preserves locked buyer total; exactly one missing fee is inferred from all known components. Ambiguous/negative values, duplicate paid orders and open disputes produce conflicts without arbitrary status changes. Regression checks run only on disposable SQLite databases. No --apply on user DB.
- Original migrations 0000..0009 unchanged; 0010 adds local comments/follows/Capsule; 0011 records explicit sandbox payment provenance. Ledger records hashes, locks migrations transactionally and refuses inconsistent financial obligations before 0009.
- Real browser integration passed with two newly registered Matrix accounts: profile edit, cross-account post/comment/follow, Stories, channel invite/join, Capsule persistence/isolation, encrypted group invite/join/delivery (raw event encrypted), photo upload, authenticated moderation, checkout and sandbox payment. Test-only Business entitlements are explicitly seeded; payments are simulated, not charged.
- Found and fixed during live testing: unbound crypto.randomUUID breaking React rendering; binary upload route querying cached request query parameters; private channel media authorization; media attachment linkage on post edit; Capsule replay; concurrent migration startup.
- Full Python suite: 353 passed, 2 skipped (Windows mlock/liboqs availability), 105.60 s. Targeted financial settlement tests after the final guard: 5 passed (16.01 s); includes refusal to close unverified obligations and explicit sandbox-only resolution. npm unit tests: 5 passed. Financial remediation regression: 1 passed. TypeScript app + recovered services: passed. Oxlint: passed without warnings. Ruff syntax/undefined-name checks: passed. Production build: passed (Matrix SDK bundle size warning remains).
- Final combined live browser run: 2 passed in 45.1 s. Private messaging covers delivery, reply, reload, account/room isolation and refusal of plaintext rooms; integrated workflow includes encrypted group delivery with raw ciphertext assertion. Evidence: presidium/.cache/argon-final-browser/.
- Docker image presidium:argon-integration built successfully. Container smoke test passed: API/Qt imports, Node 24, real SQLite migrations and profile service on a temporary database.
- Local API stopped/restarted using scripts/run-local.py --sandbox. SHA-256 of ordered rows in profiles/posts/Capsule/orders matched before/after (12 profiles, 4 posts, 4 notes, 3 sandbox orders); real Matrix-authenticated HTTP API and subscription configuration checked after restart. No data reset.
- Existing Git staged object IDs verified unchanged against saved index. Recovered source, archives, runtime data, secrets, node_modules and generated module remain excluded from commits.
- Added explicit sandbox payment receipts. Dispute resolution without a receipt refuses with settlement_not_verified; real/imported financial obligations cannot be closed just by changing a status. No real payment/refund executor is connected.
- External AI provider and real billing provider are not configured. Missing AI returns explicit 503. Webhook signatures/replays tested locally; actual provider response/payment not claimed.
- Local URLs: frontend http://127.0.0.1:5173, API http://127.0.0.1:8000, Matrix http://localhost:8008 (server API root, not product UI).
- Restart command from presidium/: .venv\Scripts\python.exe scripts\run-local.py --sandbox (Node >=24, installed npm dependencies, running Matrix required). Matrix: docker compose up -d tuwunel.
- Source of truth for next verification: tests/test_argon_integration.py, web/tests/e2e/private-messaging.spec.ts, web/tests/e2e/integrated-product.spec.ts, argon/tests/remediation.test.mjs. Latest successful integration browser artifacts: presidium/.cache/argon-settlement-browser/ (final guard, 1 passed, 1.8 min); combined private/product run: presidium/.cache/argon-final-browser/.

Do not apply migration remediation to user databases. Do not commit source archives, nested Git repository, local DBs, secrets, or node_modules. No reset/clean/stash of user work.


## Reproduce / continue

From `D:\Неудашки\неудашки\Presidium\presidium` in PowerShell:

```powershell
docker compose up -d tuwunel
npm.cmd ci --prefix web
npm.cmd run build --prefix web
.venv\Scripts\python.exe scripts\run-local.py --sandbox
```

The local launcher builds the domain module, uses .env.local when present, starts API 8000 and Vite 5173 if absent, waits for readiness, and preserves Matrix/data. Node >=24 and the existing Python virtual environment are required. Python dependencies: `poetry install --with dev`. Stop launcher with Ctrl+C to stop only services it started. Existing Vite may already be running from the IDE.

Demo logins remain `demo_alice_da1f` and `demo_bob_da1f`, password `Demo-efc8803c3a10`. They received explicit **local test** Business for seven days; Alice is a locally configured moderator. These are development accounts, not user password resets. New accounts start Free; use scripts/grant-local-plan.py explicitly to exercise paid features locally. .env.local contains sandbox/moderator settings only and is ignored by Git.

Checks:

```powershell
.venv\Scripts\python.exe -m pytest tests -q --tb=short --timeout=40 --basetemp=.cache/pytest-review -o cache_dir=.cache/pytest-review-cache
npm.cmd run lint --prefix web
npm.cmd test --prefix web
npm.cmd run test:argon --prefix web
npm.cmd run build --prefix web
cd web
$env:PRESIDIUM_TEST_OUTPUT_DIR='../.cache/browser-review'
npx.cmd playwright test tests/e2e/private-messaging.spec.ts tests/e2e/integrated-product.spec.ts
```

Use a fresh browser output directory: the original user-staged artifacts must remain intact. Integrated browser test uses two disposable real Matrix accounts, explicitly seeds their test Business subscriptions, and uses the test moderator configured in .env.local. It removes its own public content and deactivates disposable accounts; sandbox order audit rows remain. Run only against a development database/homeserver.

## Remaining limits and external settings

- Real AI not exercised: configure CAPSULE_AGENT_URL (HTTPS endpoint using the documented Argon JSON contract), optionally CAPSULE_AGENT_TOKEN, and all three CAPSULE_AI_* pricing/budget settings. Budget reservations and duplicate requests are server checked; missing configuration is explicit.
- Real billing checkout/payment/refund/settlement is unavailable. Configure a billing provider and PRESIDIUM_BILLING_WEBHOOK_SECRET to accept signed subscription events. Marketplace sandbox never charges money; imported/real dispute settlement stays blocked until a real settlement interface exists.
- Story text/media/expiry is integrated; the old local layer/poll editor and extra notebook presentation views were not recreated as unverified server features. Old Argon gateway login and alternate Matrix wrapper are intentionally not activated.
- Profile/social/marketplace/Capsule data is server-side product data; only Matrix conversations claim E2EE. No production load test: SQLite writes are serialized and each business request starts a local Node module. Matrix SDK assets are large (build warning), not a build failure.
- Browser crypto keys stay in IndexedDB. Cross-device key recovery/backups and full device verification UX are not implemented by this integration.
