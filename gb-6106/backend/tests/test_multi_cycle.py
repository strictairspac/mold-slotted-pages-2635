from sqlalchemy import select

from src.config.resources import engine
from src.constants.job_state import JobState
from src.domain.fixtures import FIXTURES
from src.domain import scenario
from src.models.runs import runs
from src.services import execution, runs as run_service


def test_multiple_pause_takeover_cycles_match_single_pass(db):
    payload = FIXTURES[0]["payload"]
    with engine.begin() as connection:
        baseline_id = run_service.enqueue(connection, 1, 192, "operator")["id"]
    while execution.service_one():
        pass
    with engine.connect() as connection:
        baseline = connection.execute(select(runs.c.result).where(runs.c.id == baseline_id)).scalar()

    with engine.begin() as connection:
        run_id = run_service.enqueue(connection, 1, 192, "operator")["id"]

    for cut in (2, 4, 6):
        claim = execution.claim_next()
        assert claim["id"] == run_id
        replay = scenario.open_replay(payload, claim["parameter"], claim["checkpoint"])
        while replay.steps < cut:
            assert execution.advance(run_id, claim["epoch"], replay, payload["operations"][replay.steps]) is None
        with engine.begin() as connection:
            run_service.pause(connection, run_id, "operator")
        assert execution.advance(run_id, claim["epoch"], replay, payload["operations"][replay.steps]) == "paused"
        with engine.connect() as connection:
            assert connection.execute(select(runs.c.status).where(runs.c.id == run_id)).scalar() == JobState.PAUSED
        with engine.begin() as connection:
            run_service.takeover(connection, run_id, "operator")

    while execution.service_one():
        pass
    with engine.connect() as connection:
        final = connection.execute(select(runs.c.result).where(runs.c.id == run_id)).scalar()
        status = connection.execute(select(runs.c.status).where(runs.c.id == run_id)).scalar()
    assert status == JobState.DONE
    assert final == baseline
