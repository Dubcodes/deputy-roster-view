# Domain Rules

## Racing Codes

- `T-` means Thoroughbred racing.
- `H-` means Harness racing.
- `T-Cambridge` should display as Cambridge Synthetic when that is the track code from the user's roster.

## Important Track/Location Labels

- `Office` and `Clow Place` both mean the base/start-of-day location.
- `On track` is the arrival time at the track.
- `Ruak` / `RUAK` should display as Ruakaka.
- Vehicle maintenance days are `[VEH] Vehicles`.
- Overnight/travel codes such as `8PE` should be treated as out-of-region travel context, not a normal track.

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

Hide `Out of Region` from the normal on-track crew list. It is noise for race-day crew display.

Crew changed badges should only mean the person/position/open-slot assignment changed. Do not badge timing-only crew schedule changes.

## Overnight Travel

Known but not fully solved. For now:

- Travel day may show `Travel then Overnighter`.
- The travel day and next race day usually involve the same crew.
- If the travel day has no useful crew list, it can borrow the next day's crew list and label it as next-day crew.
- Hotel/stay location and travel timing rules need a real current example before building more logic.
