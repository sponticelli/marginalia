"""``marginalia serve`` — HTTP capture endpoint behavior tests.

We spin up a real ``ThreadingHTTPServer`` on an ephemeral port for
each test and hit it with ``http.client``. This mirrors how a real
browser extension would talk to it (HTTP wire shape, headers,
status codes) — the alternative (calling handler methods directly)
would miss the dispatch + parsing layer.
"""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from engine.cli.serve import _build_handler_for_tests

# ─── server fixture ─────────────────────────────────────────────────


@pytest.fixture
def serve_with_inbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Yield a (host, port, token) triple with a fresh inbox env wired up."""
    inbox = tmp_path / "inbox"
    monkeypatch.setenv("WIKI_RAW_PATH", str(inbox))
    token = "test-token-not-secure-for-real-use"

    handler_cls = _build_handler_for_tests(token)
    # Port 0 → OS picks an ephemeral free port; serves race-free in CI.
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield host, port, token
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _post_add(host: str, port: int, token: str | None, body: dict) -> tuple[int, dict]:
    """Send a POST /add and return ``(status, json_body)``."""
    conn = http.client.HTTPConnection(host, port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    payload = json.dumps(body).encode("utf-8")
    conn.request("POST", "/add", body=payload, headers=headers)
    resp = conn.getresponse()
    body_bytes = resp.read()
    conn.close()
    return resp.status, json.loads(body_bytes) if body_bytes else {}


# ─── tests ──────────────────────────────────────────────────────────


def test_post_add_stages_url(serve_with_inbox, tmp_path: Path) -> None:
    """Happy path: POST a URL → 200 + {"ok": true, "staged": "<inbox path>"}."""
    host, port, token = serve_with_inbox

    status, body = _post_add(host, port, token, {"target": "https://example.com/article"})

    assert status == 200
    assert body["ok"] is True
    assert "example-com" in body["staged"]  # filename derived from host
    # File actually landed in the inbox.
    staged = Path(body["staged"])
    assert staged.is_file()
    assert staged.read_text().strip() == "https://example.com/article"


def test_post_add_rejects_missing_token(serve_with_inbox) -> None:
    """No Authorization header → 401."""
    host, port, _token = serve_with_inbox

    status, body = _post_add(host, port, None, {"target": "https://x.com"})
    assert status == 401
    assert body["ok"] is False
    assert "missing or malformed" in body["error"].lower()


def test_post_add_rejects_wrong_token(serve_with_inbox) -> None:
    """Wrong token → 401 with 'invalid token'."""
    host, port, _token = serve_with_inbox

    status, body = _post_add(host, port, "wrong-token", {"target": "https://x.com"})
    assert status == 401
    assert "invalid token" in body["error"].lower()


def test_post_add_rejects_missing_target(serve_with_inbox) -> None:
    """Empty/missing target field → 400."""
    host, port, token = serve_with_inbox

    status, body = _post_add(host, port, token, {})
    assert status == 400
    assert "missing 'target'" in body["error"]


def test_post_add_rejects_invalid_json(serve_with_inbox) -> None:
    """Body that isn't valid JSON → 400."""
    host, port, token = serve_with_inbox

    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request(
        "POST",
        "/add",
        body=b"not json",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Content-Length": "8",
        },
    )
    resp = conn.getresponse()
    body = json.loads(resp.read())
    conn.close()
    assert resp.status == 400
    assert "invalid JSON" in body["error"]


def test_post_add_returns_409_on_already_staged(serve_with_inbox) -> None:
    """Posting the same URL twice → second returns 409 Conflict."""
    host, port, token = serve_with_inbox

    s1, _ = _post_add(host, port, token, {"target": "https://example.com/a"})
    assert s1 == 200
    s2, body2 = _post_add(host, port, token, {"target": "https://example.com/a"})
    assert s2 == 409
    assert "already" in body2["error"]


def test_post_add_force_overrides_existing(serve_with_inbox) -> None:
    """force=true → 200 even if the inbox already has the entry."""
    host, port, token = serve_with_inbox

    _post_add(host, port, token, {"target": "https://example.com/b"})
    status, body = _post_add(host, port, token, {"target": "https://example.com/b", "force": True})
    assert status == 200
    assert body["ok"] is True


def test_get_root_returns_health_check(serve_with_inbox) -> None:
    """GET / works without auth — it's a health check."""
    host, port, _token = serve_with_inbox

    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("GET", "/")
    resp = conn.getresponse()
    body = json.loads(resp.read())
    conn.close()
    assert resp.status == 200
    assert body["ok"] is True
    assert body["service"] == "marginalia-serve"


def test_options_returns_cors_headers(serve_with_inbox) -> None:
    """Preflight OPTIONS for browser fetch() works without auth."""
    host, port, _token = serve_with_inbox

    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request(
        "OPTIONS",
        "/add",
        headers={"Origin": "chrome-extension://abc", "Access-Control-Request-Method": "POST"},
    )
    resp = conn.getresponse()
    resp.read()
    conn.close()
    assert resp.status == 204
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"
    assert "POST" in resp.headers.get("Access-Control-Allow-Methods", "")


def test_unknown_path_returns_404(serve_with_inbox) -> None:
    """POST /<anything-else> → 404 (after auth check)."""
    host, port, token = serve_with_inbox

    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request(
        "POST",
        "/wrong",
        body=b"{}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Content-Length": "2",
        },
    )
    resp = conn.getresponse()
    body = json.loads(resp.read())
    conn.close()
    assert resp.status == 404
    assert body["ok"] is False


# ─── --init-token CLI test ──────────────────────────────────────────


def test_init_token_writes_64_hex_chars(tmp_path: Path) -> None:
    """`marginalia serve --init-token` writes a 64-hex-char token + sets perms."""
    from typer.testing import CliRunner

    from engine.cli.main import app

    runner = CliRunner()
    token_path = tmp_path / "serve.token"
    result = runner.invoke(app, ["serve", "--init-token", "--token-path", str(token_path)])
    assert result.exit_code == 0, result.output
    assert token_path.is_file()
    content = token_path.read_text().strip()
    assert len(content) == 64
    assert all(c in "0123456789abcdef" for c in content)
