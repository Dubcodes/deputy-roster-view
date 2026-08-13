# Re-Deputy Pre-Deployment Safety Log

Purpose: maintain a durable record of significant pre-deployment defects, investigation results, attempted fixes, validation evidence, remaining risks, and release decisions so the same work is not repeated.

Do not delete old entries when an issue is fixed. Update its status and add the release/commit that resolved it.

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
