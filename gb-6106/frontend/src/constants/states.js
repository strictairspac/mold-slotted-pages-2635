export const States = Object.freeze({
  queued: "等待执行", running: "执行中", pausing: "正在暂停…", paused: "已暂停", done: "已完成", failed: "执行失败",
});

export const Actions = Object.freeze({
  create_case: "登记资料", enqueue: "提交执行", done: "执行完成", failed: "执行失败", review: "复核签署",
  pause_requested: "请求暂停", paused: "暂停确认", takeover: "接办续跑",
});

export const HaltReasons = Object.freeze({
  paused: "交班核对，已在最后确认处暂停", pause_waiting: "交班核对，等待当前步骤落定",
  failed: "执行失败",
});

export const ACTIVE = new Set(["queued", "running", "pausing"]);
export const PAUSED = "paused";
