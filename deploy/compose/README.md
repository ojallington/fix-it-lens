# Fix-It Lens with `docker compose up`

The app on your own machine, with HTTPS your phone accepts and a QR code to join. Replay mode by default: no key,
nothing spent.

Just looking? `docker compose up demo`, then open http://127.0.0.1:8080 on this machine. It plays the recordings
in `./demo/replays` and nothing else: no Tailscale, no camera, no key, no account, nothing written. It is published
on 127.0.0.1 only. The same mode runs without Docker: `make demo`.

1. **A Tailscale auth key.** The sidecar puts the app on your tailnet as `https://fixit.<tailnet>.ts.net`, with a
   real certificate (the camera and the service worker need HTTPS; a self-signed certificate breaks the service
   worker). In the Tailscale admin console, turn on MagicDNS and HTTPS certificates, make an auth key, and save it
   by path: `mkdir -p private && (umask 077; cat > private/ts-authkey)` (or point `TS_AUTHKEY_FILE` at it).
2. **`docker compose up`.** The first start prints the address, a QR code and one invite for this home. Scan the QR
   with your phone's camera, join, and add the app to your home screen. The phone must be on your tailnet.
3. **More homes or phones:** `docker compose exec app python -m app.schedule.invites new 1 --link https://fixit.<tailnet>.ts.net`.
4. **Check it:** `https://fixit.<tailnet>.ts.net/preflight` on the phone.

What it opens: no port on the host, and Funnel is off; only your tailnet reaches the app. The app binds 127.0.0.1
inside the sidecar's network namespace, read-only, with every capability dropped. Your data (the store, sessions,
push's key) is the `fixit-data` volume; `docker compose down -v` deletes it.

**Live mode** (your own Meta model key, billed to you): set `key_path` in
`config/models.toml` to a path of your own under `/data`, mount the key read-only there, add `budgets.toml` to `/data`,
and start with `FIXIT_CALLS=live docker compose up`. Every call then needs a joined household's token and stays
inside its daily cap and the ledger's caps.

**Test without a tailnet:** `docker compose -f compose.yaml -f deploy/compose/stub.yaml up -d` stubs the sidecar
(no HTTPS, no auth key), then `docker compose -f compose.yaml -f deploy/compose/stub.yaml exec app python
deploy/compose/e2e.py` runs the end-to-end check from inside. `down -v` after: the check writes a test household.
