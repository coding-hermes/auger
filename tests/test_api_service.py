from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

import auger

REAL_NAMESPACE_CHECK = auger._namespace


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


def test_bind_conflict_fails_loudly_with_address():
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        host, port = occupied.getsockname()
        with pytest.raises(SystemExit, match=rf"cannot bind {host}:{port}"):
            auger.serve(host, port)
