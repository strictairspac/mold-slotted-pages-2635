import json

import pytest

from src.domain import scenario
from src.domain.fixtures import FIXTURES
from src.domain.replay import Replay


@pytest.mark.parametrize("fixture", FIXTURES, ids=[f["code"] for f in FIXTURES])
@pytest.mark.parametrize("size", [16, 40, 64, 96, 192, 4096])
def test_checkpoint_resume_matches_one_pass(fixture, size):
    payload = fixture["payload"]
    one_pass = scenario.run(payload, size)

    replay = scenario.open_replay(payload, size)
    checkpoints = []
    for operation in payload["operations"]:
        replay.step(operation)
        checkpoints.append(json.loads(json.dumps(replay.checkpoint())))
    assert replay.finalize() == one_pass

    for index in range(len(checkpoints)):
        resumed = Replay.restore(size, checkpoints[index])
        for operation in payload["operations"][resumed.steps:]:
            resumed.step(operation)
        assert resumed.finalize() == one_pass, f"resume at step {index}"


def test_last_rows_stay_stable_and_rejections_are_counted_once():
    payload = FIXTURES[1]["payload"]  # contains a duplicate insert that is denied
    one_pass = scenario.run(payload, 192)
    assert one_pass["metrics"]["拒绝操作"] == 1

    replay = scenario.open_replay(payload, 192)
    for operation in payload["operations"][:2]:
        replay.step(operation)
    restored = Replay.restore(192, json.loads(json.dumps(replay.checkpoint())))
    for operation in payload["operations"][2:]:
        restored.step(operation)
    final = restored.finalize()
    assert final["metrics"]["拒绝操作"] == 1
    assert final == one_pass


def test_corrupt_checkpoint_is_rejected():
    payload = FIXTURES[0]["payload"]
    checkpoint = json.loads(json.dumps(scenario.open_replay(payload, 192).checkpoint()))
    for broken in (
        {**checkpoint, "version": 99},
        {**checkpoint, "page": {**checkpoint["page"], "data": "AAAA"}},
        {**checkpoint, "page": {**checkpoint["page"], "tail": 99999}},
    ):
        with pytest.raises(ValueError):
            Replay.restore(192, broken)
