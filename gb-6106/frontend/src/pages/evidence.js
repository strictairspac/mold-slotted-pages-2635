import { api } from "../api/workbench.js";
import { empty, pageHeading, section } from "../components/common/panel.js";
import { errorText, field } from "../components/common/fields.js";
import { status } from "../components/common/status.js";
import { resultTable } from "../components/common/table.js";
import { submit } from "../hooks/submit.js";
import { el } from "../utils/dom.js";
import { cell, download, time } from "../utils/format.js";

function reviewForm(id, saved) {
  const form = el("form", {id: "review-form"},
    field("复核意见", el("textarea", {name: "note", required: true, minlength: 3, maxlength: 500, rows: 3})),
    errorText(), el("button", {type: "submit"}, "签署复核"));
  submit(form, async values => saved(await api.submitReview(id, {note: values.get("note")})));
  return form;
}

function signature(review) {
  return el("div", {"data-testid": "reviewed", class: "signature"},
    el("strong", {}, `已签署 · ${review.actor}`), el("p", {}, review.note), el("time", {}, time(review.created_at)));
}

function progress(run) {
  return el("div", {"data-testid": "progress", class: "progress"},
    el("p", {}, `已确认 ${run.confirmed_step || 0} / ${run.total_steps || 0} 步`),
    run.last_confirmed
      ? el("p", {class: "last-step"}, `最后确认：第 ${run.last_confirmed["步骤"]} 步 · ${run.last_confirmed["操作"]} ${run.last_confirmed["履历"] || ""}`)
      : el("p", {class: "empty"}, "尚未确认任何步骤。"));
}

function handoff(run) {
  const issue = errorText();
  const button = el("button", {type: "button", "data-testid": "export", onclick: async () => {
    button.disabled = true;
    issue.textContent = "";
    try {
      await download(await api.export(run.id), `evidence-${run.id}.json`);
    } catch (error) {
      issue.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }}, "下载交班凭据");
  return el("div", {class: "handoff"}, el("p", {}, "重放已完成，交班凭据可随工票下载。"), button, issue);
}

export async function evidencePage(config, user, route) {
  const runs = await api.runs();
  const done = runs.filter(item => item.status === "done");
  const run = route.id ? await api.run(route.id) : done[0];
  if (!run) return el("div", {class: "page"},
    pageHeading(config.modules[3], null), empty("提交一个批次后可以查看进度、签核和下载证据。"),
    el("a", {href: "#runs"}, config.modules[2]));

  if (run.status !== "done") {
    const select = el("select", {"aria-label": "选择批次", onchange: event => {
      location.hash = `evidence/${event.target.value}`;
    }}, runs.map(item => el("option", {value: item.id, selected: item.id === run.id}, `#${item.id} ${item.case_title}`)));
    return el("div", {class: "page evidence"},
      pageHeading(config.modules[3], run.case_title),
      select,
      section("批次状态", el("div", {class: "stack"}, status(run.status), progress(run),
        el("p", {}, run.status === "paused" ? "交班核对中，接办后从此处继续重放；完成后才开放交班凭据。" : "重放尚未完成，暂不开放交班凭据下载。"),
        el("a", {href: `#runs/${run.id}`}, "返回槽页重放处理"))));
  }

  const review = await api.review(run.id);
  const signing = el("div", {});
  signing.append(review ? signature(review) : user.role === "reviewer" ?
    reviewForm(run.id, data => signing.replaceChildren(signature(data))) : empty("待复核员签署。"));
  const options = el("select", {"aria-label": "选择已完成的批次", onchange: event => {
    location.hash = `evidence/${event.target.value}`;
  }}, done.map(item => el("option", {value: item.id, selected: item.id === run.id}, `#${item.id} ${item.case_title}`)));
  return el("div", {class: "page evidence"},
    pageHeading(config.modules[3], run.case_title, handoff(run)),
    options,
    el("section", {class: "feature evidence-summary"}, el("div", {}, el("h2", {}, "批次判定"), el("p", {}, run.result.summary)),
      el("details", {}, el("summary", {}, "证据摘要"), el("code", {}, run.digest))),
    el("dl", {class: "metrics"}, Object.entries(run.result.metrics).map(([label, value]) =>
      el("div", {}, el("dt", {}, label), el("dd", {}, cell(value))))),
    section("业务结果明细", run.result.rows.length ? resultTable(run.result.rows) : empty("此批次无明细。")),
    section("复核签署", signing));
}
