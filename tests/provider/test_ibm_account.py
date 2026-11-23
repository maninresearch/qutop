import json
from pathlib import Path
from typing import Any

import pytest

from qutop.providers.base import NotConfiguredError
from qutop.providers.http import Http, Response
from qutop.providers.ibm_account import (
    Account,
    IAMToken,
    api_base,
    load_account,
    resolve_instances,
)
from stubs import CRN, IAM, SEARCH, FakeTransport


def write(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "qiskit-ibm.json"
    path.write_text(json.dumps(data))
    return path


def entry(token: str, **extra: Any) -> dict[str, Any]:
    return {
        "channel": "ibm_quantum_platform",
        "token": token,
        "url": "https://cloud.ibm.com",
        **extra,
    }


def test_default_account_wins(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        {
            "default-ibm-quantum-platform": entry("a"),
            "work": entry("b", instance=CRN, is_default_account=True),
        },
    )
    account = load_account(path, env={})
    assert account.token == "b"
    assert account.instance == CRN


def test_default_name_used_without_flag(tmp_path: Path) -> None:
    path = write(tmp_path, {"zzz": entry("a"), "default-ibm-quantum-platform": entry("b")})
    assert load_account(path, env={}).token == "b"


def test_named_account(tmp_path: Path) -> None:
    path = write(tmp_path, {"one": entry("a"), "two": entry("b")})
    assert load_account(path, name="two", env={}).token == "b"
    with pytest.raises(NotConfiguredError, match="named 'three'"):
        load_account(path, name="three", env={})


def test_environment_overrides_file(tmp_path: Path) -> None:
    path = write(tmp_path, {"default": entry("file")})
    env = {"QISKIT_IBM_TOKEN": "env", "QISKIT_IBM_INSTANCE": CRN}
    account = load_account(path, env=env)
    assert (account.token, account.instance) == ("env", CRN)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(NotConfiguredError, match="no saved IBM Quantum account"):
        load_account(tmp_path / "nope.json", env={})


def test_retired_channel(tmp_path: Path) -> None:
    path = write(tmp_path, {"old": {"channel": "ibm_quantum", "token": "x"}})
    with pytest.raises(NotConfiguredError, match="retired"):
        load_account(path, env={})


def test_broken_file(tmp_path: Path) -> None:
    path = tmp_path / "qiskit-ibm.json"
    path.write_text("{not json")
    with pytest.raises(NotConfiguredError, match="cannot read"):
        load_account(path, env={})


def test_token_never_in_repr() -> None:
    assert "s3cret" not in repr(Account(token="s3cret", instance=None))


@pytest.mark.parametrize(
    ("crn", "private", "expected"),
    [
        (CRN, False, "https://quantum.cloud.ibm.com/api/v1"),
        (CRN.replace("us-east", "eu-de"), False, "https://eu-de.quantum.cloud.ibm.com/api/v1"),
        (CRN, True, "https://private.us-east.quantum.cloud.ibm.com/api/v1"),
    ],
)
def test_api_base(crn: str, private: bool, expected: str) -> None:
    assert api_base(Account("k", crn, private_endpoint=private), crn) == expected


def test_bad_crn() -> None:
    with pytest.raises(NotConfiguredError, match="not a valid CRN"):
        api_base(Account("k", None), "crn:v1")


def http(transport: FakeTransport) -> Http:
    return Http(transport=transport, sleep=lambda _: None)


def test_iam_token_cached_until_near_expiry() -> None:
    now = {"t": 1000.0}
    transport = FakeTransport({("POST", IAM): {"access_token": "t1", "expires_in": 3600}})
    token = IAMToken(Account("k", CRN), http(transport), clock=lambda: now["t"])
    assert token.get() == "t1"
    now["t"] += 3000
    token.get()
    assert len(transport.requests) == 1
    now["t"] += 560  # within a minute of expiry
    token.get()
    assert len(transport.requests) == 2


def test_rejected_api_key() -> None:
    transport = FakeTransport({("POST", IAM): Response(400, {}, b'{"errorMessage": "bad key"}')})
    with pytest.raises(NotConfiguredError, match="rejected the saved API key"):
        IAMToken(Account("k", CRN), http(transport)).get()


def resolve(instance: str | None, items: list[dict[str, str]]) -> list[str]:
    transport = FakeTransport(
        {
            ("POST", IAM): {"access_token": "t", "expires_in": 3600},
            ("POST", SEARCH): {"items": items},
        }
    )
    account = Account("k", instance)
    return resolve_instances(account, http(transport), IAMToken(account, http(transport)))


def test_crn_needs_no_lookup() -> None:
    assert resolve(CRN, []) == [CRN]


def test_instance_by_name() -> None:
    items = [{"name": "a", "crn": "crn:a"}, {"name": "b", "crn": "crn:b"}]
    assert resolve("b", items) == ["crn:b"]
    with pytest.raises(NotConfiguredError, match="no IBM Quantum instance named 'c'"):
        resolve("c", items)


def test_all_instances_when_none_set() -> None:
    items = [{"name": "a", "crn": "crn:a"}, {"name": "b", "crn": "crn:b"}]
    assert resolve(None, items) == ["crn:a", "crn:b"]
    with pytest.raises(NotConfiguredError, match="no IBM Quantum instances"):
        resolve(None, [])
