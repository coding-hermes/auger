from __future__ import annotations

import json
import socket
import struct
import threading
import time
import urllib.error
import urllib.request

import pytest

import auger

REAL_NAMESPACE_CHECK = auger._namespace
REAL_NAMESPACE_STATUS = auger.namespace_status


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(auger, "_namespace", lambda ns: None)
    monkeypatch.setattr(
        auger,
        "namespace_status",
        lambda ns, pid: {
            "namespace": ns,
            "project_id": "P-1",
            "name": "sample",
            "status": "open",
            "counts": {
                "decisions": 1,
                "options": 2,
                "escalations": 0,
                "unknowns": 0,
                "domains": 1,
            },
            "confidence": {"min": 0.8, "mean": 0.8, "max": 0.8},
            "pending_memory_warnings": [],
            "thin_decisions": [],
        },
    )
    monkeypatch.setattr(
        auger,
        "embedding_status",
        lambda: {
            "tier": "semantic",
            "healthy": True,
            "provider": "fixture",
            "model": "embed",
        },
    )
    monkeypatch.setattr(auger, "render_dump", lambda *a: "configuration: sample")
    monkeypatch.setattr(auger, "render_export", lambda *a: "export: sample")
    monkeypatch.setattr(
        auger,
        "recall",
        lambda *a, **k: [
            {
                "key": "/auger/P-1/seed",
                "score": 0.9,
                "content": "sample evidence",
                "timestamp": "2026-01-01",
            }
        ],
    )
    monkeypatch.setattr(
        auger,
        "embedding_search",
        lambda *a, **k: {
            "namespace": "sample",
            "query": "sample",
            "limit": 5,
            "project": None,
            "tier": "semantic",
            "embedding_healthy": True,
            "count": 1,
            "results": [
                {
                    "key": "/auger/P-1/seed",
                    "kind": "seed",
                    "decision_id": None,
                    "score": 0.9,
                    "domain": None,
                    "content": "sample",
                    "snippet": "sample",
                    "timestamp": None,
                }
            ],
        },
    )
    monkeypatch.setattr(
        auger,
        "select",
        lambda *a, **k: [
            {
                "id": "V-1",
                "project_id": "P-1",
                "config_summary": "sample",
                "verdict": "good",
                "judged_by": "human",
                "created_at": "2026-01-01",
            }
        ],
    )
    monkeypatch.setattr(auger, "jev", lambda *a, **k: (None, "unreachable"))
    monkeypatch.setattr(auger, "check_verdict", lambda *a: "ALREADY ANSWERED")
    server = auger.APIServer(("127.0.0.1", 0), auger.APIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def request(api, path, data=None):
    req = urllib.request.Request(
        api + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return (
                response.status,
                response.headers.get_content_type(),
                response.read().decode(),
            )
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get_content_type(), exc.read().decode()


def test_all_eight_contract_operations_return_data(api):
    cases = [
        ("/health", None),
        ("/api/ns/demo/status", None),
        ("/api/ns/demo/dump", None),
        ("/api/ns/demo/export", None),
        ("/api/ns/demo/check", {"question": "sample question"}),
        ("/api/ns/demo/recall?q=sample", None),
        ("/api/ns/demo/embeddings/search?q=sample", None),
        ("/api/ns/demo/verdicts", None),
    ]
    for path, data in cases:
        status, _content_type, body = request(api, path, data)
        assert status == 200, (path, body)
        assert body, (path, body)
        if path != "/health":
            assert "sample" in body, (path, body)
    check = json.loads(
        request(api, "/api/ns/demo/check", {"question": "sample question"})[2]
    )
    assert check["verdict"] == "UNKNOWN" and check["jev_used"] is False


def test_specs_route_serves_active_blocks_without_project_lookup(api, monkeypatch):
    monkeypatch.setattr(
        auger,
        "spec_http_payload",
        lambda block_id=None: {
            "root": "specs",
            "count": 1,
            "blocks": [
                {
                    "id": "SPEC-1",
                    "kind": "requirement",
                    "status": "active",
                    "text": "build it",
                }
            ],
        },
    )
    status, content_type, body = request(api, "/api/ns/demo/specs")
    assert status == 200 and content_type == "application/json"
    assert json.loads(body)["blocks"][0]["id"] == "SPEC-1"


def test_spec_http_payload_exposes_the_shipped_active_corpus():
    payload = auger.spec_http_payload()
    assert payload["count"] == len(payload["blocks"]) > 0
    assert all(
        block["status"] == "active" and block["text"] for block in payload["blocks"]
    )


def test_check_returns_bounded_unknown_when_retrieval_times_out(api, monkeypatch):
    monkeypatch.setattr(
        auger,
        "recall",
        lambda *a, **k: (_ for _ in ()).throw(auger.SubstrateError("timed out")),
    )
    status, _content_type, body = request(
        api, "/api/ns/demo/check", {"question": "sample question"}
    )
    payload = json.loads(body)
    assert status == 200
    assert payload["verdict"] == "UNKNOWN" and payload["jev_used"] is False


def test_check_cost_matches_string_schema(api, monkeypatch):
    monkeypatch.setattr(
        auger,
        "jev",
        lambda *a, **k: (
            {"answers": {"already_answered": {"noul": 0.5}}, "usage": {"cost": 0.012}},
            None,
        ),
    )
    status, _content_type, body = request(
        api, "/api/ns/demo/check", {"question": "sample question"}
    )
    payload = json.loads(body)
    assert status == 200
    assert payload["jev_cost"] == "0.012"
    assert isinstance(payload["jev_cost"], str)


def test_unknown_namespace_is_typed_404(api, monkeypatch):
    monkeypatch.setattr(auger, "_namespace", REAL_NAMESPACE_CHECK)
    monkeypatch.setattr(
        auger,
        "select",
        lambda *a, **k: (_ for _ in ()).throw(
            SystemExit("namespace 'missing' does not exist — run: auger init first")
        ),
    )
    status, content_type, body = request(api, "/api/ns/missing/status")
    assert (status, content_type) == (404, "application/json")
    assert "does not exist" in json.loads(body)["error"]


def test_unreachable_substrate_fails_closed(api, monkeypatch):
    monkeypatch.setattr(
        auger,
        "namespace_status",
        lambda *a: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    status, content_type, body = request(api, "/api/ns/demo/status")
    assert status in (502, 503) and content_type == "application/json"
    assert "error" in json.loads(body)


def test_status_answers_an_unreachable_substrate_as_502_not_404(api, monkeypatch):
    """AUG-101: a dead store is not a missing namespace.

    `namespace_status` turns `_project`'s SystemExit into 404 — "run: `auger init` first".
    `SubstrateError` ("the store could not be asked") is a SystemExit SUBCLASS, so without its own
    branch the API answered 404 to a caller whose namespace is perfectly fine while the substrate
    is DOWN: the error-shape-dependent contract the row is about, on the HTTP surface. 502 is the
    code this route's own OpenAPI entry documents for a substrate failure.
    """
    monkeypatch.setattr(auger, "namespace_status", REAL_NAMESPACE_STATUS)
    monkeypatch.setattr(
        auger,
        "select",
        lambda *a, **k: (_ for _ in ()).throw(
            auger.substrate_read_failure(
                "demo", "select project", 0, {"error": "transport: connection refused"}
            )
        ),
    )
    status, content_type, body = request(api, "/api/ns/demo/status")
    assert (status, content_type) == (502, "application/json"), body
    assert auger.FAIL_CLOSED_SENTENCE in json.loads(body)["error"], body
    assert "auger init" not in body, body


def test_bind_conflict_fails_loudly_with_address():
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        host, port = occupied.getsockname()
        with pytest.raises(SystemExit, match=rf"cannot bind {host}:{port}"):
            auger.serve(host, port)


# ---------------------------------------------------------------- AUG-212: a mid-response hang-up is silent
# A client that aborts during the response write must leave NO phantom 5xx access-log
# line and NO traceback: a fallback status written to the socket the peer already
# closed is a line nobody received, and a chained traceback for a peer who is gone is
# noise, not a failure. Both harnesses block the route's substrate call on an Event, so
# the server's write is guaranteed to happen only AFTER the client has aborted with a
# TCP RST (SO_LINGER 0) — no timing race in the assertion.


def _rst_abort(request_line: bytes):
    """Return a function that connects, sends `request_line`, and aborts with an RST."""

    def connect(port: int, entered: threading.Event, release: threading.Event):
        conn = socket.create_connection(("127.0.0.1", port), timeout=5)
        conn.sendall(request_line)
        assert entered.wait(5), "server never entered the route"
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        conn.close()  # SO_LINGER 0 forces an RST: the peer's next write/read fails
        release.set()

    return connect


def _assert_no_phantom_5xx_no_traceback(capsys):
    err = capsys.readouterr().err
    assert "Traceback" not in err, err
    assert '" 502 ' not in err and '" 500 ' not in err and '" 503 ' not in err, err


def test_api_client_hangup_mid_response_is_silent(capsys, monkeypatch):
    """AUG-212 (api surface): a hang-up during the /status write is fully silent."""
    monkeypatch.setattr(auger, "_namespace", lambda ns: None)
    entered, release = threading.Event(), threading.Event()

    def blocking_status(ns, pid=None):
        entered.set()
        release.wait(5)
        return {"namespace": ns}

    monkeypatch.setattr(auger, "namespace_status", blocking_status)
    server = auger.APIServer(("127.0.0.1", 0), auger.APIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connect = _rst_abort(b"GET /api/ns/demo/status HTTP/1.1\r\nHost: x\r\n\r\n")
        connect(server.server_address[1], entered, release)
        time.sleep(0.5)  # let the request thread hit the closed socket and settle
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    _assert_no_phantom_5xx_no_traceback(capsys)


def test_serve_client_hangup_mid_response_is_silent(capsys, monkeypatch):
    """AUG-212 (serve surface): the embeddings route answers a hang-up in silence."""
    monkeypatch.setattr(
        auger,
        "embedding_status",
        lambda: {
            "tier": "semantic",
            "healthy": True,
            "provider": "fixture",
            "model": "e",
        },
    )
    entered, release = threading.Event(), threading.Event()

    def blocking_search(ns, q, limit, project_id=None):
        entered.set()
        release.wait(5)
        return {"namespace": ns, "count": 0, "results": []}

    monkeypatch.setattr(auger, "embedding_search", blocking_search)
    server = auger.EmbeddingsHTTPServer(
        ("127.0.0.1", 0), auger.EmbeddingsHTTPHandler, namespace="demo"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connect = _rst_abort(
            b"GET /api/ns/demo/embeddings/search?q=x HTTP/1.1\r\nHost: x\r\n\r\n"
        )
        connect(server.server_address[1], entered, release)
        time.sleep(0.5)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    _assert_no_phantom_5xx_no_traceback(capsys)
