# Admin mutation inventory (0.5.6)

This matrix is derived from every mutating FastAPI `/admin` route in `app/main.py` and checked against the Admin templates/forms. All rows receive a central `admin_action_audit` route record with central redaction. Existing specialist history remains authoritative for its domain; it is not replaced or copied wholesale. `PB` means a required pre-destructive backup.

| Route | Action / target | Existing specialist history | Central audit | PB |
|---|---|---|---|---|
| `/admin/users/{u}/devices/{d}/revoke` | revoke trusted device | trusted-device row | account/access with actual revoke/no-op result | No |
| `/admin/users/{u}/role` | promote/demote account | `app_role_audit` | account/access | No |
| `/admin/users/{u}/pin` | Admin PIN reset | account timestamp | account/access; PIN redacted | No |
| `/admin/users/{u}/deputy-login` | saved Deputy credential metadata | encrypted secret store | account/access; credentials redacted | No |
| `/admin/users/{u}/deactivate`, `/reactivate` | account state | device revocation/sync state | account/access before/after active state | No |
| `/admin/users/{u}/reset-roster` | delete local roster copy | `shift_changes` is removed with rows; marks are not recoverable | account/access with safe shift/change/mark counts | **Yes** |
| `/admin/users/{u}/purge` | hard purge inactive account | purge policy, FK history | account/access with compact result | **Yes** |
| `/admin/cleanup` | age-based inactive cleanup | purge policy | account/access with eligible/purged/blocked counts | **Yes, once per batch** |
| `/admin/users/{u}/sync`, `/admin/clear-changed` | operator sync/acknowledge | sync log / shift changes | admin operations | No |
| `/admin/account-invitations` | create account invite | hash-only invitation row | account/access; raw link/token redacted | No |
| `/admin/account-invitations/{i}/reissue`, `/revoke`, `/delete` | invitation lifecycle | hash-only invitation row | account/access | No |
| `/admin/contractors/invites` | contractor identity/invite | contractor invite and crew person | contractors; raw link/token redacted | No |
| `/admin/contractors/invites/{i}/reissue`, `/revoke` | contractor invitation lifecycle | contractor invite | contractors | No |
| `/admin/deputy-api/config` | installation OAuth/write config | Deputy config / write audit | non-secret configured/write-mode state; credentials redacted | No |
| `/admin/deputy-api/person-mapping`, `/unit-mapping` | Deputy mapping | mapping timestamps only | compact mapping IDs/context before/after-safe summary | No |
| `/admin/roster-days/{id}/deputy-trial/execute` | explicit trial write | `deputy_write_operations` authoritative | high-level route event only | No |
| `/admin/travel-defaults`, `/{id}/edit`, `/{id}/delete` | travel default CRUD | row timestamps only | concise label/base/minutes/source before/after | No |
| `/admin/travel-routes`, `/{id}/delete` | directed route CRUD | row timestamps only | concise endpoints/minutes/source before/after | No |
| `/admin/crew/{id}` | crew person, aliases, account link | identity history | crew/identity | No |
| `/admin/identity-reconciliation/preview`, `/apply` | identity review/repair | `crew_identity_merge_audit` | crew/identity | No |
| `/admin/crew-link/resolve` | transfer/merge/cancel link | `crew_identity_merge_audit` | crew/identity | No |
| `/admin/teams/save`, `/{id}/members`, `/admin/crew/{id}/teams` | team CRUD/membership | `crew_team_audit` | crew/identity | No |
| `/admin/vehicles/save` | vehicle CRUD | `crew_vehicle_audit` | crew/identity | No |
| `/admin/workday-roles/save` | role catalogue | row timestamps only | concise label/key/order/active before/after | No |
| `/admin/roster-days/save` | draft create/edit/assignment/vehicle | draft rows are mutable | bounded normalized day fields plus assignment count; notes excluded | No |
| `/admin/roster-days/{id}/publish` | publish workday | `roster_day_versions`, publication snapshot | workdays | No |
| `/admin/roster-days/{id}/delete` | never-published draft delete | deletion blockers / cascades | workdays with compact before/after | **Yes** |
| `/admin/workday-applications/{id}/review` | open-position decision | application row / workday event | workdays | No |
| `/admin/overrides`, `/{id}/disable` | timing override | versioned `admin_overrides` | racing/maps | No |
| `/admin/planning-locations`, `/location-team` | planning visibility/team | preference/mapping timestamps only | enabled/primary-team before/after | No |
| `/admin/love-racing-refresh`, `/preview`, `/unresolved-refresh`, `/times-refresh` | planning/race cache operation | Love Racing cache/jobs | racing/maps | No |
| `/admin/track-maps-refresh`, `/track-maps/{key}/upload`, `/reset` | map cache/manual file | `track_maps`, migration warnings | racing/maps | No |
| `/admin/track-map-locations/{key}/classify`, `/reset` | location classification | location rule row is mutable | classification/canonical venue before/after | No |
| `/admin/backups/create` | manual safety snapshot | `backup_runs`, manifest | safety/recovery with validation result | N/A |

Every Admin mutation has a durable write-ahead route record. A route without explicit mutation evidence ends as `request_finished`, not `completed`; handlers use controlled outcomes including `completed`, `unchanged`, `blocked`, `invalid`, and `not_found`. Non-Admin authenticated mutations (`/settings`, `/day`, `/shift`, applications) retain their existing specialist/domain history and are intentionally outside this Admin-only central audit. There is no generic Undo, restore, backup-download, or web restore route.

## Existing recovery/history systems

- `app_role_audit`: role changes.
- `deputy_write_operations` / `deputy_roster_links`: authoritative trial-write audit and ownership boundary.
- `admin_overrides`: append-only/superseding timing corrections.
- `roster_day_versions` and published snapshots: manual-workday publication history.
- Account/contractor invitations: hash-only lifecycle history.
- `shift_changes`, `deputy_schedule_event_changes`, assignment history: source roster changes.
- `notification_events` / deliveries: notification history.
- `crew_identity_merge_audit`, `crew_team_audit`, `crew_vehicle_audit`: specialist identity/team/vehicle history.
- `user_event_transport_preference_audit`: reversible personal self-travel history.
- `backup_runs` plus managed manifests: backup attempts/results.
- `admin_action_audit`: concise cross-domain operator history; it references specialist history where applicable rather than copying sensitive/raw payloads.
