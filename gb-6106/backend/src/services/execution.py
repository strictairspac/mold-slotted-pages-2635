"""Checkpointed, fenced execution of replay runs.

Each confirmed step is committed together with its checkpoint, so an
interrupted run continues from the last confirmed step and no step is counted
twice.  A per-run lease and an epoch counter fence stale workers: once another
worker takes the run over, writes that carry the old epoch affect no row and
are therefore never accounted.
"""
import logging
import os
import socket
from time import time
from uuid import uuid4

from sqlalchemy import and_, or_, select, update

from src.config.resources import engine
from src.constants.job_state import JobState
from src.domain import scenario
from src.models.cases import cases
from src.models.runs import runs
from src.services.audit import record
from src.utils.serial import content_digest

logger = logging.getLogger(__name__)

LEASE_SECONDS = 30
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
# An owner may keep committing as long as the epoch matches and the run has not
# settled.  PAUSING means a pause was requested but not yet confirmed.
OWNED_STATES = (JobState.RUNNING, JobState.PAUSING)


class JobFailure(Exception):
    def __init__(self, run_id, epoch, cause):
        super().__init__(str(cause))
        self.run_id = run_id
        self.epoch = epoch
        self.cause = cause


def claim_next():
    """Atomically lease the oldest run that may execute without a takeover.

    Queued runs are always claimable.  In-flight runs (crashed worker) are
    claimable once their lease has expired, which is what lets an interrupted
    replay continue from its last confirmed step.  Paused runs are excluded on
    purpose: after a confirmed pause only an explicit takeover may resume them.
    """
    now = int(time())
    statement = (
        select(runs)
        .where(or_(
            runs.c.status == JobState.QUEUED,
            and_(
                runs.c.status.in_((JobState.RUNNING, JobState.PAUSING)),
                or_(runs.c.lease_until.is_(None), runs.c.lease_until < now),
            ),
        ))
        .order_by(runs.c.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    with engine.begin() as db:
        task = db.execute(statement).mappings().first()
        if task is None:
            return None
        epoch = task["epoch"] + 1
        db.execute(update(runs).where(runs.c.id == task["id"]).values(
            status=JobState.RUNNING,
            epoch=epoch,
            lease_owner=WORKER_ID,
            lease_until=now + LEASE_SECONDS,
            started_at=task["started_at"] or now,
        ))
        return {**task, "epoch": epoch, "lease_owner": WORKER_ID}


def service_one():
    claim = claim_next()
    if claim is None:
        return False
    execute(claim)
    return True


def execute(claim):
    run_id = claim["id"]
    epoch = claim["epoch"]
    try:
        with engine.connect() as db:
            payload = db.scalar(select(cases.c.payload).where(cases.c.id == claim["case_id"]))
        operations = payload["operations"]
        replay = scenario.open_replay(payload, claim["parameter"], claim["checkpoint"])
    except Exception as exc:
        logger.exception("Run %s could not be opened", run_id)
        fail(JobFailure(run_id, epoch, exc))
        return

    while replay.steps < len(operations):
        if advance(run_id, epoch, replay, operations[replay.steps]) is not None:
            return
    settle(run_id, epoch, replay)


def advance(run_id, epoch, replay, operation):
    """Commit one confirmed step, or honour a pause / discard a stale worker."""
    with engine.begin() as db:
        row = db.execute(
            select(runs).where(runs.c.id == run_id).with_for_update()
        ).mappings().first()
        if not _owned(row, epoch):
            return "fenced"
        if row["pause_requested"] or row["status"] == JobState.PAUSING:
            confirm_pause(db, run_id)
            return "paused"
        try:
            replay.step(operation)
        except Exception as exc:
            raise JobFailure(run_id, epoch, exc) from exc
        changed = db.execute(update(runs).where(
            runs.c.id == run_id, runs.c.epoch == epoch, runs.c.status.in_(OWNED_STATES),
        ).values(
            checkpoint=replay.checkpoint(),
            confirmed_step=replay.steps,
            lease_owner=WORKER_ID,
            lease_until=int(time()) + LEASE_SECONDS,
        )).rowcount
        return None if changed else "fenced"


def settle(run_id, epoch, replay):
    """Persist the final result unless a newer epoch has taken the run over."""
    try:
        result = replay.finalize()
        output = {"status": JobState.DONE, "result": result, "digest": content_digest(result)}
    except Exception as exc:
        logger.exception("Run %s could not be finalized", run_id)
        fail(JobFailure(run_id, epoch, exc))
        return
    with engine.begin() as db:
        row = db.execute(
            select(runs).where(runs.c.id == run_id).with_for_update()
        ).mappings().first()
        if not _owned(row, epoch):
            return
        if row["pause_requested"] or row["status"] == JobState.PAUSING:
            confirm_pause(db, run_id)
            return
        changed = db.execute(update(runs).where(
            runs.c.id == run_id, runs.c.epoch == epoch, runs.c.status.in_(OWNED_STATES),
        ).values(**output, finished_at=int(time()), lease_owner=None, lease_until=None)).rowcount
        if changed:
            record(db, str(output["status"]), "worker", "run", run_id)


def confirm_pause(db, run_id):
    db.execute(update(runs).where(runs.c.id == run_id).values(
        status=JobState.PAUSED, pause_requested=False, halt_reason="paused",
        lease_owner=None, lease_until=None,
    ))
    record(db, "paused", "worker", "run", run_id)


def fail(job_failure):
    with engine.begin() as db:
        run_id = job_failure.run_id
        row = db.execute(
            select(runs.c.status, runs.c.epoch).where(runs.c.id == run_id).with_for_update()
        ).first()
        # A stale worker (older epoch) or a settled run is not accounted.
        if row is None or row.epoch != job_failure.epoch or row.status not in OWNED_STATES:
            return
        cause = job_failure.cause
        if isinstance(cause, (ValueError, TypeError, KeyError, OverflowError)):
            reason = str(cause) or "输入资料或批次参数不符合要求"
        else:
            reason = "处理失败，请检查 worker 日志"
        changed = db.execute(update(runs).where(
            runs.c.id == run_id, runs.c.epoch == job_failure.epoch, runs.c.status.in_(OWNED_STATES),
        ).values(
            status=JobState.FAILED, failure=reason[:320], finished_at=int(time()),
            halt_reason="failed", lease_owner=None, lease_until=None,
        )).rowcount
        if changed:
            record(db, str(JobState.FAILED), "worker", "run", run_id)


def _owned(row, epoch):
    return row is not None and row["epoch"] == epoch and row["status"] in OWNED_STATES
