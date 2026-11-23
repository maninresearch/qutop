"""A small JSON-over-HTTPS client on the standard library.

``urllib`` honours ``HTTPS_PROXY``/``NO_PROXY`` and the system certificate
store (and ``SSL_CERT_FILE``), so nothing extra is needed for proxies or
custom CAs. The transport is injectable so tests never touch the network.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from qutop import __version__
from qutop.providers.base import ProviderError

USER_AGENT = f"qutop/{__version__}"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class Request:
    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes | None = None


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes


Transport = Callable[[Request, float], Response]


class HttpError(ProviderError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def urllib_transport(request: Request, timeout: float) -> Response:
    req = urllib.request.Request(
        request.url, data=request.body, headers=dict(request.headers), method=request.method
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return Response(resp.status, dict(resp.headers.items()), resp.read())
    except urllib.error.HTTPError as exc:
        return Response(exc.code, dict(exc.headers.items()), exc.read())


@dataclass
class Http:
    transport: Transport = urllib_transport
    timeout: float = 20.0
    retries: int = 2
    sleep: Callable[[float], None] = field(default=time.sleep)

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        form: Mapping[str, str] | None = None,
        json_body: Any = None,
    ) -> Any:
        all_headers = {"Accept": "application/json", "User-Agent": USER_AGENT, **(headers or {})}
        body: bytes | None = None
        if form is not None:
            body = urllib.parse.urlencode(form).encode()
            all_headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif json_body is not None:
            body = json.dumps(json_body).encode()
            all_headers["Content-Type"] = "application/json"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        request = Request(method, url, all_headers, body)
        where = _describe(url)
        for attempt in range(self.retries + 1):
            try:
                response = self.transport(request, self.timeout)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == self.retries:
                    reason = getattr(exc, "reason", exc)
                    raise HttpError(f"{where}: {reason}") from exc
                self.sleep(2.0**attempt)
                continue

            if response.status in RETRY_STATUSES and attempt < self.retries:
                self.sleep(_retry_after(response) or 2.0**attempt)
                continue
            if response.status >= 400:
                raise HttpError(
                    f"{where}: HTTP {response.status}{_error_detail(response.body)}",
                    response.status,
                )
            try:
                return json.loads(response.body) if response.body else None
            except ValueError as exc:
                raise HttpError(f"{where}: response is not JSON") from exc
        raise AssertionError("unreachable")  # pragma: no cover


def _describe(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.hostname}{parts.path}"


def _retry_after(response: Response) -> float | None:
    value = {k.lower(): v for k, v in response.headers.items()}.get("retry-after")
    try:
        return min(float(value), 30.0) if value is not None else None
    except ValueError:
        return None


def _error_detail(body: bytes) -> str:
    """A short, safe summary of an API error body (never echo it in full)."""
    try:
        data = json.loads(body)
    except ValueError:
        return ""
    if isinstance(data, dict):
        for key in ("errorMessage", "message", "error_description", "error"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return f" ({value[:200]})"
        errors = data.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            message = errors[0].get("message")
            if isinstance(message, str):
                return f" ({message[:200]})"
    return ""
