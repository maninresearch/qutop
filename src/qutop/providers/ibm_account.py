"""IBM Quantum credentials: the saved Qiskit account, and IBM Cloud IAM tokens.

qutop reads the account file written by ``QiskitRuntimeService.save_account()``
(``~/.qiskit/qiskit-ibm.json``) or the ``QISKIT_IBM_*`` environment variables,
choosing an account the same way Qiskit does. It never writes credentials and
never prints them.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qutop.providers.base import NotConfiguredError, ProviderError
from qutop.providers.http import Http, HttpError

DEFAULT_ACCOUNT_FILE = Path("~/.qiskit/qiskit-ibm.json")
DEFAULT_CLOUD_URL = "https://cloud.ibm.com"
CHANNELS = ("ibm_quantum_platform", "ibm_cloud")
_DEFAULT_NAMES = ("default-ibm-quantum-platform", "default-ibm-cloud", "default")

SAVE_HINT = (
    "save one with QiskitRuntimeService.save_account(token=..., instance=..., "
    "set_as_default=True), or set QISKIT_IBM_TOKEN and QISKIT_IBM_INSTANCE"
)


@dataclass(frozen=True)
class Account:
    token: str
    instance: str | None
    """An instance CRN, an instance name, or None to use every instance."""
    url: str = DEFAULT_CLOUD_URL
    private_endpoint: bool = False

    def __repr__(self) -> str:  # never show the token
        return f"Account(instance={self.instance!r}, url={self.url!r})"


def load_account(
    path: Path | None = None,
    name: str | None = None,
    env: Mapping[str, str] | None = None,
) -> Account:
    env = os.environ if env is None else env
    path = (path or DEFAULT_ACCOUNT_FILE).expanduser()

    if name is None and env.get("QISKIT_IBM_TOKEN"):
        return Account(
            token=env["QISKIT_IBM_TOKEN"],
            instance=env.get("QISKIT_IBM_INSTANCE") or None,
            url=env.get("QISKIT_IBM_URL") or DEFAULT_CLOUD_URL,
        )

    try:
        saved: dict[str, Any] = json.loads(path.read_text())
    except FileNotFoundError:
        raise NotConfiguredError(f"no saved IBM Quantum account; {SAVE_HINT}") from None
    except (OSError, ValueError) as exc:
        raise NotConfiguredError(f"cannot read {path}: {exc}") from exc

    if name is not None:
        if name not in saved:
            raise NotConfiguredError(f"no saved IBM Quantum account named {name!r} in {path}")
        return _from_saved(name, saved[name])

    usable = {k: v for k, v in saved.items() if isinstance(v, dict)}
    if not usable:
        raise NotConfiguredError(f"no saved IBM Quantum account; {SAVE_HINT}")
    candidates = (
        [k for k, v in usable.items() if v.get("is_default_account")]
        + [k for k in _DEFAULT_NAMES if k in usable]
        + [k for k, v in usable.items() if v.get("channel") in CHANNELS]
        + list(usable)
    )
    chosen = candidates[0]
    return _from_saved(chosen, usable[chosen])


def _from_saved(name: str, data: dict[str, Any]) -> Account:
    channel = data.get("channel") or "ibm_quantum_platform"
    if channel not in CHANNELS:
        raise NotConfiguredError(
            f"saved account {name!r} uses channel {channel!r}, which IBM has retired; {SAVE_HINT}"
        )
    token = data.get("token")
    if not token:
        raise NotConfiguredError(f"saved account {name!r} has no token; {SAVE_HINT}")
    return Account(
        token=token,
        instance=data.get("instance") or None,
        url=data.get("url") or DEFAULT_CLOUD_URL,
        private_endpoint=bool(data.get("private_endpoint", False)),
    )


def _cloud_host(account: Account) -> str:
    host = urllib.parse.urlsplit(account.url).hostname
    if not host:
        raise NotConfiguredError(f"saved account has an invalid url: {account.url!r}")
    return host


def is_crn(value: str) -> bool:
    return value.startswith("crn:")


def region_from_crn(crn: str) -> str:
    parts = crn.split(":")
    if len(parts) < 6 or not parts[5]:
        raise NotConfiguredError(f"instance is not a valid CRN: {crn!r}")
    return parts[5]


def api_base(account: Account, crn: str) -> str:
    """Runtime API base URL for an instance, as qiskit-ibm-runtime computes it."""
    host = _cloud_host(account)
    region = region_from_crn(crn)
    if account.private_endpoint:
        return f"https://private.{region}.quantum.{host}/api/v1"
    prefix = "" if region == "us-east" else f"{region}."
    return f"https://{prefix}quantum.{host}/api/v1"


class IAMToken:
    """Exchanges the API key for an IAM bearer token and caches it until near expiry."""

    def __init__(
        self, account: Account, http: Http, clock: Callable[[], float] = time.time
    ) -> None:
        self._account = account
        self._http = http
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0

    @property
    def url(self) -> str:
        host = _cloud_host(self._account)
        prefix = "private." if self._account.private_endpoint else ""
        return f"https://{prefix}iam.{host}/identity/token"

    def get(self) -> str:
        if self._token is None or self._clock() > self._expires_at - 60:
            self._refresh()
        assert self._token is not None
        return self._token

    def invalidate(self) -> None:
        self._token = None

    def _refresh(self) -> None:
        try:
            data = self._http.request_json(
                "POST",
                self.url,
                form={
                    "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                    "apikey": self._account.token,
                },
            )
        except HttpError as exc:
            if exc.status in (400, 401, 403):
                raise NotConfiguredError(
                    f"IBM Cloud rejected the saved API key (HTTP {exc.status}); {SAVE_HINT}"
                ) from exc
            raise
        if not isinstance(data, dict) or not data.get("access_token"):
            raise ProviderError("IBM Cloud login returned no access token")
        self._token = str(data["access_token"])
        now = self._clock()
        if "expiration" in data:
            self._expires_at = float(data["expiration"])
        else:
            self._expires_at = now + float(data.get("expires_in", 3600))


def find_instances(account: Account, http: Http, token: IAMToken) -> list[tuple[str, str]]:
    """List (name, crn) of the account's Qiskit Runtime instances via IBM Cloud Global Search."""
    host = _cloud_host(account)
    url = f"https://api.global-search-tagging.{host}/v3/resources/search"
    found: list[tuple[str, str]] = []
    cursor: str | None = None
    for _ in range(20):  # 100 per page; far more instances than anyone has
        body: dict[str, Any] = {
            "query": "service_name:quantum-computing",
            "fields": ["crn", "name"],
        }
        if cursor:
            body["search_cursor"] = cursor
        data = http.request_json(
            "POST",
            url,
            params={"limit": "100"},
            headers={"Authorization": f"Bearer {token.get()}"},
            json_body=body,
        )
        items = data.get("items", []) if isinstance(data, dict) else []
        found += [(str(i.get("name", "")), str(i["crn"])) for i in items if i.get("crn")]
        cursor = data.get("search_cursor") if isinstance(data, dict) else None
        if not cursor or not items:
            break
    return found


def resolve_instances(account: Account, http: Http, token: IAMToken) -> list[str]:
    """CRNs to query: the configured CRN, the instance with the configured name, or all."""
    if account.instance and is_crn(account.instance):
        return [account.instance]
    instances = find_instances(account, http, token)
    if account.instance:
        matches = [crn for name, crn in instances if name == account.instance]
        if not matches:
            raise NotConfiguredError(
                f"no IBM Quantum instance named {account.instance!r} on this account"
            )
        return matches
    if not instances:
        raise NotConfiguredError("this IBM Cloud account has no IBM Quantum instances")
    return [crn for _, crn in instances]
