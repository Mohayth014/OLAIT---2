"""
Officer inspection-session workflow acceptance tests.

Covers the additive session/product layer on top of the existing single-scan
pipeline: jurisdiction round-tripping, session/product creation, sample-scan
linking via POST /api/scan, session-summary aggregation (pass/review/non-
compliant counts computed purely from each linked sample's overall_status),
and BOLA checks mirroring test_auth_rbac.py's pattern for the new endpoints.

Uses the seeded demo accounts created by backend.database.db.init_db():
inspector1@trace.gov / inspector2@trace.gov / manager1@trace.gov.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.database.db import get_connection

DATASET_A = "20260904_152024.jpg.jpeg"


@pytest.fixture(scope="module", autouse=True)
def preserve_officer_jurisdictions():
    """These tests run against the shared dev database, and several of them set or
    clear an officer's jurisdiction. Snapshot it up front and put it back afterwards
    so running the suite never disturbs a real officer's saved profile."""
    conn = get_connection()
    saved = {r["id"]: r["jurisdiction"] for r in conn.execute("SELECT id, jurisdiction FROM users")}
    conn.close()
    yield
    conn = get_connection()
    for user_id, jurisdiction in saved.items():
        conn.execute("UPDATE users SET jurisdiction = ? WHERE id = ?", (jurisdiction, user_id))
    conn.commit()
    conn.close()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _login(client, email, password):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text}"
    return r.json()


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def ctx(client):
    insp1 = _login(client, "inspector1@trace.gov", "Inspector@123")
    insp2 = _login(client, "inspector2@trace.gov", "Inspector@123")
    mgr = _login(client, "manager1@trace.gov", "Manager@123")
    return {"insp1": insp1["access_token"], "insp2": insp2["access_token"], "mgr": mgr["access_token"]}


def test_login_accepts_officer_id_and_email(client):
    """Officers sign in with their service number; the e-mail alias still works."""
    by_officer_id = client.post("/api/auth/login", json={"identifier": "LMO-001", "password": "Inspector@123"})
    print(f"\n[login] Officer ID -> HTTP {by_officer_id.status_code}")
    assert by_officer_id.status_code == 200
    body = by_officer_id.json()
    assert body["officer_id"] == "LMO-001"
    assert body["role"] == "inspector"
    assert body["role_label"] == "Legal Metrology Officer"

    # Case-insensitive, and the legacy e-mail field still resolves the same user.
    assert client.post("/api/auth/login", json={"identifier": "lmo-001", "password": "Inspector@123"}).status_code == 200
    by_email = client.post("/api/auth/login", json={"email": "inspector1@trace.gov", "password": "Inspector@123"})
    assert by_email.status_code == 200
    assert by_email.json()["user_id"] == body["user_id"]

    controller = client.post("/api/auth/login", json={"identifier": "CLM-001", "password": "Manager@123"})
    assert controller.status_code == 200
    assert controller.json()["role_label"] == "Controller of Legal Metrology"

    assert client.post("/api/auth/login", json={"identifier": "LMO-001", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"identifier": "NOBODY", "password": "x"}).status_code == 401


def test_jurisdiction_round_trips_through_login(client, ctx):
    r_set = client.put(
        "/api/profile/jurisdiction",
        json={"jurisdiction": "Test District"},
        headers=_hdr(ctx["insp1"]),
    )
    print(f"\n[jurisdiction] set -> HTTP {r_set.status_code} body={r_set.json()}")
    assert r_set.status_code == 200
    assert r_set.json()["jurisdiction"] == "Test District"

    # A fresh login must now carry the jurisdiction automatically.
    body = _login(client, "inspector1@trace.gov", "Inspector@123")
    print(f"[jurisdiction] re-login jurisdiction={body.get('jurisdiction')}")
    assert body["jurisdiction"] == "Test District"

    r_empty = client.put("/api/profile/jurisdiction", json={"jurisdiction": "   "}, headers=_hdr(ctx["insp1"]))
    assert r_empty.status_code == 400


def test_session_creation_requires_jurisdiction(client, ctx):
    # Force a clean "never set" precondition regardless of what earlier test runs
    # against this persistent dev db may have left behind (there is deliberately no
    # API to unset a jurisdiction once saved, so this resets it directly).
    conn = get_connection()
    conn.execute("UPDATE users SET jurisdiction = NULL WHERE id = 'u-insp-2'")
    conn.commit()
    conn.close()

    r = client.post(
        "/api/sessions",
        json={"location": "Test Mart", "inspection_type": "Routine", "notes": ""},
        headers=_hdr(ctx["insp2"]),
    )
    print(f"\n[session] no jurisdiction -> HTTP {r.status_code} body={r.json()}")
    assert r.status_code == 400

    r_set = client.put("/api/profile/jurisdiction", json={"jurisdiction": "Other District"}, headers=_hdr(ctx["insp2"]))
    assert r_set.status_code == 200


def test_session_rejects_invalid_inspection_type(client, ctx):
    r = client.post(
        "/api/sessions",
        json={"location": "Test Mart", "inspection_type": "Not A Real Type", "notes": ""},
        headers=_hdr(ctx["insp1"]),
    )
    print(f"\n[session] invalid inspection_type -> HTTP {r.status_code}")
    assert r.status_code == 400


@pytest.fixture(scope="module")
def session_ctx(client, ctx):
    """One officer's full session -> product -> linked scan, shared by the tests below."""
    r_sess = client.post(
        "/api/sessions",
        json={"location": "ABC Mart, MG Road", "inspection_type": "Routine", "notes": "acceptance test"},
        headers=_hdr(ctx["insp1"]),
    )
    assert r_sess.status_code == 200, r_sess.text
    session_id = r_sess.json()["id"]
    assert r_sess.json()["officer_id"] == "u-insp-1"
    assert r_sess.json()["status"] == "ACTIVE"

    r_prod = client.post(
        f"/api/sessions/{session_id}/products",
        json={
            "product_name": "ABC Biscuits", "manufacturer": "ABC Foods",
            "total_quantity": 500, "sample_target": 3, "category": "Bakery",
        },
        headers=_hdr(ctx["insp1"]),
    )
    assert r_prod.status_code == 200, r_prod.text
    product_id = r_prod.json()["products"][0]["id"]

    r_scan = client.post(
        "/api/scan",
        data={"dataset_filename": DATASET_A, "session_id": session_id, "product_id": product_id},
        headers=_hdr(ctx["insp1"]),
    )
    assert r_scan.status_code == 200, r_scan.text
    inspection_id = r_scan.json()["inspection_id"]

    return {"session_id": session_id, "product_id": product_id, "inspection_id": inspection_id}


def test_product_rejects_non_positive_quantities(client, ctx, session_ctx):
    r = client.post(
        f"/api/sessions/{session_ctx['session_id']}/products",
        json={"product_name": "Bad Product", "total_quantity": 0, "sample_target": 3},
        headers=_hdr(ctx["insp1"]),
    )
    print(f"\n[product] total_quantity=0 -> HTTP {r.status_code}")
    assert r.status_code == 400


def test_scan_links_to_product_and_summary_reflects_it(client, ctx, session_ctx):
    r = client.get(f"/api/sessions/{session_ctx['session_id']}/summary", headers=_hdr(ctx["insp1"]))
    assert r.status_code == 200
    summary = r.json()
    print(f"\n[summary] totals={summary['totals']}")

    product = summary["products"][0]
    assert product["id"] == session_ctx["product_id"]
    assert product["samples_inspected"] == 1
    assert product["remaining_samples"] == 2
    assert product["uninspected_stock"] == 499
    assert (product["pass_count"] + product["review_count"] + product["non_compliant_count"]) == 1
    linked_ids = {s["inspection_id"] for s in product["scans"]}
    assert session_ctx["inspection_id"] in linked_ids

    # The sample-count tally is a pure reflection of each scan's already-computed
    # overall_status -- assert it matches exactly, no separate compliance logic.
    scan = next(s for s in product["scans"] if s["inspection_id"] == session_ctx["inspection_id"])
    status_to_field = {"COMPLIANT": "pass_count", "NON_COMPLIANT": "non_compliant_count", "REVIEW_REQUIRED": "review_count"}
    assert product[status_to_field[scan["overall_status"]]] >= 1


def test_ad_hoc_scan_without_session_is_unaffected(client, ctx):
    """A plain scan with no session_id/product_id must behave exactly as before."""
    r = client.post("/api/scan", data={"dataset_filename": DATASET_A}, headers=_hdr(ctx["insp1"]))
    print(f"\n[ad-hoc] plain scan -> HTTP {r.status_code}")
    assert r.status_code == 200
    assert r.json()["overall_status"] in ("COMPLIANT", "NON_COMPLIANT", "REVIEW_REQUIRED")


def test_scan_rejects_session_id_without_product_id(client, ctx, session_ctx):
    r = client.post(
        "/api/scan",
        data={"dataset_filename": DATASET_A, "session_id": session_ctx["session_id"]},
        headers=_hdr(ctx["insp1"]),
    )
    print(f"\n[scan] session_id without product_id -> HTTP {r.status_code}")
    assert r.status_code == 400


def test_bola_second_officer_cannot_touch_first_officers_session(client, ctx, session_ctx):
    sid = session_ctx["session_id"]

    r_summary = client.get(f"/api/sessions/{sid}/summary", headers=_hdr(ctx["insp2"]))
    print(f"\n[BOLA] inspector2 GET summary of inspector1's session -> HTTP {r_summary.status_code}")
    assert r_summary.status_code == 403

    r_product = client.post(
        f"/api/sessions/{sid}/products",
        json={"product_name": "Intruder Product", "total_quantity": 10, "sample_target": 1},
        headers=_hdr(ctx["insp2"]),
    )
    print(f"[BOLA] inspector2 adds product to inspector1's session -> HTTP {r_product.status_code}")
    assert r_product.status_code == 403

    r_close = client.post(f"/api/sessions/{sid}/close", headers=_hdr(ctx["insp2"]))
    print(f"[BOLA] inspector2 closes inspector1's session -> HTTP {r_close.status_code}")
    assert r_close.status_code == 403

    # A scan tagged with someone else's session/product must not go through either --
    # this must 404 (not silently succeed as an ad-hoc scan) since it looks owned but isn't.
    r_scan = client.post(
        "/api/scan",
        data={"dataset_filename": DATASET_A, "session_id": sid, "product_id": session_ctx["product_id"]},
        headers=_hdr(ctx["insp2"]),
    )
    print(f"[BOLA] inspector2 scans into inspector1's product -> HTTP {r_scan.status_code}")
    assert r_scan.status_code == 404

    # Sanity: the real owner still has full access.
    r_owner = client.get(f"/api/sessions/{sid}/summary", headers=_hdr(ctx["insp1"]))
    assert r_owner.status_code == 200


def test_officer_sessions_list_is_own_only(client, ctx, session_ctx):
    r1 = client.get("/api/sessions", headers=_hdr(ctx["insp1"]))
    r2 = client.get("/api/sessions", headers=_hdr(ctx["insp2"]))
    ids1 = {s["id"] for s in r1.json()}
    ids2 = {s["id"] for s in r2.json()}
    print(f"\n[list] inspector1 sees own session={session_ctx['session_id'] in ids1} "
          f"| inspector2 sees it={session_ctx['session_id'] in ids2}")
    assert session_ctx["session_id"] in ids1
    assert session_ctx["session_id"] not in ids2


def test_close_session_blocks_further_products_and_scans(client, ctx, session_ctx):
    sid = session_ctx["session_id"]
    r_close = client.post(f"/api/sessions/{sid}/close", headers=_hdr(ctx["insp1"]))
    assert r_close.status_code == 200
    assert r_close.json()["status"] == "CLOSED"

    r_close_again = client.post(f"/api/sessions/{sid}/close", headers=_hdr(ctx["insp1"]))
    print(f"\n[close] closing an already-closed session -> HTTP {r_close_again.status_code}")
    assert r_close_again.status_code == 400

    r_add = client.post(
        f"/api/sessions/{sid}/products",
        json={"product_name": "Too Late", "total_quantity": 10, "sample_target": 1},
        headers=_hdr(ctx["insp1"]),
    )
    print(f"[close] adding a product after close -> HTTP {r_add.status_code}")
    assert r_add.status_code == 400

    r_scan = client.post(
        "/api/scan",
        data={"dataset_filename": DATASET_A, "session_id": sid, "product_id": session_ctx["product_id"]},
        headers=_hdr(ctx["insp1"]),
    )
    print(f"[close] scanning into a closed session -> HTTP {r_scan.status_code}")
    assert r_scan.status_code == 400


def test_officer_registration(client):
    """Registration captures name / Officer ID / password / jurisdiction, and the
    jurisdiction is auto-populated on every later login."""
    oid = f"LMO-REG-{uuid.uuid4().hex[:4].upper()}"
    r = client.post("/api/auth/register", json={
        "officer_name": "Test Registrant", "officer_id": oid,
        "password": "Passw0rd!", "jurisdiction": "Trichy District",
    })
    print(f"\n[register] -> HTTP {r.status_code}")
    assert r.status_code == 200
    body = r.json()
    assert body["officer_id"] == oid
    assert body["jurisdiction"] == "Trichy District"
    # Self-registration must never be able to grant Controller powers.
    assert body["role"] == "inspector"

    assert client.post("/api/auth/register", json={
        "officer_name": "Someone", "officer_id": oid,
        "password": "Passw0rd!", "jurisdiction": "X"}).status_code == 409
    assert client.post("/api/auth/register", json={
        "officer_name": "Someone", "officer_id": f"LMO-{uuid.uuid4().hex[:4]}",
        "password": "short", "jurisdiction": "X"}).status_code == 400
    assert client.post("/api/auth/register", json={
        "officer_name": "Someone", "officer_id": f"LMO-{uuid.uuid4().hex[:4]}",
        "password": "Passw0rd!", "jurisdiction": "   "}).status_code == 400

    # Login with name + Officer ID + password; jurisdiction comes back automatically.
    lr = client.post("/api/auth/login", json={
        "officer_name": "Test Registrant", "identifier": oid, "password": "Passw0rd!"})
    assert lr.status_code == 200
    assert lr.json()["jurisdiction"] == "Trichy District"
    # A mismatched officer name is rejected like any other bad credential.
    assert client.post("/api/auth/login", json={
        "officer_name": "Wrong Person", "identifier": oid, "password": "Passw0rd!"}).status_code == 401

    conn = get_connection()
    conn.execute("DELETE FROM users WHERE officer_id = ?", (oid,))
    conn.commit()
    conn.close()


def test_product_status_rollup_and_session_tallies(client, ctx, session_ctx):
    """Level-2 history data: each product carries a rolled-up compliance status and
    the session reports how many products are compliant vs non-compliant."""
    summary = client.get(f"/api/sessions/{session_ctx['session_id']}/summary", headers=_hdr(ctx["insp1"])).json()
    product = summary["products"][0]
    assert product["product_status"] in ("COMPLIANT", "NON_COMPLIANT", "REVIEW_REQUIRED", "NOT_INSPECTED")
    print(f"\n[rollup] product_status={product['product_status']} "
          f"compliant={summary['compliant_products']} non_compliant={summary['non_compliant_products']}")

    # The rolled-up status must agree with the product's own sample tallies.
    if product["non_compliant_count"] > 0:
        assert product["product_status"] == "NON_COMPLIANT"
    elif product["review_count"] > 0:
        assert product["product_status"] == "REVIEW_REQUIRED"
    elif product["samples_inspected"] > 0:
        assert product["product_status"] == "COMPLIANT"

    # The single-query tally used by the session list must match the detailed summary.
    row = next(s for s in client.get("/api/sessions", headers=_hdr(ctx["insp1"])).json()
               if s["id"] == session_ctx["session_id"])
    assert row["compliant_products"] == summary["compliant_products"]
    assert row["non_compliant_products"] == summary["non_compliant_products"]


def test_seizure_generates_and_dispatches_violation_reports(client, ctx):
    """Confirming seizure auto-generates a manufacturer-specific violation report,
    resolves the registered contact, dispatches it and records the outcome."""
    # A session with one product whose samples are genuinely non-compliant.
    sess = client.post("/api/sessions", json={
        "location": "Seizure Test Depot", "inspection_type": "Enforcement", "notes": ""},
        headers=_hdr(ctx["insp1"])).json()
    sid = sess["id"]
    prod = client.post(f"/api/sessions/{sid}/products", json={
        "product_name": "Britannia Marie Gold", "manufacturer": "Britannia Industries Ltd",
        "total_quantity": 100, "sample_target": 2, "category": "Bakery"},
        headers=_hdr(ctx["insp1"])).json()
    pid = prod["products"][0]["id"]

    # Nothing non-compliant yet -> nothing to seize.
    early = client.post(f"/api/sessions/{sid}/seize", headers=_hdr(ctx["insp1"]))
    print(f"\n[seize] with no non-compliant products -> HTTP {early.status_code}")
    assert early.status_code == 400

    # Link a real non-compliant inspection that carries rule-engine violations.
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM inspections WHERE overall_status='NON_COMPLIANT' AND violations_json NOT IN ('[]','') LIMIT 1"
    ).fetchone()
    if not row:
        conn.close()
        pytest.skip("No non-compliant inspection with violations available in this database.")
    conn.execute(
        "INSERT OR IGNORE INTO session_product_scans (id, session_id, product_id, inspection_id, created_at)"
        " VALUES (?,?,?,?,datetime('now'))",
        (f"LINK-{uuid.uuid4().hex[:10].upper()}", sid, pid, row["id"]),
    )
    conn.commit()
    conn.close()

    r = client.post(f"/api/sessions/{sid}/seize", headers=_hdr(ctx["insp1"]))
    print(f"[seize] -> HTTP {r.status_code}")
    assert r.status_code == 200
    body = r.json()
    assert body["reports"], "seizure must generate at least one manufacturer report"
    rep = body["reports"][0]
    assert rep["manufacturer"]
    # Britannia is in the seeded contact directory, so it must resolve and dispatch.
    assert rep["recipient_email"] == "legal.metrology@britannia.example.in"
    assert rep["dispatch_status"] in ("SENT", "SIMULATED")
    assert rep["sent_at"], "a dispatched report must record when it was sent"
    print(f"[seize] {rep['manufacturer']} -> {rep['recipient_email']} [{rep['dispatch_status']}]")

    # Seizure is one-way.
    assert client.post(f"/api/sessions/{sid}/seize", headers=_hdr(ctx["insp1"])).status_code == 400

    # The stored report carries every field the statutory notice needs.
    stored = client.get(f"/api/sessions/{sid}/violation-reports", headers=_hdr(ctx["insp1"])).json()
    assert len(stored) == 1
    report = stored[0]["report"]
    for key in ("manufacturer", "session_id", "products_inspected", "compliant_samples",
                "non_compliant_samples", "violations", "evidence", "officer_name",
                "officer_id", "jurisdiction", "inspection_date", "seizure_status"):
        assert key in report, f"violation report missing {key}"
    assert report["seizure_status"] == "SEIZED"
    assert len(report["violations"]) > 0

    html = client.get(f"/api/violation-reports/{stored[0]['id']}/html", headers=_hdr(ctx["insp1"]))
    assert html.status_code == 200
    assert "SEIZURE CONFIRMED" in html.text

    # Another officer can neither seize nor read this session's reports.
    assert client.post(f"/api/sessions/{sid}/seize", headers=_hdr(ctx["insp2"])).status_code == 403
    assert client.get(f"/api/sessions/{sid}/violation-reports", headers=_hdr(ctx["insp2"])).status_code == 403


def test_officer_metrics_endpoint(client, ctx):
    r = client.get("/api/officer/metrics", headers=_hdr(ctx["insp1"]))
    print(f"\n[metrics] {r.json()}")
    assert r.status_code == 200
    body = r.json()
    for key in ("active_sessions", "completed_inspections", "pending_reviews", "non_compliance_findings"):
        assert key in body

    r_mgr = client.get("/api/officer/metrics", headers=_hdr(ctx["mgr"]))
    print(f"[metrics] manager forbidden -> HTTP {r_mgr.status_code}")
    assert r_mgr.status_code == 403
