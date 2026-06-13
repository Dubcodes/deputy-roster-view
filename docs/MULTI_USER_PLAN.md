# Multi-User Expansion Plan

## Archive Point

The current working single-user app is archived at Git tag `single-user-v1`.

Use that tag if we ever need to recover the simple private version before the shared multi-user work.

## Direction

Keep this repo, but evolve it into a new multi-user version. Do not throw away the current work. The existing calendar, day view, schedule capture, race-day parsing, and timing rules become the base for the shared system.

Recommended deployment:

- One app instance.
- One shared database.
- One trycloudflared URL while testing.
- Per-user pages and trusted-device sessions.
- Shared roster/schedule capture data so one user's Deputy account can fill gaps for everyone.

## User Flow

Signup should be one-time:

1. User opens `/signup`.
2. User enters Deputy email, Deputy password, and a self-set PIN.
3. App logs into Deputy once and captures the user's Deputy identity/name.
4. App stores a long-lived trusted-device cookie.
5. User normally opens the same phone tab and gets straight to their roster.

The app should not show or log Deputy passwords.

## Auth And Secrets

Use two separate concepts:

- App PIN: hash with Argon2 or bcrypt. Never store plain text.
- Deputy session: prefer encrypted Deputy browser/session cookies after first login.

If Deputy login credentials must be retained for background capture, store them encrypted at rest with a server-side key supplied through env. Do not put the encryption key in the database.

Trusted-device tokens:

- Long-lived cookie on the user's phone.
- Store only a hash of the token in the database.
- Admin can revoke device sessions.

## Shared Data Model

Shared tables should hold:

- Race days / roster days.
- Track and racing type.
- Crew assignments.
- Vehicle assignments.
- Available/open shifts.
- Schedule capture coverage.
- Raw sanitized capture diagnostics.
- Admin corrections/overrides.

User-owned tables should hold:

- User identity.
- User's own Deputy roster shifts.
- User notes and timing overrides.
- User viewed/change state.
- Trusted devices.
- Deputy session/credential secrets.

## Capture Strategy

The app should track capture coverage by date and location:

- Date.
- Track/location.
- Source user/account.
- Capture time.
- Crew row count.
- Open shift count.
- Warnings/unavailable/published counts.
- Whether the day is missing or partial.

Background jobs should prioritize upcoming days with missing or partial crew data. If one user's account cannot see a day clearly, another user's account may fill the gap.

## Admin Panel

Admin should be able to:

- View users and sync health.
- Revoke sessions.
- See which days are missing crew data.
- Trigger capture for a date/track.
- Edit/override interpreted timing rows.
- Edit/override crew position or vehicle assignments.
- Add admin notes.
- See audit history for manual corrections.

Admin should not see Deputy passwords.

## Data Warning

Show a small note in the app:

> This is a copy of Deputy roster data and may lag behind Deputy. Check Deputy if something looks wrong or urgent.

## Migration Checklist

- Archive the current single-user version. Done: `single-user-v1`.
- Design the shared schema.
- Add Postgres support for multi-user deployment.
- Add user signup and trusted-device auth.
- Add encrypted Deputy session storage.
- Add shared schedule capture coverage.
- Add admin panel and override audit trail.
- Test with the current user first.
- Add a second test user.
- Run both old and new systems briefly.
- Once the multi-user system is stable, remind Jayden to delete the old single-user server/container and old data volume.
