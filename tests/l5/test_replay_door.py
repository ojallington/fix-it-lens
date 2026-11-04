"""deploy/replay_door.py opens a door only after checking the host is replays-only; the public one is a Cloudflare
tunnel it opens at golive and closes at rollback, against a fake Cloudflare API here."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from deploy import replay_door
from tests.l5.test_replay_host import clock as clock  # noqa: F401  # the fixtures
from tests.l5.test_replay_host import host as host  # noqa: F401

TID = "11111111-2222-3333-4444-555555555555"


def test_the_replay_host_passes_the_check(host: TestClient) -> None:  # noqa: F811
    assert replay_door.check_host("http://testserver", http=host) == []


def test_the_full_app_fails_the_check() -> None:
    def full(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<!doctype html>")  # the production app answers / and every route
    problems = replay_door.check_host("http://x", http=httpx.Client(transport=httpx.MockTransport(full)))
    assert problems and "not the replay host" in problems[0]


def test_no_host_no_door() -> None:
    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")
    problems = replay_door.check_host("http://x", http=httpx.Client(transport=httpx.MockTransport(down)))
    assert "no answer" in problems[0]


class FakeCF:
    """Just enough of the Cloudflare API: one zone, tunnels, a tunnel's configuration and token, DNS records."""

    def __init__(self, tunnel: bool = False, records: list[dict] | None = None) -> None:
        self.tunnels = [{"id": TID, "name": "fixit-replay", "status": "inactive"}] if tunnel else []
        self.records = records or []
        self.config: dict | None = None
        self.writes: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        p, m = req.url.path.removeprefix("/client/v4"), req.method
        if m != "GET":
            self.writes.append(f"{m} {p}")
        if p == "/zones":
            return self.ok([{"id": "z1", "account": {"id": "a1"}}])
        if p == "/accounts/a1/cfd_tunnel" and m == "GET":
            return self.ok(self.tunnels)
        if p == "/accounts/a1/cfd_tunnel" and m == "POST":
            self.tunnels = [{"id": TID, "name": json.loads(req.content)["name"], "status": "inactive"}]
            return self.ok(self.tunnels[0])
        if p == f"/accounts/a1/cfd_tunnel/{TID}/configurations":
            self.config = json.loads(req.content)["config"]
            return self.ok({})
        if p == f"/accounts/a1/cfd_tunnel/{TID}/token":
            return self.ok("run-token-secret")
        if p == "/zones/z1/dns_records" and m == "GET":
            return self.ok(self.records)
        if p == "/zones/z1/dns_records" and m == "POST":
            self.records.append({"id": "r1", **json.loads(req.content)})
            return self.ok(self.records[-1])
        if p.startswith("/zones/z1/dns_records/") and m == "DELETE":
            self.records = [r for r in self.records if r["id"] != p.rsplit("/", 1)[1]]
            return self.ok({})
        return httpx.Response(404, json={"success": False, "errors": [{"message": "no route"}]})

    @staticmethod
    def ok(result: object) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "result": result})

    def client(self) -> replay_door.Cloudflare:
        return replay_door.Cloudflare(httpx.Client(base_url=replay_door.CF_API, transport=httpx.MockTransport(self)))


@pytest.fixture
def token_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "fixit-tunnel.token"
    monkeypatch.setattr(replay_door, "TUNNEL_TOKEN", path)
    monkeypatch.setattr(replay_door.shutil, "which", lambda _: "/usr/bin/cloudflared")
    return path


def test_golive_needs_the_host_check_first(token_file: Path) -> None:
    fake, ran = FakeCF(), []
    assert replay_door.run("golive", runner=lambda a: ran.append(a) or 0, check=lambda: ["/docs answered 200"],
                           cf=fake.client) == 1
    assert ran == [] and fake.writes == [] and not token_file.exists()


def test_dry_run_changes_nothing(token_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fake, ran = FakeCF(), []
    assert replay_door.run("golive", dry_run=True, runner=lambda a: ran.append(a) or 0, check=lambda: [],
                           cf=fake.client) == 0
    assert fake.writes == [] and ran == [] and not token_file.exists()
    assert "would route watch.example.org" in capsys.readouterr().out


def test_golive_routes_only_the_replay_host_then_publishes(token_file: Path) -> None:
    fake, ran = FakeCF(), []
    assert replay_door.run("golive", runner=lambda a: ran.append(list(a)) or 0, check=lambda: [], cf=fake.client) == 0
    assert fake.config == {"ingress": [{"hostname": "watch.example.org", "service": "http://127.0.0.1:8791"},
                                       {"service": "http_status:404"}]}
    assert token_file.read_text() == "run-token-secret" and token_file.stat().st_mode & 0o777 == 0o600
    start = next(a for a in ran if a[0] == "systemd-run")
    assert start[-2:] == ["--token-file", str(token_file)] and "run-token-secret" not in " ".join(start)
    assert fake.records == [{"id": "r1", "type": "CNAME", "name": "watch.example.org",
                             "content": f"{TID}.cfargotunnel.com", "proxied": True,
                             "comment": "Fix-It Lens replay host (deploy/replay_door.py)"}]
    assert fake.writes[-1] == "POST /zones/z1/dns_records"  # DNS last: nothing points at a tunnel that is not up


def test_no_dns_when_cloudflared_fails(token_file: Path) -> None:
    fake = FakeCF()
    assert replay_door.run("golive", runner=lambda a: 1 if a[0] == "systemd-run" else 0, check=lambda: [],
                           cf=fake.client) == 1
    assert fake.records == []


def test_a_record_someone_else_made_stops_golive(token_file: Path) -> None:
    fake = FakeCF(tunnel=True, records=[{"id": "x", "type": "A", "content": "203.0.113.7"}])
    assert replay_door.run("golive", runner=lambda a: 0, check=lambda: [], cf=fake.client) == 1
    assert fake.writes == [] and not token_file.exists()


def test_rollback_removes_only_its_record_and_stops_the_tunnel(token_file: Path) -> None:
    other = {"id": "x", "type": "TXT", "content": "keep me"}
    fake = FakeCF(tunnel=True, records=[{"id": "r1", "type": "CNAME", "content": f"{TID}.cfargotunnel.com"}, other])
    ran: list[Sequence[str]] = []
    assert replay_door.run("rollback", runner=lambda a: ran.append(list(a)) or 0, cf=fake.client) == 0
    assert fake.records == [other]
    assert ["systemctl", "--user", "stop", "fixit-tunnel"] in ran
    assert not any("tailscale" in a for a in ran)  # the tailnet route stays


def test_rehearse_is_tailnet_only() -> None:
    assert replay_door.rehearse_commands() == [["tailscale", "serve", "--bg", "--https=10000",
                                                "http://127.0.0.1:8791"]]


def test_no_cloudflare_token_is_said_plainly(tmp_path: Path) -> None:
    with pytest.raises(replay_door.CloudflareError, match="no Cloudflare API token"):
        replay_door.Cloudflare(token_file=tmp_path / "missing")
