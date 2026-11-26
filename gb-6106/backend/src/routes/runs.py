from fastapi import APIRouter, Depends, HTTPException, Response

from src.config.resources import engine
from src.constants.job_state import JobState
from src.controllers.access import handoff, identity, reviewer
from src.controllers.workbench import new_review, wake
from src.schemas.reviews import ReviewEntry, ReviewInput
from src.schemas.runs import RunEntry
from src.services import reviews, runs
from src.utils.serial import canonical_json

router = APIRouter(prefix="/runs", tags=["Execution"])

# The handoff credential is the same shape as an uninterrupted run and carries
# the verdict, the step evidence and the review.  Pause/takeover bookkeeping
# (checkpoint, epoch, lease, last confirmed row) stays in the workbench so the
# downloaded slots, offsets and rejections match a single-pass run exactly.
EXPORT_FIELDS = (
    "id", "case_id", "case_title", "status", "parameter", "actor", "created_at",
    "finished_at", "result", "failure", "digest",
)


@router.get("", response_model=list[RunEntry], dependencies=[Depends(identity)])
def list_runs():
    with engine.connect() as db:
        return runs.list_entries(db)


@router.get("/{run_id}", response_model=RunEntry, dependencies=[Depends(identity)])
def run_detail(run_id: int):
    with engine.connect() as db:
        return runs.get(db, run_id)


@router.post("/{run_id}/pause", response_model=RunEntry)
def pause(run_id: int, user=Depends(handoff)):
    with engine.begin() as db:
        return runs.pause(db, run_id, user["username"])


@router.post("/{run_id}/takeover", response_model=RunEntry, status_code=202)
def resume(run_id: int, user=Depends(handoff)):
    with engine.begin() as db:
        run = runs.takeover(db, run_id, user["username"])
    wake(run_id)
    return run


@router.get("/{run_id}/review", response_model=ReviewEntry | None, dependencies=[Depends(identity)])
def review_detail(run_id: int):
    with engine.connect() as db:
        runs.get(db, run_id)
        return reviews.get(db, run_id)


@router.post("/{run_id}/review", response_model=ReviewEntry, status_code=201)
def sign(run_id: int, form: ReviewInput, user=Depends(reviewer)):
    return new_review(run_id, form, user)


@router.get("/{run_id}/export", dependencies=[Depends(identity)])
def export(run_id: int):
    with engine.connect() as db:
        run = runs.get(db, run_id)
        if run["status"] != JobState.DONE:
            raise HTTPException(409, "没有可导出的执行结果")
        run["review"] = reviews.get(db, run_id)
    # The handoff credential is the same shape as an uninterrupted run and
    # carries the verdict, the step evidence and the review.  Pause/takeover
    # bookkeeping stays in the workbench so the downloaded slots, offsets and
    # rejections match a single-pass run exactly.
    evidence = {key: run[key] for key in EXPORT_FIELDS}
    evidence["review"] = run["review"]
    return Response(canonical_json(evidence), media_type="application/json", headers={
        "Content-Disposition": f"attachment; filename=evidence-{run_id}.json",
    })
