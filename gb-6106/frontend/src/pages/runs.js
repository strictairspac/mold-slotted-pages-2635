import { ACTIVE, HaltReasons, PAUSED } from "../constants/states.js";
import { el } from "../utils/dom.js";
import { api } from "../api/workbench.js";
import { empty, pageHeading, section } from "../components/common/panel.js";
import { status } from "../components/common/status.js";
import { resultTable, table } from "../components/common/table.js";
import { time } from "../utils/format.js";

function progress(run) {
  return el("div", {"data-testid": "progress", class: "progress"},
    el("p", {}, `已确认 ${run.confirmed_step || 0} / ${run.total_steps || 0} 步`),
    run.last_confirmed
      ? el("p", {"data-testid": "last-confirmed", class: "last-step"},
          `最后确认：第 ${run.last_confirmed["步骤"]} 步 · ${run.last_confirmed["操作"]} ${run.last_confirmed["履历"] || ""} — ${run.last_confirmed["结果"]}`)
      : el("p", {class: "empty"}, "尚未确认任何步骤。"));
}

function confirmedTable(run) {
  return run.last_confirmed ? resultTable([run.last_confirmed]) : empty("尚未确认任何步骤。");
}

function actionButton(label, testid, action, onDone) {
  const issue = el("p", {role: "alert", class: "feedback"});
  const button = el("button", {type: "button", "data-testid": testid, onclick: async () => {
    button.disabled = true;
    issue.textContent = "";
    try {
      await action();
      onDone();
    } catch (error) {
      issue.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }}, label);
  return el("span", {}, button, issue);
}

function controls(run, canHandle, onDone) {
  if (!canHandle) return null;
  const group = el("div", {class: "actions"});
  if (run.status === "queued" || run.status === "running") {
    group.append(actionButton("暂停核对", "pause", () => api.pause(run.id), onDone));
  } else if (run.status === "pausing") {
    group.append(el("span", {class: "empty"}, "正在暂停，待当前步骤落定…"));
  } else if (run.status === PAUSED) {
    group.append(actionButton("接办续跑", "takeover", () => api.takeover(run.id), onDone));
  }
  return group.childElementCount ? group : null;
}

function reportState(run, canHandle, onDone) {
  const message = run.status === "queued" || run.status === "running" || run.status === "pausing"
    ? "后台正在重放，步骤确认后页面自动更新。"
    : run.status === PAUSED
      ? (HaltReasons[run.halt_reason] || "已在最后确认处暂停，等待接办。")
      : run.failure || run.result?.summary;
  return el("div", {class: "stack", id: "run-state", "data-testid": "run-state"}, status(run.status),
    el("p", {}, message), progress(run), controls(run, canHandle, onDone),
    run.status === "done" ? el("a", {class: "button", href: `#evidence/${run.id}`}, "查看业务证据") : null,
    section("最后确认的步骤", confirmedTable(run)));
}

export async function runsPage(config, user, route, signal) {
  const canHandle = user.role === "operator" || user.role === "reviewer";
  const items = await api.runs();
  const latest = route.id ? await api.run(route.id, signal) : items[0];
  const detail = el("div", {});
  let timer;

  async function tick() {
    clearTimeout(timer);
    try {
      const next = await api.run(latest.id, signal);
      if (signal.aborted) return;
      detail.replaceChildren(reportState(next, canHandle, tick));
      const badge = document.querySelector(`[data-run="${next.id}"] .status`);
      if (badge) badge.replaceWith(status(next.status));
      if (ACTIVE.has(next.status)) timer = setTimeout(tick, 700);
    } catch (error) {
      if (!signal.aborted) detail.append(el("p", {role: "alert"}, error.message));
    }
  }

  if (latest) {
    detail.append(reportState(latest, canHandle, tick));
    signal.addEventListener("abort", () => clearTimeout(timer), {once: true});
    if (ACTIVE.has(latest.status)) timer = setTimeout(tick, 700);
  } else {
    detail.append(empty("进入资料页，选用一个业务样本并提交执行。"));
  }

  return el("div", {class: "page"},
    pageHeading(config.modules[2], config.parameter_label),
    latest ? section(latest.case_title, detail) : detail,
    section("执行批次", items.length ? table(["资料", "编号", "参数", "状态", "进度", "提交时间"],
      items.map(run => [el("a", {"data-run": run.id, href: `#runs/${run.id}`}, run.case_title), run.id,
        run.parameter, el("span", {"data-run": run.id}, status(run.status)),
        `${run.confirmed_step || 0}/${run.total_steps || 0}`, time(run.created_at)]), "执行批次") :
      empty("暂无执行记录。")));
}
