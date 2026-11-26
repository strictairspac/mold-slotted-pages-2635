import pytest
from fastapi.testclient import TestClient
from src.config.resources import engine
from src.config.settings import DEMO_PASSWORD
from src.constants.roles import Role
from src.models.users import users
from src.services import execution
from src.utils.passwords import hash_password


@pytest.fixture()
def client(db):
    from src.main import app
    with engine.begin() as connection:
        for role in Role:
            connection.execute(users.insert().values(
                username=role.value, role=role.value, password_hash=hash_password(DEMO_PASSWORD),
            ))
    with TestClient(app) as test_client:
        yield test_client


def auth(client, role):
    token = client.post("/auth/login", json={"username": role, "password": DEMO_PASSWORD}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def submit(client, headers, parameter=192, case_id=None):
    if case_id is None:
        # The C4 fixture (8 operations) is the multi-step demo case.
        case_id = next(item["id"] for item in client.get("/cases", headers=headers).json()
                       if item["title"] == "C4 三号模具清班")
    response = client.post(f"/cases/{case_id}/runs", json={"parameter": parameter}, headers=headers)
    assert response.status_code == 202, response.text
    return response.json()


def pause(client, headers, run_id):
    return client.post(f"/runs/{run_id}/pause", headers=headers)


def takeover(client, headers, run_id):
    return client.post(f"/runs/{run_id}/takeover", headers=headers)


def test_pause_and_takeover_api_requires_staff_roles(client):
    operator = auth(client, "operator")
    viewer = auth(client, "viewer")
    run = submit(client, operator)
    assert pause(client, viewer, run["id"]).status_code == 403
    paused = pause(client, operator, run["id"])
    assert paused.status_code == 200 and paused.json()["status"] == "paused"
    assert takeover(client, viewer, run["id"]).status_code == 403


def test_paused_run_is_not_dispatched_and_export_is_gated(client):
    operator = auth(client, "operator")
    run = submit(client, operator)
    pause(client, operator, run["id"])
    # An idle worker must not move a paused run.
    while execution.service_one():
        pass
    detail = client.get(f"/runs/{run['id']}", headers=operator).json()
    assert detail["status"] == "paused"
    assert detail["confirmed_step"] == 0
    export = client.get(f"/runs/{run['id']}/export", headers=operator)
    assert export.status_code == 409


def test_takeover_dispatches_once_and_resumes(client):
    operator = auth(client, "operator")
    reviewer = auth(client, "reviewer")
    run = submit(client, operator)
    pause(client, operator, run["id"])
    first = takeover(client, reviewer, run["id"])
    assert first.status_code == 202
    assert first.json()["takeover_count"] == 1
    second = takeover(client, operator, run["id"])
    assert second.status_code == 409
    while execution.service_one():
        pass
    done = client.get(f"/runs/{run['id']}", headers=operator).json()
    assert done["status"] == "done"
    export = client.get(f"/runs/{run['id']}/export", headers=operator)
    assert export.status_code == 200


def test_pause_takeover_result_identical_to_uninterrupted(client):
    operator = auth(client, "operator")
    uninterrupted = submit(client, operator)
    while execution.service_one():
        pass
    baseline = client.get(f"/runs/{uninterrupted['id']}/export", headers=operator).json()

    interrupted = submit(client, operator)
    pause(client, operator, interrupted["id"])
    takeover(client, operator, interrupted["id"])
    while execution.service_one():
        pass
    resumed = client.get(f"/runs/{interrupted['id']}/export", headers=operator).json()

    assert resumed["result"] == baseline["result"]
    assert resumed["digest"] == baseline["digest"]
    assert resumed["status"] == "done"
    # The credential keeps its original shape, unchanged by the interruption.
    assert set(resumed) == set(baseline)


def test_partial_run_paused_and_resumed_exports_identical_evidence(client):
    operator = auth(client, "operator")
    reference = submit(client, operator)
    while execution.service_one():
        pass
    baseline = client.get(f"/runs/{reference['id']}/export", headers=operator).json()

    run_id = submit(client, operator)["id"]
    claim = execution.claim_next()
    from src.domain import scenario
    from src.domain.fixtures import FIXTURES
    payload = FIXTURES[0]["payload"]
    replay = scenario.open_replay(payload, claim["parameter"], claim["checkpoint"])
    for operation in payload["operations"][:5]:
        assert execution.advance(run_id, claim["epoch"], replay, operation) is None
    pause = client.post(f"/runs/{run_id}/pause", headers=operator)
    assert pause.status_code == 200
    assert execution.advance(run_id, claim["epoch"], replay, payload["operations"][5]) == "paused"
    assert client.get(f"/runs/{run_id}", headers=operator).json()["confirmed_step"] == 5
    assert takeover(client, operator, run_id).status_code == 202
    while execution.service_one():
        pass

    resumed = client.get(f"/runs/{run_id}/export", headers=operator).json()
    assert resumed["result"] == baseline["result"]
    assert resumed["digest"] == baseline["digest"]
