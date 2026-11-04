"""invites token: a joined household's bearer token, and nothing for an unused code."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.schedule import invites, store
from tests.l5.conftest import T0


def test_token_only_for_a_joined_household(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("FIXIT_DB", str(tmp_path / "store.sqlite"))
    conn = store.open_store(tmp_path / "store.sqlite")
    joined, unused = store.create_invites(conn, 2, cap_usd_day=0.45, at=T0)
    hid = store.claim_invite(conn, joined.code, consent=True, at=T0)
    conn.commit()
    invites.main(["token", str(hid)])
    token = capsys.readouterr().out.strip()
    secret = store.load_secret(tmp_path / "secrets/household.secret")
    assert store.household_from_token(conn, secret, token)["id"] == hid
    with pytest.raises(SystemExit):
        invites.main(["token", unused.household_id])


def test_new_prints_links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("FIXIT_DB", str(tmp_path / "store.sqlite"))
    invites.main(["new", "2", "--link", "https://fixit.test/"])
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    code, _, link = lines[0].split()
    assert link == f"https://fixit.test/?invite={code}"
    conn = store.open_store(tmp_path / "store.sqlite")
    assert store.claim_invite(conn, code, consent=True, at=T0) is not None  # the dashed form is accepted


def test_token_kind_issues_a_client_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("FIXIT_DB", str(tmp_path / "store.sqlite"))
    conn = store.open_store(tmp_path / "store.sqlite")
    [inv] = store.create_invites(conn, 1, cap_usd_day=0.45, at=T0)
    hid = store.claim_invite(conn, inv.code, consent=True, at=T0)
    invites.main(["token", str(hid), "--kind", "ha", "--read-only", "--label", "living room"])
    token = capsys.readouterr().out.strip()
    secret = store.load_secret(tmp_path / "secrets/household.secret")
    c = store.caller_from_token(store.open_store(tmp_path / "store.sqlite"), secret, token)
    assert c is not None and (c.household_id, c.by_kind, c.scope) == (hid, "ha", "read")
    with pytest.raises(SystemExit):
        invites.main(["token", str(hid), "--read-only"])  # the household's own token is never read-only


def test_rotate_replaces_only_the_households_own_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("FIXIT_DB", str(tmp_path / "store.sqlite"))
    conn = store.open_store(tmp_path / "store.sqlite")
    mine, theirs = store.create_invites(conn, 2, cap_usd_day=0.45, at=T0)
    hid = store.claim_invite(conn, mine.code, consent=True, at=T0)
    other = store.claim_invite(conn, theirs.code, consent=True, at=T0)
    assert hid and other
    secret = store.load_secret(tmp_path / "secrets/household.secret")
    old, other_token = store.household_token(secret, hid), store.household_token(secret, other)
    _, ha = store.issue_token(conn, hid, by_kind="ha", scope="read", label="Home Assistant")
    feed = store.feed_token(secret, hid)

    invites.main(["token", hid, "--rotate"])
    new = capsys.readouterr().out.strip()
    assert new != old and len(new) == len(old)
    assert store.caller_from_token(conn, secret, old) is None  # the leaked token is dead everywhere
    c = store.caller_from_token(conn, secret, new)
    assert c is not None and (c.household_id, c.by_kind, c.scope) == (hid, "app", "write")
    assert store.household_from_token(conn, secret, other_token)["id"] == other  # another home is untouched
    assert store.caller_from_token(conn, secret, ha) is not None  # and so are its HA, agent and calendar tokens
    assert store.household_from_feed_token(conn, secret, feed)["id"] == hid

    invites.main(["token", hid])
    assert capsys.readouterr().out.strip() == new  # the plain verb prints the current one
    invites.main(["token", hid, "--rotate"])
    assert store.caller_from_token(conn, secret, new) is None  # each rotation retires the one before
    with pytest.raises(SystemExit):
        invites.main(["token", hid, "--kind", "ha", "--rotate"])


def test_return_link_opens_the_home_on_a_phone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("FIXIT_DB", str(tmp_path / "store.sqlite"))
    conn = store.open_store(tmp_path / "store.sqlite")
    joined, unused = store.create_invites(conn, 2, cap_usd_day=0.45, at=T0)
    hid = store.claim_invite(conn, joined.code, consent=True, at=T0)
    invites.main(["return-link", str(hid), "https://fixit.test:8443/"])
    url = capsys.readouterr().out.split()[0]
    assert url.startswith("https://fixit.test:8443/#return/")
    from app.schedule import door

    got = door.use_return_code(conn, url.rsplit("/", 1)[1])
    assert got is not None and got[0] == hid
    assert door.use_return_code(conn, url.rsplit("/", 1)[1]) is None  # once
    with pytest.raises(SystemExit):
        invites.main(["return-link", unused.household_id, "https://fixit.test"])
