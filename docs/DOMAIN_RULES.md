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

Opening a changed badge or the Change History section must not automatically clear the changed flag. The app should only clear change flags through explicit clear actions, otherwise phone taps can make important changes disappear before the user has read them.

Deputy schedule rows can leave stale local rows behind when an assignment is replaced with a new Deputy shift id. For display, overlapping rows for the same date/location/position should prefer the latest captured assignment and suppress the older one. If the older row carried an assignment-change flag, carry that change summary onto the displayed replacement row.

The same stale-row problem can leave one person with two overlapping production roles. If those roles came from different captures, show only the newer captured role. If both roles came from the same capture, preserve both because Deputy is explicitly reporting both assignments.

If Deputy has known schedule areas for the user's race-day location but no current employee row for one of the normal production positions, show a muted `TBC` placeholder row. This makes likely contractor/unknown slots visible without pretending the person is known. Keep this inferred from Deputy's area list for now; do not add a heavy manual region/default-position UI yet.

## Overnight Travel

Known but not fully solved. For now:

- Travel day may show `Travel then Overnighter`.
- An explicit `Travel then Overnighter` shift immediately before a race day may teach the one-way `Office / Clow Place` to track travel time from that shift's duration.
- The travel day and next race day usually involve the same crew.
- If the travel day has no useful crew list, it can borrow the next day's crew list and label it as next-day crew.
- Hotel-to-track travel must remain attached to a named hotel/base. Do not infer it until the actual stay is known, because crews may be split across several hotels or a nearby town.

## Public Race-Day Planning

Love Racing data is public planning information only. Use it to show future race days at known worked locations, but do not treat it as a roster source.

- Only location/date are useful.
- Deputy rostered shifts and Deputy schedule rows are always higher priority.
- Planning markers should look different from confirmed shifts and should use Love Racing gold plus the location colour.
- Planning markers open the app's day view. Show only the saved date, location, club, and Love Racing calendar source; do not link out or infer roster details.
- If a Deputy shift already exists for the same user/date/location, hide the public planning marker for that date/location.
- Admins may ignore public planning locations that do not concern this crew. This affects only Love Racing planning hints; Deputy shifts and shared crew data remain unchanged.
