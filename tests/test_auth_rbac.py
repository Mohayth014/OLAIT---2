"""
Two-portal role-based access control acceptance tests.

Covers the 8 acceptance criteria from the RBAC spec:
1. Inspector token can POST /api/scan successfully.
2. Manager token gets 403 on POST /api/scan.
3. Inspector A's GET /api/inspections does NOT include inspector B's scan.
4. Inspector A calling GET /api/inspections/{inspector B's scan id} directly
   returns 403 (BOLA check -- independent of the list-level filter).
5. Manager's GET /api/inspections returns both inspectors' scans.
6. Manager can override a verdict with a reason; inspector attempting the
   same call gets 403; an empty reason is rejected.
7. GET /api/dashboard/metrics is 403 for inspector, 200 for manager.
8. (Covered by running the full `pytest tests/` suite alongside this file.)

Uses the seeded demo accounts created by backend.database.db.init_db():
inspector1@trace.gov / inspector2@trace.gov / manager1@trace.gov.
"""
import pytest
from fastapi.testclient import TestClient

from backend.app import app

DATASET_A = "20260904_152024.jpg.jpeg"
DATASET_B = "20260904_152054.jpg.jpeg"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _login(client, email, password):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text}"
    body = r.json()
    assert body["role"] in ("inspector", "manager")
    return body["access_token"]


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def ctx(client):
    """One-time setup shared by every acceptance test below: three logged-in
    demo users plus one scan each from the two inspector accounts."""
    insp1 = _login(client, "inspector1@trace.gov", "Inspector@123")
    insp2 = _login(client, "inspector2@trace.gov", "Inspector@123")
    mgr = _login(client, "manager1@trace.gov", "Manager@123")

    r_a = client.post("/api/scan", data={"dataset_filename": DATASET_A}, headers=_hdr(insp1))
    assert r_a.status_code == 200, r_a.text
    id_a = r_a.json()["inspection_id"]
    assert r_a.json()["inspector_id"] == "u-insp-1"

    r_b = client.post("/api/scan", data={"dataset_filename": DATASET_B}, headers=_hdr(insp2))
    assert r_b.status_code == 200, r_b.text
    id_b = r_b.json()["inspection_id"]
    assert r_b.json()["inspector_id"] == "u-insp-2"

    return {"insp1": insp1, "insp2": insp2, "mgr": mgr, "id_a": id_a, "id_b": id_b}


def test_ac1_inspector_can_scan(client, ctx):
    r = client.post("/api/scan", data={"dataset_filename": DATASET_A}, headers=_hdr(ctx["insp1"]))
    print(f"\n[AC1] inspector POST /api/scan -> HTTP {r.status_code}")
    assert r.status_code == 200
    assert r.json()["overall_status"] in ("COMPLIANT", "NON_COMPLIANT", "REVIEW_REQUIRED")


def test_ac2_manager_cannot_scan(client, ctx):
    r = client.post("/api/scan", data={"dataset_filename": DATASET_A}, headers=_hdr(ctx["mgr"]))
    print(f"\n[AC2] manager POST /api/scan -> HTTP {r.status_code} body={r.json()}")
    assert r.status_code == 403


def test_ac3_inspector_list_is_own_only(client, ctx):
    r = client.get("/api/inspections", headers=_hdr(ctx["insp1"]))
    assert r.status_code == 200
    ids = {row["inspection_id"] for row in r.json()}
    print(f"\n[AC3] inspector1 list size={len(ids)} "
          f"contains own(id_a)={ctx['id_a'] in ids} contains other's(id_b)={ctx['id_b'] in ids}")
    assert ctx["id_a"] in ids
    assert ctx["id_b"] not in ids


def test_ac4_bola_direct_id_access_is_forbidden(client, ctx):
    r = client.get(f"/api/inspections/{ctx['id_b']}", headers=_hdr(ctx["insp1"]))
    print(f"\n[AC4] inspector1 GET /api/inspections/{{inspector2's id}} -> HTTP {r.status_code} body={r.json()}")
    assert r.status_code == 403
    # Sanity: the same record IS visible to its own owner.
    r_owner = client.get(f"/api/inspections/{ctx['id_b']}", headers=_hdr(ctx["insp2"]))
    print(f"[AC4] inspector2 GET their own record -> HTTP {r_owner.status_code}")
    assert r_owner.status_code == 200


def test_ac5_manager_list_includes_all_inspectors(client, ctx):
    r = client.get("/api/inspections", headers=_hdr(ctx["mgr"]))
    assert r.status_code == 200
    ids = {row["inspection_id"] for row in r.json()}
    print(f"\n[AC5] manager list size={len(ids)} contains id_a={ctx['id_a'] in ids} contains id_b={ctx['id_b'] in ids}")
    assert ctx["id_a"] in ids
    assert ctx["id_b"] in ids

    # Manager's optional inspector_id filter narrows to one inspector.
    r_filtered = client.get("/api/inspections", params={"inspector_id": "u-insp-1"}, headers=_hdr(ctx["mgr"]))
    filtered_ids = {row["inspection_id"] for row in r_filtered.json()}
    print(f"[AC5] manager filtered by inspector_id=u-insp-1 -> contains id_a={ctx['id_a'] in filtered_ids} "
          f"contains id_b={ctx['id_b'] in filtered_ids}")
    assert ctx["id_a"] in filtered_ids
    assert ctx["id_b"] not in filtered_ids


def test_ac6_override_is_manager_only_and_requires_reason(client, ctx):
    body = {"new_status": "NON_COMPLIANT", "reason": "Physical re-inspection found undeclared MRP tampering."}

    r_inspector = client.post(f"/api/inspections/{ctx['id_a']}/override", json=body, headers=_hdr(ctx["insp1"]))
    print(f"\n[AC6] inspector attempts override -> HTTP {r_inspector.status_code} body={r_inspector.json()}")
    assert r_inspector.status_code == 403

    r_manager = client.post(f"/api/inspections/{ctx['id_a']}/override", json=body, headers=_hdr(ctx["mgr"]))
    print(f"[AC6] manager overrides with reason -> HTTP {r_manager.status_code} "
          f"new_status={r_manager.json().get('inspection', {}).get('overall_status')}")
    assert r_manager.status_code == 200
    assert r_manager.json()["inspection"]["overall_status"] == "NON_COMPLIANT"
    history = r_manager.json()["inspection"]["override_history"]
    assert len(history) == 1
    assert history[0]["manager_id"] == "u-mgr-1"
    assert history[0]["reason"] == body["reason"]

    r_empty_reason = client.post(
        f"/api/inspections/{ctx['id_a']}/override",
        json={"new_status": "COMPLIANT", "reason": "   "},
        headers=_hdr(ctx["mgr"]),
    )
    print(f"[AC6] manager overrides with blank reason -> HTTP {r_empty_reason.status_code}")
    assert r_empty_reason.status_code == 400

    # The override must be a distinct code path from the inspector's own review:
    # confirm officer_verification (the review record) is untouched by the override.
    detail = client.get(f"/api/inspections/{ctx['id_a']}", headers=_hdr(ctx["mgr"])).json()
    assert detail["officer_verification"] is None
    assert len(detail["override_history"]) == 1


def test_ac7_dashboard_metrics_is_manager_only(client, ctx):
    r_insp = client.get("/api/dashboard/metrics", headers=_hdr(ctx["insp1"]))
    r_mgr = client.get("/api/dashboard/metrics", headers=_hdr(ctx["mgr"]))
    print(f"\n[AC7] inspector -> HTTP {r_insp.status_code} | manager -> HTTP {r_mgr.status_code}")
    assert r_insp.status_code == 403
    assert r_mgr.status_code == 200
    assert "total_inspections" in r_mgr.json()


def test_rules_read_both_roles_write_manager_only(client, ctx):
    """Not one of the 8 numbered criteria, but explicitly in the permission table."""
    r_insp = client.get("/api/rules", headers=_hdr(ctx["insp1"]))
    r_mgr = client.get("/api/rules", headers=_hdr(ctx["mgr"]))
    assert r_insp.status_code == 200
    assert r_mgr.status_code == 200
    rule_id = r_mgr.json()[0]["rule_id"]

    body = {"rule_id": rule_id, "enabled": True, "severity": "MAJOR", "required": True}
    r_insp_put = client.put(f"/api/rules/{rule_id}", json=body, headers=_hdr(ctx["insp1"]))
    r_mgr_put = client.put(f"/api/rules/{rule_id}", json=body, headers=_hdr(ctx["mgr"]))
    print(f"\n[rules] inspector PUT -> HTTP {r_insp_put.status_code} | manager PUT -> HTTP {r_mgr_put.status_code}")
    assert r_insp_put.status_code == 403
    assert r_mgr_put.status_code == 200


def test_reports_are_owner_gated_like_the_record(client, ctx):
    """Reports/exports: inspector own-only, manager any (per the permission table)."""
    r_other = client.get(f"/api/reports/{ctx['id_b']}/html", headers=_hdr(ctx["insp1"]))
    r_own = client.get(f"/api/reports/{ctx['id_b']}/html", headers=_hdr(ctx["insp2"]))
    r_mgr = client.get(f"/api/reports/{ctx['id_b']}/export", params={"format": "json"}, headers=_hdr(ctx["mgr"]))
    print(f"\n[reports] inspector1 on B's report -> HTTP {r_other.status_code} | "
          f"inspector2 (owner) -> HTTP {r_own.status_code} | manager export -> HTTP {r_mgr.status_code}")
    assert r_other.status_code == 403
    assert r_own.status_code == 200
    assert r_mgr.status_code == 200


def test_unauthenticated_requests_are_rejected(client):
    r = client.get("/api/inspections")
    print(f"\n[auth] no token on /api/inspections -> HTTP {r.status_code}")
    assert r.status_code == 401

    r_bad = client.get("/api/dashboard/metrics", headers={"Authorization": "Bearer not-a-real-token"})
    print(f"[auth] garbage token -> HTTP {r_bad.status_code}")
    assert r_bad.status_code == 401
