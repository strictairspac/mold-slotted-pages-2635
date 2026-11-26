from time import time

from fastapi import HTTPException
from sqlalchemy import insert, select, update

from src.constants.job_state import JobState
from src.domain.scenario import CONFIG
from src.models.cases import cases
from src.models.runs import runs
from . import audit, cases as case_service

SETTLED = (JobState.DONE, JobState.FAILED)

# Columns exposed to the API.  The checkpoint blob stays in the database; only
# its last confirmed row is surfaced.
ENTRY_COLUMNS = (
    runs.c.id, runs.c.case_id, runs.c.status, runs.c.parameter, runs.c.actor,
    runs.c.created_at, runs.c.finished_at, runs.c.result, runs.c.failure, runs.c.digest,
    runs.c.total_steps, runs.c.confirmed_step, runs.c.halt_reason,
    runs.c.taken_by, runs.c.takeover_count,
)


def query():
    return select(*ENTRY_COLUMNS, cases.c.title.label("case_title")).join(
        cases, cases.c.id == runs.c.case_id
    )


def _entry(row, checkpoint=None):
    entry = dict(row)
    entry.pop("checkpoint", None)
    entry["last_confirmed"] = last_confirmed(checkpoint)
    return entry


def last_confirmed(checkpoint):
    if not isinstance(checkpoint, dict):
        return None
    rows = [row for row in checkpoint.get("rows", []) if isinstance(row, dict) and row.get("步骤") is not None]
    return rows[-1] if rows else None


def get(db, run_id):
    row = db.execute(
        query().add_columns(runs.c.checkpoint).where(runs.c.id == run_id)
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "执行批次不存在")
    return _entry(row, row["checkpoint"])


def list_entries(db):
    return [dict(row, last_confirmed=None) for row in
            db.execute(query().order_by(runs.c.id.desc()).limit(100)).mappings()]


def enqueue(db, case_id, parameter, actor):
    if parameter > CONFIG["max_parameter"]:
        raise HTTPException(422, f"参数不能超过 {CONFIG['max_parameter']}")
    case = case_service.get(db, case_id)
    change = db.execute(insert(runs).values(
        case_id=case_id, status=JobState.QUEUED, parameter=parameter, actor=actor,
        created_at=int(time()), total_steps=len(case["payload"]["operations"]),
    ))
    run_id = change.inserted_primary_key[0]
    audit.record(db, "enqueue", actor, "run", run_id)
    return get(db, run_id)


def pause(db, run_id, actor):
    """Stop the replay at its last confirmed step and wait for a takeover.

    A queued run has not started, so the pause is already confirmed.  A running
    run keeps its live worker, which commits the step it is on and then flips
    the run to paused.  Either way a paused run is never picked up again by the
    worker's ordinary scan; only an explicit takeover resumes it.
    """
    row = db.execute(
        select(runs.c.status, runs.c.epoch).where(runs.c.id == run_id).with_for_update()
    ).first()
    if row is None:
        raise HTTPException(404, "执行批次不存在")
    if row.status in SETTLED:
        raise HTTPException(409, "已结束的批次不能暂停")
    if row.status == JobState.PAUSED or row.status == JobState.PAUSING:
        return get(db, run_id)
    if row.status == JobState.QUEUED:
        db.execute(update(runs).where(
            runs.c.id == run_id, runs.c.status == JobState.QUEUED,
        ).values(status=JobState.PAUSED, halt_reason="paused"))
        audit.record(db, "paused", actor, "run", run_id)
    else:
        db.execute(update(runs).where(
            runs.c.id == run_id, runs.c.status == JobState.RUNNING,
        ).values(status=JobState.PAUSING, pause_requested=True, halt_reason="pause_waiting"))
        audit.record(db, "pause_requested", actor, "run", run_id)
    return get(db, run_id)


def takeover(db, run_id, actor):
    """Atomically hand a paused replay back to the queue.

    The row lock serialises concurrent requests.  The state check and the
    update share one WHERE clause, so only the first request flips the row and
    dispatches work; later requests observe a run that is no longer paused and
    are rejected.  Bumping the epoch fences any worker still executing the
    previous incarnation, so a late result is never accounted, and no waiting
    for that worker's exit is required.
    """
    row = db.execute(
        select(runs.c.status, runs.c.epoch).where(runs.c.id == run_id).with_for_update()
    ).first()
    if row is None:
        raise HTTPException(404, "执行批次不存在")
    if row.status != JobState.PAUSED:
        raise HTTPException(409, "只有已确认暂停的批次可以接办")
    values = dict(
        status=JobState.QUEUED, epoch=row.epoch + 1, pause_requested=False, halt_reason=None,
        lease_owner=None, lease_until=None, taken_by=actor, started_at=int(time()),
    )
    change = db.execute(update(runs).where(
        runs.c.id == run_id, runs.c.status == JobState.PAUSED,
    ).values(**values, takeover_count=runs.c.takeover_count + 1))
    if change.rowcount != 1:
        raise HTTPException(409, "只有已确认暂停的批次可以接办")
    audit.record(db, "takeover", actor, "run", run_id)
    return get(db, run_id)
