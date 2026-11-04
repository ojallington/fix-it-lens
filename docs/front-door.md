# The front door

A household opens in one of three ways. An invite code is single-use and handed over in person. The open door
needs no code. A judges' link is multi-use, and each scan opens its own household under the link's caps. Each way
answers with the household and its token (`POST /api/v1/households` or `/households/try`, contracts `HouseholdCreated`).

## The open door

The door is shut unless an operator has opened it. Only this command opens or shuts it, on the server:

```sh
uv run python -m app.schedule.invites door                  # open or shut, since when, and who said so
uv run python -m app.schedule.invites door open --by <name>
uv run python -m app.schedule.invites door close --by <name>
```

Each run adds a row to `door_log` with who and when. The latest row wins, and with no row the door is shut. No
route, flag or setting opens it. While it is shut, `POST /households` without `invite_code` answers
`403 {"error": "door_closed"}`. While it is open, the request needs `consent` and `age_ok`. A new household is
still turned away with `429` `global_cap` when the free credits for public sessions are used up. The body is the
contract's `CapError`, with `reopens_at` when a reopening time is known.

The door also stops for the rest of the UTC day once one client address has opened 3 new homes (`address_cap`),
or once 50 new homes have opened through it in all (`door_cap`). Both answer `429` with `reopens_at` at the next
UTC midnight. The numbers are server settings (`Deps`). The count per address is kept in memory, so a restart
clears it. `GET /households/door` answers `{"state": "open" | "shut" | "capped", "reopens_at"}` without a token and
changes nothing, so the phone can offer the replay before anyone ticks consent.

## A judges' link

```sh
uv run python -m app.schedule.invites link new --judges --label "<label>" --cap-home <usd/day> --total <usd> \
  --homes <n> --expires <YYYY-MM-DD> --out <new file> --origin https://<host>
uv run python -m app.schedule.invites link list
uv run python -m app.schedule.invites link revoke <link_id>
```

Every cap is set when the link is made, and none has a default. `--cap-home` is each household's daily spend.
`--total` is all its households' spend together, for good. `--homes` is how many households it may open. The link
works to the end of `--expires`, Amsterdam time. Its URL, `<origin>/try#k=<code>`, is written once to `--out` as a
new 0600 file and never printed. Only the code's hash is stored. The code rides in the URL fragment, so it reaches
no access log.

`POST /households/try` answers `404` for an unknown, expired or revoked code, the same answer for all three. It
answers `429` with `judges_full` once the link has opened all its homes, which never reopens. It answers `judges_cap`
once the total is spent, and `global_cap` when the shared credits are out. A household from a link is a real home,
marked with the link (`invite_link_id`), which is how the pilot's counts tell it apart.

## A return link

From a phone that has the household, `POST /household/return-link` gives
`{"url": "<origin>/#return/<code>", "expires_at": ...}`. The link opens the household on one other device, once,
within 15 minutes, as the same person. A new link replaces that person's earlier one. Redeeming it
(`POST /households/return {"code"}`) issues a new app token for that person. It answers `404` for a code that is
used, expired or unknown, or whose person has left the home. Agent, Home Assistant and view-only tokens cannot make
a return link (`403`).
