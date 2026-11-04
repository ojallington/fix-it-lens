"""The Home Assistant add-on's start and its sidebar panel (deploy/ha_addon.py): only the ingress proxy is answered,
an invite is made one at a time, and "Connect" hands a key to Home Assistant's discovery without showing it."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import fields
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.schedule import store
from deploy import ha_addon
from deploy.ha_addon import Options, Supervisor, panel, read_options
from tests.l5.conftest import add_appliance, add_household

ROOT = Path(__file__).resolve().parents[2]
PEER = "testclient"  # TestClient's own address, standing in for the Supervisor's ingress proxy
INGRESS = {"X-Ingress-Path": "/api/hassio_ingress/abc123"}
APP_URL = "http://local-fixit-lens:8790"


class FakeSupervisor:
    def __init__(self, status: int = 200) -> None:
        self.status, self.sent = status, []  # type: ignore[var-annotated]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.sent.append((request.headers.get("authorization"), json.loads(request.content)))
        return httpx.Response(self.status, json={"result": "ok" if self.status == 200 else "error"})

    def client(self, token: str | None = "sup-token") -> Supervisor:
        http = httpx.Client(base_url="http://supervisor", transport=httpx.MockTransport(self.handler))
        return Supervisor(token, http)


def ui(conn: sqlite3.Connection, sup: Supervisor, opts: Options | None = None, peer: str = PEER) -> TestClient:
    return TestClient(panel(conn, opts or Options(phone_url="https://ha.example.org:8790"), sup, APP_URL, peer=peer))


def test_only_the_ingress_proxy_is_answered(conn: sqlite3.Connection) -> None:
    c = ui(conn, FakeSupervisor().client(), peer=ha_addon.INGRESS_PEER)
    for r in (c.get("/"), c.post("/invite"), c.post("/connect", data={"household": "h_x"})):
        assert r.status_code == 403 and "sidebar" in r.text
    assert conn.execute("SELECT count(*) FROM households").fetchone()[0] == 0  # the refused invite made nothing


def test_the_page_lists_joined_homes_under_the_ingress_path(conn: sqlite3.Connection) -> None:
    hid = add_household(conn)
    add_appliance(conn, hid)
    store.create_invites(conn, 1)  # made, not joined: not a home yet
    r = ui(conn, FakeSupervisor().client()).get("/", headers=INGRESS)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"]
    assert 'action="/api/hassio_ingress/abc123/invite"' in r.text
    assert "action='/api/hassio_ingress/abc123/connect'" in r.text and r.text.count(f"value='{hid}'") == 1
    assert "<td>1</td>" in r.text and "Connect to Home Assistant" in r.text
    assert "Replay mode" in r.text and "https://ha.example.org:8790" in r.text and APP_URL in r.text


def test_an_invite_is_a_qr_for_the_phone_address(conn: sqlite3.Connection) -> None:
    r = ui(conn, FakeSupervisor().client()).post("/invite", headers=INGRESS)
    [row] = conn.execute("SELECT invite_code, consent_at FROM households").fetchall()
    code = f"{row['invite_code'][:5]}-{row['invite_code'][5:]}"
    assert row["consent_at"] is None  # waiting for the phone
    assert f"https://ha.example.org:8790/?invite={code}" in r.text and "<svg" in r.text
    again = ui(conn, FakeSupervisor().client()).get("/", headers=INGRESS)
    assert code not in again.text  # shown once


def test_without_a_phone_address_the_page_says_how(conn: sqlite3.Connection) -> None:
    c = ui(conn, FakeSupervisor().client(), Options())
    assert "No phone address yet" in c.get("/").text
    r = c.post("/invite")
    assert "<svg" not in r.text and "type this code" in r.text


def test_connect_hands_a_key_to_discovery_and_never_shows_it(conn: sqlite3.Connection,
                                                             caplog: pytest.LogCaptureFixture) -> None:
    hid = add_household(conn)
    sup = FakeSupervisor()
    with caplog.at_level(logging.INFO):
        r = ui(conn, sup.client()).post("/connect", data={"household": hid}, headers=INGRESS)
    assert r.status_code == 200 and "Devices &amp; services" in r.text
    [(auth, body)] = sup.sent
    assert auth == "Bearer sup-token" and body["service"] == "fixit_lens"
    assert body["config"]["url"] == APP_URL and set(body["config"]) == {"url", "token"}
    token = body["config"]["token"]
    caller = store.caller_from_token(conn, b"\x01" * 32, token)
    assert caller is not None and caller.household_id == hid and caller.by_kind == "ha" and caller.scope == "write"
    assert token not in r.text and token not in caplog.text
    assert "connected" in ui(conn, sup.client()).get("/").text


@pytest.mark.parametrize("sup", [FakeSupervisor(status=400).client(), FakeSupervisor().client(token=None)])
def test_a_refused_discovery_keeps_no_key(conn: sqlite3.Connection, sup: Supervisor) -> None:
    hid = add_household(conn)
    r = ui(conn, sup).post("/connect", data={"household": hid})
    assert r.status_code == 502 and "no key was kept" in r.text
    assert store.list_tokens(conn, hid) == []


def test_connect_only_a_joined_home_that_is_here(conn: sqlite3.Connection) -> None:
    hid = add_household(conn)
    conn.execute("UPDATE households SET deleted_at = '2026-10-27T12:00:00Z' WHERE id = ?", (hid,))
    [waiting] = store.create_invites(conn, 1)
    sup = FakeSupervisor()
    for h in (hid, waiting.household_id, "h_nowhere"):
        assert ui(conn, sup.client()).post("/connect", data={"household": h}).status_code == 404
    assert sup.sent == [] and conn.execute("SELECT count(*) FROM household_tokens").fetchone()[0] == 0


def _opts(tmp_path: Path, **raw: Any) -> Options:
    p = tmp_path / "options.json"
    p.write_text(json.dumps(raw))
    return read_options(p)


def test_options_default_to_replay_and_refuse_what_cannot_work(tmp_path: Path) -> None:
    assert read_options(tmp_path / "none.json") == Options()
    assert _opts(tmp_path, calls="live", phone_url="https://ha.example.org:8790/", ssl=True) == Options(
        calls="live", phone_url="https://ha.example.org:8790", ssl=True)
    assert _opts(tmp_path, phone_url="  ").phone_url is None
    for bad in ({"calls": "free"}, {"phone_url": "http://ha.local:8790"}, {"certfile": "../secrets/x.pem"},
                {"keyfile": "/etc/ssl/key.pem"}):
        with pytest.raises(SystemExit):
            _opts(tmp_path, **bad)


def test_live_mode_links_the_key_by_path(tmp_path: Path) -> None:
    home, config = tmp_path / "data", tmp_path / "config"
    config.mkdir()
    assert ha_addon.live_key(home, config) is False
    (config / ha_addon.KEY_NAME).write_text("not-a-real-key")
    assert ha_addon.live_key(home, config) and ha_addon.live_key(home, config)  # twice is fine
    dst = home / ".config/fleet" / ha_addon.KEY_NAME
    assert dst.is_symlink() and dst.resolve() == (config / ha_addon.KEY_NAME).resolve()
    assert (home / ".config/fleet").stat().st_mode & 0o777 == 0o700


def test_the_key_path_is_where_models_toml_reads_it() -> None:
    assert '"~/.config/fleet/meta-model-api.key"' in (ROOT / "config/models.toml").read_text()


def test_app_url_is_the_addon_hostname() -> None:
    assert ha_addon.app_url("a0d7b954-fixit-lens") == "http://a0d7b954-fixit-lens:8790"


def _stage() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("stage", ROOT / "integrations/homeassistant/addon/stage.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_build_folder_holds_tracked_files_only(tmp_path: Path) -> None:
    stage = _stage()
    stray = ROOT / "fixtures" / "untracked-addon-check.jsonl"  # an untracked file in a folder the image takes
    stray.write_text("{}\n")
    try:
        addon = stage.stage(tmp_path / "out")
    finally:
        stray.unlink()
    src = addon / "src"
    assert not (src / "fixtures" / stray.name).exists()
    assert (src / "deploy/ha_addon.py").is_file() and (src / "integrations/mcp_server/server.py").is_file()
    assert (addon / "config.yaml").is_file() and (tmp_path / "out/repository.yaml").is_file()
    assert sorted(str(p.relative_to(src)) for p in src.rglob("*") if p.is_file()) == stage.tracked()
    with pytest.raises(SystemExit):
        stage.stage(tmp_path / "out")  # never into a folder that has something in it


def test_one_repository_holds_the_addon_and_the_integration_at_its_root(tmp_path: Path) -> None:
    """--repo: the Supervisor reads repository.yaml and fixit_lens/, HACS reads hacs.json and custom_components/."""
    out = tmp_path / "repo"
    _stage().stage(out, repo=True)
    ha = ROOT / "integrations/homeassistant"
    assert json.loads((out / "hacs.json").read_text()) == json.loads((ha / "hacs.json").read_text())
    manifest = json.loads((out / "custom_components/fixit_lens/manifest.json").read_text())
    assert manifest["domain"] == "fixit_lens"
    assert not list((out / "custom_components").rglob("__pycache__"))
    assert (out / "repository.yaml").is_file() and (out / "fixit_lens/config.yaml").is_file()
    assert any((out / "blueprints").rglob("*.yaml")) and (out / "LICENSE").is_file()
    assert (out / "dashboards/fixit_lens.yaml").is_file() and (out / "dashboards/screenshot.png").is_file()
    assert "HACS" in (out / "README.md").read_text()
    # The store takes every config.yaml in the repository for an app: a second one would be built with no src/.
    assert [p.relative_to(out) for p in out.rglob("config.yaml")] == [Path("fixit_lens/config.yaml")]
    # Not yet a public repository: the Supervisor builds on the device, and nothing names an owner or links a button.
    assert sorted(p.name for p in (out / ".github/workflows").iterdir()) == ["validate.yaml"]
    assert "image:" not in (out / "fixit_lens/config.yaml").read_text() and manifest["codeowners"] == []
    readme = (out / "README.md").read_text()
    assert "my.home-assistant.io" not in readme and "<!--" not in readme


def test_the_public_repository_is_ready_for_the_default_lists(tmp_path: Path) -> None:
    """--github: what HACS's default list and hassfest ask of the manifest, the add-on's prebuilt image under the same
    owner, its Builder workflows, and the README's buttons."""
    import yaml

    out = tmp_path / "repo"
    _stage().stage(out, repo=True, github="Sam/fixit-lens-ha", codeowner="@sam")
    manifest = json.loads((out / "custom_components/fixit_lens/manifest.json").read_text())
    assert manifest["documentation"] == "https://github.com/Sam/fixit-lens-ha"
    assert manifest["issue_tracker"] == "https://github.com/Sam/fixit-lens-ha/issues"
    assert manifest["codeowners"] == ["@sam"]
    assert "categories" not in manifest  # the released hassfest refuses it until Home Assistant 2026.11 (README step 5)
    assert list(manifest)[:2] == ["domain", "name"] and list(manifest)[2:] == sorted(list(manifest)[2:])  # hassfest
    hacs = json.loads((out / "hacs.json").read_text())
    assert hacs["homeassistant"] >= "2026.3.0"  # the first release that reads brand/ inside the integration
    cfg = yaml.safe_load((out / "fixit_lens/config.yaml").read_text())
    assert cfg["image"] == "ghcr.io/sam/fixit-lens-app" and cfg["url"] == "https://github.com/Sam/fixit-lens-ha"
    assert yaml.safe_load((out / "repository.yaml").read_text())["url"] == cfg["url"]
    flows = {p.name: yaml.safe_load(p.read_text()) for p in (out / ".github/workflows").iterdir()}
    assert sorted(flows) == ["build-app.yaml", "builder.yaml", "validate.yaml"]
    validate = flows["validate.yaml"]["jobs"]
    assert validate["hacs"]["steps"][0]["with"] == {"category": "integration"}
    assert any(st.get("uses", "").startswith("home-assistant/actions/hassfest") for st in validate["hassfest"]["steps"])
    assert flows["builder.yaml"]["jobs"]["build-app"]["permissions"] == {"contents": "read", "id-token": "write",
                                                                          "packages": "write"}
    readme = (out / "README.md").read_text()
    assert "repository_url=https%3A%2F%2Fgithub.com%2FSam%2Ffixit-lens-ha" in readme
    assert "owner=Sam&repository=fixit-lens-ha&category=integration" in readme and "domain=fixit_lens" in readme
    assert ("redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FSam%2Ffixit-lens-ha%2Fblob%2Fmain%2F"
            "blueprints%2Fautomation%2Ffixit_lens%2Fjob_due_notify.yaml") in readme
    assert (out / "blueprints/automation/fixit_lens/job_due_notify.yaml").is_file()  # what the button imports
    assert "<!--" not in readme
    with pytest.raises(SystemExit):
        _stage().stage(tmp_path / "bad", repo=True, github="Sam/fixit-lens-ha")  # no code owner


def test_the_images_take_every_package_the_server_imports() -> None:
    """The app imports integrations/ (the MCP door), so both images copy it: compose's and the add-on's."""
    dockerfile = (ROOT / "deploy/Dockerfile").read_text()
    for top in _stage().SOURCE:
        if (ROOT / top).is_dir() and top != "phone":
            assert f"COPY {top} {top}" in dockerfile, top
    assert "!integrations/" in (ROOT / ".dockerignore").read_text()


def test_the_addon_config_matches_the_start() -> None:
    import yaml

    cfg = yaml.safe_load((ROOT / "integrations/homeassistant/addon/fixit_lens/config.yaml").read_text())
    assert cfg["ingress_port"] == ha_addon.PANEL_PORT and f"{ha_addon.APP_PORT}/tcp" in cfg["ports"]
    assert cfg["ports"][f"{ha_addon.APP_PORT}/tcp"] is None  # no host port until the owner gives one
    assert cfg["discovery"] == [ha_addon.SERVICE]
    assert set(cfg["schema"]) == {f.name for f in fields(Options)}
    assert "hassio_api" not in cfg and "homeassistant_api" not in cfg  # it asks the Supervisor for discovery only
    assert {m["type"] for m in cfg["map"]} == {"ssl", "addon_config"}


def _png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def test_home_assistant_shows_our_own_mark() -> None:
    """The integration's brand/ and the add-on's icon and logo are brand/'s files as they are, at the sizes HA reads."""
    ha = ROOT / "integrations/homeassistant"
    integration = ha / "custom_components/fixit_lens/brand"
    for name in ("icon", "dark_icon", "logo", "dark_logo"):
        for suffix in ("", "@2x"):
            png = f"{name}{suffix}.png"
            assert (integration / png).read_bytes() == (ROOT / "brand/ha" / png).read_bytes(), png
    assert _png_size(integration / "icon.png") == (256, 256) and _png_size(integration / "icon@2x.png") == (512, 512)
    for png in ("icon.png", "logo.png"):
        assert (ha / "addon/fixit_lens" / png).read_bytes() == (ROOT / "brand/addon" / png).read_bytes(), png
    assert _png_size(ha / "addon/fixit_lens/icon.png") == (128, 128)
