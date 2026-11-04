"""docs/api.md: every curl example runs against the app as written, with a token from the CLI the doc shows, and the
sample item has the fields the API answers with."""

from __future__ import annotations

import json
import re
import shlex
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app.schedule import store
from tests.l5.conftest import add_appliance
from tests.l5.test_pilot_door import api, clock, deps, join  # noqa: F401 (fixtures)

DOC = Path(__file__).resolve().parents[2] / "docs" / "api.md"


def blocks(lang: str) -> list[str]:
    return re.findall(rf"```{lang}\n(.*?)```", DOC.read_text(), re.S)


def curls() -> list[list[str]]:
    lines = "\n".join(blocks("sh")).replace("\\\n", " ").splitlines()
    return [shlex.split(ln) for ln in lines if ln.startswith("curl ")]


def test_every_curl_example_works(api: TestClient, conn: sqlite3.Connection) -> None:  # noqa: F811
    hid, _ = join(api, conn)
    aid = add_appliance(conn, hid)
    _, token = store.issue_token(conn, hid, by_kind="agent", scope="write", label="my script", created_by=None)
    job = f"/api/v1/appliances/{aid}/jobs/clean_debris_filter"
    examples = curls()
    assert len(examples) == 12
    api.post(f"{job}/done", json={"date": "2026-10-01"}, headers={"Authorization": f"Bearer {token}"})
    for argv in examples:
        assert argv[:4] == ["curl", "-s", "-H", "@fixit.auth"], argv  # the token only ever from the 0600 file
        method, body, url = "GET", None, ""
        rest = iter(argv[4:])
        for a in rest:
            if a == "-X":
                method = next(rest)
            elif a == "-d":
                body = json.loads(next(rest))
            elif a == "-H":
                assert next(rest) == "content-type: application/json"
            else:
                url = a
        path = url.replace("$FIXIT/appliances/a_washer/jobs/clean_debris_filter", job).replace("$JOB", job) \
            .replace("$FIXIT", "/api/v1")
        r = api.request(method, path, json=body, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code < 300, (argv, r.text)
    log = api.get(f"{job}/changes", headers={"Authorization": f"Bearer {token}"}).json()
    assert {c["by"]["label"] for c in log} == {"my script"}


def test_the_doc_names_the_job_the_examples_use_and_the_cli_it_shows() -> None:
    text = DOC.read_text()
    assert 'JOB="$FIXIT/appliances/a_washer/jobs/clean_debris_filter"' in text
    assert "invites token <household_id> --kind agent --label" in text and "umask 077" in text


def test_the_sample_item_has_the_apis_fields(api: TestClient, conn: sqlite3.Connection) -> None:  # noqa: F811
    hid, auth = join(api, conn)
    add_appliance(conn, hid)
    [real] = api.get("/api/v1/due", headers=auth).json()
    [sample] = [json.loads(b) for b in blocks("json")]
    assert set(sample) <= set(real) and set(sample["last_done"]) == set(real["last_done"])
    assert set(sample["manual"]) == set(real["manual"])


def test_the_readme_points_here_and_names_only_what_is_built() -> None:
    text = (DOC.parents[1] / "README.md").read_text()
    section = text.split("## Calendar, Home Assistant and scripts", 1)[1].split("\n## ", 1)[0]
    assert "`docs/api.md`" in section and "The tests run every one of them." in section
    built, roadmap = section.split("Not built yet:", 1)
    for later in ("CalDAV", "Microsoft To Do", "Google Tasks", "Alexa", "SmartThings", "Muse connector"):
        assert later in roadmap and later not in built, later
    for never in ("Matter", "HomeKit"):
        assert never not in text, never
