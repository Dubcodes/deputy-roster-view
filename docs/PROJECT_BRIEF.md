# Project Brief

## Name

Deputy Roster View

## Purpose

Build a private roster viewer that pulls Deputy roster and schedule data for multiple trusted users, stores it locally, and presents the useful work information in a compact, phone-friendly way.

The app exists because Deputy's roster view is hard to scan. The main questions the app should answer quickly are:

- Where am I working?
- What is my role?
- What time do I start?
- What are the important race-day timings?
- Who else is working and what are they assigned to?
- Has anything changed since I last checked?
- Are there available/open shifts?

## Current Scope

- Capture logged-in Deputy web schedule data for roster, crew, role, open shift, and count context.
- Keep Deputy iCal as an optional backup roster feed.
- Store shifts, local notes, change history, schedule rows, users, trusted devices, sync state, and sync logs in SQLite.
- Show month calendar, list view, day details, settings, diagnostics, manual sync, and timesheet summaries.
- Support one-time signup with encrypted Deputy credentials and long-lived trusted devices.
- Let admins revoke trusted devices, reset PINs, clear noisy changed flags, and record manual overrides.
- Let each user choose their own display colour theme.
- Stagger background syncs across users so Deputy is not hit by every account at once.
- Run with Docker Compose / Portainer.

## Out Of Scope For Now

- Applying for shifts.
- Writing back to Deputy.
- Official Deputy API integration unless the user later gets API access.
- Full overnight/travel-day calculations beyond simple display and next-day crew fallback.
- Timesheet submission and leave/time-off workflows.

## Primary UX

Phone-first. The month calendar is the landing page, but the day page must be dense, readable, and useful during work.
