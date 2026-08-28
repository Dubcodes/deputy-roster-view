# User purge policy

This is the explicit `app_users` foreign-key policy for Re-Deputy 0.5.3. Purge starts an immediate SQLite transaction, rejects active accounts, preflights retention-critical references, and then either commits the complete cleanup or rolls it all back.

## Cascade with the purged account

These rows are owned by the account and use `ON DELETE CASCADE`:

- `app_user_deputy_identity.app_user_id`
- `deputy_oauth_connections.app_user_id`
- `deputy_oauth_states.app_user_id`
- `deputy_personal_assignment_evidence.owner_user_id`
- `deputy_personal_capture_coverage.owner_user_id`
- `deputy_reference_employees.app_user_id`
- `deputy_reference_units.app_user_id`
- `deputy_schedule_observations.observer_user_id`
- `deputy_user_secrets.user_id`
- `notification_events.app_user_id`
- `notification_preferences.app_user_id`
- `push_subscriptions.app_user_id`
- `sync_generation_members.user_id`
- `trusted_devices.user_id`
- `user_crew_memberships.user_id`
- `user_event_time_overrides.user_id`
- `user_event_transport_preference_audit.user_id`
- `user_event_transport_preferences.user_id`
- `user_sync_state.user_id`
- `workday_open_position_applications.app_user_id`
- `workday_user_visibility.user_id`

Personal `shifts.owner_user_id` is a legacy non-FK owner column. Purge explicitly deletes those shifts plus their marks and changes in the same transaction.

## Preserve the record and detach the user

These audit/evidence records use `ON DELETE SET NULL` and remain in place:

- `admin_overrides.created_by_user_id`
- `capture_coverage.source_user_id`
- `crew_identity_merge_audit.app_user_id`
- `crew_identity_merge_audit.merged_by_user_id`
- `crew_known_locations.source_user_id`
- `crew_people.app_user_id`
- `crew_team_audit.actor_user_id`
- `crew_vehicle_audit.actor_user_id`
- `deputy_event_coverage.source_user_id`
- `deputy_web_captures.owner_user_id`
- `error_reports.user_id`
- `location_team_mappings.updated_by_user_id`
- `roster_day_assignments.user_id`
- `roster_day_versions.published_by_user_id`
- `roster_days.created_by_user_id`
- `roster_days.updated_by_user_id`
- `roster_days.published_by_user_id`
- `workday_assignments.user_id`
- `workday_audit_events.actor_user_id`
- `workday_open_position_applications.reviewed_by_user_id`
- `backup_runs.requested_by_user_id`
- `admin_action_audit.actor_user_id`

`contractor_invites.activated_user_id` is explicitly detached while retaining the contractor-person invitation history. The non-FK audit columns `admin_overrides.disabled_by_user_id` and `crew_people.merged_by_user_id` are also nulled.

`admin_action_audit` also stores actor display/account snapshots, so its history remains intelligible after `actor_user_id` is detached. It never stores credentials, PINs, tokens, or raw invitation links.

## Delete only self-related records

- `account_invitations.activated_user_id`: delete the consumed invitation that activated the purged account.
- `app_role_audit.target_user_id`: delete role-change rows describing changes of the purged account.
- Eligible `crew_people` with `identity_source='account_synthetic'`, no Deputy employee ID, employee person type, and no workday or contractor-invite evidence: detach and delete. Deputy-backed, manual, assigned, and contractor people are preserved.

## Retention-critical references that block purge

These `NO ACTION` references preserve actions performed on other persistent records. If present after excluding the self-related rows above, purge returns `blocked` with a safe reason before deleting anything:

- `account_invitations.created_by_user_id`: invitations created for other accounts.
- `app_role_audit.actor_user_id`: role changes performed on other accounts.
- `contractor_invites.created_by_user_id`: contractor invitations created for other people.
- `deputy_oauth_config.updated_by_user_id`: installation OAuth configuration audit.
- `deputy_person_mappings.updated_by_user_id`: retained Deputy person-mapping audit.
- `deputy_unit_mappings.updated_by_user_id`: retained Deputy unit-mapping audit.
- `deputy_write_operations.app_user_id`: retained Deputy write-operation audit.

`PRAGMA foreign_keys` remains enabled. An unexpected integrity failure rolls the transaction back and returns a generic blocked result without exposing raw SQLite text.
