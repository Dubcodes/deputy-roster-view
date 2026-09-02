# Re-Deputy Pre-Deployment Safety Log

## 0.5.1 release-candidate closure

Starting baseline: `c29f918f88e98f366add4b1ff9fb52a9198192e8` (`0.5.0`).

Scope: account/crew separation, explicit external contractors, invitation lifecycle, per-account trusted-device LRU cap, conservative Travel participant union, narrow racing gap-fill verification, quieter semantic history, targeted placeholder filtering, limited responsive cleanup, and a tracked hardened Portainer Compose definition.

Safety boundaries retained: production was not deployed or restarted; no live Deputy tenant was contacted; Deputy write mode remains off by default; contractors, Travel, vehicles, Open/TBC, and background work cannot enter Deputy mutation paths. `SIGNUP_ENABLED=true` and `COOKIE_SECURE=true` remain deliberate production settings in the canonical root `docker-compose.yml`; no host application port is published.

Release selection is now the immutable Git tag `v0.5.1`, created only after exact-release-SHA CI succeeds. Validation evidence and the final tag SHA are recorded in the release handoff rather than guessed before CI.

## 2026.08.21.7 account/onboarding closure

Phase 0 first reran the canonical deterministic offline release gate against the unchanged 2026.08.21.6 state-machine foundation; it passed, including sync generations, integrity settlement/deduplication, production-shaped interpretation fixtures, migration rehearsal, SQLite integrity/foreign keys, and collision audits. Phase 1 then added hash-only ordinary invitations, independent Re-Deputy/Deputy email handling, the central 4–32 digit PIN contract, healthy no-Deputy behavior, installation-level URL forms, and notification navigation fallback. No live Deputy access/write or deployment was performed.

Final review on 2026-08-24 closed these release blockers:

* invitation activation, optional encrypted credentials, sync state, and invite consumption now commit in one `BEGIN IMMEDIATE` transaction; forced credential failure rolls the account back and concurrent use creates exactly one account;
* invalid, expired, revoked, replayed, and validation-failed invitation POSTs return a private/no-store rendered error without putting the raw token in a redirect `Location`;
* Admin no longer decrypts every user's Deputy email into the broad page context; one authenticated, private/no-store endpoint loads one selected user's email on demand, while Settings decrypts only the current account's email;
* a blank or invalid stored Deputy URL falls back to the valid installation URL;
* null/rejected notification navigation and rejected focus open the safe same-origin target, covered by an executable service-worker smoke;
* migration rehearsal seeds representative account, encrypted-secret, sync-state, trusted-device, and crew/team data before recreating the invitation table.

Evidence: the canonical offline gate passed; the complete local gate passed at both 320px and 375px; the seeded migration ran twice with `integrity_check=ok`, zero foreign-key rows, write mode `off`, and zero assignment/link collision groups. No live Deputy access or mutation was used. Final status: code-level release blockers are FIXED; commit-SHA CI and production-host deployment checks remain required.

Purpose: maintain a durable record of significant pre-deployment defects, investigation results, attempted fixes, validation evidence, remaining risks, and release decisions so the same work is not repeated.

Do not delete old entries when an issue is fixed. Update its status and add the release/commit that resolved it.

## 2026.08.21.2 sync integrity and interpretation closure

Real synchronisation proved that per-event partial-coverage warnings were being
queued during intermediate capture states, causing an operator notification
storm. The notification gate now waits for all sync activity to stop and for a
90-second settle period, then produces one deterministic aggregate from the
committed current coverage state. Unchanged aggregate signatures dedupe through
the existing durable notification-event revision key; write safety and personal
notification classes are unchanged.

Real roster notes also exposed a legacy `WORD remaining-text` compatibility
parser that promoted unrecognised names such as Troy, Nate and Leger to vehicle
labels. Only the authoritative affirmative vehicle parser may now create a
crew allocation. Personal Settings/error diagnostics now use owner-scoped
captures and source payloads; normal users no longer receive global sync-log
or capture data. No schema migration, live Deputy contact, or deployment was
performed. Final status pending release-gate evidence.

## 2026.08.21.1 canonical interpreted-workday closure

The 2026.08.20.3 correction joined touching vehicle/production rows and shared
the note grammar, but the interpreter still lacked an explicit preceding
Travel/Overnighter evidence input and did not retain all unresolved note-only
people as first-class diagnostics. Its day route also did not pass the same
structured identity evidence used by notifications.

The canonical interpreter now accepts current and preceding structured/source
evidence. It keeps current-day note and structured precedence, then applies a
single unambiguous preceding Travel note or structured vehicle only for the
same resolved person. Travel rows are retained as provenance rather than
merged into the following workday. Identity resolution remains ID/exact-name/
observed-display/confirmed-alias first and only permits unique first-name
resolution inside the already isolated cohort. `qua684` is explicitly mapped
to `684`; other `QUA###` labels are preserved without generic rewriting.

Regression coverage includes Taupo touching rows, separated duties,
multi-person Taupo/Ruakaka notes, #13/#14 and Danny/Esq separation, note-only
diagnostics, cross-location Grant isolation, direct LAN and trusted/untrusted
proxy mutation routes, notification dedupe, and the preceding-Travel API.
No live Deputy tenant was contacted or mutated. The complete deterministic
release gate and local 320px/375px Playwright gate passed; CI remains required
for the committed SHA and Docker image/container rehearsal.

## 2026.08.20.3 final deployment-closure correction

Starting source: clean `main`/`origin/main` at `f4491823df2e25aac02622f18bda1b15dd7d3a38`.

Independent review found three defects in `2026.08.20.2`: exact-boundary
Travel/vehicle and production segments were split into separate workdays; the
new interpreter maintained a second simplified `_note_vehicle()` parser that
could not interpret real multi-person notes; and strict origin comparison had
not been proven for the real public-HTTPS → cloudflared → HTTP-Uvicorn topology.

Root causes were an unconditional `start >= current_end` split, duplicated note
grammar/identity resolution, and comparing browser Origin only with the
internal ASGI origin without an explicit trusted-proxy boundary.

Fixes in build `2026.08.20.3`:

* connected cohorts join true overlaps and exact touching boundaries only when same-location vehicle/travel and production evidence are complementary; genuine gaps and unrelated touching production blocks remain separate;
* `app/roster_note_interpretation.py` is the single roster-note allocation parser/resolver used by day display, interpreted workdays and notifications, including multi-person lines, vehicle-first/last, `Rav`, `qua684`, truck-class notes, guarded aliases and cohort-unique first names;
* final vehicle diagnostics retain the final value/source, structured value/rows, note value/evidence, unresolved note evidence and source shift IDs;
* browser-visible forwarded origin metadata is accepted only from an actual peer explicitly matched by `TRUSTED_PROXY_SOURCES`; direct LAN requests use their actual origin and spoofed forwarding headers from other peers are ignored/rejected;
* Docker Compose defaults the trusted source to the controlled `deputy-roster-tunnel` DNS name rather than a transient container IP.

Regression evidence includes the contiguous Taupo `Rav91 07:30–09:30` +
`Sound/VT 09:30–19:30` workday/reminder, real Taupo and Ruakaka multi-person
notes, Gary #13/#14 and Danny/Esq separation, structured fallback, ambiguous
identity retention, repeated notification dedupe, four authenticated mutations
through trusted and spoofed proxy topologies, and retained direct LAN/strict
origin cases. No live Deputy mutation or deployment was performed.

Final status: FIXED in build `2026.08.20.3`; commit and CI evidence are reported with the release handoff.

## 2026.08.20.2 deployment-closure correction

Starting source: clean `main`/`origin/main` at `96432aa88247f86a3b2d63510960ac82ab56029b`.

The `2026.08.20.1` audit overstated several boundaries. Review found that the
four Settings OAuth handlers denied contractors but did not require Admin on
the server, contractor middleware blocked owner-scoped shift/map routes, the
workday notification projection grouped only by date/location, origin checks
did not consistently compare scheme and effective port, unresolved-write
alerts omitted `ambiguous`, and schedule extraction discarded useful state.

Root causes were duplicated presentation/server assumptions, an overly broad
contractor prefix gate, a lossy grouping key, duplicated hostname-only origin
checks, narrow alert SQL, and explicit extraction allowlists that had not kept
pace with the richer Deputy response shape.

Fixes in build `2026.08.20.2`:

* connect/callback/recheck/disconnect now call the server-side Admin guard while retaining initiating-user OAuth state ownership;
* contractors may reach authenticated track-map files and owner-scoped shift detail/mark routes, while cross-user IDs remain 404 and global/Admin/OAuth routes remain 403;
* day-header and notification workdays share the same overlap-cohort interpreter, current-note vehicle allocation overrides structured vehicle only on an exact person match, and rostered notification timing ignores personal time overrides;
* unsafe mutations compare normalized scheme, lower-case hostname and effective port (80/443), without trusting arbitrary forwarding headers;
* Admin Alerts include deduplicated `ambiguous` writes and credentialed, active, non-contractor primary syncs stale beyond the existing 36-hour threshold; a subsequent healthy sync creates a new stale episode boundary;
* management/shared schedule extraction retains editability, timesheet, approval/confirmation, meal-break, warning, open and publication evidence in stored raw payloads;
* identity matching remains exact/conservative; no broad fuzzy fallback was added.

Regression evidence is deterministic and production-shaped: two non-overlapping
same-place duties, overlapping production/vehicle rows, per-person roster-note
override, rostered-versus-personal reminder time, all four OAuth role matrices,
contractor ownership guessing, strict origin/port/scheme cases including LAN
HTTP, stale-reset and unresolved-write alert episodes, and rich field survival.
No live Deputy tenant was contacted or mutated and no deployment was performed.

Final status: FIXED in build `2026.08.20.2`; commit/CI evidence to be added after the release gate passes.

## Release history relevant to this gate

### 2026.08.13.2

Commit: `8f0809377e1da45af5dac895e6587081cf5ed66a`

Objective:
Separate Deputy read readiness from Deputy write readiness.

Implemented:

* Distinct connected/read-ready/write-ready states.
* Ordinary users may use verified Deputy read access without `Can_Roster_Manage`.
* Trial write readiness requires roster-management permission, trial mode and exact tenant allow-listing.
* Fresh permission/identity checks before mutation.
* Per-user OAuth identity/tenant/permission ownership checks.
* No cross-user credential fallback.
* No automatic/background Deputy write path.
* Re-Deputy Admin remains distinct from Deputy roster-manager authority.

Result:
Passed focused mocked integration testing. No live Deputy writes performed.

---

### 2026.08.13.3

Commit: `d01d6a358a739044e00cc4ca11b74d75a3acbd77`

Objective:
Broader hardening of the Deputy integration before deployment.

Important outcome:
Created the baseline subsequently used for the final closure review.

---

### 2026.08.13.4

Commit: `911158056cd9c81e09c5fef7c239fbdb0356c99c`

Objective:
Close identified pre-deployment safety gaps.

Implemented and verified by Codex:

* Corrected Resource/Roster Mealbreak interpretation using realistic `Slots`, `B`/`W` rows and localized `Mealbreak`.
* Added realistic Unix timestamp Resource fixtures.
* Introduced `adopted_existing` ownership for exact pre-existing Deputy rosters.
* Prevented ordinary adopted records from automatically gaining deletion authority.
* Added deterministic reconciliation for lost-response CREATE operations.
* Updated link provenance after UPDATE.
* Added live-state checking for nominally UNCHANGED operations.
* Added optimistic drift protection for UPDATE and DELETE.
* Hardened Deputy browser URL validation.
* Added first-pass iCal SSRF protection: HTTPS-only, DNS/IP validation, redirect validation, size and timeout bounds.
* Added HMAC-keyed login failure throttling.
* Hardened malformed OAuth responses.
* Added assignment-key/link collision audit.
* Added migration rehearsal.
* Updated runtime dependency pins after local vulnerability audit.
* Added GitHub Actions Release Gate.
* Updated README/AGENTS write-safety documentation.

Reported validation:

* Complete local offline smoke suite passed.
* Migration rehearsal passed twice/idempotently.
* Assignment-key duplicates: 0.
* Link collisions: 0.
* Local dependency audit reported clean.
* Responsive checks at 320px and 375px passed.
* GitHub Release Gate passed.
* Live Deputy API calls: 0.
* Live Deputy writes: 0.

Independent review result:
2026.08.13.4 is substantially improved but is NOT the final trial-write deployment sign-off because additional edge cases were found after the Codex pass.

---

## Open findings after independent review of 2026.08.13.4

### RD-SAFE-001 — Stale ownership after delete/rebind

Severity: BLOCKER for Deputy trial writes
Status: OPEN
Found after: 2026.08.13.4

Observed mechanism:

1. Re-Deputy creates a Deputy roster.
2. Link ownership is correctly `re_deputy_created_trial`.
3. Re-Deputy later deletes the roster.
4. The link remains; `deputy_roster_id` is cleared and state becomes deleted, but ownership remains `re_deputy_created_trial`.
5. The same stable assignment later becomes a CREATE candidate again.
6. An exact pre-existing Deputy roster is found, so runtime correctly determines the new binding should be `adopted_existing`.
7. `_save_roster_link()` reuses the existing `(tenant_host, stable_assignment_key)` row.
8. Its conflict-update clause does not update ownership.
9. The newly adopted external roster can therefore retain stale `re_deputy_created_trial` ownership.
10. A later DELETE may incorrectly pass the ownership gate.

Why existing tests missed it:
The 13.4 tests cover:

* fresh adoption → `adopted_existing`;
* adopted roster UPDATE preserving `adopted_existing`;
* ordinary adopted roster DELETE denied.

They do not cover:
`Re-Deputy create → delete → same stable assignment key → adopt externally-created exact roster → attempt delete`.

Required resolution:
Ownership must describe the CURRENT roster binding, not historical ownership of a previous roster ID.

Required regression cases:

* RD create → delete → external exact roster adopted → ownership becomes `adopted_existing` → DELETE denied.
* RD create → delete → no external exact roster → new POST-created roster → ownership becomes `re_deputy_created_trial`.
* adopted roster → UPDATE → remains `adopted_existing`.
* lost-response CREATE may acquire `re_deputy_created_trial` only after deterministic reconciliation proves that the transmitted CREATE produced the exact roster.

---

### RD-SEC-002 — iCal DNS rebinding / validation-to-connect gap

Severity: HIGH
Status: OPEN
Found after: 2026.08.13.4

Current protection:

* HTTPS only.
* No credentials/custom port.
* Resolver checks addresses and rejects non-global addresses.
* Every redirect is revalidated.
* Bounded redirects, timeout and response size.

Remaining problem:
The URL is resolved during validation, but the subsequent HTTP request is still made by hostname. The HTTP library may perform another DNS resolution when opening the connection.

Therefore:
Validation may see a permitted public IP while the actual socket connection could resolve the same hostname differently.

Required resolution:
The address actually connected to must be one of the addresses that passed validation, while:

* TLS certificate verification remains enabled;
* certificate hostname verification uses the original hostname;
* TLS SNI uses the original hostname;
* HTTP Host remains the original hostname;
* redirects are independently revalidated and repinned;
* private/local/link-local/reserved destinations remain impossible;
* TLS verification must NEVER be disabled to make IP pinning work.

Tests must demonstrate there is no second uncontrolled DNS lookup that can redirect the connection to a private address.

No live calendar feed is required for the test.

---

### RD-DEPLOY-003 — Public HTTPS deployment configuration

Severity: DEPLOYMENT CONFIGURATION GATE
Status: OPEN

Current tracked/default configuration still permits:

* `SIGNUP_ENABLED=true`
* `COOKIE_SECURE=false`

Required production decision:
For permanent public HTTPS access:

* public signup should be disabled once intended accounts exist;
* trusted-device cookies should be Secure when the installation is intended to operate HTTPS-only.

Important:
Do not blindly set `COOKIE_SECURE=true` if direct HTTP LAN access must continue to function. Document the chosen deployment model rather than breaking LAN access.

Potential safe improvement:
Investigate whether `SIGNUP_ENABLED=false` can safely become the shipped default while retaining first-user bootstrap. Existing application logic appears intended to permit first-user creation even when later signup is disabled; prove this with tests before changing the default.

---

### RD-PRIV-004 — Public GitHub repository contains operational information

Severity: PRIVACY
Status: OPEN

Repository visibility at review time:
PUBLIC.

Tracked operational information includes:

* the real Deputy installation hostname in configuration/default files;
* historical hard-coded shift context containing genuine dates, locations, crew identifiers/names and roles.

No obvious committed `.env`, SQLite production database, plaintext Deputy password, or similar secret was found during this review.

Required action:

* Remove unnecessary real tenant defaults from tracked configuration.
* Inventory hard-coded operational data.
* Do not silently delete operational fallback behaviour if it is still needed.
* Do not rewrite or force-push Git history automatically.
* Historical exposure remains even after current-tree sanitisation; repository visibility/history remediation requires an explicit separate decision.

---

### RD-CI-005 — CI coverage narrower than reported local release suite

Severity: RELEASE CONFIDENCE
Status: OPEN

GitHub Release Gate currently proves:

* requirements install;
* Python compilation;
* template rendering;
* Deputy release-gate smoke;
* security-closure smoke;
* release-integration smoke.

Codex's 13.4 report also relied on additional local checks including migration rehearsal, assignment audit, responsive tests and broader smoke coverage.

Required action:
Establish one clearly defined release-gate command/suite and align GitHub Actions with as much of that deterministic offline suite as practical.

At minimum consider:

* migration rehearsal;
* assignment/link audit;
* dependency consistency (`pip check`);
* dependency vulnerability audit;
* Docker image build;
* the major deterministic application smoke suites.

If browser-responsive testing is intentionally kept local because of CI browser cost/setup, document that honestly rather than claiming CI ran it.

---

## Current release decision

Build `2026.08.13.4`:

Normal Re-Deputy/read-only operation:
Strong candidate for deployment after environment/configuration checks.

Deputy trial writes:
NOT YET APPROVED due to RD-SAFE-001.

Public/internet-facing deployment:
Requires explicit resolution of RD-DEPLOY-003 and a privacy decision for RD-PRIV-004.

No production Deputy writes should be enabled while RD-SAFE-001 remains open.

Next intended build:
`2026.08.13.5` — targeted final closure pass.

---

## 2026.08.13.5 closure results

Resolution build: `2026.08.13.5`

Resolution commit: the release commit titled `Close final deployment safety gaps`.

All validation used deterministic fixtures, disposable databases, or a disposable CI container. No live Deputy tenant was contacted and no deployment was performed.

### RD-SAFE-001 — FIXED

Root cause confirmed: the stable-assignment upsert preserved historical ownership even when CREATE established a different current roster binding.

Fix:

* CREATE now explicitly replaces ownership for the new binding; ordinary UPDATE continues to preserve ownership.
* Adoption replaces stale provenance with `adopted_existing`.
* A successful Re-Deputy POST establishes `re_deputy_created_trial`.
* Network-error CREATE reconciliation can claim Re-Deputy provenance only after the POST was actually transmitted and deterministic exact reconciliation succeeds.

Regression evidence:

* Re-Deputy create → verified → delete → replacement POST → `re_deputy_created_trial` passed.
* Re-Deputy create/delete → exact external adoption → `adopted_existing` → later DELETE denied passed.
* Adopted UPDATE retained `adopted_existing`.
* Re-Deputy-created UPDATE retained `re_deputy_created_trial`.
* Pre-transmission timeout claimed no roster link.
* Post-transmission lost response reconciled exactly and retained valid Re-Deputy provenance.
* Existing identity, tenant, reference, permission snapshot, drift, Timesheet, overlap, read-back, and cross-user gates remained green.

Final status: FIXED in `2026.08.13.5`.

---

## 2026.08.20.1 correctness-release audit

Audit date: 2026-08-20 (Pacific/Auckland)

Starting source: `main` at `ab2725322c6f1c76f3454051f1691e9547b85466`, initially clean and aligned with the locally recorded `origin/main`.

This pass was performed without a live Deputy write and without changing the production data volume, application secret, accounts, credentials, OAuth records, trusted devices, maps, routes, Portainer stack, or tunnel configuration.

### Closed correctness and safety issues

* Shared Deputy schedule evidence is now observer-aware. A complete-looking personalized capture can retire only that observer's evidence and cannot delete a row still actively supported by another account. Legacy rows without observer evidence are preserved rather than guessed away.
* Availability now requires a raw Open flag and no assigned employee identity. Assigned rows that also carry `isOpen=true` remain assigned evidence and cannot become apply-able vacancies.
* Shared and extended-personal JSON ingestion follows bounded cursors, deduplicates by stable Deputy shift ID, detects repeated cursors, preserves successful early pages, and marks incomplete pagination non-authoritative.
* Overall sync health no longer lets a successful iCal fallback conceal failed or missing authoritative shared Deputy web coverage.
* Notification reminders operate on interpreted workdays, use rostered rather than personal timekeeping values, emit location/role/vehicle/start in that order, and support an exact one-hour reminder.
* Existing notification-enabled Admins receive a one-time default for the separately opt-out-able Admin Alerts preference. New error reports and both Making My Own Way audit transitions are deduplicated Admin-only alert sources.
* Lost/ambiguous CREATE reconciliation may establish equivalent business state but cannot fabricate Re-Deputy ownership or later DELETE authority.
* The obsolete trial-host allowlist was removed while retaining initiating-Admin, initiating-user OAuth identity, Deputy identity/permission, supported-operation, mapping, overlap, drift, Timesheet, read-back, and audit gates. Write mode remains off by default.
* Contractor accounts use the normal personal calendar/day/timesheet/settings/help flows while server-side enforcement denies shared/global and Admin access. Authenticated browser mutations are same-origin checked and `/sync-now` is POST-only.
* Settings no longer computes or presents Shared Crew Stats. Builder same-date conflict markers use canonical identity and refresh when the date changes.
* Guarded employee-name history and alias backfills preserve Deputy employee identity for the reviewed #7/#13/#14 evidence without guessing across IDs.

### Additive migrations

* `deputy_schedule_observations` stores first/last-seen and absence/active state per stable source shift and observer.
* `deputy_employee_name_history` stores observed Deputy names against immutable employee IDs.
* Notification preferences add `one_hour_before` and `admin_alerts`; the Admin default migration is guarded by an idempotent application setting.

Migration rehearsal ran twice against a disposable copy of the prior SQLite schema. Pre-existing user, trusted-device, personal-shift, shared-schedule, and capture row counts were preserved; `PRAGMA integrity_check` returned `ok` and `PRAGMA foreign_key_check` returned no rows.

### Validation evidence

* Canonical deterministic offline release gate: PASS.
* Focused Deputy write/pagination, roster-integrity, notification, route/integration, and note-interpretation regressions: PASS.
* Local Playwright responsive gate: PASS at 320px and 375px with the retained compact single-row mobile header.
* Python compilation and `git diff --check`: PASS.
* Host `pip check`: ENVIRONMENT FAILURE only — unrelated global `gradio-client 0.15.1` requires `websockets<12` while the host has `websockets 16.0`.
* `pip-audit`: NOT AVAILABLE in the host environment.
* Existing isolated project audit environment: `pip check` PASS and `pip-audit -r requirements.txt` PASS with no known vulnerabilities.
* Local Docker image/container gate: BLOCKED before build because the Docker Desktop Linux engine pipe is unavailable. This must be repeated on the production Docker/Portainer host before deployment sign-off.

### Remaining release gates and deliberate boundaries

Deployment is not recorded as complete until the release commit is pushed, the existing Portainer stack is redeployed without recreating its data/configuration, container build/start/restart succeed, authenticated role checks are performed, and one read-only Deputy sync succeeds. No live Deputy mutation is permitted as a smoke test.

Browser-based Deputy writes remain intentionally unimplemented. Deputy custom fields `f01` and `f02` remain read/raw evidence only because their native write contract is unverified. Conservative travel/name interpretation still refuses ambiguous global fuzzy matching; unresolved names remain unresolved rather than receiving a guessed employee ID.

### RD-SEC-002 — FIXED

Root cause confirmed: URL validation and the hostname-based HTTP connection performed separate DNS decisions.

Fix:

* Each URL/redirect is resolved and validated once, then the HTTPS pool connects directly to one of those validated public IP addresses.
* TLS SNI and certificate hostname verification use the original hostname, and HTTP `Host` uses the original authority.
* Direct pools do not inherit environment proxies.
* IPv4/IPv6, HTTPS/443-only, credential rejection, redirect, timeout, and response-size safeguards remain enforced.

Regression evidence includes direct local/private/link-local/reserved and IPv6 rejection, private redirect rejection, redirect/size limits, and a rebinding transport test proving the socket pool receives the validated public IP while TLS/Host retain the original name. No live calendar was used.

Final status: FIXED in `2026.08.13.5`.

### RD-DEPLOY-003 — MANUAL CONFIG REQUIRED

The shipped `SIGNUP_ENABLED` default is now false. Route regressions prove an empty database can still bootstrap its first user and that GET/POST signup close after that account exists. `COOKIE_SECURE` remains configurable so direct HTTP LAN operation is not broken. The deployment checklist requires `COOKIE_SECURE=true` for permanent HTTPS-only operation and explicitly records the LAN tradeoff.

Final status: MANUAL CONFIG REQUIRED for the operator's HTTPS-versus-LAN choice.

### RD-PRIV-004 — CURRENT DEFAULT FIXED; HISTORY AND RUNTIME-DATA ACTION REQUIRED

The genuine Deputy tenant hostname was removed from all current tracked defaults and fixtures. The global browser URL is now safely unconfigured by default; stored per-user and explicitly configured valid URLs remain supported, while an empty URL exits before Playwright starts.

The exact historical shift-context fallback remains because it is the only compatibility path for an older capture lacking complete structured area context. Removing it without a production-data migration could silently damage historical interpretation. Move this installation-specific fallback to private runtime data in a future migration, then remove the tracked compatibility data. Repository visibility/history remediation remains a separate explicit operator decision; no history rewrite or force push was performed.

Final status: PARTIAL — current tenant default fixed; operational fallback migration and historical exposure action remain.

### RD-CI-005 — FIXED

`scripts/release_gate.py` is now the canonical deterministic offline gate used locally and by GitHub Actions. It includes compilation, template rendering, Deputy/security/integration suites, migration, collision audits, and the broad committed feature fixtures. CI additionally runs dependency consistency/vulnerability checks, builds the actual Dockerfile, and boots/restarts a disposable container while proving write mode stays off and no Deputy write operation appears.

The Playwright 320px/375px check is explicitly classified and documented as a local browser gate.

Final status: FIXED in `2026.08.13.5`.

---

## 0.5.2 trusted-device and contractor atomicity patch

Starting release: immutable `v0.5.1` at `a6d24758ff0a2a63ee7ce6134c4e690677a60cab`.

This narrow patch replaces the hard-coded trusted-device cap with bounded `TRUSTED_DEVICE_LIMIT` configuration and makes new-contractor identity plus initial-invitation creation one SQLite transaction. Deterministic failure coverage uses a disposable-database trigger to fail invitation insertion after the person insert and proves that neither row remains. Replacement invitation and activation behavior remain covered.

Validation evidence:

* Focused 0.5.2 trusted-device/contractor smoke: PASS.
* Existing authentication, onboarding, and release-integration smokes: PASS.
* Canonical deterministic offline release gate, including migration rehearsal twice, SQLite integrity, zero foreign-key violations, and effective Deputy write mode `off`: PASS.
* Local responsive release gate at 375px and 320px: PASS.
* Python compilation, service-worker JavaScript syntax, production Compose render, and `git diff --check`: PASS.
* Rendered production Compose retains no host application port, `/data/compose/22/data:/app/data`, `SIGNUP_ENABLED=true`, `COOKIE_SECURE=true`, and `deputy-roster-multi_default` while adding `TRUSTED_DEVICE_LIMIT=10`.

No production deployment, Portainer interaction, live Deputy access, or Deputy write occurred during this patch.

---

## 0.5.3 safe user-purge and one-shot notices patch

Starting release: immutable `v0.5.2` at `a605455384dda048a555a8d9858589a5f888677c`.

The invited → activated → promoted Admin → demoted → deactivated → purged lifecycle was reproduced before editing. The purge failed with `FOREIGN KEY constraint failed` because the activated invitation and target-side role-audit rows still referenced the inactive account. The failed transaction left both references and the account intact, with zero foreign-key violations.

This patch gives user purge an explicit policy for every one of the 51 schema foreign keys to `app_users`: owned account state cascades, shared operational records detach, self-only activation/target history is deleted, and retention-critical authorship or Deputy write/configuration history blocks purge before mutation. Legacy user-like columns without foreign keys are handled explicitly. Purge runs under one immediate SQLite transaction, never disables foreign-key enforcement, and returns safe structured outcomes instead of exposing database errors. See `docs/USER_PURGE_POLICY.md` for the complete table-by-table classification.

Regression evidence:

* Plain inactive accounts and trusted devices purge successfully.
* The complete invitation, activation, Admin promotion/demotion, deactivation, and purge route lifecycle succeeds.
* Account-owned synthetic people are removed; Deputy-backed, manual, and contractor identities are preserved and detached according to policy.
* Active and nonexistent accounts return structured non-destructive outcomes.
* Retention-critical Deputy write history blocks purge cleanly and preserves the user, device, shift, and audit row.
* SQLite `integrity_check` returns `ok` and `foreign_key_check` returns no rows after every destructive fixture.
* Shared notice banners render on the first response, then remove only the `notice` query parameter with `history.replaceState`, preserving other query parameters and the URL fragment without navigation or reload.
* The existing Admin account/invitation/contractor layout remains covered by regression assertions.
* Canonical deterministic offline release gate and the 375px/320px responsive browser gate: PASS.
* Python and JavaScript syntax checks, migration rehearsal twice, production Compose render, and `git diff --check`: PASS.
* Rendered production Compose remains unchanged: no host application port, `/data/compose/22/data:/app/data`, `SIGNUP_ENABLED=true`, `COOKIE_SECURE=true`, `TRUSTED_DEVICE_LIMIT=10`, and the shared `deputy-roster-multi_default` network.

No production deployment, restart, Portainer interaction, live Deputy access, or Deputy write occurred during this patch. Deputy write mode remains off by default.

---

## 0.5.4 Admin workflow continuity, invitations, and disposable drafts

Starting release: immutable `v0.5.3` at `154f6027389357ddaabec56ba1c80f340f02cc33`.

The ordinary account-invitation route returned the Admin POST response directly. Refresh could therefore resubmit creation, and the existing single-active-invite invariant revoked the pending token before inserting its replacement. Account and contractor creation now use POST → 303 → GET with a client-only fragment/session handoff; repeated GET/HEAD scans are non-mutating, new expiry is 24 hours, and replacement is explicit.

Never-published draft deletion is transactional and refuses publication/version history, Deputy link/write history, or any other retained relationship identified by the `roster_days` relationship audit. Draft-only assignments, Open/TBC rows, and audit children delete through declared cascades with foreign keys enabled. Expected blocked/missing outcomes return ordinary Admin notices.

Admin same-page actions restore nested disclosures and approximate scroll once from short-lived `sessionStorage`. Locations use five compact desktop columns and stack on phones; revoked devices are collapsed without deletion; Manual overrides is collapsed; Help reuses the application's icon geometry and shows Admin activity date-only.

Focused validation is committed in `smoke_patch_054.py`, `smoke_admin_context.js`, and `smoke_admin_invitations.js`. Final canonical, responsive, Compose, integrity/FK, and exact-SHA CI evidence is recorded in the release handoff after those gates pass. No production deployment or Deputy write is authorized by this release.

Vehicle correctness is release-ready as a narrow interpretation change. The two confirmed code-level loss points were the interpreter's fixed vehicle-only selection and its `_row_is_vehicle()` filter, which ignored explicit normalized vehicle evidence on a production row. The real 28 August Deputy JSON property name was not independently captured; `vehicle: "684"` is only a sanitized/synthetic normalized fixture, not a proven live schema. Accordingly, Re-Deputy does not add speculative raw capture parsing. The normal primary representation remains separate Travel/vehicle and production rows. The interpreter also accepts an already-recorded normalized vehicle label on a production row, deduplicates equal current values, surfaces different current values as `structured_deputy_conflict`, lets an explicit current roster note resolve that conflict, and applies preceding Travel only if no current explicit vehicle remains. Owner-scoped personal evidence fills only its matching shared blank and cannot overwrite an explicit conflicting shared value or leak to another person. `smoke_vehicle_combined_rows.py` covers the split/combined, conflict, privacy, changed/removed, and self-travel-preservation interpretation matrix; it is registered in the release gate. No further production evidence is required for this release. No production deployment or Deputy write occurred.

---

## 0.5.5 operational safety: backup, recovery, and central Admin audit

Starting release: immutable `v0.5.4` at `0180b10001a702f7c1db7a16787816b76fc2d267`.

This release adds a private SQLite-online-backup service, tagged validation manifests, managed-only retention, a 03:30 Pacific/Auckland APScheduler job, one Admin manual-backup control, and an offline-only restore tool. The production definition preserves the data mount and adds only `/data/compose/22/backups:/app/backups`; no host port, Cloudflare network, signup, secure-cookie, trusted-device, or Deputy-write setting changes are included.

Central Admin audit uses a write-ahead durable `started` row before every mutating `/admin` handler. If that write cannot begin, the handler is not called. Completed/rejected/blocked/failed result metadata finalizes the same safe/redacted row; a finalization fault leaves `started`, never a false completed record. Inactive account purge and unpublished workday-draft deletion require a fresh successful snapshot before their destructive database transaction.

The complete local deterministic release gate (including 320px/375px and Admin responsive checks), focused backup/recovery/audit smoke, migration rehearsal twice, SQLite integrity/FK checks, JavaScript/Python syntax, Compose render with a process-local placeholder, and `git diff --check` passed. Docker Desktop's Linux engine was unavailable on this workstation, so the requested disposable Linux-container backup proof could not run; the portable online-backup, promotion, retention, consecutive-backup, failure-preservation, and restore-dry-run fixture evidence is retained in `smoke_patch_055.py`. Exact-SHA CI and immutable tag evidence are still required. No production deployment, restart, Portainer interaction, live Deputy access, or Deputy write is authorized by this release.

---

## 0.5.6 operational safety correctness closure

Starting release: immutable `v0.5.5` at `20d65d99f09ef1d70cd94079d742c040f9abf677`.

Startup no longer calls hard inactive-account purge. Deliberate inactive cleanup, roster reset, individual purge, and unpublished-draft deletion require a fresh successful safety backup before their destructive transaction. Central Admin auditing now treats redirect-only results as neutral `request_finished` until a handler has supplied a verified outcome, and adds compact recovery snapshots where specialist history is insufficient. Scheduled backups take the shared application version/build source. Pending final validation and exact-SHA CI/tag evidence; no production deployment or Deputy write is authorized.

---

## 0.5.7 root Portainer Compose closure

Starting release: immutable `v0.5.6` at `f20a23d3e735e5c4dbda60db9849a6d3c331abe8`.

The actual Git-backed Portainer stack reads repository-root `docker-compose.yml`, so it is now the sole canonical hardened production definition. The previous root development configuration moved to `docker-compose.dev.yml`; the duplicate production Compose was removed. Root production keeps the existing data mount, adds the backups mount, exposes 8000 only internally on the named shared network, has no host application port, and accepts either the existing external `APP_SECRET_KEY` or the persistent `/app/data/app_secret.key` fallback. Routine Portainer settings do not change. Pending final validation and exact-SHA CI/tag evidence; no production deployment is authorized.

---

## 0.5.13 interpretation stabilization candidate

Starting baseline: `bfe9103aa17f76229924a872f7a479d0ba2be3fd` (`Re-Deputy 0.5.13`).

Authenticated personal Deputy rows are now retained before semantic classification. Production positions, Travel participants, vehicle/operational context, and unknown labels remain distinct; the upgrade backfill classifies retained labels without promoting unknown evidence. Production coverage consumes production-position evidence only. Event-scoped Travel membership is the union of shared structured participants and strongly identified authenticated personal participants for the same narrow Travel/T-Travel family and overlapping event window. Roster notes enrich established members but cannot create crew membership. Generic truck evidence renders as `Truck` while retaining generic metadata, and note-versus-structured vehicle disagreement is reported without changing note authority.

The dedicated Travel collector, broad-ALL absence exclusions, per-observer evidence, partial-capture protection, historical event locks, capture recovery, structured vehicle collector, raw-owner isolation, short-lead handoff duration guard, and Deputy roster timing authority remain intact. The complete deterministic 0.5.13 release gate and its 320px/375px responsive variant passed, including fresh initialization, representative in-place 0.5.13 migration twice, SQLite integrity/FK checks, assignment/link collision audit, and `git diff --check`. No live Deputy access, write enablement, deployment, merge, or push occurred.
