# Domain Rules

## Racing Codes

- `T-` means Thoroughbred racing.
- `H-` means Harness racing.
- `G-` means Greyhound racing. At Cambridge this is the same physical venue as Cambridge Harness, but keep it logically distinct unless a future admin mapping says otherwise.
- `T-Cambridge` should display as Cambridge Synthetic when that is the track code from the user's roster.
- `H-Cambridge` is Cambridge Harness. The current captured Deputy location ID seen on personal shift rows is `121`; older/static `56` references were stale and should not be used for Harness.
- Deputy can put an adjacent vehicle/travel row on the wrong meeting location while the real production-position row is correct. If the same person's rows touch end-to-start and share the same roster note, merge them and let the real position row supply the location and race type.
- Deputy can also split vehicle context into short adjacent rows before the real production row, with the first short row carrying an older/stale note. If the rows touch and both sides have race-day timing context, merge the short vehicle lead-in into the real production row and keep the production row's location/race type.

## Important Track/Location Labels

- `Office` and `Clow Place` both mean the base/start-of-day location.
- `Clow Pl` is a common shorthand for `Clow Place`.
- `On track` is the arrival time at the track.
- `Ruak` / `RUAK` should display as Ruakaka.
- Vehicle maintenance days are `[VEH] Vehicles`.
- Overnight/travel codes such as `8PE` should be treated as out-of-region travel context, not a normal track.
- Short generic rows such as `Manager / Northern Ops - Contractors` can be Deputy context rows for the same race day. If they are adjacent to a real track shift and share the same roster note, merge them into the real shift for display/totals instead of showing a separate `Web / Shift` entry.

## Race-Day Timing Maths

For normal race days with enough roster-note timing data:

1. Start from `Office` or `Clow Place`.
2. Travel time is `On track - base start`.
3. Last race time is the race start time.
4. Add 3 minutes for the last race.
5. Round up to the next 15-minute interval.
6. Add 1 hour pack-up.
7. Add return travel time.
8. Total is calculated finish minus base start.

If a roster note is missing either the base/start time or the on-track time, the app may use a saved default travel time for that track. Manual admin defaults are preferred over learned defaults. Learned defaults come from previous roster notes where both base and on-track times were present.

Learned travel samples are counted once per track/date, not once per rostered person. Shared crew copies of the same note must not give one race day extra weight. Generic context such as `Northern Ops Contractors`, `Web`, `Travel`, or `Vehicles` is not a travel destination.

Default travel times are only a fallback. If the roster note gives both base and on-track times, use the note.

Manual override rules:

- If "Finished/back at office" is entered, use that as the final end time and round up to the next 15 minutes.
- If "Last race time changed" is entered, use that as the last race time and then apply the normal race-day maths.

## Roster Notes

Roster notes are human-entered and inconsistent. Known patterns include:

- `Trucks 0815`
- `Clow Place 0830`
- `Office 0715`
- `On track 0845`
- `First cross 1100`
- `Records 1030 Live 1100` where `Live` can be treated as the first-cross/live time.
- `10 races 1110 | 1624`
- `First race 1157 | Last race 1616`

The parser should accept both dash and pipe separators for first/last race shorthand.

## Crew Data

The reliable crew list should come from Deputy web schedule rows where possible, not from free-text roster notes.

Deputy's `SVT` area means a combined Sound/VT position only when no separate VT operator is rostered for the same event. If a different person has an overlapping `VT` assignment at the same date and Deputy location, display `SVT` as `Sound` and keep the other assignment as `VT`.

Crew data must be scoped by date and Deputy location/track, not only by date. Multiple race meetings or work groups can happen on the same day, so each user should see the crew list for the location attached to their own shift.

The race-day crew table should stay as three columns: position, name, vehicle. A user's own vehicle can be shown beside their position in the day header, but should not be merged into the crew table position column.

Vehicle-only schedule rows should not create standalone crew table rows. Use vehicle rows to attach a vehicle to someone who also has a production position, but hide people who only have a vehicle/travel assignment so forgotten vehicle rows do not look like confirmed working positions.

On the day page, the large heading should be the track/location first, with the user's vehicle beside it when known. The role/position belongs on the subtitle line with race type and hours.

Hide `Out of Region` from the normal on-track crew list. It is noise for race-day crew display.

When a generic context row is merged into a real shift, generic labels such as `Shift`, `Manager`, `Northern`, or contractor context should not appear in the visible position chain if a real position is available.

Roster notes sometimes shorten names. Prefer the Deputy schedule name for display, such as `Jayden-lee`, but allow unambiguous note aliases such as `Jayden` to fill missing vehicle allocations. If the same alias could match more than one crew member on the day, do not guess.

When parsing vehicle allocations from roster notes, three-digit tokens such as `684` and `685` are vehicles. Four-digit tokens such as `0845` and `0900` are clock times and must not be shown as vehicles.

Roster notes can put the clock before the timing label, for example `0845 Trucks`, `0900 Clow Place`, or `0930 On track`. These are timing rows, not vehicle rows, even if extra people or vehicle allocations appear later in the same line.

Crew changed badges should only mean the person/position/open-slot assignment changed. Do not badge timing-only crew schedule changes.

Crew change text should be position-centred but person-focused, for example `Side 1: Nate -> Leger`. When Deputy reports a chain of position moves, use those moves to reconstruct who previously occupied each current position instead of showing only `Position: Head On -> Side 1`.

Persist assignment changes separately from the current schedule row. A later sync must not erase the previous person's name from `old person -> new person` history.

Authoritative crew captures are compared as complete event snapshots by date, Deputy location, and overlapping production window. Use employee ID first, then the canonical crew directory or a unique normalized name. This comparison must preserve connected moves, replacements, open/filled positions, and Sound/VT merge or split changes even when Deputy recreates rows with different shift IDs. Repeating the same effective snapshot must not create another history event.

Generic schedule labels such as `Vehicle` or `Vehicles` are context, not vehicle names. If a specific allocation such as `684`, `Rav91`, or `OB` is known, show only that specific vehicle.

Opening a changed badge or the Change History section must not automatically clear the changed flag. The app should only clear change flags through explicit clear actions, otherwise phone taps can make important changes disappear before the user has read them.

Deputy schedule rows can leave stale local rows behind when an assignment is replaced with a new Deputy shift id. For display, overlapping rows for the same date/location/position should prefer the latest captured assignment and suppress the older one. If the older row carried an assignment-change flag, carry that change summary onto the displayed replacement row.

The same stale-row problem can leave one person with two overlapping production roles. If those roles came from different captures, show only the newer captured role. If both roles came from the same capture, preserve both because Deputy is explicitly reporting both assignments.

A successful complete Deputy schedule-search window is authoritative for that window. Rows that were previously saved in the same date/location scope but are absent from the latest complete result should be removed. Failed, partial, or ordinary page captures must never prune saved crew rows. This is how a removed roster assignment, such as someone no longer working that day, disappears without guessing from roles or notes.

Completeness is judged per upcoming date and Deputy location, not only by HTTP success. Compare known production areas, the previous populated event, and registered users' personal roster assignments. Missing production evidence requires an exact-date selected-location retry using `locationIds`, never `areaIds`. If evidence is still absent, retain the previous rows, mark the event partial, and show any safe personal evidence.

A registered user's personal Deputy roster is valid evidence for that person's assignment. Named shared assignments take precedence, matching personal evidence is merged, confirmed personal evidence replaces only a TBC/open placeholder, and TBC remains last. If shared and personal sources name different people for one position, show a compact conflict and retain both sources. Match by Deputy employee ID first; never merge people on a common first name alone.

One complete own-roster capture that omits a future shift marks it possibly missing. A second independent complete omission may retire it. Failed, partial, empty, or truncated captures do not count. Reappearance resets the count, while an explicit Deputy cancellation applies immediately. Local user notes and timing marks survive every state.

Completed events lock after the latest known finish plus six hours in Pacific/Auckland, or early the following morning when no finish is known. A locked day keeps its personal shifts, people, vehicles, notes, calculations, and history. Later blank fields may be enriched, but missing rows cannot prune it and conflicting nonblank data becomes a historical discrepancy rather than rewriting the snapshot. Locking clears active alert badges, not stored history.

Visible changes are operational facts: substantive track, role, start, finish, assignment, cancellation, existing-note, or confirmed vehicle changes. Initial enrichment, Web-to-real context, spelling/presentation normalization, parser reinterpretation, and duplicate derived-hours changes remain technical audit only.

If Deputy has known schedule areas for the user's race-day location but no current employee row for one of the normal production positions, show a muted `TBC` placeholder row. This makes likely contractor/unknown slots visible without pretending the person is known. Keep this inferred from Deputy's area list for now; do not add a heavy manual region/default-position UI yet.

`RTS` and `FM` are assigned-only positions. Show either position when Deputy supplies a person, but do not create a `TBC` placeholder merely because the area exists. Their presence varies with the production and they commonly appear together, but the app must not infer one from the other.

## Overnight Travel

Overnight and multi-day travel uses two directed legs: start origin to track, then track to finish destination.

- Travel day may show `Travel then Overnighter`.
- An explicit `Travel then Overnighter` shift immediately before a race day may teach the one-way `Office / Clow Place` to track travel time from that shift's duration.
- The travel day and next race day usually involve the same crew.
- If the travel day has no useful crew list, it can borrow the next day's crew list and label it as next-day crew.
- Hotel-to-track travel must remain attached to a named hotel/base. Do not infer it until the actual stay is known, because crews may be split across several hotels or a nearby town.
- Beachfront Motel to Ruakaka is a known 30-minute outbound fallback unless an admin route overrides it. It must not be reused for a Ruakaka-to-office return.
- Resolve each leg from day-specific roster-builder selection, a published user hotel, a parsed accommodation line, adjacent overnight context, then saved directed routes. Stop rather than guessing when no safe route exists.
- A hotel origin does not imply the same hotel is the finish. The usual last day of a trip returns to `Office / Clow Place`; a following out-of-region day may instead resolve to the next published hotel.
- Missing return travel leaves the last-race, clearance, and pack-up maths visible with `Return travel not configured`; it does not invent a finish or calculated total.
- Common sequences such as `Office -> Track A -> Hotel` then `Hotel -> Track B -> Office` are data-driven and must not be hard-coded to Ruakaka.
- If the roster note's on-track time is earlier than Deputy's shift start, retain and show both values. Timing maths follows the explicit on-track note and accommodation travel time, while Deputy remains the source for rostered hours; flag the discrepancy for review.
- Manually planned travel days may assign different crew members to different named hotels. Hotel assignments are user-specific and should remain collapsible in the admin builder because they are occasional rather than part of every race day.

## Shared Calendar

The personal calendar remains the default. The global crew calendar groups shared Deputy schedule rows by date and location and shows the overall time window for maintenance planning. It must not expose private notes, personal change flags, open shifts, or timesheet markers. Do not present the captured employee count as complete because some contractor assignments are not exposed by Deputy's web schedule.

## Public Race-Day Planning

Love Racing data is public planning information only. Use it to show future race days at known worked locations, but do not treat it as a roster source.

- Only location/date are useful.
- Deputy rostered shifts and Deputy schedule rows are always higher priority.
- Planning markers should look different from confirmed shifts and should use Love Racing gold plus the location colour.
- Planning markers open the app's day view. Show only the saved date, location, club, and Love Racing calendar source; do not link out or infer roster details.
- If a Deputy shift already exists for the same user/date/location, hide the public planning marker for that date/location.
- Admins may ignore public planning locations that do not concern this crew. This affects only Love Racing planning hints; Deputy shifts and shared crew data remain unchanged.

## Track Maps

For race days, the app may show a cached or admin-uploaded 2D course map at the bottom of the day view.

- Only cache maps for racecourses already seen in roster data.
- Do not attach the Cambridge Thoroughbred map to Cambridge Harness or Greyhound meetings.
- Cambridge Thoroughbred trial labels use the Cambridge Synthetic image venue. Other trial labels use the ordinary physical venue whenever that course is already known to the map catalogue, including Avondale, Pukekohe, Rotorua, Taupo, Te Rapa, and Waipa.
- Office/base, vehicle, training, travel, abandoned, contractor-context, and out-of-region labels are not image venues. This classification affects maps only and never renames historical shifts.
- Alexandra Park, Manukau, Cambridge Harness, and Cambridge Greyhound remain valid manual-image venues even without a Love Racing automatic image.
- Unknown crew locations remain visible to an admin for venue, alias, or exclusion classification rather than being guessed.
- A canonical manual upload wins. Alias uploads may be adopted only when the canonical venue has no manual image; conflicting files must remain recoverable.
- Serve cached maps from the app; the day view should not link users out to Love Racing.
- Refresh map files no more than about monthly unless an admin/developer explicitly forces a refresh.
- If a course cannot be matched confidently or the image fetch fails, show no map rather than guessing.
- Prefer the highest-resolution validated official candidate from the course page and catalog. Keep a working cached map when a replacement fails or is lower quality.
- Render at or below natural width with preserved aspect ratio. Do not upscale a small original and call it an improvement.

## Public Holidays

- A holiday marker belongs to the date heading, not to each shift.
- National New Zealand public holidays and observed days are calculated locally. Ordinary weekends are not holidays.
- The marker may expose more than one holiday name on a date and must work by mouse, touch, and keyboard.
- Holiday display does not alter rostered hours, calculated hours, or holiday-pay rules.
- Keep the small star in reserved normal-flow date-heading space. Its touch target may be larger than the glyph, but it must not overlap the date number, weekday, shifts, or neighbouring cells at narrow phone widths.
