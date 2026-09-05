"""Phase 6 — tests for the read-only ``/api/*`` frontend routes.

Reuses the Phase 5 test pattern (module-scoped simulator run + promoted
T-learner) and additionally runs the five deterministic demo scenarios
(RETRY / MESSAGE / NO_ACTION / policy-block / multi-cycle) so the
dashboard, list, and detail endpoints have guaranteed coverage of every
shape the contract requires.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.main import app
from backend.database.session import get_db
from backend.models import Base, enums
from backend.repositories.governance import ModelVersionRepository
from ml.inference.recovery import clear_cache
from ml.training.uplift import train_uplift_model
from simulation.config import SimConfig
from simulation.generator.runner import run_simulation
from simulation.scenarios.demo_cases import run_all
from backend.services import recovery_flow as flow


@pytest.fixture(scope="module")
def api_client(tmp_path_factory):
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(eng, "connect")
    def _fk(c, _):  # noqa: ANN001
        cur = c.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, future=True, expire_on_commit=False)
    db = Session()
    clear_cache()

    run_simulation(
        db, replace(SimConfig(seed=42), n_cases=1200, customers_per_merchant=250)
    )
    tr = train_uplift_model(
        db, kind="t_learner", version="p6-api", seed=42,
        artifact_dir=tmp_path_factory.mktemp("p6apiart"),
    )
    repo = ModelVersionRepository(db)
    repo.transition_status(tr.model_version, enums.ModelVersionStatus.VALIDATED.value)
    repo.transition_status(tr.model_version, enums.ModelVersionStatus.PROMOTED.value)
    db.commit()

    demo_results = run_all(db)
    db.commit()

    # Scenario A only evaluates (RETRY recommended, Intervention REQUESTED,
    # no outcome yet). Take it to a RECOVERED outcome so the dashboard's
    # "hero recovered case" query — which must be driven by the actual
    # promoted T-learner, not the bulk simulator's internal placeholder —
    # has a real, guaranteed candidate.
    scenario_a_dr_id = next(
        r.audits[0].decision_record_id for r in demo_results if r.key == "A"
    )
    flow.execute_decision(db, decision_record_id=scenario_a_dr_id)
    flow.record_outcome(
        db, decision_record_id=scenario_a_dr_id, result="RECOVERED",
        recovery_amount="2500.00",
    )
    db.commit()

    def _override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override
    client = TestClient(app)
    try:
        yield client, db, demo_results
    finally:
        app.dependency_overrides.clear()
        db.close()
        eng.dispose()
        clear_cache()


def _case_id_for(demo_results, key: str) -> str:
    for r in demo_results:
        if r.key == key:
            return str(r.audits[0].recovery_case_id)
    raise AssertionError(f"no demo scenario {key}")


# ------------------------------------------------------------- /api/dashboard


def test_dashboard_aggregates_are_consistent(api_client):
    client, _db, _demo = api_client
    r = client.get("/api/dashboard")
    assert r.status_code == 200, r.text
    body = r.json()

    for key in (
        "total_cases", "open_cases", "recovered_cases", "not_recovered_cases",
        "total_amount_at_risk", "total_recovery_amount", "decision_cycle_count",
        "action_counts", "no_action_count", "policy_blocked_count",
        "execution_status_summary", "recovery_by_action", "highlighted_cases",
    ):
        assert key in body

    assert body["total_cases"] >= body["recovered_cases"] + body["not_recovered_cases"]
    assert body["decision_cycle_count"] > 0
    # scenarios A-E guarantee at least one of each action + one policy block
    assert body["action_counts"]["RETRY"] >= 1
    assert body["action_counts"]["MESSAGE"] >= 1
    assert body["action_counts"]["NO_ACTION"] >= 1
    assert body["no_action_count"] == body["action_counts"]["NO_ACTION"]
    assert body["policy_blocked_count"] >= 1

    for action in ("RETRY", "MESSAGE", "NO_ACTION"):
        bucket = body["recovery_by_action"][action]
        assert "recovered" in bucket and "not_recovered" in bucket

    hc = body["highlighted_cases"]
    assert hc["hero_recovered_case_id"] is not None  # scenario A, taken to RECOVERED above
    assert hc["policy_block_case_id"] is not None  # scenario D
    assert hc["multi_cycle_case_id"] is not None  # scenario E

    # Every highlighted case must be driven by the actual promoted
    # T-learner — never the bulk simulator's internal "sim-naive-prior"
    # placeholder, which is VALIDATED but never PROMOTED and isn't the
    # real live inference path.
    for key in ("hero_recovered_case_id", "policy_block_case_id", "multi_cycle_case_id"):
        detail = client.get(f"/api/recovery-cases/{hc[key]}").json()
        mv = detail["cycles"][0]["model_version"]
        assert mv["status"] == "PROMOTED", f"{key} not driven by the promoted model: {mv}"
        assert mv["model_name"] == "recovery-t-learner-logreg"


# --------------------------------------------------------- /api/recovery-cases


def test_list_recovery_cases_basic_and_pagination(api_client):
    client, _db, _demo = api_client
    r = client.get("/api/recovery-cases", params={"limit": 5, "offset": 0})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["limit"] == 5
    assert body["offset"] == 0
    assert len(body["items"]) == 5
    assert body["total"] > 5

    item = body["items"][0]
    for key in (
        "recovery_case_id", "case_display_id", "payment_id", "payment_amount",
        "currency", "status", "cycle_count", "latest_recommended_action",
        "latest_final_action", "latest_outcome_result", "opened_at",
    ):
        assert key in item
    assert item["cycle_count"] >= 1

    r2 = client.get("/api/recovery-cases", params={"limit": 5, "offset": 5})
    assert r2.status_code == 200
    ids_page1 = {i["recovery_case_id"] for i in body["items"]}
    ids_page2 = {i["recovery_case_id"] for i in r2.json()["items"]}
    assert ids_page1.isdisjoint(ids_page2)


def test_list_recovery_cases_status_filter(api_client):
    client, _db, _demo = api_client
    r = client.get("/api/recovery-cases", params={"status": "RECOVERED", "limit": 50})
    assert r.status_code == 200
    body = r.json()
    assert all(i["status"] == "RECOVERED" for i in body["items"])


# ------------------------------------------------------ /api/recovery-cases/{id}


def test_case_detail_not_found(api_client):
    client, _db, _demo = api_client
    import uuid

    r = client.get(f"/api/recovery-cases/{uuid.uuid4()}")
    assert r.status_code == 404


def test_case_explanation_endpoint_never_crashes_without_llm_configured(
    api_client, monkeypatch
):
    """Phase 12A/12C: with no NVIDIA_NIM_API_KEY configured, the endpoint
    must still respond 200 with a safe 'unavailable' explanation, never a
    500, and the rest of the decision API must be completely unaffected
    by hitting it. Forced via monkeypatch — a real key may legitimately
    be present in the ambient .env for live verification, and this test
    must stay deterministic regardless."""
    monkeypatch.setattr("backend.core.config.settings.nvidia_nim_api_key", "")
    client, _db, demo = api_client
    case_id = _case_id_for(demo, "A")

    r = client.get(f"/api/recovery-cases/{case_id}/explanation")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is False
    assert "disclaimer" in body and body["disclaimer"]

    # the authoritative decision endpoint is untouched by the call above
    r2 = client.get(f"/api/recovery-cases/{case_id}")
    assert r2.status_code == 200
    assert r2.json()["cycles"][0]["final_action"] == "RETRY"


def test_case_explanation_unknown_case_404(api_client):
    import uuid

    client, _db, _demo = api_client
    r = client.get(f"/api/recovery-cases/{uuid.uuid4()}/explanation")
    assert r.status_code == 404


def test_case_detail_retry_scenario(api_client):
    client, _db, demo = api_client
    case_id = _case_id_for(demo, "A")
    r = client.get(f"/api/recovery-cases/{case_id}")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["recovery_case_id"] == case_id
    assert body["payment"] is not None
    assert body["payment"]["id"] == body["payment_id"]
    assert isinstance(body["payment_events"], list) and len(body["payment_events"]) >= 2
    assert len(body["cycles"]) >= 1

    cycle = body["cycles"][0]
    # all three predictions represented
    assert {a["action"] for a in cycle["actions_considered"]} == {
        "RETRY", "MESSAGE", "NO_ACTION",
    }
    assert cycle["recommended_action"] == "RETRY"
    # recommendation == final_action here (not blocked)
    assert cycle["final_action"] == cycle["recommended_action"]
    assert cycle["model_version"] is not None
    assert cycle["model_version"]["status"] == "PROMOTED"
    # RETRY is executable -> an intervention exists
    assert cycle["intervention_action"] == "RETRY"


def test_case_detail_no_action_has_no_intervention(api_client):
    client, _db, demo = api_client
    case_id = _case_id_for(demo, "C")
    r = client.get(f"/api/recovery-cases/{case_id}")
    assert r.status_code == 200
    body = r.json()
    cycle = body["cycles"][0]
    assert cycle["final_action"] == "NO_ACTION"
    assert cycle["intervention_action"] is None
    assert cycle["execution_status"] is None


def test_case_detail_policy_block_recommendation_differs_from_final(api_client):
    client, _db, demo = api_client
    case_id = _case_id_for(demo, "D")
    r = client.get(f"/api/recovery-cases/{case_id}")
    assert r.status_code == 200
    body = r.json()
    cycle = body["cycles"][0]
    assert cycle["recommended_action"] == "RETRY"
    assert cycle["final_action"] != cycle["recommended_action"]
    assert cycle["was_blocked"] is True
    blocked = [a for a in cycle["actions_considered"] if a["policy_result"] == "BLOCKED"]
    assert any(a["action"] == "RETRY" for a in blocked)


def test_case_detail_multiple_cycles_stay_separate(api_client):
    client, _db, demo = api_client
    case_id = _case_id_for(demo, "E")
    r = client.get(f"/api/recovery-cases/{case_id}")
    assert r.status_code == 200
    body = r.json()
    assert len(body["cycles"]) == 2

    c1, c2 = body["cycles"][0], body["cycles"][1]
    assert c1["cycle_number"] == 1
    assert c2["cycle_number"] == 2
    assert c1["decision_record_id"] != c2["decision_record_id"]
    # cycle 2 carries a summary of cycle 1 in its own previous_cycles
    assert len(c2["previous_cycles"]) == 1
    assert c2["previous_cycles"][0]["cycle_number"] == 1
    # cycle 1 itself has no previous cycles
    assert c1["previous_cycles"] == []


def test_case_detail_is_read_only_and_stable(api_client):
    """Two reads of the same case return byte-identical decision history —
    the API never mutates historical records."""
    client, _db, demo = api_client
    case_id = _case_id_for(demo, "A")
    r1 = client.get(f"/api/recovery-cases/{case_id}").json()
    r2 = client.get(f"/api/recovery-cases/{case_id}").json()
    assert r1["cycles"] == r2["cycles"]
