# Deputy API Laboratory — Round 2

Date: 13 August 2026 (Pacific/Auckland)

Tenant: disposable Deputy trial only (`898f4a13061404.au.deputy.com`)

## Scope and safety

- No production Deputy tenant or production credential was used.
- No Re-Deputy application source, authentication, schema, UI, or write path was changed.
- Tokens, passwords, OAuth secrets, contact addresses, private offer links, and unrelated personal data are omitted.
- Requests and responses below are sanitized. IDs are disposable trial IDs.
- Round 1 remains the baseline. This report records only Round 2 experiments and conclusions.
- Notification-free publish mode `4` was used except for controlled confirmation/offer tests addressed only to disposable lab aliases.

## 1. Executive conclusions

1. The modern single-shift API is supported in this tenant:
   - create: `POST /api/management/v2/shifts`;
   - get: `GET /api/management/v2/shifts/{id}`;
   - update: `PUT /api/management/v2/shifts/{id}`;
   - delete: `DELETE /api/management/v2/shifts/{id}`;
   - list: `GET /api/management/v2/shifts?...`;
   - bulk asynchronous upsert: `POST /api/management/v2/shifts:bulk` plus `GET /api/operations/v2/operations/{operationId}`;
   - offers: `POST /api/management/v2/shifts/offers:notify`.
2. Re-Deputy should initially use the v2 single-shift endpoints for create/update/get/delete, the Resource API for deterministic reconciliation queries, and the proven legacy publish endpoint. V2 returns a cleaner normalized object, structured errors, explicit timezone offsets, confirmation enums, and `canEdit`. No v2 publish endpoint was established.
3. V2 does **not** solve create idempotency. Bulk `externalIds` are diagnostic correlation values for the asynchronous job; they were not stored on `Roster.ExternalId`, were not queryable as a roster key, and were not unique. Reusing one with a changed non-overlapping payload created another roster.
4. Location Manager and Supervisor credentials could perform the required roster-management workflow without System Administrator. Supervisor is the lowest practical tested access level in this tenant.
5. Each tested action used the acting identity's own OAuth token. `/me` returned separate `UserId` and `EmployeeId`; the invariant remains that Re-Deputy must bind both and must never fall back to another user's token.
6. A live Employee `Role` mutation did not change `/me Permissions` or authorization for either an existing token or a newly issued token. The employee record reflected the new role, but the OAuth identity retained its original permission set. Re-Deputy must check `/me`, not infer authority from `Employee.Role`, and must require reconnection/readiness revalidation after Deputy access changes.
7. A linked Timesheet immediately made both a past draft roster and a past published roster non-editable (`canEdit=false`). Update returned structured `400 SHIFT_VALIDATION` and delete returned `403`. Timesheet approval did not unlock the roster.
8. Open-shift offer creation was validated with invited users: `RosterOpen` rows appeared only after a usable, invited identity was eligible. A complete accept/decline transition was not completed; the roster remained `Employee=0`, `Open=true` and the offer row remained pending.
9. Travel, production role, and vehicle context remain ordinary rosters differentiated only by Operational Unit and Re-Deputy semantics. Exact-boundary adjacent travel/production shifts for the same employee were accepted and retained separate IDs.
10. `Employee.Id` and `UserId` remained stable through display-name and contact-email mutation. An Operational Unit ID remained stable through rename, and linked roster reads immediately showed the new Area name.
11. Normal responses exposed no rate-limit, retry-after, request/correlation ID, ETag, or last-modified headers in the tested calls.

## 2. Identities and permission matrix

### Tested identities

| Identity | Deputy role at token issue | `/me` | UserId | EmployeeId | Permission count | Notes |
|---|---|---:|---:|---:|---:|---|
| Administrator | System Administrator | 200 | 1 | 1 | 88 | Trial owner; cleanup and comparison only. |
| Manager | Location Manager | 200 | 5 | 5 | 61 | Own OAuth authorization and token. |
| Supervisor | Supervisor | 200 | 6 | 6 | 23 | Own OAuth authorization and token. |
| Employee fixture | Employee | not completed | 7 | 7 | not measured | Invitation/login still reported unaccepted. No credential was fabricated or substituted. |
| Advisor fixture | Advisor | not completed | 8 | 8 | not measured | Invitation/login still reported unaccepted. No credential was fabricated or substituted. |

The tenant role configuration was also inspected. The Employee role lacks roster-management/add-edit permissions. Advisor is operationally unusual: it reports roster-manager/all-departments authority but not add/edit-shifts. Those two configurations are informative but are **not** counted as API permission tests.

### Experimentally observed matrix

`Yes` means the API call was made with that identity's own credential and succeeded. `Not tested` is deliberately not inferred.

| Action | Administrator | Location Manager | Supervisor | Employee | Advisor |
|---|---:|---:|---:|---:|---:|
| `/me` | Yes | Yes | Yes | Not tested | Not tested |
| Read Employees | Yes | Yes (8 rows) | Yes (4 visible rows) | Not tested | Not tested |
| Read Operational Units | Yes | Yes | Yes | Not tested | Not tested |
| Read Rosters | Yes | Yes | Yes | Not tested | Not tested |
| Read `RosterOpen` | Yes | Yes | Yes, but only authorized/related rows | Not tested | Not tested |
| Read Timesheets | Yes | Yes | Yes | Not tested | Not tested |
| Create own assigned shift | Yes | Yes | Yes | Not tested | Not tested |
| Create another employee's shift | Yes | Yes | Yes | Not tested | Not tested |
| Update own/another shift | Yes | Yes | Yes | Not tested | Not tested |
| Change employee | Yes | Yes | Yes | Not tested | Not tested |
| Change Area | Yes | Yes | Yes | Not tested | Not tested |
| Change start/end/break/note | Yes | Yes | Yes | Not tested | Not tested |
| Publish mode 4 | Yes | Yes | Yes | Not tested | Not tested |
| Create Open shift | Yes | Yes | Yes | Not tested | Not tested |
| Send Open-shift offer | Yes | Yes | Yes | Not tested | Not tested |
| Delete shift | Yes | Yes | Yes | Not tested | Not tested |
| Create/edit another employee Timesheet | Yes | Yes | Yes | Not tested | Not tested |
| Approve another employee Timesheet | Yes | Yes | Yes | Not tested | Not tested |

Supervisor's reduced Employee result set is an important reminder: a `200` does not imply unfiltered visibility. Reads must be scoped and reconciled using IDs available to the acting identity.

### Lowest useful permission profile

The tenant's built-in **Supervisor** role is the lowest fully tested profile that performed the complete assigned-roster workflow plus normal disposable Timesheet creation/edit/approval. Its relevant `/me Permissions` included:

- `Can_Roster_Manage`;
- `Can_Roster_All_Departments`;
- `Allow_Roster_Shift_Outsite_Templ`;
- `Can_Approve_Timesheet_Hours`;
- `Can_ApproveTS_All_Departments`;
- `Can_ApproveTS_Outside_Period`;
- `Can_Enter_Own_Timesheet`;
- `Can_Bump_Own_Timesheet`.

The API accepted v2 shift writes even though `Can_AddEdit_Roster_Shifts` was absent from the Supervisor's `/me` list. Therefore Re-Deputy must not gate on that one string alone. A sensible readiness gate is:

1. `/me` succeeds for the exact stored tenant;
2. returned `UserId` and `EmployeeId` match the app-user binding;
3. `Can_Roster_Manage` is present;
4. the intended Area and target employee are readable;
5. a tenant compatibility/readiness probe has established that the required endpoint works for that role;
6. Timesheet controls additionally require the applicable `Can_Approve_Timesheet_Hours`/department scope.

## 3. Per-user identity and permission-change findings

The OAuth flow returned a tenant-specific access token and rotating refresh token. The token was validated with `/api/v1/me` before use. No token was shared across identities.

Required app-user binding:

- normalized Deputy hostname/tenant;
- Deputy `UserId`;
- Deputy `EmployeeId`;
- connected identity display label for confirmation only;
- OAuth client/connection identifier and encrypted credential reference;
- last verified `/me` permission snapshot and hash;
- last verification time and endpoint-compatibility version.

Do not bind by email or display name. Email is reconciliation metadata only.

Role-change experiment:

1. Administrator changed Employee 5 from Location Manager to Supervisor, then Employee, through the v2 employee API.
2. Employee read-back showed the requested `Role` IDs.
3. `/me Permissions` stayed at the original 61 values for the existing token.
4. A new OAuth authorization/token while the Employee record showed role 50 still returned the original 61 permissions.
5. Shift creation still succeeded.

Conclusion: changing `Employee.Role` is not a reliable way to change the associated OAuth user's access level in this tenant, and `/me` is the authoritative observed capability snapshot. If Deputy permissions change later, Re-Deputy should immediately suspend writes, require a fresh `/me` verification, compare the identity/permission hash, and run a non-destructive readiness probe. It must never retry using an administrator token.

## 4. Legacy versus v2 shift API

Equivalent assigned shifts were created by legacy and v2 endpoints.

| Characteristic | `/api/v1/supervise/roster` | `/api/management/v2/shifts` |
|---|---|---|
| Create/update distinction | `intRosterId` absent/present | POST create; PUT by ID |
| Response | Full legacy `Roster` in this tenant | `success` plus normalized shift object |
| Employee/Area | `intRosterEmployee`, `intOpunitId` | integer `employee`, `area` |
| Times | Unix seconds | ISO-8601 with offset |
| Break | duration minutes or legacy slots | typed `mealbreakSlots` plus normalized duration |
| Comment | `strComment` | `note` |
| Open | `blnOpen`, Employee 0 | `isOpen`, employee 0 |
| Publish | separate legacy publish endpoint | no v2 publish endpoint established |
| Confirmation | integer fields | readable enum such as `ROSTER_CONFIRMATION_REQUIRED` |
| Editability | inferred from failures/relations | explicit `canEdit` |
| Errors | mixed legacy shapes | generally `{success:false,error:{code,message,details}}` |
| Update retry | identical retry advanced `Modified` in Round 1 | identical PUT returned 200 without advancing `modifiedAt` in this test |
| Concurrency | no ETag/version observed | no ETag/version observed |
| External key | `Roster.ExternalId` existed but was null | single-shift API exposed none |

V2 update preserved the roster ID while changing employee, Area, start/end, break, note, and `approvalRequired`. Resource API read-back saw the same underlying roster ID.

Recommendation: use v2 single-shift write methods initially because their contract and read-back are safer to normalize. Continue using Resource `Roster/QUERY` for reconciliation and legacy `/supervise/roster/publish` mode 4 for publishing. Do not use v2 bulk merely because it is newer; its asynchronous result and non-idempotent external IDs add complexity without solving the primary safety problem.

## 5. External IDs and idempotency

Bulk request shape (sanitized):

```json
{
  "items": [{
    "externalIds": ["LAB-R2-EXT-001"],
    "shift": {
      "area": 11,
      "employee": 5,
      "start": "2026-09-09T09:00:00+12:00",
      "end": "2026-09-09T12:00:00+12:00",
      "note": "LAB R2 BULK EXTERNAL"
    }
  }]
}
```

Observed:

- first submit: 200, asynchronous operation completed with `success:1` and roster ID 9 found by reconciliation;
- `Roster.ExternalId`: null;
- identical repeat: submit 200, asynchronous result `success:0` with two structured overlap errors carrying the supplied external ID;
- changed non-overlapping payload with the same external ID: `success:1`, new roster ID 10;
- no Resource query key was established for the supplied value.

`externalIds` correlate bulk input rows to bulk errors. They do not enforce uniqueness and are not a durable assignment ID.

### Safe CREATE algorithm

1. Persist a local write operation before network transmission with a UUID, stable assignment key, desired normalized state, tenant, acting `UserId`/`EmployeeId`, and status `prepared`.
2. Acquire a per-assignment lock and reject another active create for the same stable assignment key.
3. Revalidate `/me`, tenant, identity binding, permission hash, employee ID, Area ID, and `canEdit`/existing mapping.
4. Query the exact local operational date with deterministic `Id asc` pagination.
5. Normalize candidates to `(employeeId, areaId, start instant, end instant, break model, open, note fingerprint)`.
6. If one exact candidate is already bound to this assignment, adopt/verify it; if multiple match, stop for manual reconciliation; if a conflicting overlap exists, stop.
7. Send one v2 POST and atomically store the returned roster ID before marking complete.
8. GET by returned ID and compare normalized business fields.

### Unknown CREATE result

The lab deliberately discarded a successful create response, then created a similar adjacent control record. A query by date + employee + Area returned both candidates, but the intended start/end/comment fingerprint selected exactly one.

Recovery:

- never blindly resend;
- query date range by employee and Area, sorted by ID;
- exact-match employee, Area, start, end, open state, break, and a bounded operation-note fingerprint where policy permits;
- one exact match: adopt ID and GET-verify;
- zero: retry once using the same local operation record only after the reconciliation window and with the assignment lock held;
- more than one: mark `ambiguous`, disable automatic mutation, require operator reconciliation.

The comment fingerprint is supporting evidence, not a secret idempotency channel and not a replacement for the local stable assignment key.

### UPDATE, publish, and delete recovery

- UPDATE unknown result: GET known roster ID; if normalized desired state matches, succeed; otherwise re-check `canEdit` and retry PUT. A validation failure must not be interpreted as an unknown network result.
- Publish unknown result: GET every requested ID. If all are `Published=true`, succeed. Otherwise repeat mode 4 only for unresolved IDs. Assigned and Open rosters both retained their IDs/state across repeated publish; repeat publish did not advance `Modified`.
- Delete unknown result: GET by ID. A 404 means Deputy no longer has the roster; only the local operation audit can say whether this was the intended deletion or an external deletion. Repeated v2 delete returned 400 `No shift found`.

## 6. Open shifts, confirmation, and approval

V2 Open creation returned `employee=0`, `isOpen=true`, `isPublished=false`. Publishing mode 4 retained employee 0/Open and set Published.

With invited, eligible disposable identities:

- offer notify returned 200 and an asynchronous operation ID;
- a `RosterOpen` row appeared with Roster ID, Employee ID, pending flags, and a private link;
- the private link is sensitive and is not reproduced;
- pending read-back showed `Accepted=false`, `Declined=false`, and no seen time;
- the offered roster remained `Employee=0`, `Open=true`, `Published=true`.

The accept/decline step was not completed in a controllable lab session, so removal of other offers and the exact post-accept transition remain unresolved. Official Deputy documentation says offer rows are temporary and removed after fill, but Re-Deputy must not implement that lifecycle from documentation alone.

Mode 5 confirmation experiment:

- publish returned 200;
- Resource roster: `Published=true`, `ConfirmStatus=1`, `ConfirmBy=0`, `ConfirmTime=0`;
- v2: `confirmationStatus=ROSTER_CONFIRMATION_REQUIRED`, empty confirmer/time.

`approvalRequired=true` is specific to an employee claiming an Open shift and requiring manager approval; it is not Timesheet approval or ordinary assigned-roster approval. The flag survived v2 write/read-back. The claim/manager-approval transition remains untested.

Initial Re-Deputy scope should support creating/publishing Open rosters only behind a separate feature gate. Claim, decline, confirmation, and manager approval should remain read-only/unimplemented until the remaining lifecycle is tested.

## 7. Timesheet lock lifecycle

| Roster state | Timesheet creation | `canEdit` after link | Update | Delete | After Timesheet approval |
|---|---|---:|---:|---:|---|
| Future draft | 400: end time cannot be future | n/a | not applicable | not applicable | n/a |
| Future published | 400: end time cannot be future | n/a | not applicable | not applicable | n/a |
| Past draft + draft Timesheet | 200 | false | 400 | 403 | still locked |
| Past published + draft Timesheet | 200 | false | 400 | 403 | still locked |

Every tested edit category—note, Area, employee, time, and break—returned v2 `400 Shift validation failed`, with detail field `SHIFT_VALIDATION` and description `Sorry! This shift cannot be edited.` Deletion returned 403. Approval set `TimeApproved=true` and `PayRuleApproved=true` but did not change `canEdit=false`.

Re-Deputy should show **Locked by Timesheet** and stop before a write when any of these is true:

- v2 `canEdit=false`;
- Resource `MatchedByTimesheet != 0`;
- v2 `timesheet != 0`.

Do not offer a destructive workaround. Display the linked Timesheet ID and instruct the user to resolve it in Deputy under their own authority.

## 8. Race-day, travel, and vehicle mapping

A realistic disposable scenario created separate rosters for:

- prior-day `Travel then Overnighter`;
- vehicle context `Vehicle 684`, later changed to `Tender` by changing Area;
- Director;
- Side 1;
- Sound/VT;
- an Open Tender role.

The Supervisor credential then changed Director employee, moved a person from Side 1 to the Sound/VT Area, changed vehicle Area, changed a start time, and published all records. IDs were preserved through edits.

For the same employee/workday, a Travel roster ended at 08:00 and a Production roster began exactly at 08:00. Both were accepted, retained distinct IDs, and the Production end could be extended without changing the Travel roster.

Deputy still exposes no first-class vehicle or travel field. Re-Deputy should own:

- Operational Unit classification (`production_role`, `travel`, `vehicle_context`, `generic`);
- vehicle identity/label and source-history semantics;
- rules for composing adjacent display blocks;
- distinction between Deputy Open and local TBC.

Deputy should receive the selected Area ID and a concise operational note only. Do not overload one roster to represent both travel and production.

## 9. Identity stability

Employee 8 was changed from `LAB R2 Advisor` to `LAB R2 Advisor Renamed`, with its controlled contact email also changed. Before and after:

- `Employee.Id = 8`;
- `UserId = 8`.

Display name and contact email are mutable metadata. Canonical mapping remains `app_user -> tenant + Employee.Id`, with `UserId` also bound for acting-identity verification.

Operational Unit 14 was renamed while roster 15 referenced it. After rename:

- Area ID stayed 14;
- roster `OperationalUnit` stayed 14;
- joined metadata immediately showed the new name.

Persist IDs, never names, as foreign keys.

## 10. Pagination, timezone, headers, and errors

### Pagination

`Schedule/QUERY` with `sort:{Id:"asc"}`, `start:0`, `max:10` returned 10 IDs. `start:10` returned the next 10 with no overlap. `start:1000` returned an empty array.

Production Resource pagination:

1. always sort by immutable `Id asc`;
2. request `start=0,max=500`;
3. process and deduplicate by ID;
4. if count is 500, increment start by 500 and continue;
5. stop on count below 500;
6. for rapidly changing data, bound by date/modified window and overlap the next sync window, reconciling by ID.

The 500 ceiling is documented; this lab did not manufacture 500 records.

### Timezone and DST

Company, Operational Unit, and `/me` did not expose an explicit timezone-name field in tested payloads. Returned roster timestamps used New Zealand offsets. A DST-crossing shift from `2026-09-27T01:30:00+12:00` to `2026-09-27T04:30:00+13:00` returned a two-hour duration and preserved both offsets.

Re-Deputy should derive an aware Pacific/Auckland instant from local operational time, send ISO-8601 with its actual offset, and verify both instant and localized date/time on read-back. Never send a naive local timestamp or assume a fixed +12/+13 offset.

### Response headers

Normal legacy and v2 calls exposed a `Date` header but no tested:

- `Retry-After`;
- rate-limit limit/remaining;
- request ID;
- correlation ID;
- ETag;
- Last-Modified concurrency validator.

Use bounded exponential backoff with jitter only for transport failures, 429, and eligible 5xx. Respect `Retry-After` if Deputy later supplies it. Do not retry validation, overlap, permission, or lock errors automatically.

### Error classification

| Class | Evidence | Retry policy |
|---|---|---|
| `AUTH` | 401 missing/invalid token; sometimes empty body | stop, reconnect exact user |
| `PERMISSION` | 403 delete on Timesheet-linked roster; endpoint-specific permission messages | stop; never retry as admin |
| `VALIDATION` | 400 structured field violations, missing required fields, future Timesheet | correct input only |
| `OVERLAP` | 400/async bulk `START_TIME` and `END_TIME` overlap details | reconcile; user decision |
| `LOCKED` | v2 `canEdit=false`, `SHIFT_VALIDATION`, Timesheet link | stop and show linked Timesheet |
| `NOT_FOUND` | GET 404; repeated delete 400 `No shift found` | reconcile against local audit |
| `RATE_LIMIT` | not observed | retry only with bounded backoff/Retry-After |
| `SERVER` | representative 5xx not intentionally induced | bounded retry then unknown-result reconciliation |
| `UNKNOWN_NETWORK_RESULT` | client discarded response/transport ambiguity | never classify as ordinary failure; reconcile first |

Production logic should prefer HTTP status plus structured code/detail fields. English text is diagnostic fallback, not the sole classifier.

## 11. Recommended Re-Deputy integration contract

For every write:

1. Select exactly one app user and that user's encrypted Deputy connection. No shared, global, fallback, or administrator credential.
2. Validate hostname allowlist/equality, `/me`, UserId, EmployeeId, active connection, and permission hash.
3. Resolve canonical employee and Area IDs; preserve Re-Deputy domain classification separately.
4. Persist a prepared local operation and stable assignment key before transmission.
5. Show the monitored-write confirmation.
6. Use v2 POST/PUT/DELETE for the shift, Resource queries for reconciliation, and legacy mode-4 publish.
7. GET read-back and compare normalized business state.
8. Complete the audit only after verification; otherwise retain `unknown`, `conflict`, `locked`, or `failed` with evidence.

Minimum audit fields:

- Re-Deputy `app_user_id`;
- verified Deputy hostname;
- Deputy UserId and EmployeeId;
- permission snapshot/hash and verification time;
- operation type and endpoint/method;
- local workday ID and stable assignment key;
- local operation UUID and attempt number;
- Deputy roster ID, if known;
- sanitized before, desired, response, and read-back states;
- HTTP status and structured error classification;
- request-start, response, reconciliation, and completion timestamps;
- outcome (`verified`, `unknown`, `conflict`, `locked`, `externally_missing`, `failed`);
- deletion/cancellation reason and before-image.

Never audit credentials, authorization codes, refresh tokens, passwords, private offer links, or unrelated employee data.

Local duplicate-prevention storage must include `(tenant, stable_assignment_key)` uniqueness, active operation UUID, Deputy roster ID, employee ID, Area ID, normalized instants, open state, break fingerprint, desired-state hash, publish state, and last verified Deputy state/version timestamp.

## 12. Monitored-write UX

Before write, show at minimum:

```text
Deputy write

Tenant: 898f4a13061404.au.deputy.com
Acting as: <connected Deputy display name>
Deputy identity: Employee #<id> · User #<id>
Permission checked: <time>

Action: Create / Update / Publish / Delete
Workday: <date and event/location>
Assignment: <person or Open> · <Area> · <classification>
Time: <old> -> <new>, including timezone
Break / publish / confirmation changes
Local operation: <short UUID>

Proceed | Cancel
```

After write, show Deputy roster ID, HTTP outcome, read-back result, and one of:

- `Deputy updated — read-back verified`;
- `Deputy result unknown — reconciling; do not retry`;
- `Locked by Timesheet #…`;
- `Permission changed — reconnect required`;
- `Conflict — operator review required`.

Delete must say that Deputy removes the roster rather than keeping a cancelled row and that Re-Deputy will retain its own audit before-image.

## 13. Remaining unknowns and release boundary

1. Ordinary Employee and Advisor API credentials were not obtained; their invitation/login state still reported unaccepted. Their negative/edge permission rows remain untested.
2. Open-shift accept, decline, competing offers after acceptance, and manager approval of an `approvalRequired` claim remain incomplete.
3. Employee confirmation response to mode 5 remains incomplete.
4. The correct Deputy UI/API operation for changing an existing user's access level, as distinct from `Employee.Role`, needs a targeted lab round.
5. No 429 or 5xx was intentionally generated.

The architecture is safe enough to begin implementing a **feature-flagged, non-production, assigned-shift-only** integration design using per-user OAuth, local idempotency, read-back verification, and Supervisor-or-higher tested authority. It is **not** safe to enable production writes or Open-shift lifecycle controls until the Employee negative permission test, access-level change behavior, and Open accept/decline/approval lifecycle are resolved and reviewed.

## 14. Representative experiment ledger

| # | Identity | Endpoint/method | Sanitized request | Status / IDs | Read-back and conclusion |
|---:|---|---|---|---|---|
| 1 | Admin | `GET /api/v1/me` | none | 200 | User 1, Employee 1, Company 1. |
| 2 | Manager | `GET /api/v1/me` | none | 200 | User/Employee 5; 61 permissions. |
| 3 | Supervisor | `GET /api/v1/me` | none | 200 | User/Employee 6; 23 permissions. |
| 4 | Manager | Resource QUERY ×5 | bounded searches | 200 | Employees 8, Areas 11, Rosters 14 at that point, Open rows 1, Timesheets 3. |
| 5 | Supervisor | Resource QUERY ×5 | bounded searches | 200 | Employee visibility reduced to 4; other reads succeeded. |
| 6 | Admin | `POST /api/management/v2/shifts` | assigned Area 10, Employee 5, ISO times, typed break | 200, ID 7 | Full normalized object and Resource read-back. |
| 7 | Admin | `PUT .../shifts/7` | employee 6, Area 12, changed times/break, approval true | 200, ID 7 | Same ID; all fields changed; identical retry did not advance modified time. |
| 8 | Admin | legacy `/supervise/roster` | equivalent assignment | 200, ID 8 | Same underlying Roster model; legacy confirmation validation differs. |
| 9 | Admin | `POST .../shifts:bulk` | external ID + one shift | 200, operation | Operation succeeded; roster ID 9; `ExternalId` null. |
| 10 | Admin | repeat bulk | same external ID/payload | 200 async, 0 success | Structured overlap; not idempotent. |
| 11 | Admin | bulk changed payload | same external ID, non-overlap | 200 async, ID 10 | New roster created; no uniqueness. |
| 12 | Admin | ambiguous create simulation | response ignored | 200, ID 12 | Exact fingerprint found one among two adjacent candidates. |
| 13 | Admin | ambiguous update simulation | response ignored then GET | 200 | GET by ID distinguished unsuccessful overlap from successful later update. |
| 14 | Admin | ambiguous delete simulation | DELETE then ignored | 200; GET 404 | Repeat DELETE 400 `No shift found`. |
| 15 | Manager | v2 own/other create | two assigned shifts | 200, IDs 22/23 | Own and another employee accepted. |
| 16 | Manager | v2 updates | employee, Area, time, break, note | 200 | IDs preserved. |
| 17 | Manager | publish mode 4 | IDs 22/23 | 200 | Both published. |
| 18 | Manager | Open create/publish/offer | Employee 0, Open true | 200, ID 24; offer row 2 | Pending `RosterOpen` record created. |
| 19 | Manager | Timesheet create/edit/approve | Employee 7, disposable past time | 200, ID 5 | Time and pay approval true. |
| 20 | Supervisor | own/other create and updates | IDs 30/31 | 200 | Full roster management succeeded. |
| 21 | Supervisor | publish/Open/offer/delete | mode 4 and v2 | 200 | Supervisor is practical least-privileged tested role. |
| 22 | Supervisor | Timesheet create/edit/approve | disposable ID 6 | 200 | Time and pay approval true. |
| 23 | Admin | linked Timesheets | past draft/published rosters | 200, TS 3/4 | `canEdit=false`; every edit 400; delete 403. |
| 24 | Admin | approve linked Timesheets | IDs 3/4 | 200 | Roster remained locked. |
| 25 | Admin | publish mode 5 | assigned ID 21 | 200 | Confirm required state and v2 enum. |
| 26 | Supervisor | realistic race day | travel/vehicle/roles/Open | 200, IDs 33–38 | Edits and publish mapped cleanly. |
| 27 | Supervisor | adjacent travel/production | exact 08:00 boundary | 200, IDs 39/40 | Separate, independently mutable IDs. |
| 28 | Admin | employee PATCH/read | display/contact mutation | 200, Employee 8 | EmployeeId/UserId stable. |
| 29 | Admin | Area rename/read | Area 14 linked to roster 15 | 200 | Area/roster FK stable; joined name changed. |
| 30 | Admin | `Schedule/QUERY` pages | start 0/10, max 10, ID asc | 200 | Non-overlapping deterministic pages; beyond end empty. |
| 31 | Admin | DST v2 create/read | +12 start, +13 end | 200, ID 16 | Offsets preserved; real duration 2 hours. |
| 32 | Admin | cleanup | tracked Timesheets, Rosters, Areas | 200 | Final `LAB R2` Roster and Area queries empty. |
| 33 | Admin | terminate employees 5–8 | disposable identities only | 200 | All verified inactive. |

## 15. Cleanup result

- Disposable Timesheets 3–6 deleted.
- All tracked Round 2 rosters deleted; final `LAB R2` Roster query returned zero.
- Operational Units 10–15 deleted; final `LAB R2` Area query returned zero.
- Employees 5–8 terminated and verified inactive; hard deletion was not attempted after history existed.
- Local administrator permanent token revoked.
- Global Round 2 OAuth client deleted, revoking its per-user OAuth grants.
- Local single-install OAuth client has no client-delete control in this tenant UI; its sole Access Token was explicitly deleted/revoked. The inert client metadata remains as a cleanup limitation.
- Temporary secret file removed after revocation.
- Authenticated lab tabs closed/finalized.
- No production Deputy installation was contacted.

## Official references consulted

- [Using OAuth 2.0](https://developer.deputy.com/docs/using-oauth-20)
- [Adding/updating a shift](https://developer.deputy.com/docs/adding-a-shift)
- [Micro-scheduling and v2 shift API](https://developer.deputy.com/docs/shift-plans-1)
- [Shift offers](https://developer.deputy.com/docs/shift-offers)
- [RosterOpen](https://developer.deputy.com/docs/rosteropen)
- [Getting shifts](https://developer.deputy.com/docs/getting-shifts)
- [Timesheet management calls](https://developer.deputy.com/docs/timesheet-management-calls)
- [Resource API pagination guidance](https://developer.deputy.com/docs/getting-data-resources-api)

Experimental tenant behavior in this report takes precedence over documentation examples for this tenant and date. Production rollout still requires a compatibility probe and explicit review.
