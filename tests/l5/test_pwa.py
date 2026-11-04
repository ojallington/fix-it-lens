from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from deploy.pwa import mount_pwa


def test_pwa_beside_the_api(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>app</html>")
    (tmp_path / "sw.js").write_text("self")
    (tmp_path / "assets/a.js").write_text("x" * 5000)
    app = FastAPI()

    @app.get("/api/v1/x")
    def x() -> dict[str, int]:
        return {"ok": 1}

    mount_pwa(app, tmp_path)
    c = TestClient(app)
    assert c.get("/api/v1/x").json() == {"ok": 1}
    sw = c.get("/sw.js")
    assert sw.headers["cache-control"] == "no-cache" and sw.headers["service-worker-allowed"] == "/"
    a = c.get("/assets/a.js", headers={"accept-encoding": "gzip"})
    assert "immutable" in a.headers["cache-control"] and a.headers["content-encoding"] == "gzip"
    assert c.get("/job/route").text == "<html>app</html>"
    assert c.get("/../../etc/passwd").text == "<html>app</html>"
    assert '/api/v1/preflight' in c.get("/preflight").text
