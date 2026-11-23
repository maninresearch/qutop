from datetime import UTC, datetime
from typing import Any

import pytest

from qutop.providers.base import ProviderError, Status
from qutop.providers.http import Http, Response
from qutop.providers.ibm import (
    API_VERSION,
    IBMProvider,
    median_2q_error,
    parse_backend_item,
    parse_calibration,
    status_from,
)
from qutop.providers.ibm_account import Account
from stubs import API, CRN, IAM, FakeTransport, ibm_routes, kingston_properties

ACCOUNT = Account(token="secret-api-key", instance=CRN)


def provider(transport: FakeTransport) -> IBMProvider:
    return IBMProvider(ACCOUNT, Http(transport=transport, sleep=lambda _: None))


def test_statuses_from_one_request() -> None:
    transport = FakeTransport(ibm_routes())
    statuses = provider(transport).statuses()
    assert [(s.name, s.status, s.pending_jobs) for s in statuses] == [
        ("ibm_kingston", Status.ONLINE, 12),
        ("ibm_torino", Status.MAINTENANCE, 0),
    ]
    gets = [r for r in transport.requests if r.method == "GET"]
    assert len(gets) == 1


def test_request_headers() -> None:
    transport = FakeTransport(ibm_routes())
    provider(transport).statuses()
    [get] = [r for r in transport.requests if r.method == "GET"]
    assert get.headers["Authorization"] == "Bearer iam-token"
    assert get.headers["Service-CRN"] == CRN
    assert get.headers["IBM-API-Version"] == API_VERSION
    assert get.headers["User-Agent"].startswith("qutop/")
    [login] = [r for r in transport.requests if r.method == "POST"]
    assert b"apikey=secret-api-key" in login.body
    assert "secret-api-key" not in login.url


def test_backends_summary() -> None:
    summaries = provider(FakeTransport(ibm_routes())).backends()
    kingston, torino = summaries
    assert kingston.num_qubits == 156
    assert kingston.median_2q_error is not None
    assert 0 < kingston.median_2q_error < 0.05
    assert kingston.calibrated_at == datetime(2026, 4, 15, 7, 15, 15, tzinfo=UTC)
    assert torino.status is Status.MAINTENANCE
    assert torino.num_qubits == 133
    assert torino.median_2q_error is None  # no calibration reported


def test_calibration_filed_under_requested_name() -> None:
    cal = provider(FakeTransport(ibm_routes())).calibration("ibm_kingston")
    assert cal is not None
    assert cal.backend == "ibm_kingston"
    assert len(cal.qubits) == 156


def test_calibration_for_unknown_backend() -> None:
    with pytest.raises(ProviderError, match="not available"):
        provider(FakeTransport(ibm_routes())).calibration("ibm_nowhere")


def test_token_reused_between_requests() -> None:
    transport = FakeTransport(ibm_routes())
    p = provider(transport)
    p.backends()
    p.statuses()
    assert sum(r.url == IAM for r in transport.requests) == 1


def test_expired_token_is_refreshed_once() -> None:
    calls = {"n": 0}

    def backends(_request: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            return Response(401, {}, b'{"message": "token expired"}')
        return {"devices": []}

    transport = FakeTransport(ibm_routes({("GET", f"{API}/backends"): backends}))
    assert provider(transport).statuses() == []
    assert sum(r.url == IAM for r in transport.requests) == 2


def test_server_error_becomes_provider_error() -> None:
    error = Response(503, {}, b'{"errors": [{"message": "service unavailable"}]}')
    transport = FakeTransport(ibm_routes({("GET", f"{API}/backends"): error}))
    with pytest.raises(ProviderError, match="HTTP 503 \\(service unavailable\\)"):
        provider(transport).statuses()


def test_instances_are_merged_without_duplicates() -> None:
    other = CRN.replace("inst-1", "inst-2").replace("us-east", "eu-de")
    eu_api = "https://eu-de.quantum.cloud.ibm.com/api/v1"
    routes = ibm_routes(
        {
            ("POST", "https://api.global-search-tagging.cloud.ibm.com/v3/resources/search"): {
                "items": [{"name": "a", "crn": CRN}, {"name": "b", "crn": other}]
            },
            ("GET", f"{eu_api}/backends"): {
                "devices": [
                    {"name": "ibm_kingston", "status": {"name": "online"}, "queue_length": 99},
                    {"name": "ibm_aachen", "status": {"name": "online"}, "queue_length": 3},
                ]
            },
        }
    )
    transport = FakeTransport(routes)
    p = IBMProvider(
        Account(token="k", instance=None), Http(transport=transport, sleep=lambda _: None)
    )
    names = [s.name for s in p.statuses()]
    assert names == ["ibm_kingston", "ibm_torino", "ibm_aachen"]


def test_backend_list_in_unexpected_format() -> None:
    transport = FakeTransport(ibm_routes({("GET", f"{API}/backends"): {"devices": "nope"}}))
    with pytest.raises(ProviderError, match="unexpected format"):
        provider(transport).statuses()


@pytest.mark.parametrize(
    ("item", "status", "message", "queue"),
    [
        (
            {"name": "a", "status": {"name": "online"}, "queue_length": 5},
            Status.ONLINE,
            "online",
            5,
        ),
        ({"name": "a", "status": "online"}, Status.ONLINE, "online", 0),
        (
            {"name": "a", "status": {"name": "paused"}, "queue_length": -1},
            Status.OFFLINE,
            "paused",
            0,
        ),
        (
            {"name": "a", "status": {"name": "offline", "reason": "Scheduled maintenance"}},
            Status.MAINTENANCE,
            "Scheduled maintenance",
            0,
        ),
    ],
)
def test_parse_backend_item(item: dict[str, Any], status: Status, message: str, queue: int) -> None:
    parsed = parse_backend_item(item)
    assert (parsed.status, parsed.status_message, parsed.pending_jobs) == (status, message, queue)


@pytest.mark.parametrize(
    ("state", "reason", "expected"),
    [
        ("online", "", Status.ONLINE),
        ("active", "", Status.ONLINE),
        ("maintenance", "", Status.MAINTENANCE),
        ("online", "maintenance soon", Status.MAINTENANCE),
        ("offline", "", Status.OFFLINE),
        ("internal", "", Status.OFFLINE),
    ],
)
def test_status_from(state: str, reason: str, expected: Status) -> None:
    assert status_from(state, reason) is expected


# Parsing calibration JSON


def test_parse_kingston_calibration() -> None:
    cal = parse_calibration(kingston_properties())
    assert cal.backend == "ibm_kingston"
    assert cal.calibrated_at == datetime(2026, 4, 15, 7, 15, 15, tzinfo=UTC)
    assert len(cal.qubits) == 156
    q0 = cal.qubits[0]
    assert q0.qubit == 0
    assert q0.t1_us == pytest.approx(384.16052477415343)
    assert q0.t2_us == pytest.approx(353.8457928146477)
    assert q0.readout_error == pytest.approx(0.009521484375)
    assert q0.sx_error is not None
    assert cal.edges
    assert {e.gate for e in cal.edges} == {"cz"}  # rzz is reported too, but not native


def gate(name: str, qubits: list[int], error: float) -> dict[str, Any]:
    return {
        "gate": name,
        "qubits": qubits,
        "parameters": [{"name": "gate_error", "value": error, "unit": ""}],
    }


def props(gates: list[dict[str, Any]], qubits: list[list[dict[str, Any]]] | None = None) -> Any:
    return {
        "backend_name": "ibm_test",
        "last_update_date": "2026-11-20T09:00:00Z",
        "qubits": qubits or [],
        "gates": gates,
    }


def test_2q_median_ignores_fractional_gates() -> None:
    gates = [
        gate("cz", [0, 1], 0.002),
        gate("cz", [1, 2], 0.004),
        gate("rzz", [0, 1], 0.5),
        gate("rzz", [1, 2], 0.5),
    ]
    assert median_2q_error(parse_calibration(props(gates))) == pytest.approx(0.003)


def test_2q_falls_back_to_ecr() -> None:
    cal = parse_calibration(props([gate("ecr", [0, 1], 0.007)]))
    assert [e.gate for e in cal.edges] == ["ecr"]


def test_2q_median_without_native_gate() -> None:
    assert median_2q_error(parse_calibration(props([]))) is None


def test_time_units_and_missing_values() -> None:
    qubits = [
        [
            {"name": "T1", "value": 0.2, "unit": "ms"},
            {"name": "T2", "value": 95000, "unit": "ns"},
        ],
        [{"name": "T1", "value": 1, "unit": "fortnights"}],
    ]
    cal = parse_calibration(props([], qubits))
    assert cal.qubits[0].t1_us == pytest.approx(200)
    assert cal.qubits[0].t2_us == pytest.approx(95)
    assert cal.qubits[0].readout_error is None
    assert cal.qubits[1].t1_us is None  # unknown unit: refuse to guess


def test_timestamp_is_utc() -> None:
    data = props([])
    data["last_update_date"] = "2026-11-20T11:00:00+02:00"
    assert parse_calibration(data).calibrated_at == datetime(2026, 11, 20, 9, tzinfo=UTC)
