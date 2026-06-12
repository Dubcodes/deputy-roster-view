# Project Brief

## Name

Deputy Roster View

## Purpose

Build a private roster viewer that pulls a user's Deputy roster and schedule data, stores it locally, and presents the useful work information in a compact, phone-friendly way.

The app exists because Deputy's roster view is hard to scan. The main questions the app should answer quickly are:

- Where am I working?
- What is my role?
- What time do I start?
- What are the important race-day timings?
- Who else is working and what are they assigned to?
- Has anything changed since I last checked?
- Are there available/open shifts?

## Current Scope

- Fetch Deputy iCal roster feed as the base roster source.
- Capture logged-in Deputy web schedule data for richer crew/role context.
- Store shifts, local notes, change history, schedule rows, and sync logs in SQLite.
- Show month calendar, list view, day details, settings, diagnostics, manual sync, and timesheet summaries.
- Run with Docker Compose / Portainer.

## Out Of Scope For Now

- Applying for shifts.
- Writing back to Deputy.
- Official Deputy API integration unless the user later gets API access.
- Full overnight/travel-day calculations beyond simple display and next-day crew fallback.
- Timesheet submission and leave/time-off workflows.

## Primary UX

Phone-first. The month calendar is the landing page, but the day page must be dense, readable, and useful during work.
