# The schedule API

Everything the app shows about upkeep is in this API: what is due, when it was last done, and the household's own
schedule beside the manual's. Home Assistant, the MCP server and the calendar feed use the same routes. Every route is
under `/api/v1` and answers JSON. An error is `{"error": "...", "detail": "..."}`.

## A token

On the server, make a token for your script and keep it in a header file only you can read:

```sh
umask 077
uv run python -m app.schedule.invites token <household_id> --kind agent --label "my script" \
  | sed 's/^/Authorization: Bearer /' > fixit.auth
export FIXIT=https://fixit.example.ts.net/api/v1
```

`curl -H @fixit.auth` reads the header from that file, so the token never shows up in your shell history or in the
process list. Add `--read-only` for a token that can read but not change anything. `invites list` shows the
household ids. A token's changes show in the app as made by its label, and can be undone there. Revoke one from the
household (`DELETE /household/tokens/<id>`) and it stops working at once.

## What is due

```sh
curl -s -H @fixit.auth "$FIXIT/due"
curl -s -H @fixit.auth "$FIXIT/due?within_days=14"
```

One item per upkeep job:

```json
{
  "appliance_id": "a_washer", "job_id": "clean_debris_filter", "title": "Clean the debris filter",
  "due_name": "debris filter",
  "due_on": "2027-01-01", "status": "upcoming", "window_end": null, "snoozed_until": null,
  "last_done": {"date": "2026-10-01", "source": "user"},
  "manual": {"every": 3, "unit": "month", "page": 3, "quote": "Clean the debris filter every 3 months."},
  "schedule": null, "interval_quote": "from your manual, p. 3", "page": 3
}
```

`due_name` is the job in a few words, as its reminder names it. `status` is `upcoming`, `due`, `overdue`, or `none`
while nobody has said when it was last done. `manual` is what the manual says, with its page and its own words;
`schedule` is the household's own choice, if it made one.

Some upkeep jobs have no interval in the manual (bleeding radiators, say). Such a job is listed only once a person in
the app has set a schedule for it. Its item then has `"manual": null`, and `interval_quote`, `quote` and `page` are
null too: the date is the household's choice alone, and nothing cites the manual for it.

The examples below use one job:

```sh
JOB="$FIXIT/appliances/a_washer/jobs/clean_debris_filter"
```

## Mark it done

```sh
curl -s -H @fixit.auth -X POST "$JOB/done"
curl -s -H @fixit.auth -X POST "$JOB/done" -H 'content-type: application/json' -d '{"date": "2026-10-20"}'
```

With no date it is done today. A date can be in the past, never in the future. The answer is the job's new item.

## Snooze it

```sh
curl -s -H @fixit.auth -X PUT "$JOB/snooze" -H 'content-type: application/json' -d '{"until": "2027-02-15"}'
curl -s -H @fixit.auth -X PUT "$JOB/snooze" -H 'content-type: application/json' -d '{"until": null}'
```

A snooze only ever moves a date later: `until` must be after today and after the date the job is due now. `null`
clears it. Marking the job done clears it too.

## Your own schedule

```sh
curl -s -H @fixit.auth -X PUT "$JOB/schedule" -H 'content-type: application/json' \
  -d '{"kind": "window", "min": 3, "max": 6, "unit": "month"}'
curl -s -H @fixit.auth -X PUT "$JOB/schedule" -H 'content-type: application/json' \
  -d '{"kind": "every", "every": 4, "unit": "month"}'
curl -s -H @fixit.auth -X PUT "$JOB/schedule" -H 'content-type: application/json' -d '{"kind": "manual"}'
```

`every` is a fixed interval; `window` is due from `min` and overdue after `max`; `manual` goes back to the manual's
interval. The manual's value stays in `manual`.

When the manual gives no interval for a job, only a person in the app can set or clear its schedule. A script's or
Home Assistant's token gets `403` with `"error": "person_only"`, because no automation picks an interval the manual
does not give. Setting `manual` on such a job answers `204`: there is no manual interval to go back to, so the job
leaves `/due`. A fault fix has no schedule at all (`422`, `"error": "fix_has_no_schedule"`).

## Who changed what, and undo

```sh
curl -s -H @fixit.auth "$JOB/changes"
curl -s -H @fixit.auth -X POST "$JOB/undo"
```

Each change has who made it (the app, a person, Home Assistant or an agent's label) and when. Undo reverts the latest
change that is not already undone. Both work for a job that is off `/due` too. If an undo takes away the only
schedule of a job with no manual interval, it answers `204`; if it brings that schedule back, it answers with the
job's item. With `{"own": true}` it undoes only a change made with the same token (by its id, not its label), and
otherwise answers `409` `not_own` with nothing changed; the household's own token always gets `not_own`. The MCP
server's `undo_my_change` always sends it, so an agent never undoes a person's change.

## A calendar

```sh
curl -s -H @fixit.auth "$FIXIT/household/feeds"
```

The answer's `ics_url` is a calendar: subscribe to it in any calendar app. With a script's token, as here, it is the
whole home's. In the app, each person gets their own, which holds what their alerts follow (below). Each job is an
all-day event from its due date, spanning a window when you chose one, with an alert and a to-do. Each entry links to
the job in the app, where you mark it done or snooze it. The link opens the job and changes nothing. The calendar
holds no token: it has its own address, which you can treat like a password.

## Each person's alerts

Each person sets their own alerts in the app on their phone, a view-only member too (`GET` and `PUT /me/alerts`, a
person's own token only). A job assigned to someone alerts only them. An unassigned job alerts everyone who has
every item on, or who follows its appliance. With push off, nothing is pushed to that person. With the calendar off,
their calendar stays subscribed but empty. Quiet hours are in the home's time and may run over midnight. A reminder
that comes due in them is pushed when they end. Each person is pushed a reminder once for its due date, and once
more after a snooze runs out. A browser's push subscription belongs to the person who saved it, so it follows their
alerts.
