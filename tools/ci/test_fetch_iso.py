import hashlib
import http.server
import threading

import pytest

from .fetch_iso import FetchError, fetch, validate_url


def test_rejects_non_https_credentials_and_fragments():
    for url in ("http://example.com/a.iso", "https://user:pass@example.com/a.iso",
                "https://example.com/a.iso#fragment", "file:///tmp/a.iso"):
        with pytest.raises(FetchError):
            validate_url(url)


def test_rejects_private_addresses(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(FetchError, match="public"):
        validate_url("https://example.com/game.iso")
    assert validate_url("https://example.com/game.iso", allow_private=True)


class _Server(http.server.BaseHTTPRequestHandler):
    payload = b"x" * 4096
    routes = {}

    def do_GET(self):
        status, headers, body = self.routes.get(self.path, (404, {}, b""))
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    previous = dict(_Server.routes)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Server)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    thread.join()
    httpd.server_close()
    _Server.routes = previous


def _fetch(url, path, **kwargs):
    # The production CLI always uses HTTPS. The local fixture exercises the
    # streaming/redirect code over loopback without opening a public listener.
    import unittest.mock as mock
    options = {"min_bytes": 1, "max_bytes": 1024 * 1024, "allow_private": True}
    options.update(kwargs)
    with mock.patch("tools.ci.fetch_iso.validate_url", side_effect=lambda u, **kw: u):
        return fetch(url, path, **options)


def test_streams_and_hashes(server, tmp_path):
    _Server.routes["/game.iso"] = (200, {}, _Server.payload)
    result = _fetch(server + "/game.iso", tmp_path / "game.iso",
                    expected_sha256=hashlib.sha256(_Server.payload).hexdigest())
    assert (tmp_path / "game.iso").read_bytes() == _Server.payload
    assert result == {"size": len(_Server.payload), "sha256": hashlib.sha256(_Server.payload).hexdigest()}


def test_redirect(server, tmp_path):
    _Server.routes["/start"] = (302, {"Location": "/game.iso"}, b"")
    _Server.routes["/game.iso"] = (200, {}, _Server.payload)
    _fetch(server + "/start", tmp_path / "game.iso")
    assert (tmp_path / "game.iso").read_bytes() == _Server.payload


def test_hash_mismatch_leaves_no_file(server, tmp_path):
    _Server.routes["/game.iso"] = (200, {}, _Server.payload)
    with pytest.raises(FetchError, match="SHA-256 mismatch"):
        _fetch(server + "/game.iso", tmp_path / "game.iso", expected_sha256="0" * 64)
    assert not (tmp_path / "game.iso").exists()
    assert list(tmp_path.iterdir()) == []


def test_size_limit_and_http_error(server, tmp_path):
    _Server.routes["/game.iso"] = (200, {}, _Server.payload)
    with pytest.raises(FetchError, match="size"):
        _fetch(server + "/game.iso", tmp_path / "game.iso", max_bytes=10)
    with pytest.raises(FetchError, match="404"):
        _fetch(server + "/missing.iso", tmp_path / "missing.iso")
    assert list(tmp_path.iterdir()) == []


def test_redirect_loop_is_bounded(server, tmp_path):
    _Server.routes["/loop"] = (302, {"Location": "/loop"}, b"")
    with pytest.raises(FetchError, match="redirected too many"):
        _fetch(server + "/loop", tmp_path / "game.iso")
