"use strict";

const implementationStarts = new Map();
let startTaskSnapshot = null;

function renderImplementationStart(task) {
  startTaskSnapshot = task;
  const state = implementationStarts.get(task.id) || {};
  const form = el("div");
  form.id = "implementation-start";
  const label = el("label", "Run with ");
  label.htmlFor = "implementation-host";
  const host = el("select");
  host.id = "implementation-host";
  for (const [value, text] of [["", "Choose a host"], ["codex", "Codex"], ["claude", "Claude"]]) {
    const option = el("option", text);
    option.value = value;
    host.append(option);
  }
  host.value = state.host || state.payload?.host || "";
  host.disabled = !!state.sending || (!!state.retry && !state.preflight);
  host.addEventListener("change", () => {
    implementationStarts.set(task.id, { ...state, host: host.value,
      ...(state.preflight ? { payload: null, retry: false, preflight: false, message: null, error: false } : {}) });
    if (state.preflight) repaintStart(task.id);
    else button.disabled = !host.value || !!state.sending || !!state.reload;
  });
  const button = el("button", state.sending ? "Starting…" : state.retry ? "Retry same start request" : "Start implementation");
  button.id = "implementation-start-button";
  button.type = "button";
  button.disabled = !host.value || !!state.sending || !!state.reload;
  button.addEventListener("click", () => sendImplementationStart(task, state.retry ? state.payload : {
    task: task.id, host: host.value, approval_binding: task.launch.binding,
  }));
  form.append(label, host, button);
  if (state.message) {
    const message = el("p", state.message);
    message.setAttribute("role", state.error ? "alert" : "status");
    form.append(message);
  }
  if (state.reload) {
    const reload = el("button", "Reload current approval");
    reload.id = "implementation-start-reload";
    reload.type = "button";
    reload.addEventListener("click", async () => {
      implementationStarts.delete(task.id);
      await refreshDashboard();
    });
    form.append(reload);
  }
  return form;
}

function repaintStart(taskId) {
  const node = document.getElementById("implementation-start");
  if (node && startTaskSnapshot?.id === taskId) withStartFocus(() => node.replaceWith(renderImplementationStart(startTaskSnapshot)));
}

async function sendImplementationStart(task, payload) {
  if (implementationStarts.get(task.id)?.sending) return;
  implementationStarts.set(task.id, { sending: true, payload });
  repaintStart(task.id);
  const { res, data, netErr } = await postJson("/api/execution/start", payload);
  if (res?.ok) {
    implementationStarts.delete(task.id);
    await refreshDashboard();
  } else {
    const readinessFailure = res?.status === 409 && data.retry === true && data.reload === false;
    const preflight = readinessFailure && data.host_switchable === true;
    const reserved = readinessFailure && !preflight;
    const uncertain = !res || res.status === 503;
    implementationStarts.set(task.id, { payload, host: payload.host, error: true, preflight,
      retry: uncertain || readinessFailure, reload: !uncertain && !readinessFailure,
      message: preflight ? `${data.error}. No new run was reserved by this request. Repair this host or choose another host, then retry.`
        : reserved ? `${data.error}. Execution ${data.execution_id} remains reserved for ${payload.host}. Repair this host and retry the same request; the host and approval stay bound.`
        : uncertain ? `${data.error || netErr || "Response unavailable"}. Retry keeps the same host and approval and reuses its reservation.`
        : `${data.error || res.statusText}. Reload before explicitly starting again.` });
    repaintStart(task.id);
  }
}

function withStartFocus(fn) {
  const id = document.activeElement?.id;
  const result = fn();
  if (["implementation-host", "implementation-start-button", "implementation-start-reload"].includes(id)) {
    const node = document.getElementById(id);
    if (node && !node.disabled) node.focus({ preventScroll: true });
  }
  return result;
}
