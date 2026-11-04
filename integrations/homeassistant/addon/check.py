"""Run the add-on image the way the Supervisor would, and check it end to end. Everything runs in containers on a
network of its own; nothing is published on the host.

    uv run python integrations/homeassistant/addon/stage.py OUT && docker build -t fixit-lens-addon OUT/fixit_lens
    uv run python integrations/homeassistant/addon/check.py fixit-lens-addon

- a stub Supervisor (the name `supervisor`) that records what discovery is sent;
- the add-on with /data (its options.json), /ssl (a throwaway self-signed certificate) and ssl on;
- the ingress proxy's address, 172.30.32.2, asking for the panel; and another address, which is refused;
- a phone joining with the panel's invite over HTTPS, then Home Assistant reading the home with the key discovery
  carried.
The key is never printed. Everything made here is removed at the end, the containers and the network included.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

NET_SUBNET, INGRESS_IP, OTHER_IP = "172.30.32.0/23", "172.30.32.2", "172.30.32.9"

STUB = r"""
import json, http.server
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["content-length"]))
        with open("/stub/discovery.json", "a") as f:
            f.write(json.dumps({"auth": self.headers.get("authorization"), "body": json.loads(body)}) + "\n")
        self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers()
        self.wfile.write(b'{"result": "ok", "data": {"uuid": "1"}}')
    def log_message(self, *a): pass
http.server.HTTPServer(("0.0.0.0", 80), H).serve_forever()
"""

# Run in a container at an address of our choosing: one HTTP call, its status and body as JSON on stdout.
CALL = r"""
import json, sys, httpx
method, url, headers, data = sys.argv[1], sys.argv[2], json.loads(sys.argv[3]), sys.argv[4]
try:
    r = httpx.request(method, url, headers=headers, content=data or None, verify=False, timeout=20)
except httpx.HTTPError as e:  # no answer at all: status 0
    print(json.dumps({"status": 0, "text": type(e).__name__}))
else:
    print(json.dumps({"status": r.status_code, "text": r.text}))
"""


def sh(*args: str, check: bool = True) -> str:
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"{' '.join(args[:3])}… failed: {r.stderr.strip()[-400:]}")
    return r.stdout


class Check:
    def __init__(self, image: str) -> None:
        self.image, self.tag = image, "fixit-check-" + secrets.token_hex(3)
        self.net, self.addon, self.stub = self.tag, self.tag + "-addon", self.tag + "-supervisor"
        self.tmp = Path(tempfile.mkdtemp(prefix="fixit-addon-check-"))
        self.failures: list[str] = []

    def ok(self, cond: bool, what: str) -> None:
        print(("ok    " if cond else "FAIL  ") + what)
        if not cond:
            self.failures.append(what)

    def call(self, ip: str, method: str, url: str, headers: dict[str, str] | None = None,
             data: str = "") -> dict[str, Any]:
        out = sh("docker", "run", "--rm", "--network", self.net, "--ip", ip, "--entrypoint", "python", self.image,
                 "-c", CALL, method, url, json.dumps(headers or {}), data)
        answer: dict[str, Any] = json.loads(out)
        return answer

    def logs(self) -> str:
        r = subprocess.run(["docker", "logs", self.addon], capture_output=True, text=True)
        return r.stdout + r.stderr

    def setup(self) -> None:
        data, ssl_dir, stub = self.tmp / "data", self.tmp / "ssl", self.tmp / "stub"
        for d in (data, ssl_dir, stub):
            d.mkdir(mode=0o777)
            d.chmod(0o777)
        sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=fixit-addon-check",
           "-keyout", str(ssl_dir / "privkey.pem"), "-out", str(ssl_dir / "fullchain.pem"))
        for f in ssl_dir.iterdir():
            f.chmod(0o644)
        (data / "options.json").write_text(json.dumps({"calls": "replay", "ssl": True, "certfile": "fullchain.pem",
                                                       "keyfile": "privkey.pem",
                                                       "phone_url": "https://homeassistant.example.org:8790"}))
        # the containers' own addresses come from the upper half, so the ingress address stays free for the call
        sh("docker", "network", "create", "--subnet", NET_SUBNET, "--ip-range", "172.30.33.0/24", self.net)
        sh("docker", "run", "-d", "--name", self.stub, "--network", self.net, "--network-alias", "supervisor",
           "-v", f"{stub}:/stub", "--entrypoint", "python", self.image, "-c", STUB)
        sh("docker", "run", "-d", "--name", self.addon, "--hostname", "local-fixit-lens", "--network", self.net,
           "--network-alias", "local-fixit-lens", "-e", "SUPERVISOR_TOKEN=stub-supervisor-token",
           "-v", f"{data}:/data", "-v", f"{ssl_dir}:/ssl:ro", self.image)

    def wait(self) -> bool:
        for _ in range(60):
            logs = self.logs()
            if "Uvicorn running on https://0.0.0.0:8790" in logs and ":8099" in logs:
                return True
            if sh("docker", "inspect", "-f", "{{.State.Running}}", self.addon).strip() != "true":
                return False
            time.sleep(1)
        return False

    def run(self) -> None:
        self.setup()
        started = self.wait()
        self.ok(started, "the add-on starts: the app on 8790 over HTTPS and the panel on 8099")
        if not started:
            print(self.logs()[-3000:])
            return
        panel = "http://local-fixit-lens:8099"
        ingress = {"X-Ingress-Path": "/api/hassio_ingress/checktoken"}
        r = self.call(OTHER_IP, "GET", panel + "/")
        self.ok(r["status"] == 403, "the panel refuses an address that is not the ingress proxy")
        r = self.call(INGRESS_IP, "GET", panel + "/", ingress)
        self.ok(r["status"] == 200 and 'action="/api/hassio_ingress/checktoken/invite"' in r["text"],
                "the panel answers the ingress proxy, its forms under the ingress path")
        self.ok("No home has joined yet" in r["text"], "no home yet")
        r = self.call(INGRESS_IP, "POST", panel + "/invite", ingress)
        m = re.search(r"\?invite=([A-Z0-9]{5}-[A-Z0-9]+)", r["text"])
        self.ok(r["status"] == 200 and m is not None and "<svg" in r["text"], "an invite with its QR code")
        if m is None:
            return
        app = "https://local-fixit-lens:8790"
        r = self.call(OTHER_IP, "GET", app + "/")
        self.ok(r["status"] == 200 and "<html" in r["text"].lower(), "the phone's app is served over HTTPS")
        body = json.dumps({"invite_code": m.group(1), "consent": True, "lang": "en"})
        r = self.call(OTHER_IP, "POST", app + "/api/v1/households", {"content-type": "application/json"}, body)
        self.ok(r["status"] == 201, f"a phone joins with the invite ({r['status']})")
        if r["status"] != 201:
            return
        hid = json.loads(r["text"])["id"]
        r = self.call(INGRESS_IP, "POST", panel + "/connect", {**ingress, "content-type":
                                                              "application/x-www-form-urlencoded"},
                      f"household={hid}")
        self.ok(r["status"] == 200 and "Discovered" in r["text"], "Connect to Home Assistant")
        sent = [json.loads(x) for x in (self.tmp / "stub/discovery.json").read_text().splitlines()]
        self.ok(len(sent) == 1 and sent[0]["auth"] == "Bearer stub-supervisor-token"
                and sent[0]["body"]["service"] == "fixit_lens"
                and sent[0]["body"]["config"]["url"] == "http://local-fixit-lens:8790",
                "discovery reaches the Supervisor with the add-on's own address")
        token = sent[0]["body"]["config"]["token"]
        self.ok(token not in r["text"] and token not in self.logs(),
                "the key is not on the page or in the log")
        r = self.call(OTHER_IP, "GET", "https://local-fixit-lens:8790/api/v1/household",
                      {"authorization": f"Bearer {token}"})
        self.ok(r["status"] == 200 and json.loads(r["text"])["id"] == hid,
                "Home Assistant reads the home with the discovered key")
        r = self.call(OTHER_IP, "GET", "http://local-fixit-lens:8790/api/v1/household")
        self.ok(r["status"] != 200, "plain HTTP is not served when ssl is on")
        mode = sh("docker", "exec", self.addon, "stat", "-c", "%a", "/data/store.sqlite").strip()
        self.ok(mode == "600", f"the store in /data is 0600 ({mode})")
        logs = self.logs()
        self.ok("Traceback" not in logs, "no traceback in the add-on's log")

    def teardown(self) -> None:
        for c in (self.addon, self.stub):
            sh("docker", "rm", "-f", c, check=False)
        sh("docker", "network", "rm", self.net, check=False)
        # /data was written by the container's root: clear it from a container, then the folder itself
        sh("docker", "run", "--rm", "-v", f"{self.tmp}:/t", "--entrypoint", "sh", self.image, "-c",
           "rm -rf /t/data /t/ssl /t/stub", check=False)
        sh("rmdir", str(self.tmp), check=False)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the add-on image as the Supervisor would and check it.")
    ap.add_argument("image")
    c = Check(ap.parse_args().image)
    try:
        c.run()
    finally:
        c.teardown()
    print(f"{'FAILED: ' + str(len(c.failures)) if c.failures else 'all checks passed'}")
    return 1 if c.failures else 0


if __name__ == "__main__":
    sys.exit(main())
