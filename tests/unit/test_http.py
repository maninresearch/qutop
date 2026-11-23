import urllib.error

import pytest

from qutop.providers.http import Http, HttpError, Request, Response
from stubs import FakeTransport

URL = "https://example.test/api/v1/things"


def make(transport: object, sleeps: list[float] | None = None) -> Http:
    record = sleeps if sleeps is not None else []
    return Http(transport=transport, sleep=record.append)  # type: ignore[arg-type]


def test_get_json_with_params() -> None:
    transport = FakeTransport({("GET", URL): {"ok": True}})
    assert make(transport).request_json("GET", URL, params={"limit": "5"}) == {"ok": True}
    [req] = transport.requests
    assert req.url == f"{URL}?limit=5"
    assert req.headers["Accept"] == "application/json"


def test_form_and_json_bodies() -> None:
    transport = FakeTransport({("POST", URL): {}})
    http = make(transport)
    http.request_json("POST", URL, form={"a": "1 2"})
    http.request_json("POST", URL, json_body={"b": 2})
    form, js = transport.requests
    assert (form.body, form.headers["Content-Type"]) == (
        b"a=1+2",
        "application/x-www-form-urlencoded",
    )
    assert (js.body, js.headers["Content-Type"]) == (b'{"b": 2}', "application/json")


def test_retries_then_succeeds() -> None:
    answers = iter(
        [
            Response(503, {}, b""),
            Response(429, {"Retry-After": "3"}, b""),
            Response(200, {}, b"[1]"),
        ]
    )
    sleeps: list[float] = []
    http = make(lambda req, timeout: next(answers), sleeps)
    assert http.request_json("GET", URL) == [1]
    assert sleeps == [1.0, 3.0]


def test_gives_up_after_retries() -> None:
    http = make(lambda req, timeout: Response(502, {}, b"<html>"))
    with pytest.raises(HttpError, match=r"example.test/api/v1/things: HTTP 502$") as exc:
        http.request_json("GET", URL)
    assert exc.value.status == 502


def test_client_errors_are_not_retried() -> None:
    calls: list[Request] = []

    def transport(req: Request, timeout: float) -> Response:
        calls.append(req)
        return Response(404, {}, b'{"message": "no such backend"}')

    with pytest.raises(HttpError, match="HTTP 404 \\(no such backend\\)"):
        make(transport).request_json("GET", URL)
    assert len(calls) == 1


def test_network_error() -> None:
    def transport(req: Request, timeout: float) -> Response:
        raise urllib.error.URLError("Name or service not known")

    with pytest.raises(HttpError, match="Name or service not known"):
        make(transport).request_json("GET", URL)


def test_error_message_is_not_a_full_body_dump() -> None:
    body = ('{"message": "' + "x" * 1000 + '"}').encode()
    with pytest.raises(HttpError) as exc:
        make(lambda req, timeout: Response(400, {}, body)).request_json("GET", URL)
    assert len(str(exc.value)) < 300


def test_non_json_response() -> None:
    with pytest.raises(HttpError, match="not JSON"):
        make(lambda req, timeout: Response(200, {}, b"<html>")).request_json("GET", URL)


def test_urllib_transport_against_local_server(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from qutop.providers.http import urllib_transport

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            status = 200 if self.path == "/ok" else 404
            body = b'{"path": "%s"}' % self.path.encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    for var in ("http_proxy", "HTTP_PROXY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        ok = urllib_transport(Request("GET", f"{base}/ok", {}), 5)
        missing = urllib_transport(Request("GET", f"{base}/missing", {}), 5)
    finally:
        server.shutdown()
        server.server_close()
    assert (ok.status, ok.body) == (200, b'{"path": "/ok"}')
    assert missing.status == 404
