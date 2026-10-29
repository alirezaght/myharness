"use strict";

// The Approval Center and its Approve & Implement button (P2-07). DOM built with textContent
// only. Loaded after compare.js and before app.js; uses el, withCode, alertText, renderBlocks
// and refreshDashboard (app.js) and postJson (comments.js), all called only after load.

const APPROVE_API = "/api/approve";
const APPROVE_FOCUS_IDS = new Set(["approve-button", "approve-retry", "approve-reload"]);
const READY_TEXT =
  "ready to approve: every comment is resolved, no blocking question remains and the final" +
  " review passes";
const approvalState = new Map();
const approvalPreviews = new Set();
let approvalTask = null;

function approvalGroup(id, title, items) {
  const section = el("section");
  section.id = id;
  section.append(el("h4", title));
  if (!items.length) {
    section.append(el("p", "none"));
    return section;
  }
  const list = el("ul");
  for (const item of items) {
    const entry = el("li");
    entry.append(el("p", `${item.ref} · ${item.kind}: ${item.title}`));
    entry.append(withCode("decide: ", item.command));
    list.append(entry);
  }
  section.append(list);
  return section;
}

function renderApprovalCenter(task) {
  approvalTask = task;
  const center = task.approval_center;
  const section = el("section");
  section.id = "approvals";
  const approved = center.final.blockers.some((blocker) => blocker.code === "approved");
  section.append(el("h3", approved ? "Specification approved" : "Approval Center"));
  if (!approved || center.groups.decisions.length) section.append(
    approvalGroup("approvals-decisions", "Decisions", center.groups.decisions));
  if (!approved || center.groups.test_plan.length) section.append(
    approvalGroup("approvals-test-plan", "Test plan", center.groups.test_plan));
  section.append(renderFinalSpec(task.id, center.groups.final_spec, center.final));
  return section;
}

function approvalBlockers(final) {
  if (!final.blockers.length) {
    const ready = el("p", READY_TEXT);
    ready.id = "approve-blockers";
    return ready;
  }
  const list = el("ul");
  list.id = "approve-blockers";
  for (const blocker of final.blockers) {
    const item = el("li", blocker.text);
    item.dataset.code = blocker.code;
    list.append(item);
  }
  return list;
}

function approvalPreview(taskId, final) {
  const details = el("details");
  details.id = "approve-preview";
  details.dataset.taskId = taskId;
  details.open = approvalPreviews.has(taskId);
  details.addEventListener("toggle", () => {
    if (details.open) approvalPreviews.add(taskId);
    else approvalPreviews.delete(taskId);
  });
  const article = renderBlocks(final.preview);
  article.id = "approve-preview-document";
  const approved = final.blockers.some((blocker) => blocker.code === "approved");
  details.append(el("summary", `${approved ? "Approved specification" : "Specification to be sealed"} (${final.spec_hash})`), article);
  return details;
}

function approvalResult(taskId, state) {
  const result = el("div");
  result.id = "approve-result";
  if (state.sending) {
    const status = el("p", "approving…");
    status.id = "approve-status";
    status.setAttribute("role", "status");
    result.append(status);
  }
  if (state.output) {
    const list = el("ul");
    list.id = "approve-output";
    list.tabIndex = -1;
    for (const line of state.output) list.append(el("li", line));
    result.append(list);
  }
  if (state.launch) {
    const launch = state.launch;
    const status = el("p", `Approval recorded. AL launch: ${launch.status || launch.state || "requested"}${launch.error ? ` · ${launch.error}` : ""}`);
    status.id = "approval-launch-status";
    status.setAttribute("role", launch.error ? "alert" : "status");
    result.append(status);
    if (launch.error || launch.status === "Unavailable") {
      const retryLaunch = el("button", "Retry AL launch");
      retryLaunch.type = "button";
      retryLaunch.addEventListener("click", () => sendApproval(taskId, state.payload));
      result.append(retryLaunch);
    }
  }
  if (state.error) result.append(alertText(state.error));
  if (state.reload) {
    const reload = el("button", "Reload");
    reload.id = "approve-reload";
    reload.type = "button";
    reload.addEventListener("click", () => reloadApproval(taskId));
    result.append(reload);
  }
  if (state.retry) {
    const retry = el("button", "Retry");
    retry.id = "approve-retry";
    retry.type = "button";
    retry.addEventListener("click", () => sendApproval(taskId, state.payload));
    result.append(retry);
  }
  return result;
}

function renderFinalSpec(taskId, items, final) {
  const approved = final.blockers.some((blocker) => blocker.code === "approved");
  const section = approved ? el("section") : approvalGroup("approvals-final", "Final spec", items);
  if (approved) {
    section.id = "approvals-final";
    section.append(el("h4", "Current approval"));
    section.append(el("p", "The specification is approved. Follow execution progress below."));
  }
  const state = approvalState.get(taskId) || {};
  if (final.status && !approved) section.append(el("p", `approval: ${final.status}`));
  const row = el("div");
  row.className = "approve-row";
  const button = el("button", approved ? "Approved" : "Approve & Implement");
  button.id = "approve-button";
  button.type = "button";
  button.disabled = !final.enabled || !!state.sending;
  button.setAttribute("aria-describedby", "approve-blockers");
  button.addEventListener("click", () => approveTask(taskId, final));
  row.append(button);
  section.append(row, approvalBlockers(final));
  if (!approved) section.append(withCode("or run: ", final.command));
  if (final.preview) section.append(approvalPreview(taskId, final));
  section.append(approvalResult(taskId, state));
  return section;
}

function approveTask(taskId, final) {
  sendApproval(taskId, {
    task: taskId, version: final.version, spec_hash: final.spec_hash,
    review_hash: final.review_hash,
  });
}

async function sendApproval(taskId, payload) {
  if (approvalState.get(taskId)?.sending) return;
  const active = document.activeElement;
  const hadFocus = !!active && APPROVE_FOCUS_IDS.has(active.id);
  const idle = { sending: false, error: null, reload: false, retry: false, output: null };
  approvalState.set(taskId, { ...idle, sending: true, payload });
  repaintApprovals();
  try {
    const { res, data, netErr } = await postJson(APPROVE_API, payload);
    if (res && (res.status === 201 || res.status === 200)) {
      const launch = data.launch || null;
      approvalState.set(taskId, {
        ...idle,
        output: data.output || [],
        payload: launch && (launch.error || launch.status === "Unavailable") ? payload : null,
        launch,
      });
      repaintApprovals();
      await refreshDashboard();
      return;
    }
    if (!res || res.status === 503) {
      const why = netErr || data.error || "no response";
      const error = `could not approve (${why}); Retry sends the same request again and never` +
        " records a second approval";
      approvalState.set(taskId, { ...idle, retry: true, error, payload });
      repaintApprovals();
      return;
    }
    const reload = res.status === 409 && data.reload === true;
    const error = data.error || res.statusText;
    approvalState.set(taskId, { ...idle, reload, error, payload: null });
    repaintApprovals();
    if (!reload && (res.status === 409 || res.status === 404)) await refreshDashboard();
  } finally {
    if (hadFocus) focusApproval();
  }
}

function reloadApproval(taskId) {
  const state = approvalState.get(taskId);
  if (state) approvalState.set(taskId, { ...state, error: null, reload: false });
  refreshDashboard();
}

function repaintApprovals() {
  const node = document.getElementById("approvals");
  if (node && approvalTask) withApprovalFocus(() => node.replaceWith(renderApprovalCenter(approvalTask)));
}

function focusApproval() {
  for (const id of ["approve-retry", "approve-reload", "approve-button"]) {
    const node = document.getElementById(id);
    if (node && !node.disabled) {
      node.focus({ preventScroll: true });
      return;
    }
  }
  const output = document.getElementById("approve-output");
  if (output) output.focus({ preventScroll: true });
}

function withApprovalFocus(fn) {
  const active = document.activeElement;
  const saved = active && APPROVE_FOCUS_IDS.has(active.id) ? active.id : null;
  const preview = document.getElementById("approve-preview");
  const scroll = preview && preview.dataset.taskId
    ? { taskId: preview.dataset.taskId, top: document.getElementById("approve-preview-document")?.scrollTop || 0 }
    : null;
  const result = fn();
  if (scroll) {
    const nextPreview = document.getElementById("approve-preview");
    const nextDocument = document.getElementById("approve-preview-document");
    if (nextPreview?.dataset.taskId === scroll.taskId && nextDocument) {
      nextDocument.scrollTop = scroll.top;
    }
  }
  if (saved) {
    const node = document.getElementById(saved);
    if (node && !node.disabled) node.focus({ preventScroll: true });
  }
  return result;
}
