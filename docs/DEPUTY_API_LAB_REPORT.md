# Deputy API Laboratory Report

Date: 13 August 2026 (Pacific/Auckland)

Tenant: isolated disposable Deputy trial only

Status: laboratory work complete; no Re-Deputy application code or production Deputy installation was changed.

## Safety and scope

- All requests targeted the supplied disposable trial tenant.
- No production Deputy credentials or production installation were used.
- The permanent token, OAuth client secret, login password, offer links, contact details, photos, and addresses are excluded from this report.
- The token was held outside Git and used only as a Bearer credential.
- Publishing tests used notification mode `4` (none), except for one explicit open-shift offer test to disposable employees with no contact information. Deputy reported that both recipients lacked contact details.
- Created records were labelled `LAB` and cleaned up. Shifts, areas, Event, Schedule, and the lock-test Timesheet were deleted. The three disposable employees could not be hard-deleted after related history existed, so they were terminated and verified inactive.

## Re-Deputy domain assumptions carried into the lab

- `Employee.Id`, not a display name, is the canonical Deputy person key.
- `OperationalUnit` is Deputy's Area. Re-Deputy must classify an Area as a production role, vehicle context, travel context, or generic context; Deputy does not provide that Re-Deputy semantic classification.
- A race-day event must be keyed by date plus Deputy location/Area context, not date alone.
- Travel and production blocks can be adjacent independent rosters for the same operational day.
- Open and TBC are different. Deputy open is an actual `Roster` with `Open=true` and `Employee=0`; a Re-Deputy TBC placeholder is inferred/local and must never be written as though it were a Deputy open shift.
- Future writes must use the authenticated Re-Deputy user's own verified Deputy identity and credential. Re-Deputy `admin` is not Deputy authority.
- Duplicate prevention must be enforced by Re-Deputy before create; Deputy overlap validation is useful but is not an integration idempotency key.

## Executive conclusions

1. The working legacy shift write endpoint is `POST /api/v1/supervise/roster`. It creates when `intRosterId` is absent and updates the existing row when `intRosterId` is present.
2. Contrary to older documentation saying the successful body may be empty, this trial returned the complete `Roster` object, including the new or updated `Id` and joined employee/area metadata.
3. Employee identity is an integer `Employee.Id`. `/api/v1/me` separately returns `UserId` and `EmployeeId`; they happened to both be `1` in this tenant but must not be assumed equal.
4. An Operational Unit is the concrete roster role/area foreign key. Production role, vehicle, and travel were structurally identical rosters; only their Area IDs/names and comments distinguished them.
5. Open shifts are `Employee=0`, `Open=true`. `RosterOpen` is not a mirror of all open rosters. It stayed empty for draft open shifts, published open shifts, and offers sent to employees without usable contact/login state.
6. Publishing is a separate bulk operation. Repeating it returned `200`, retained the same IDs and state, and did not advance `Modified` in read-back.
7. Repeating a create is not a safe idempotency strategy. A same-employee overlap returned `400`; `blnForceOverwrite=1` did not bypass this. A non-overlapping retry could still create a second row, so Re-Deputy needs its own durable operation key and read-before-write reconciliation.
8. Repeating an identical update returned `200` and advanced `Modified`, even though business data was unchanged. Do not treat `Modified` alone as proof of an operational change.
9. Updating role, employee, start, end, break, and comment preserved the roster ID and `Created`, while changing `Modified`.
10. A linked Timesheet made the roster non-editable: update returned `400` (`CanEdit check failed`) and delete returned `403`. Deleting the disposable Timesheet restored the ability to delete the roster.
11. `POST /supervise/roster/discard` and `DELETE /api/management/v2/shifts/{id}` both removed shifts. Neither delete path is idempotent: a repeat returned `400`.
12. Resource objects can be created with `POST /resource/{Object}`, updated with `POST /resource/{Object}/{id}`, read with `GET`, queried with `POST .../QUERY`, and deleted with `DELETE` for the tested `Schedule` and `Event` objects.
13. Employee `DELETE /resource/Employee/{id}` returned `401`, and `POST /supervise/employee/{id}/delete` returned `400 Employee cannot be deleted`. `POST .../{id}/terminate` succeeded and set `Active=false` plus `TerminationDate`.

## Authentication and permissions

### `/api/v1/me`

Sanitized result:

```json
{
  "UserId": 1,
  "EmployeeId": 1,
  "Company": 1,
  "Name": "Jayden",
  "Permissions": ["ADMINISTRATOR", "Can_Roster_Manage", "Can_Roster_All_Departments", "..."]
}
```

The authenticated identity exposed 88 permission strings. A deliberately invalid Bearer token returned `401` with an empty body. Omitting Authorization from the v2 shift-offer call returned a structured `401 Unauthorized` response.

Design rule: validate every saved credential with `/me`, persist its Deputy `EmployeeId`, tenant host, and relevant permission snapshot, and never infer write authority from a Re-Deputy role.

## Resource schemas observed with `INFO`

| Resource | Initial count | Important fields | Joins/associations relevant to Re-Deputy |
|---|---:|---|---|
| Employee | 1 | `Id`, `Company`, `DisplayName`, `UserId`, `Active`, `Role`, `Modified` | `CompanyObject`, `RoleObject`; Area associations |
| OperationalUnit | 5 | `Id`, `Company`, `OperationalUnitName`, `Active`, `ShowOnRoster`, `RosterSortOrder`, `OperationalUnitType` | Company, parent Area, active-hours Schedule; Employee and Event associations |
| Roster | 0 | `Id`, `Date`, `StartTime`, `EndTime`, `Mealbreak`, `Slots`, `TotalTime`, `OperationalUnit`, `Employee`, `Comment`, `Published`, `Open`, `MatchedByTimesheet`, `ConnectStatus`, timestamps | `EmployeeObject`, `OperationalUnitObject`, `MatchedByTimesheetObject` |
| RosterOpen | 0 | `Id`, `Roster`, `Employee`, `Accepted`, `Seen`, `Declined`, `Link`, `Message` | `RosterObject`, `EmployeeObject` |
| Event | 0 | `Id`, `Title`, `Schedule`, `Colour`, `ShowOnRoster`, `AddToBudget`, `BlockTimeOff` | `ScheduleObject`; OperationalUnit association |
| Schedule | 146 | recurrence name/date/time/repeat fields, `Saved`, `Template` | none reported |

`QUERY` is `POST`, supports `search`, `sort`, `join`, `assoc`, `start`, and `max`, and returns arrays. Deputy documents a 500-row response ceiling, so production reads will require deterministic pagination.

## Employee experiments

### Create

Endpoint: `POST /api/v1/supervise/employee`

Sanitized request pattern:

```json
{
  "strFirstName": "Lab Alice",
  "strLastName": "Camera",
  "intCompanyId": 1,
  "intGender": 0,
  "strCountryCode": "NZ",
  "strStartDate": "2026-08-13"
}
```

Three creates returned `200` and complete Employee objects with IDs `2`, `3`, and `4`. Deputy automatically supplied:

- `DisplayName` from first and last name;
- a `UserId` and generated username even though no invitation/contact data was supplied;
- `Active=true`, default role `50`, agreement/history data, `Creator`, `Created`, and `Modified`;
- `AllowLogin=false` for these uninvited employees.

Read-back through `Employee/QUERY` returned the same IDs. This confirms `Employee.Id` is stable and suitable for canonical identity linkage; generated usernames or names are not.

### Delete and terminate

| Action | Status | Sanitized response/read-back | Conclusion |
|---|---:|---|---|
| `DELETE /resource/Employee/{id}` | 401 | `Not authorized to delete` | Generic Resource DELETE is not the supported employee lifecycle call in this tenant. |
| `POST /supervise/employee/{id}/delete` | 400 | `Employee cannot be deleted` | Even disposable uninvited employees may become non-deletable after related history exists. |
| `POST /supervise/employee/{id}/terminate` | 200 | Same IDs; `Active=false`; termination date set | Termination/archive is the reliable cleanup state. Inactive rows remain queryable. |

## Operational Unit experiments

Endpoint: `PUT /api/v1/supervise/department`

Sanitized request pattern:

```json
{
  "intCompanyId": 1,
  "strOpunitName": "LAB T-Ruakaka — Side 1",
  "strExportName": "LAB_SIDE1",
  "intSortOrder": 20,
  "intOpunitType": 0
}
```

Four areas returned `200` with IDs `6`–`9`:

- production role: `LAB T-Ruakaka — Side 1`;
- production role: `LAB T-Ruakaka — Sound`;
- vehicle context: `LAB Vehicle 684`;
- travel context: `LAB Travel then Overnighter`.

Deputy automatically set `Active=true`, `ShowOnRoster=true`, `ParentOperationalUnit=0`, creator/timestamps, blank colour, company metadata, and `OperationalUnitType=0`.

Setting preferred employees with `POST /resource/OperationalUnit/7` and `{"RosterEmployeeOperationalUnit":[2,3,4]}` returned `200`. A plain GET did not surface the association; association data must be explicitly requested where supported.

Deletion with `DELETE /resource/OperationalUnit/{id}` returned `200` and a label identifying the deleted Area. Final `QUERY` read-back found no `LAB` areas.

Important interpretation: Deputy stores no first-class distinction between Side 1, Sound, vehicle 684, or Travel. Re-Deputy's classifier remains necessary. An Area name can embed a venue-like label, but the authoritative workplace/location is still the Area's `Company` and company metadata.

### Location create validation probe

`PUT /supervise/company` with the documented Company-style field names returned `400 Workplace name is not given`. This endpoint expects a different management payload dialect in this tenant (the docs mix resource-style and `str...` management-style names). Do not implement location creation from this lab result; inspect the tenant's exact management contract first if location writes ever enter scope.

## Exact roster payload

The following is the smallest payload proven for a normal assigned shift in this tenant:

```json
{
  "intStartTimestamp": 1787171400,
  "intEndTimestamp": 1787202000,
  "intRosterEmployee": 2,
  "blnPublish": false,
  "intMealbreakMinute": 30,
  "intOpunitId": 6,
  "blnForceOverwrite": 0,
  "blnOpen": 0,
  "strComment": "LAB-RD-001 Office 0830 | On track 1000 | 8 races 1200 | 1630",
  "intConfirmStatus": 0
}
```

Endpoint: `POST /api/v1/supervise/roster`

Status: `200`

Returned ID: `1`

Read-back result:

```json
{
  "Id": 1,
  "Date": "2026-08-20T00:00:00+12:00",
  "StartTimeLocalized": "2026-08-20T08:30:00+12:00",
  "EndTimeLocalized": "2026-08-20T17:00:00+12:00",
  "Mealbreak": "...00:30:00...",
  "TotalTime": 8,
  "OperationalUnit": 6,
  "Employee": 2,
  "Published": false,
  "Open": false,
  "ApprovalRequired": false,
  "ConfirmStatus": 0,
  "MatchedByTimesheet": 0,
  "Creator": 1
}
```

Automatic behavior:

- Deputy derived local `Date` from the timestamp and tenant timezone.
- The 8.5-hour wall-clock window minus a 30-minute break yielded `TotalTime=8`.
- Deputy created a scheduled break slot. Its offsets began at zero in this endpoint response, so the payload does not select an actual break clock time.
- Deputy added joined metadata under `_DPMetaData` with employee and Area display information.
- `Created` and `Modified` were assigned server-side.
- `Cost`/`OnCost` were `0` for the disposable employees.

## Roster state experiments

### Open shift

Sanitized differences from an assigned shift:

```json
{
  "intRosterEmployee": 0,
  "blnOpen": 1,
  "blnPublish": false
}
```

The create returned roster ID `2`, `Employee=0`, `Open=true`, and `Published=false`. Publishing later retained `Employee=0` and `Open=true` while setting `Published=true`.

This is the exact Deputy distinction from an assigned shift. It is also distinct from Re-Deputy TBC, which has no Deputy Roster ID.

### Travel versus production versus vehicle

| ID | Employee | Area | Window | Structural result |
|---:|---:|---|---|---|
| 1 | 2 then 3 | production role | 20 Aug 08:30–17:00, later 09:15–18:00 | Ordinary Roster |
| 3 | 4 | Travel then Overnighter | 19 Aug 14:00–17:00 | Ordinary Roster |
| 4 | 2 | Vehicle 684 | 20 Aug 08:00–08:30 | Ordinary Roster |

No Roster field identifies travel or vehicle semantics. The travel block differed only by `OperationalUnit=9`, timestamps, employee, and comment. The vehicle lead-in touched the production shift end-to-start and was accepted without overlap. This experimentally supports Re-Deputy's existing adjacent-row merge rules while confirming that raw evidence must be preserved.

### Move between roles

Request: same create payload plus `intRosterId=1`, with `intOpunitId` changed from `6` to `7`.

Status: `200`

Read-back: roster ID stayed `1`; Operational Unit became `7`; employee and times stayed unchanged; `Created` stayed unchanged; `Modified` advanced.

### Change employee

Request: same update payload with `intRosterEmployee` changed from `2` to `3`.

Status: `200`

Read-back: roster ID stayed `1`; `Employee=3`; joined Employee display name changed; role and times remained.

### Change start/end/break

Request: same update payload with start `09:15`, end `18:00`, and meal break `45` minutes.

Status: `200`

Read-back: timestamps changed, meal-break duration changed, and `TotalTime` remained `8` (8.75 wall-clock hours less 0.75). Roster ID and `Created` remained stable; `Modified` advanced.

### Identical update

Repeating the exact update returned `200` and the same ID, but advanced `Modified`. This endpoint is state-idempotent for an existing ID but not metadata-idempotent. Re-Deputy must compare normalized business fields rather than use `Modified` as its only change signal.

### Duplicate create and force overwrite

Repeating the initial create without `intRosterId` returned `400`:

```json
{"error":{"code":400,"message":"Overlap detected! [employee] already working ..."}}
```

Read-back still had one row. Repeating an overlapping create with `blnForceOverwrite=1` also returned the same overlap error. In this endpoint/tenant, that flag did not make creation overwrite or bypass the hard overlap rule.

This is not a complete idempotency guarantee: changing employee, time, or adjacency could avoid the overlap detector and create a second logical shift. Recommended create key:

```text
tenant + initiating Deputy employee + source workday/version + canonical assignment key
```

Store the returned Deputy roster ID before retrying; on ambiguous timeout, query and reconcile by that local operation key's intended employee/area/window/comment fingerprint before issuing another create.

## Publishing behavior

Endpoint: `POST /api/v1/supervise/roster/publish`

Sanitized request:

```json
{
  "intMode": 4,
  "blnAllLocationsMode": 0,
  "intRosterArray": [1, 2, 3, 4]
}
```

Status: `200`

Response: array containing the four full Roster objects, in a different order from the request.

Read-back: all four had `Published=true`; IDs and other state were preserved. The first publish advanced `Modified`. Repeating the exact publish returned `200` and the four objects again, while subsequent read-back showed no further `Modified` changes.

Do not rely on response order matching request order; correlate by `Id`.

## `RosterOpen` and shift offers

`RosterOpen/INFO` showed fields for a per-employee offer record: `Roster`, `Employee`, acceptance/seen/declined flags, a private link, message, and timestamps.

Observed sequence:

| Step | Status | Roster state | `RosterOpen/QUERY` count |
|---|---:|---|---:|
| Create draft open roster | 200 | open, unpublished, employee 0 | 0 |
| Publish open roster | 200 | open, published, employee 0 | 0 |
| Set Area preferred employees | 200 | unchanged | 0 |
| Create another open roster with `blnPublish=true` | 200 | open, published, employee 0 | 0 |
| `POST /api/management/v2/shifts/offers:notify` to employee IDs 2 and 3 | 200 | open, published, employee 0 | 0 |

The offer response was `success=true`, returned operation metadata, and reported `countEmployeeWithNoContactInfo=2`. No `RosterOpen` records were created, plausibly because these employees were not invited and had no contact methods. That is a trial-supported inference, not a universal rule. A future lab round should repeat with disposable, invited employee accounts and safe test mailboxes.

Offer error probes:

- no Authorization: `401 Unauthorized`;
- unknown employee ID: `400 Cannot find employee for given ids`;
- valid admin token and disposable IDs: `200`.

## Deleting and cancelling shifts

### Discard

Endpoint: `POST /api/v1/supervise/roster/discard`

Request: `{"intRosterArray":[3]}`

Status: `200`; response contained the removed Roster object. `GET /resource/Roster/3` then returned `404 Object not found`. Repeating discard returned `400 No rosters given`.

### v2 delete

Endpoint: `DELETE /api/management/v2/shifts/5`

Status: `200`, `{"success":true}`. Read-back returned `404`. Repeating delete returned `400 No shift found`.

Both paths are destructive removal, not a retained cancelled-state row in Resource reads. If Re-Deputy needs cancellation history, capture the before-image and write its own audit record before deletion; never infer a cancelled row from an ordinary missing read without operation evidence.

## Locked roster behavior

1. Created past roster ID `6`, published.
2. Created Timesheet ID `2` using `POST /supervise/timesheet/update` with `intRosterId=6` and matching employee/Area/times.
3. Roster read-back showed `MatchedByTimesheet=2`.
4. Attempted roster update returned `400 INVALID roster cannot be updated because CanEdit check failed`.
5. Attempted v2 delete returned `403 You do not have permission to delete shift` even though `/me` reported administrator/roster permissions.
6. Deleted the disposable Timesheet with `DELETE /resource/Timesheet/2`.
7. Retried roster delete; it returned `200`.

The lock is relational, not represented solely by `ConnectStatus` (which remained null here). Treat nonzero `MatchedByTimesheet`, `CanEdit` failure, and relevant API status as the authoritative lock evidence.

## Validation and error behavior

| Probe | Status | Sanitized response |
|---|---:|---|
| Missing timestamps/date | 400 | `Date must be given.` |
| Unknown employee | 400 | `Employee does not exist or not active.` |
| Unknown Operational Unit | 400 | `INVALID given area can not be found` |
| End before start | 400 | `INVALID roster start/end is wrong` |
| Same-employee overlap | 400 | `Overlap detected! ...` |
| Invalid Bearer token on `/me` | 401 | empty body |
| No auth on offers endpoint | 401 | structured Unauthorized error |
| Invalid employee on offers endpoint | 400 | structured employee-not-found error |
| Update timesheet-linked roster | 400 | `CanEdit check failed` |
| Delete timesheet-linked roster | 403 | permission error |

Error bodies are not uniform across API families. The integration should record status plus a bounded, redacted JSON/text body and must not assume one error schema.

## Event and Schedule experiments

### Schedule

`Schedule/QUERY` initially returned 146 recurrence records, including public-holiday and template-like schedules. Time-only fields were localized in responses using the current response date, while `StartDate` retained the recurrence anchor. Do not mistake `StartTime`'s rendered date portion for the Schedule occurrence date.

Create:

```text
POST /api/v1/resource/Schedule
```

```json
{
  "Name": "LAB Schedule Probe",
  "StartDate": "2026-08-25",
  "StartTime": "09:00:00",
  "EndTime": "17:00:00",
  "RepeatType": 0,
  "RepeatEvery": 1,
  "Saved": true,
  "Template": false
}
```

Status `200`, returned ID `308`. `POST /resource/Schedule/308` updated name and times, retaining ID/Created and advancing Modified. GET confirmed the change. DELETE returned `200`; GET then returned `404`.

### Event

Create with `POST /resource/Event` returned `200`, ID `1`. Update with `POST /resource/Event/1` changed the title, Schedule to `308`, colour, roster visibility, budget, and block-time-off flag, and associated Operational Units `[6,7]`.

An `Event/QUERY` using `join:["ScheduleObject"]` and `assoc:["OperationalUnit"]` returned both the Schedule object and the two full Area objects. DELETE returned `200`; GET then returned `404`.

Event/Schedule are recurrence/calendar metadata objects, not substitutes for Roster. Re-Deputy race days should remain built from concrete Roster rows unless a later integration feature deliberately models Deputy Events.

## Experiment ledger

Each row below represents an experiment plus its required read-back where applicable. Requests and responses are sanitized.

| # | Endpoint and method | Sanitized request | Status / IDs | Read-back and conclusion |
|---:|---|---|---|---|
| 1 | `GET /me` | none | 200 | User 1, Employee 1, Company 1; administrator permissions. |
| 2 | `GET Employee/INFO`; `POST Employee/QUERY` | sort ID, max 50 | 200; 1 baseline row | Schema and stable Employee identity confirmed. |
| 3 | `GET OperationalUnit/INFO`; `POST .../QUERY` | sort ID, max 100 | 200; 5 baseline rows | Areas belong to Company 1. |
| 4 | `GET Roster/INFO`; `POST .../QUERY` | joins Employee/Area | 200; 0 baseline | Clean roster baseline. |
| 5 | `GET RosterOpen/INFO`; `POST .../QUERY` | joins Roster/Employee | 200; 0 baseline | Offer-record schema, not open-roster mirror. |
| 6 | `GET Event/INFO`; `POST .../QUERY` | join Schedule | 200; 0 baseline | Clean Event baseline. |
| 7 | `GET Schedule/INFO`; `POST .../QUERY` | sort ID, max 100 | 200; 146 reported | Recurrence objects already exist. |
| 8 | `POST /supervise/employee` ×3 | lab names, company 1, NZ, start date | 200; IDs 2–4 | Full Employee objects returned. |
| 9 | `PUT /supervise/company` | documented Company-style body | 400 | Management dialect rejected it: workplace name missing. |
| 10 | `PUT /supervise/department` ×4 | lab production/vehicle/travel names | 200; IDs 6–9 | Area IDs returned; automatic defaults observed. |
| 11 | `POST /supervise/roster` | assigned Employee 2, Area 6, 08:30–17:00, 30m | 200; ID 1 | Query returned derived date, 8 paid hours, break slot. |
| 12 | Repeat experiment 11 | identical | 400 | Overlap rejected; query count stayed 1. |
| 13 | `POST /supervise/roster` | Employee 0, Area 7, Open 1 | 200; ID 2 | Open true, unpublished; RosterOpen count 0. |
| 14 | `POST /supervise/roster` | Employee 4, travel Area 9 | 200; ID 3 | Ordinary Roster; no travel-specific field. |
| 15 | `POST /supervise/roster` | Employee 2, vehicle Area 8, adjacent 30m | 200; ID 4 | Ordinary adjacent Roster; no vehicle-specific field. |
| 16 | `POST /supervise/roster` | `intRosterId=1`, Area 7 | 200; ID 1 | Role moved in place; GET confirmed. |
| 17 | same endpoint | `intRosterId=1`, Employee 3 | 200; ID 1 | Employee changed in place; GET confirmed. |
| 18 | same endpoint | new start/end and 45m break | 200; ID 1 | GET confirmed; paid total recalculated. |
| 19 | `POST /supervise/roster/publish` | mode 4, IDs 1–4 | 200; four objects | All Published true; response order differed. |
| 20 | Repeat publish | same | 200 | State and Modified remained stable. |
| 21 | `POST OperationalUnit/7` | preferred employee IDs 2–4 | 200; ID 7 | Plain GET omitted association; request accepted. |
| 22 | `POST /supervise/roster` | new published open shift | 200; ID 5 | Open/published/employee 0; RosterOpen still 0. |
| 23 | `POST .../offers:notify` no auth | one shift/employee | 401 | Structured Unauthorized. |
| 24 | same endpoint, invalid employee | employee 9999 | 400 | Structured employee-not-found. |
| 25 | same endpoint, valid disposable employees | shift 5, employees 2–3 | 200 | Success; two without contact info; RosterOpen stayed 0. |
| 26 | `POST /supervise/roster/discard` | ID 3 | 200 | GET 404; repeated discard 400. |
| 27 | `DELETE /api/management/v2/shifts/5` | none | 200 | GET 404; repeated delete 400. |
| 28 | Create past roster | Employee 2, Area 6 | 200; ID 6 | Lock-test source. |
| 29 | `POST /supervise/timesheet/update` | `intRosterId=6`, matching facts | 200; Timesheet 2 | Roster MatchedByTimesheet became 2. |
| 30 | Update/delete roster 6 | changed end; then DELETE | 400 / 403 | Locked by linked Timesheet. |
| 31 | Four invalid roster creates | missing date, bad employee, bad Area, reversed times | 400 each | Specific validation messages returned. |
| 32 | `GET /me` invalid token | none | 401 | Empty error body. |
| 33 | Forced overlapping create | `blnForceOverwrite=1` | 400 | Overlap still rejected. |
| 34 | Identical update of roster 1 | same existing-ID body | 200; ID 1 | Modified advanced despite no business change. |
| 35 | `POST /resource/Schedule` | one-off lab recurrence | 200; ID 308 | Complete object returned. |
| 36 | `POST /resource/Event` | lab Event | 200; ID 1 | Complete object returned. |
| 37 | `POST Schedule/308`; GET | changed name/times | 200 | Same ID, GET matched. |
| 38 | `POST Event/1`; GET/QUERY | Schedule 308, Areas 6–7 | 200 | Join/association returned related objects. |
| 39 | DELETE Event/Schedule; GET each | IDs 1/308 | 200 then 404 | Resource delete verified. |
| 40 | DELETE Timesheet 2; DELETE shift 6 | none | 200 / 200 | Removing lock relation enabled shift deletion. |
| 41 | Discard IDs 1,2,4 | bulk ID array | 200 | Final LAB roster query count 0. |
| 42 | DELETE Areas 6–9 | none | 200 each | Final LAB Area query count 0. |
| 43 | Generic Employee DELETE ×3 | IDs 2–4 | 401 each | Unsupported/unauthorized lifecycle path. |
| 44 | Supervise employee delete ×3 | IDs 2–4 | 400 each | History prevented hard deletion. |
| 45 | Employee terminate ×3 | IDs 2–4 | 200 each | Read-back: all inactive with termination dates. |

## Recommended integration contract for Re-Deputy

Do not implement automatically from this report; use it as the design input for a separate reviewed change.

1. Credential binding
   - Store token encrypted per Re-Deputy user.
   - Bind `{tenant host, /me.EmployeeId}` to that account.
   - Revalidate tenant, identity, and required Deputy permissions before every write session.
   - Never fall back to another user's token.

2. Draft-first write plan
   - Translate one Re-Deputy published/manual assignment into an explicit intended Deputy payload.
   - Show tenant, initiating Deputy identity, employee, Area, local start/end, open/published state, break, and comment before confirmation.
   - Treat TBC as no Deputy write. Treat Open as `Employee=0`, `Open=true` only after explicit confirmation.

3. Idempotency and duplicate prevention
   - Persist a local write operation before network transmission.
   - Store the returned Deputy roster ID atomically.
   - Retry updates by ID, never by issuing another create.
   - After timeout/unknown outcome, query the exact local date/window and reconcile a fingerprint before retry.
   - Consider Deputy overlap errors safety feedback, not the primary idempotency mechanism.

4. Read-back verification
   - Query `GET /resource/Roster/{id}` after every create/update/publish/delete decision.
   - Compare normalized employee ID, Area ID, start/end, paid break, open/published state, and comment.
   - Do not compare response order or `Modified` alone.

5. Classification
   - Maintain Re-Deputy's domain classification of Operational Units.
   - Write production, vehicle, and travel as separate rosters only when that is the reviewed intended Deputy model.
   - Preserve raw IDs/names/comments so adjacent-row interpretation remains explainable.

6. Lock/cancellation handling
   - Refuse mutation when `MatchedByTimesheet` is nonzero or Deputy says `CanEdit` failed.
   - Never delete a Timesheet automatically to unlock production data; that was permissible only in this disposable lab.
   - Capture a before-image and local audit event before discard/delete because Resource read-back becomes 404.

7. Error handling
   - Store endpoint, method, sanitized request, HTTP status, bounded sanitized body, target IDs, and read-back result.
   - Treat 400 overlap/validation, 401 identity/auth, 403 permission/lock, 404 missing, and ambiguous 5xx/timeouts differently.

## Questions for a second lab round

- Repeat `RosterOpen` experiments with invited disposable employees and safe test mailboxes to observe offer-row creation, acceptance, decline, and removal after fill.
- Test `ApprovalRequired=true` through the v2 shift API and manager approval lifecycle.
- Test confirmation-required publish mode `5` with an invited disposable account.
- Map the v2 `/api/management/v2/shifts` create/update schema against the legacy `/supervise/roster` behavior, including external IDs and micro-schedules.
- Determine the tenant-exact location creation payload; the documented Company-style body was not accepted by the management endpoint.
- Test limited-permission tokens created by non-admin disposable managers/employees to build a precise permission matrix.
- Test multi-location employee/Area associations and whether an Area outside the employee's workplace creates warnings versus hard failures.

## Official references used for comparison

- [Deputy Resource API overview](https://developer.deputy.com/docs/resource-api-objects)
- [Adding/updating a shift](https://developer.deputy.com/docs/adding-a-shift)
- [Getting shifts](https://developer.deputy.com/docs/getting-shifts)
- [Publishing a roster](https://developer.deputy.com/reference/publisharoster)
- [Shift offers](https://developer.deputy.com/docs/shift-offers)
- [RosterOpen object](https://developer.deputy.com/docs/rosteropen)
- [Micro-scheduling and v2 shift deletion](https://developer.deputy.com/docs/shift-plans-1)
- [Employee object](https://developer.deputy.com/docs/employee)
- [Area/OperationalUnit object](https://developer.deputy.com/docs/operational-unit-1)
- [Event object](https://developer.deputy.com/docs/event)

The experimental observations in this report take precedence over documentation examples for this specific trial tenant and date, while still requiring a fresh compatibility check before any production rollout.
