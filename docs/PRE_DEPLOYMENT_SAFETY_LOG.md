# Re-Deputy Pre-Deployment Safety Log

Purpose: maintain a durable record of significant pre-deployment defects, investigation results, attempted fixes, validation evidence, remaining risks, and release decisions so the same work is not repeated.

Do not delete old entries when an issue is fixed. Update its status and add the release/commit that resolved it.

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
