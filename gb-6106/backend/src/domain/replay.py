"""Deterministic, checkpointable replay of slot-page operations.

The replay keeps every piece of state that influences the next step: the raw
page bytes, the forward slot directory, the record arena tail, the counters and
the rows confirmed so far.  A run can therefore stop after any confirmed step
and continue later from exactly that state, producing the same slot numbers,
offsets and rejection records as a single uninterrupted run.
"""
import base64

from .page import HEADER_BYTES, SLOT_BYTES, Slot, SlottedPage


CHECKPOINT_VERSION = 1
ACTIONS = {"insert": "录入", "delete": "撤销", "update": "补记", "compact": "整理"}


class Replay:
    def __init__(self, size):
        self.page = SlottedPage(size)
        self.steps = 0
        self.denied = 0
        self.peak_waste = 0
        self.compactions = 0
        self.rows = []
        self._result = None

    # -- checkpointing -----------------------------------------------------

    def checkpoint(self):
        page = self.page
        return {
            "version": CHECKPOINT_VERSION,
            "page": {
                "size": page.size,
                "tail": page.tail,
                "data": base64.b64encode(bytes(page.data)).decode("ascii"),
                "slots": [
                    None if item is None else
                    {"offset": item.offset, "length": item.length, "key": item.key}
                    for item in page.slots
                ],
            },
            "steps": self.steps,
            "denied": self.denied,
            "peak_waste": self.peak_waste,
            "compactions": self.compactions,
            "rows": [dict(row) for row in self.rows],
        }

    @classmethod
    def restore(cls, size, state):
        if not isinstance(state, dict) or state.get("version") != CHECKPOINT_VERSION:
            raise ValueError("断点数据格式不受支持")
        page_state = state.get("page")
        if not isinstance(page_state, dict):
            raise ValueError("断点页数据缺失")
        replay = cls(size)
        page = replay.page
        try:
            data = base64.b64decode(page_state.get("data", ""), validate=True)
            tail = int(page_state["tail"])
            raw_slots = page_state["slots"]
            steps = int(state["steps"])
            denied = int(state["denied"])
            peak_waste = int(state["peak_waste"])
            compactions = int(state["compactions"])
            rows = list(state["rows"])
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ValueError("断点页数据损坏") from None
        if len(data) != size:
            raise ValueError("断点页容量与批次参数不符")
        capacity = max(0, (size - HEADER_BYTES) // SLOT_BYTES)
        if not HEADER_BYTES <= tail <= size or len(raw_slots) > capacity:
            raise ValueError("断点页目录或分配位置越界")
        slots = []
        for item in raw_slots:
            if item is None:
                slots.append(None)
                continue
            try:
                slot = Slot(int(item["offset"]), int(item["length"]), str(item["key"]))
            except (KeyError, TypeError, ValueError):
                raise ValueError("断点槽目录损坏") from None
            if slot.offset < tail or slot.offset + slot.length > size or slot.length < 0:
                raise ValueError("断点槽地址越界")
            slots.append(slot)
        if steps < 0 or denied < 0 or compactions < 0 or len(rows) != steps:
            raise ValueError("断点进度不一致")
        keys = [item.key for item in slots if item is not None]
        if len(keys) != len(set(keys)):
            raise ValueError("断点槽目录重复")
        page.data = bytearray(data)
        page.tail = tail
        page.slots = slots
        replay.steps = steps
        replay.denied = denied
        replay.peak_waste = peak_waste
        replay.compactions = compactions
        replay.rows = [dict(row) for row in rows if isinstance(row, dict)]
        return replay

    # -- execution ---------------------------------------------------------

    def step(self, operation):
        action = operation["op"]
        key = "" if action == "compact" else operation["key"]
        prior = self.page.find(key)
        pointer = self.page.slots[prior].offset if prior is not None else None
        slot_id = prior
        message = "完成"
        try:
            if action == "compact":
                self.page.compact()
                self.compactions += 1
            elif action == "delete":
                slot_id = self.page.delete(key)
            else:
                write = self.page.insert if action == "insert" else self.page.update
                slot_id, compacted = write(key, operation["note"].encode())
                if compacted:
                    self.compactions += 1
                    message = "压实后落盘"
        except ValueError as exc:
            self.denied += 1
            message = str(exc)
        slot = self.page.slots[slot_id] if slot_id is not None else None
        self.peak_waste = max(self.peak_waste, self.page.fragmented)
        self.steps += 1
        self.rows.append({
            "步骤": self.steps, "操作": ACTIONS[action], "履历": key, "槽号": slot_id,
            "原偏移": pointer, "现偏移": slot.offset if slot else None,
            "记录字节": slot.length if slot else None, "连续空闲": self.page.contiguous,
            "碎片字节": self.page.fragmented, "结果": message,
        })

    def finalize(self):
        if self._result is not None:
            return self._result
        for index, item in enumerate(self.page.slots):
            if item:
                self.rows.append({
                    "步骤": None, "操作": "追溯地址", "履历": item.key, "槽号": index,
                    "原偏移": None, "现偏移": item.offset, "记录字节": item.length,
                    "连续空闲": self.page.contiguous, "碎片字节": self.page.fragmented,
                    "结果": self.page.read(index).decode(),
                })
        live = sum(item is not None for item in self.page.slots)
        self._result = {
            "summary": f"最终保留 {live} 条履历，压实 {self.compactions} 次，拒绝 {self.denied} 条操作。",
            "metrics": {"活槽位": live, "拒绝操作": self.denied, "最大碎片": self.peak_waste,
                        "压实次数": self.compactions, "剩余字节": self.page.free},
            "rows": self.rows,
        }
        return self._result
