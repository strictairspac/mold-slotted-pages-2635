import pytest
from sqlalchemy import select, update

from src.config.resources import engine
from src.constants.job_state import JobState
from src.domain.fixtures import FIXTURES
from src.models.runs import runs
from src.services import execution


def enqueue(case_id, parameter=192):
    from src.services import runs as run_service
    with engine.begin() as db:
        entry = run_service.enqueue(db, case_id, parameter, "operator")
    return entry["id"]


def row(run_id):
    with engine.connect() as db:
        return db.execute(select(runs).where(runs.c.id == run_id)).mappings().first()


def pause(run_id, actor="reviewer"):
    from src.services import runs as run_service
    with engine.begin() as db:
        return run_service.pause(db, run_id, actor)


def takeover(run_id, actor="reviewer"):
    from src.services import runs as run_service
    with engine.begin() as db:
        return run_service.takeover(db, run_id, actor)


def drain():
    while execution.service_one():
        pass


def test_full_run_matches_domain_one_pass(db):
    run_id = enqueue(1)
    drain()
    finished = row(run_id)
    assert finished.status == JobState.DONE
    from src.domain import scenario
    expected = scenario.run(FIXTURES[0]["payload"], 192)
    assert finished.result == expected
    assert finished.confirmed_step == finished.total_steps == len(FIXTURES[0]["payload"]["operations"])


def test_pause_waits_for_takeover_then_resumes_at_last_confirmed_step(db):
    run_id = enqueue(1)
    paused = pause(run_id)
    assert paused["status"] == JobState.PAUSED
    confirmed = paused["confirmed_step"]
    assert paused["last_confirmed"] is None or paused["last_confirmed"]["步骤"] == confirmed

    # The worker never resumes a paused run on its own.
    drain()
    assert row(run_id).status == JobState.PAUSED

    taken = takeover(run_id)
    assert taken["status"] == JobState.QUEUED
    assert taken["takeover_count"] == 1

    drain()
    finished = row(run_id)
    assert finished.status == JobState.DONE
    from src.domain import scenario
    assert finished.result == scenario.run(FIXTURES[0]["payload"], 192)


def test_partial_pause_then_takeover_matches_one_pass(db):
    payload = FIXTURES[0]["payload"]
    run_id = enqueue(1)
    # Execute exactly three steps by claiming and limiting.
    claim = execution.claim_next()
    from src.domain import scenario
    replay = scenario.open_replay(payload, claim["parameter"], claim["checkpoint"])
    for operation in payload["operations"][:3]:
        assert execution.advance(run_id, claim["epoch"], replay, operation) is None

    paused = pause(run_id)
    # Requesting a pause while a worker owns it means "pause at next step".
    assert paused["status"] in (JobState.PAUSING, JobState.PAUSED)
    # The live worker confirms the pause at its last committed step.
    assert execution.advance(run_id, claim["epoch"], replay, payload["operations"][3]) == "paused"
    paused = row(run_id)
    assert paused.status == JobState.PAUSED
    assert paused.confirmed_step == 3

    # No other worker picks it up while paused.
    drain()
    assert row(run_id).status == JobState.PAUSED

    takeover(run_id)
    drain()
    finished = row(run_id)
    assert finished.status == JobState.DONE
    assert finished.result == scenario.run(payload, 192)
    # Every step is recorded once; no duplicate rows from the resume point.
    steps = [entry["步骤"] for entry in finished.result["rows"] if entry["步骤"] is not None]
    assert steps == list(range(1, len(payload["operations"]) + 1))


def test_confirmed_pause_survives_restart(db):
    run_id = enqueue(1)
    pause(run_id)
    drain()  # an idle worker scanning after restart must not move a paused run
    assert row(run_id).status == JobState.PAUSED
    assert execution.claim_next() is None
    takeover(run_id)
    drain()
    assert row(run_id).status == JobState.DONE


def test_stale_worker_cannot_account_late_result(db):
    run_id = enqueue(1)
    claim = execution.claim_next()
    old_epoch = claim["epoch"]
    # Simulate another worker/takeover bumping the epoch while the old one runs.
    pause(run_id)
    with engine.begin() as db:
        db.execute(update(runs).where(runs.c.id == run_id).values(
            status=JobState.PAUSED, pause_requested=False,
        ))
    takeover(run_id)
    new_epoch = row(run_id).epoch
    assert new_epoch == old_epoch + 1

    # The old worker's late failure is ignored because its epoch is stale.
    execution.fail(execution.JobFailure(run_id, old_epoch, ValueError("late")))
    assert row(run_id).status == JobState.QUEUED
    # A late settle from the old epoch leaves the run untouched too.
    from src.domain import scenario
    old_replay = scenario.open_replay(FIXTURES[0]["payload"], 192, claim["checkpoint"])
    for operation in FIXTURES[0]["payload"]["operations"]:
        old_replay.step(operation)
    execution.settle(run_id, old_epoch, old_replay)
    assert row(run_id).status == JobState.QUEUED


def test_concurrent_takeover_dispatches_only_once(db):
    run_id = enqueue(1)
    pause(run_id)
    # Two requests arrive with the same paused state; the row lock serialises them.
    first = takeover(run_id)
    assert first["takeover_count"] == 1
    with pytest.raises(Exception) as error:
        takeover(run_id, actor="viewer")
    assert "接办" in str(error.value)
    assert row(run_id).takeover_count == 1
