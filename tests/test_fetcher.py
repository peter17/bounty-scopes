"""Fetcher tests against a real local HTTP server (no internet needed)."""
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from scrapers import common


class Handler(BaseHTTPRequestHandler):
    flaky_hits = 0

    def log_message(self, *args):  # silence server logs
        pass

    def _send(self, code, body=b"", ctype="text/html"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        if self.path == "/echo":
            self._send(200, json.dumps({"got": json.loads(body or b"{}"),
                                        "ctype": self.headers.get("Content-Type"),
                                        "accept": self.headers.get("Accept"),
                                        "origin": self.headers.get("Origin")}).encode(), "application/json")
        elif self.path == "/private":
            self._send(200, b"{}", "application/json")
        else:
            self._send(404)

    def do_GET(self):
        if self.path == "/robots.txt":
            self._send(200, b"User-agent: *\nDisallow: /private\n", "text/plain")
        elif self.path == "/ok":
            self._send(200, b"hello")
        elif self.path == "/flaky":  # 503 twice, then 200
            Handler.flaky_hits += 1
            if Handler.flaky_hits <= 2:
                self._send(503, b"busy")
            else:
                self._send(200, b"finally")
        elif self.path == "/private":
            self._send(200, b"secret")
        else:
            self._send(404)


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_get_ok_and_debug_log(server, caplog):
    f = common.Fetcher(server, delay=0, backoff=0)
    with caplog.at_level(logging.DEBUG, logger="scrapers"):
        assert f.get(f"{server}/ok") == "hello"
    assert f"GET {server}/ok -> 200" in caplog.text


def test_retry_is_logged_then_succeeds(server, caplog):
    Handler.flaky_hits = 0
    f = common.Fetcher(server, delay=0, backoff=0)
    with caplog.at_level(logging.WARNING, logger="scrapers"):
        assert f.get(f"{server}/flaky") == "finally"
    assert caplog.text.count("Retrying GET /flaky (HTTP 503)") == 2


def test_robots_disallow_is_respected(server):
    f = common.Fetcher(server, delay=0, backoff=0)
    with pytest.raises(common.RobotsDisallowed):
        f.get(f"{server}/private")


def test_dump_html_saves_raw_pages(server, tmp_path, monkeypatch):
    monkeypatch.setattr(common, "DUMP_DIR", tmp_path)
    f = common.Fetcher(server, delay=0, backoff=0)
    f.get(f"{server}/ok")
    files = list(tmp_path.glob("*.html"))
    assert len(files) == 1 and files[0].read_text() == "hello"


def test_post_json_sends_body_json_headers_and_no_spoofed_origin(server, caplog):
    f = common.Fetcher(server, delay=0, backoff=0)
    with caplog.at_level(logging.DEBUG, logger="scrapers"):
        out = f.post_json(f"{server}/echo", {"take": 10, "skip": 0})
    assert out == {"got": {"take": 10, "skip": 0}, "ctype": "application/json",
                   "accept": "application/json", "origin": None}
    assert f"POST {server}/echo -> 200" in caplog.text


def test_post_respects_robots(server):
    f = common.Fetcher(server, delay=0, backoff=0)
    with pytest.raises(common.RobotsDisallowed):
        f.post_json(f"{server}/private", {})
