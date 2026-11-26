"""Takeover must not wait for the old worker and must dispatch exactly one job."""
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.config.resources import engine
from src.config.settings import DEMO_PASSWORD
from src.constants.job_state import JobState
from src.constants.roles import Role
from src.domain.fixtures import FIXTURES
from src.models.runs import runs
from src.models.users import users
from src.services import execution
from src.services import runs as run_service
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


def operator(client):
    token = client.post("/auth/login", json={"username": "operator", "password": DEMO_PASSWORD}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def row(run_id):
    with engine.connect() as db:
        return db.execute(select(runs).where(runs.c.id == run_id)).mappings().first()


def test_takeover_does_not_wait_for_old_worker_and_fences_late_result(client):
    headers = operator(client)
    case_id = client.get("/cases", headers=headers).json()[0]["id"]
    run_id = client.post(f"/cases/{case_id}/runs", json={"parameter": 192}, headers=headers).json()["id"]

    with engine.begin() as db:
        run_service.pause(db, run_id, "operator")
    # Old worker has already claimed the run and is mid-step: it holds the
    # lease/epoch.  Takeover bumps the epoch while that process still lives.
    claim = execution.claim_next()
    # To take over while the old worker is alive we release the state to paused
    # again (as if the old worker confirmed a pause) but keep its epoch.
    with engine.begin() as db:
        run_service.pause(db, run_id, "operator")
        db.execute(runs.update().where(runs.c.id == run_id).values(
            status=JobState.PAUSED, pause_requested=False,
        ))
    assert row(run_id).status == JobState.PAUSED
    old_epoch = claim["epoch"] if claim else row(run_id).epoch
    response = client.post(f"/runs/{run_id}/takeover", headers=headers)
    assert response.status_code == 202
    new_epoch = row(run_id).epoch
    assert new_epoch > old_epoch

    # The old worker now reports success for its old epoch: it is discarded.
    from src.domain import scenario
    old_replay = scenario.open_replay(FIXTURES[0]["payload"], 192)
    for operation in FIXTURES[0]["payload"]["operations"]:
        old_replay.step(operation)
    execution.settle(run_id, old_epoch, old_replay)
    assert row(run_id).status == JobState.QUEUED


def test_two_simultaneous_takeovers_dispatch_one_job(client):
    headers = operator(client)
    case_id = client.get("/cases", headers=headers).json()[0]["id"]
    run_id = client.post(f"/cases/{case_id}/runs", json={"parameter": 192}, headers=headers).json()["id"]
    with engine.begin() as db:
        run_service.pause(db, run_id, "operator")

    results = []

    def attempt():
        try:
            with engine.begin() as db:
                run_service.takeover(db, run_id, "operator")
            results.append("ok")
        except Exception:  # HTTPException from the loser
            results.append("rejected")

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count("ok") == 1
    assert results.count("rejected") == 1
    assert row(run_id).takeover_count == 1


def test_concurrent_takeover_http_requests(client):
    headers = operator(client)
    case_id = client.get("/cases", headers=headers).json()[0]["id"]
    run_id = client.post(f"/cases/{case_id}/runs", json={"parameter": 192}, headers=headers).json()["id"]
    with engine.begin() as db:
        run_service.pause(db, run_id, "operator")
    codes = []

    def attempt():
        codes.append(client.post(f"/runs/{run_id}/takeover", headers=headers).status_code)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(codes) == [202, 409]
    assert row(run_id).takeover_count == 1
