"""
Parity gate for the muster contract (AUG-066): docs/openapi.yaml vs the code.

The spec is a GENERATED artifact — this suite proves it never drifts:

  * byte parity  — regenerating via scripts/gen_openapi.py must reproduce the
    committed docs/openapi.yaml EXACTLY. If the code's registry moved and the
    file was not regenerated, the bytes differ and this fails (fail = spec
    drifted from code). A hand-edited spec fails the same way, which is the
    point: the generated file is the only form that passes.
  * registry parity — every operationId in the committed spec must map to a
    real, implemented, read-only operation in the generator's registry, which
    itself is built from auger.py's constants (the served route regex, the
    health path, the version, the limit bounds). No phantom operations, no
    missing read-only ones, no mutating verb anywhere, servers url pinned to
    the contracted http://127.0.0.1:8766, and the search path template must
    match the route `auger serve` actually dispatches on.

Offline by construction: importing `auger` touches no service, and nothing
here opens a connection.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    # Same conftest.py idiom as the rest of the suite: repo root on sys.path
    # instead of a pip install.
    sys.path.insert(0, str(REPO_ROOT))

from scripts.gen_openapi import (  # noqa: E402  (needs REPO_ROOT on sys.path first)
    API_BASE_URL,
    MUTATING_VERBS,
    READ_REGISTRY,
    build_spec,
)

SPEC_PATH = REPO_ROOT / "docs" / "openapi.yaml"
GENERATOR_PATH = REPO_ROOT / "scripts" / "gen_openapi.py"


def _load_committed() -> dict:
    assert SPEC_PATH.is_file(), (
        f"{SPEC_PATH} is missing — generate it: "
        "`python3 scripts/gen_openapi.py > docs/openapi.yaml`"
    )
    doc = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), "docs/openapi.yaml is not a mapping"
    return doc


def _operations(doc: dict) -> list[tuple[str, str, dict]]:
    ops = []
    for path, item in doc.get("paths", {}).items():
        for method, op in item.items():
            ops.append((method, path, op))
    return ops


# ---------------------------------------------------------------- byte parity
def test_spec_matches_generator_byte_for_byte():
    """The committed spec is EXACTLY what the generator emits today.

    Red arms this catches: a route/registry change in gen_openapi.py or
    auger.py without regenerating (spec drifted from code), and a hand edit
    of docs/openapi.yaml (the generated file is the only form that passes).
    """
    committed = SPEC_PATH.read_bytes()
    generated = build_spec()
    import scripts.gen_openapi as gen

    text = gen.render(generated)
    assert committed.decode("utf-8") == text, (
        "docs/openapi.yaml has drifted from scripts/gen_openapi.py's output — "
        "regenerate it: `python3 scripts/gen_openapi.py > docs/openapi.yaml`"
    )


# ------------------------------------------------------------ document shape
def test_spec_is_openapi_3_with_pinned_servers_url():
    doc = _load_committed()
    assert str(doc["openapi"]).startswith("3."), (
        f"not an OpenAPI 3.x document: {doc.get('openapi')!r}"
    )
    assert doc["servers"], "servers[] must not be empty"
    assert doc["servers"][0]["url"] == API_BASE_URL, (
        f"servers url drifted: {doc['servers'][0]['url']!r} != {API_BASE_URL!r}"
    )
    info = doc["info"]
    assert info["version"], "info.version is required"
    assert "title" in info


def test_every_operation_has_id_responses_and_safety_class():
    """OpenAPI 3.x validity floor plus the safety contract, on every operation:
    unique operationId, non-empty responses, x-safety present — and the class
    is read-only everywhere. Zero mutating operations is the contract."""
    doc = _load_committed()
    ops = _operations(doc)
    assert ops, "the spec declares no operations"
    seen = set()
    for method, path, op in ops:
        where = f"{method.upper()} {path}"
        assert "operationId" in op, f"{where} has no operationId"
        assert op["operationId"] not in seen, (
            f"duplicate operationId {op['operationId']!r}"
        )
        seen.add(op["operationId"])
        assert op.get("responses"), f"{where} has no responses"
        assert "x-safety" in op, f"{where} carries no safety classification"
        assert op["x-safety"] == "read-only", (
            f"{where} is classified {op['x-safety']!r}, not read-only"
        )
    assert seen == {oid for _, _, oid in READ_REGISTRY}, (
        "the spec's operationId set differs from the frozen registry"
    )


def test_no_mutating_verb_in_any_path_segment():
    doc = _load_committed()
    for path in doc.get("paths", {}):
        for segment in path.split("/"):
            assert segment not in MUTATING_VERBS, (
                f"mutating verb {segment!r} leaked into path {path}"
            )


def test_no_credentials_in_the_document():
    """The scope contract puts no credential in the document — probe the
    serialized bytes for the known shapes (DuckBrain tokens, OpenRouter keys,
    the token files). A key that walks in via a future description field dies
    here."""
    raw = SPEC_PATH.read_text(encoding="utf-8")
    for needle in (
        "sk-or-",
        "DUCKBRAIN_API_KEY",
        "OPENROUTER_API_KEY",
        "OR_API_KEY",
        "foreman-status.token",
        "x-api-key",
        "Authorization",
        "api_key:",
        "apiKey",
    ):
        assert needle not in raw, (
            f"credential-shaped literal {needle!r} appears in the spec"
        )


# ----------------------------------------------------------- registry parity
def test_operation_ids_map_to_real_registered_operations():
    """Every operationId must be a real, implemented operation in the
    generator's registry (phantom ops fail), and every registry operation
    must be documented (missing read-only ops fail)."""
    doc = _load_committed()
    spec_ids = {op["operationId"] for _, _, op in _operations(doc)}
    registry_ids = {oid for _, _, oid in READ_REGISTRY}
    phantom = sorted(spec_ids - registry_ids)
    missing = sorted(registry_ids - spec_ids)
    assert not phantom, f"phantom operations not in the registry: {phantom}"
    assert not missing, f"read-only operations missing from the spec: {missing}"


def test_search_path_template_matches_the_served_route():
    """The spec's search path is the route `auger serve` actually dispatches
    on: the template filled with a sample namespace must satisfy auger's own
    EMBEDDINGS_SEARCH_ROUTE regex."""
    import auger

    doc = _load_committed()
    template = next(
        (p for p in doc.get("paths", {}) if p.endswith("/embeddings/search")),
        None,
    )
    assert template is not None, "the embeddings search route is not documented"
    sample = template.replace("{namespace}", "parity-probe-ns")
    assert auger.EMBEDDINGS_SEARCH_ROUTE.match(sample), (
        f"spec path {template!r} does not match the served route "
        f"({auger.EMBEDDINGS_SEARCH_ROUTE.pattern!r})"
    )


def test_health_path_matches_the_served_route():
    import auger

    doc = _load_committed()
    assert auger.EMBEDDINGS_HEALTH_ROUTE in doc.get("paths", {}), (
        f"{auger.EMBEDDINGS_HEALTH_ROUTE!r} (the served health route) is not documented"
    )


def test_bounds_are_the_code_bounds():
    """The limit bounds in the spec are read from auger.py at generation
    time; assert they still agree (a bounds change without regeneration is
    caught by byte parity, this catches an edit to the file alone)."""
    import auger

    doc = _load_committed()
    search = doc["paths"]["/api/ns/{namespace}/embeddings/search"]["get"]
    limit = next(p for p in search["parameters"] if p["name"] == "limit")
    assert limit["schema"]["maximum"] == auger.EMBEDDINGS_MAX_LIMIT
    assert limit["schema"]["default"] == auger.EMBEDDINGS_DEFAULT_LIMIT
    check = doc["paths"]["/api/ns/{namespace}/check"]["post"]
    check_schema = check["requestBody"]["content"]["application/json"]["schema"]
    if "$ref" in check_schema:  # resolve through components
        name = check_schema["$ref"].split("/")[-1]
        check_schema = doc["components"]["schemas"][name]
    check_limit = check_schema["properties"]["limit"]
    assert check_limit["default"] == auger.EMBEDDINGS_DEFAULT_LIMIT


def test_generator_carries_the_readonly_self_check():
    """The parity rule lives in the generator too: the script's own self-check
    must refuse a registry whose method set leaves the read set — the last
    line of defense before a mutating operation can be emitted at all."""
    gen_src = GENERATOR_PATH.read_text(encoding="utf-8")
    assert "def _self_check(" in gen_src, (
        "scripts/gen_openapi.py lost its _self_check gate"
    )
    assert 'raise SystemExit("self-check: ' in gen_src, (
        "scripts/gen_openapi.py's _self_check no longer raises"
    )


def test_generator_and_served_api_share_one_base_url():
    import auger
    from scripts import gen_openapi

    assert gen_openapi.API_BASE_URL == auger.AUGER_API_BASE_URL
    assert (
        gen_openapi.API_BASE_URL == f"http://127.0.0.1:{auger.AUGER_API_DEFAULT_PORT}"
    )
