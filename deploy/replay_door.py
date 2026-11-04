"""The replay host's doors: the tailnet for rehearsal, a Cloudflare tunnel at watch.<zone> for the public one.

    uv run python deploy/replay_door.py rehearse   # tailnet only: tailscale serve --https=10000 -> the replay host
    uv run python deploy/replay_door.py golive     # checks the host, then opens https://watch.<zone>
    uv run python deploy/replay_door.py rollback   # stops the tunnel and removes the DNS record; the tailnet stays
    uv run python deploy/replay_door.py status
    (any of them with --dry-run looks, prints what it would change, and changes nothing)

Before opening either door it checks that the host on 127.0.0.1:8791 is the replay host and only that: / redirects to
a replay, the replay plays, and the household, due, preflight and websocket routes are refused.

golive: the tunnel `fixit-replay` (remotely managed, made once) routes watch.<zone> to 127.0.0.1:8791 and
nothing else (everything else is 404 at the edge); its run token is written to TUNNEL_TOKEN (0600) and cloudflared
runs it as the user unit `fixit-tunnel` with --token-file; then the proxied CNAME watch -> <tunnel>.cfargotunnel.com
is made. rollback deletes that CNAME (only when it points at this tunnel) and stops the unit. The Cloudflare API
token is read by path (CF_TOKEN), never put in argv or printed. The zone is FIXIT_DOOR_ZONE, your own Cloudflare zone.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

import httpx

PORT = 10000
LOCAL = "http://127.0.0.1:8791"
MUST_REFUSE = ("/api/v1/household", "/api/v1/due", "/api/v1/preflight", "/preflight", "/docs", "/openapi.json")
ZONE = os.environ.get("FIXIT_DOOR_ZONE", "example.org")
HOSTNAME = "watch." + ZONE
TUNNEL = "fixit-replay"
UNIT = "fixit-tunnel"
METRICS = "127.0.0.1:20245"  # cloudflared's own metrics listener, loopback
CF_TOKEN = Path(os.environ.get("FIXIT_CF_TOKEN_FILE", Path.home() / ".config/fleet/cloudflare.token"))
TUNNEL_TOKEN = Path(os.environ.get("FIXIT_TUNNEL_TOKEN_FILE", Path.home() / ".config/fleet/fixit-tunnel.token"))
CF_API = "https://api.cloudflare.com/client/v4"

Run = Callable[[Sequence[str]], int]


def check_host(base: str = LOCAL, http: httpx.Client | None = None) -> list[str]:
    """What is wrong with the host behind the door; [] when it is the replay host and nothing else."""
    h = http or httpx.Client(timeout=5.0)
    try:
        r = h.get(base + "/", follow_redirects=False)
    except httpx.HTTPError as e:
        return [f"{base}: no answer ({type(e).__name__}); start deploy/serve.py --replay-only first"]
    loc = r.headers.get("location", "")
    if r.status_code != 302 or "?replay=" not in loc:
        return [f"/ answered {r.status_code}, not a redirect to a replay: this is not the replay host"]
    problems = []
    rid = loc.split("?replay=", 1)[1]
    if h.get(f"{base}/api/v1/replay/{rid}").status_code != 200:
        problems.append(f"the default replay {rid} does not play")
    for path in MUST_REFUSE:
        if (code := h.get(base + path).status_code) != 404:
            problems.append(f"{path} answered {code}, not 404")
    ws = h.get(base + "/ws", headers={"connection": "Upgrade", "upgrade": "websocket", "sec-websocket-version": "13",
                                      "sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ=="})
    if ws.status_code == 101:
        problems.append("/ws accepted a websocket")
    if h.get(base + "/").headers.get("x-robots-tag") != "noindex":
        problems.append("no noindex header")
    return problems


class CloudflareError(RuntimeError):
    pass


class Cloudflare:
    """The few Cloudflare API calls the public door needs. The token is read from its file and sent as a header."""

    def __init__(self, http: httpx.Client | None = None, token_file: Path = CF_TOKEN) -> None:
        if http is None:
            if not token_file.is_file():
                raise CloudflareError(f"no Cloudflare API token at {token_file}")
            http = httpx.Client(base_url=CF_API, timeout=20.0,
                                headers={"authorization": "Bearer " + token_file.read_text().strip()})
        self.http = http

    def _call(self, method: str, path: str, **kw: Any) -> Any:
        r = self.http.request(method, path, **kw)
        body = r.json() if r.content else {}
        if r.status_code >= 400 or not body.get("success", True):
            errs = "; ".join(e.get("message", "") for e in body.get("errors", [])) or r.reason_phrase
            raise CloudflareError(f"{method} {path.split('?')[0]}: {r.status_code} {errs}")
        return body.get("result")

    def zone(self, name: str = ZONE) -> tuple[str, str]:
        found = self._call("GET", "/zones", params={"name": name})
        if not found:
            raise CloudflareError(f"{name}: not a zone this token can see")
        return found[0]["id"], found[0]["account"]["id"]

    def tunnel(self, account: str, name: str = TUNNEL) -> dict[str, Any] | None:
        found = self._call("GET", f"/accounts/{account}/cfd_tunnel", params={"name": name, "is_deleted": "false"})
        return found[0] if found else None

    def make_tunnel(self, account: str, name: str = TUNNEL) -> dict[str, Any]:
        return self._call("POST", f"/accounts/{account}/cfd_tunnel", json={"name": name, "config_src": "cloudflare"})

    def route(self, account: str, tunnel_id: str) -> None:
        ingress = [{"hostname": HOSTNAME, "service": LOCAL}, {"service": "http_status:404"}]
        self._call("PUT", f"/accounts/{account}/cfd_tunnel/{tunnel_id}/configurations",
                   json={"config": {"ingress": ingress}})

    def run_token(self, account: str, tunnel_id: str) -> str:
        return str(self._call("GET", f"/accounts/{account}/cfd_tunnel/{tunnel_id}/token"))

    def records(self, zone: str) -> list[dict[str, Any]]:
        return self._call("GET", f"/zones/{zone}/dns_records", params={"name": HOSTNAME})

    def make_record(self, zone: str, tunnel_id: str) -> dict[str, Any]:
        return self._call("POST", f"/zones/{zone}/dns_records",
                          json={"type": "CNAME", "name": HOSTNAME, "content": f"{tunnel_id}.cfargotunnel.com",
                                "proxied": True, "comment": "Fix-It Lens replay host (deploy/replay_door.py)"})

    def drop_record(self, zone: str, record_id: str) -> None:
        self._call("DELETE", f"/zones/{zone}/dns_records/{record_id}")


def systemctl(*args: str) -> list[str]:
    return ["systemctl", "--user", *args]


def unit_command(cloudflared: str = "cloudflared") -> list[str]:
    return ["systemd-run", "--user", f"--unit={UNIT}", "--property=Restart=on-failure", "--collect",
            cloudflared, "tunnel", "--no-autoupdate", "--metrics", METRICS, "run", "--token-file", str(TUNNEL_TOKEN)]


def _write_secret(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    path.chmod(0o600)


def golive(cf: Cloudflare, *, dry_run: bool, runner: Run, say: Callable[[str], None] = print) -> int:
    if shutil.which("cloudflared") is None:
        say("cloudflared is not installed: sudo pacman -S cloudflared")
        if not dry_run:
            return 1
    zone, account = cf.zone()
    t = cf.tunnel(account)
    say(f"tunnel {TUNNEL}: " + (f"{t['id']} ({t.get('status')})" if t else "none yet; would make it"))
    recs = cf.records(zone)
    foreign = [r for r in recs if not (t and r["type"] == "CNAME" and r["content"] == f"{t['id']}.cfargotunnel.com")]
    if foreign:
        say(f"{HOSTNAME} already has records not made by this door ({', '.join(r['type'] for r in foreign)}): "
            "stopping; nothing changed")
        return 1
    if dry_run:
        say(f"would route {HOSTNAME} -> {LOCAL} (else 404), write the run token to {TUNNEL_TOKEN}, start the user unit "
            f"{UNIT}, then make CNAME {HOSTNAME} -> <tunnel>.cfargotunnel.com (proxied)")
        return 0
    t = t or cf.make_tunnel(account)
    cf.route(account, t["id"])
    _write_secret(TUNNEL_TOKEN, cf.run_token(account, t["id"]))
    say(f"routed {HOSTNAME} -> {LOCAL}; run token at {TUNNEL_TOKEN}")
    runner(systemctl("stop", UNIT))  # a previous run, if any
    if (code := runner(unit_command())) != 0:
        say(f"cloudflared did not start ({code}); nothing public yet")
        return code
    if not recs:
        cf.make_record(zone, t["id"])
    say(f"live: https://{HOSTNAME}/  (rollback: deploy/replay_door.py rollback)")
    return 0


def rollback(cf: Cloudflare, *, dry_run: bool, runner: Run, say: Callable[[str], None] = print) -> int:
    zone, account = cf.zone()
    t = cf.tunnel(account)
    mine = [r for r in cf.records(zone) if t and r["content"] == f"{t['id']}.cfargotunnel.com"]
    for r in mine:
        say(f"{'would delete' if dry_run else 'deleting'} {r['type']} {HOSTNAME} -> {r['content']}")
        if not dry_run:
            cf.drop_record(zone, r["id"])
    if not mine:
        say(f"no record for {HOSTNAME} from this door")
    say(f"{'would stop' if dry_run else 'stopping'} {UNIT}")
    if not dry_run:
        runner(systemctl("stop", UNIT))
    say(f"tailnet route on :{PORT} unchanged")
    return 0


def status(cf: Cloudflare, *, runner: Run, say: Callable[[str], None] = print) -> int:
    zone, account = cf.zone()
    t = cf.tunnel(account)
    recs = cf.records(zone)
    say(f"tunnel {TUNNEL}: " + (f"{t['id']} ({t.get('status')})" if t else "not made"))
    say(f"{HOSTNAME}: " + (", ".join(f"{r['type']} -> {r['content']}" for r in recs) or "no record (not public)"))
    runner(systemctl("is-active", UNIT))
    runner(["tailscale", "serve", "status"])
    return 0


def rehearse_commands() -> list[list[str]]:
    return [["tailscale", "serve", "--bg", f"--https={PORT}", LOCAL]]


def run(action: str, *, dry_run: bool = False, runner: Run | None = None,
        check: Callable[[], list[str]] = check_host, cf: Callable[[], Cloudflare] = Cloudflare) -> int:
    do = runner or (lambda argv: subprocess.run(list(argv), check=False).returncode)
    if action in ("rehearse", "golive"):
        problems = check()
        if problems:
            print("not opening the door:\n  " + "\n  ".join(problems), file=sys.stderr)
            return 1
        print("host checked: replays only")
    try:
        if action == "golive":
            return golive(cf(), dry_run=dry_run, runner=do)
        if action == "rollback":
            return rollback(cf(), dry_run=dry_run, runner=do)
        if action == "status":
            return status(cf(), runner=do)
    except CloudflareError as e:
        print(f"Cloudflare: {e}", file=sys.stderr)
        return 1
    for argv in rehearse_commands():
        print("$ " + " ".join(argv))
        if not dry_run and (code := do(argv)) != 0:
            print(f"{argv[0]} exited {code}", file=sys.stderr)
            return code
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["rehearse", "golive", "rollback", "status"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cast(Any, sys.stdout).reconfigure(line_buffering=True)  # our lines and the subprocesses' stay in order
    sys.exit(run(args.action, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
