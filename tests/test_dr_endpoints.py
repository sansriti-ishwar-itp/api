"""End-to-end API tests against the FastAPI app in mock mode.

These run against a real `TestClient` (so middleware + lifespan + DB engine
all execute) and validate the public contract of the DR control plane.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient


def _wait_for_job(client: TestClient, job_id: str, *, timeout_s: float = 10.0) -> dict:
    deadline = time.time() + timeout_s
    last: dict = {}
    while time.time() < deadline:
        resp = client.get(f"/v1/dr/jobs/{job_id}")
        assert resp.status_code == 200, resp.text
        last = resp.json()
        if last["status"] in ("completed", "failed", "aborted"):
            return last
        time.sleep(0.05)
    pytest.fail(f"DR job {job_id} did not finish in {timeout_s}s; last={last}")


def _register(client: TestClient, *, name: str = "vm-1", external_id: str = "ext-1") -> dict:
    resp = client.post(
        "/v1/vms",
        json={"name": name, "external_id": external_id, "rto_minutes": 15},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_health_endpoints(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/health/live").json()["status"] == "ok"
    body = client.get("/health/ready").json()
    assert body["status"] == "ready"
    assert body["adapter"] == "mock"


def test_register_and_list_vm(client: TestClient) -> None:
    body = _register(client)
    assert body["state"] == "healthy"

    resp = client.get("/v1/vms")
    assert resp.status_code == 200
    assert any(v["external_id"] == "ext-1" for v in resp.json())


def test_register_duplicate_external_id_conflicts(client: TestClient) -> None:
    _register(client, external_id="dup")
    resp = client.post(
        "/v1/vms", json={"name": "v2", "external_id": "dup", "rto_minutes": 5}
    )
    assert resp.status_code == 409


def test_dr_pipeline_completes(client: TestClient) -> None:
    # Pre-seed the mock adapter with a server whose external_id we'll register.
    from app.adapters.factory import build_adapter
    adapter = build_adapter()
    info = adapter.seed_server("ext-dr-1", name="src")  # type: ignore[attr-defined]
    _register(client, external_id=info.id)

    # Find the registered VM id.
    vms = client.get("/v1/vms").json()
    vm = next(v for v in vms if v["external_id"] == info.id)

    resp = client.post(f"/v1/vms/{vm['id']}/dr/trigger", json={"reason": "test"})
    assert resp.status_code == 202, resp.text
    assert resp.headers.get("Location", "").startswith("/v1/dr/jobs/")
    job_id = resp.json()["id"]

    final = _wait_for_job(client, job_id)
    assert final["status"] == "completed", final
    assert final["new_external_id"]
    assert final["snapshot_id"]
    assert final["rto_breached"] is False


def test_dr_trigger_idempotent(client: TestClient) -> None:
    from app.adapters.factory import build_adapter
    adapter = build_adapter()
    info = adapter.seed_server("ext-dr-2", name="src2")  # type: ignore[attr-defined]
    _register(client, external_id=info.id)
    vms = client.get("/v1/vms").json()
    vm = next(v for v in vms if v["external_id"] == info.id)

    first = client.post(f"/v1/vms/{vm['id']}/dr/trigger", json={})
    assert first.status_code == 202
    second = client.post(f"/v1/vms/{vm['id']}/dr/trigger", json={})
    # In-flight replay returns 200 with the SAME job id and an Idempotent-Replay marker.
    assert second.status_code in (200, 202)
    if second.status_code == 200:
        assert second.headers.get("Idempotent-Replay") == "true"
        assert second.json()["id"] == first.json()["id"]


def test_dr_trigger_unknown_vm_404(client: TestClient) -> None:
    resp = client.post("/v1/vms/does-not-exist/dr/trigger", json={})
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_health_check_flow_marks_suspect_then_failing(client: TestClient) -> None:
    from app.adapters.factory import build_adapter
    adapter = build_adapter()
    info = adapter.seed_server("ext-health-1", name="hp")  # type: ignore[attr-defined]
    _register(client, external_id=info.id)
    vms = client.get("/v1/vms").json()
    vm = next(v for v in vms if v["external_id"] == info.id)

    # First probe is healthy.
    r1 = client.post(f"/v1/vms/{vm['id']}/health-check")
    assert r1.status_code == 200
    assert r1.json()["healthy"] is True

    # Mark unhealthy and probe 4 times.
    adapter.mark_unhealthy(info.id)  # type: ignore[attr-defined]
    for _ in range(4):
        client.post(f"/v1/vms/{vm['id']}/health-check")

    detail = client.get(f"/v1/vms/{vm['id']}").json()
    assert detail["state"] in ("suspect", "failing")
    assert detail["failure_count"] >= 1


def test_request_id_echoed_in_header_and_audit(client: TestClient) -> None:
    from app.adapters.factory import build_adapter
    adapter = build_adapter()
    adapter.seed_server("ext-rid-1", name="rid")  # type: ignore[attr-defined]
    resp = client.post(
        "/v1/vms",
        json={"name": "rid", "external_id": "ext-rid-1", "rto_minutes": 15},
        headers={"X-Request-ID": "fixed-rid-123"},
    )
    assert resp.status_code == 201
    assert resp.headers.get("X-Request-ID") == "fixed-rid-123"

    audit = client.get("/v1/audit").json()
    assert any(e.get("request_id") == "fixed-rid-123" for e in audit)


def test_snapshot_endpoint(client: TestClient) -> None:
    from app.adapters.factory import build_adapter
    adapter = build_adapter()
    info = adapter.seed_server("ext-snap-1", name="snap")  # type: ignore[attr-defined]
    _register(client, external_id=info.id)
    vms = client.get("/v1/vms").json()
    vm = next(v for v in vms if v["external_id"] == info.id)

    resp = client.post(f"/v1/vms/{vm['id']}/snapshot", json={"reason": "manual"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["external_id"].startswith("snap-")

    listing = client.get(f"/v1/vms/{vm['id']}/snapshots").json()
    assert any(s["external_id"] == body["external_id"] for s in listing)


def test_metrics_endpoint(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert b"http_request" in resp.content or b"python_info" in resp.content
