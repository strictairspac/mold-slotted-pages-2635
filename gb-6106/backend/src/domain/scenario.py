import re

from .fixtures import FIXTURES  # noqa: F401  (re-exported for seed and callers)
from .page import HEADER_BYTES
from .replay import ACTIONS, Replay


CONFIG = {
    "title": "砺光玻璃·压模履历槽页检修",
    "team": "砺光玻璃 C4 线 7 人工装与质保小组",
    "description": "撤销空跑、补录压模复测，在清班前验证单页压实和追溯槽号。",
    "direction": "槽式页空间管理与压实",
    "mechanism": "定长槽目录、UTF-8 字节数组、页内更新迁移与压实，活记录的槽号保持稳定。",
    "modules": ["模具交班", "压模变更单", "槽页重放", "追溯地址表"],
    "unit": "压模变更单",
    "max_parameter": 4096,
    "parameter_label": "模拟页容量（字节，16–4096）",
    "default_parameter": 192,
    "facts": ["活槽位", "拒绝操作", "最大碎片"],
    "palette": 6,
    "fixture": "C4#03 · GM-C4-P07",
}

def validate(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("operations"), list):
        raise ValueError("operations 必须是操作列表")
    operations = payload["operations"]
    if not 1 <= len(operations) <= 100:
        raise ValueError("每单允许 1 至 100 条操作")
    for step, operation in enumerate(operations, 1):
        if not isinstance(operation, dict):
            raise ValueError(f"第 {step} 步必须是对象")
        action = operation.get("op")
        if not isinstance(action, str) or action not in ACTIONS:
            raise ValueError(f"第 {step} 步不支持该操作")
        if action != "compact":
            key = operation.get("key")
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", key):
                raise ValueError(f"第 {step} 步记录号须为 1 至 24 位字母、数字、下划线或连字符")
        if action in {"insert", "update"}:
            note = operation.get("note")
            if not isinstance(note, str) or not note or len(note) > 1000:
                raise ValueError(f"第 {step} 步备注须为 1 至 1000 个字符")
            try:
                note.encode()
            except UnicodeEncodeError:
                raise ValueError(f"第 {step} 步备注不是有效 UTF-8 文本") from None


def open_replay(payload, parameter, state=None):
    """Build a replay engine positioned at the start or at a confirmed checkpoint."""
    validate(payload)
    if parameter < HEADER_BYTES:
        raise ValueError(f"至少需要 {HEADER_BYTES} 字节页头")
    replay = Replay.restore(parameter, state) if state is not None else Replay(parameter)
    if replay.steps > len(payload["operations"]):
        raise ValueError("断点进度超出操作列表")
    return replay


def run(payload, parameter, db=None):
    """Run every operation in one pass; kept for callers that need no checkpoints."""
    replay = open_replay(payload, parameter)
    for operation in payload["operations"]:
        replay.step(operation)
    return replay.finalize()
