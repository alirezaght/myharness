"use strict";

const WORKFLOW_TABS = ["interview", "requirements", "spec", "plan", "diagram", "review", "implementation", "checks", "delivery", "history"];

function authoringRequestId() { return crypto.randomUUID(); }

function authoringFocus(node, state, key) {
  node.dataset.workspaceFocus = `authoring:${state.projectId}:${state.task}:${key}`;
  return node;
}

function authoringDisclosure(node, state, key) {
  node.dataset.workspaceDisclosure = `authoring:${state.projectId}:${state.task}:${key}`;
  return node;
}

function authoringBuffer(state, key, initial, base) {
  state.authoringBuffers ||= {};
  const stored = authoringStoredBuffer(state, key);
  const current = state.authoringBuffers[key];
  if (stored && (!current || Number(stored.generation || 0) >= Number(current.generation || 0))) {
    state.authoringBuffers[key] = current ? Object.assign(current, stored) : stored;
  }
  if (!state.authoringBuffers[key]) state.authoringBuffers[key] = { value: initial, base, dirty: false, generation: 0 };
  const buffer = state.authoringBuffers[key];
  buffer.generation = Number(buffer.generation || 0);
  if (!buffer.dirty) {
    buffer.value = initial;
    buffer.base = base;
    buffer.generation = Number(buffer.generation || 0);
    authoringPersistBuffer(state, key, buffer);
  }
  return buffer;
}

function authoringFormBuffer(state, key, initial, base, token = null) {
  state.authoringBuffers ||= {};
  const stored = authoringStoredBuffer(state, key);
  const current = state.authoringBuffers[key];
  if (stored?.value && typeof stored.value === "object" && !Array.isArray(stored.value)
    && (!current || Number(stored.generation || 0) >= Number(current.generation || 0))) {
    state.authoringBuffers[key] = current ? Object.assign(current, stored) : stored;
  }
  if (!state.authoringBuffers[key]) state.authoringBuffers[key] = { value: structuredClone(initial), base, token, dirty: false, generation: 0 };
  const buffer = state.authoringBuffers[key];
  buffer.generation = Number(buffer.generation || 0);
  if (!buffer.dirty) {
    buffer.value = structuredClone(initial);
    buffer.base = base;
    buffer.token = token;
  }
  authoringPersistBuffer(state, key, buffer);
  return buffer;
}

function authoringStoredBuffer(state, key) {
  try {
    const stored = JSON.parse(localStorage.getItem(`myharness-authoring:${state.projectId}:${state.task}:${key}`));
    return stored && stored.projectId === state.projectId && stored.task === state.task ? stored : null;
  } catch (_) {
    state.storageAvailable = false;
    state.storageError = "Browser storage is unavailable. This draft stays in this page until saved.";
    return null;
  }
}

function authoringFormChanged(state, key, buffer, field, value) {
  buffer.value[field] = value;
  buffer.dirty = true;
  buffer.generation += 1;
  authoringPersistBuffer(state, key, buffer);
  for (const save of document.querySelectorAll(`[data-authoring-form-key="${CSS.escape(key)}"]`)) {
    save.disabled = !buffer.dirty;
  }
}

function authoringScalarChanged(state, key, buffer, value) {
  buffer.value = value;
  buffer.dirty = true;
  buffer.generation = Number(buffer.generation || 0) + 1;
  authoringPersistBuffer(state, key, buffer);
}

function authoringFormControl(state, key, buffer, field, labelText, {
  options, type = "textarea", placeholder = "", focusScope = "",
} = {}) {
  let control;
  if (options) {
    control = el("select");
    for (const [value, label] of options) {
      const option = el("option", label); option.value = value; control.append(option);
    }
  } else if (type === "checkbox") {
    control = el("input"); control.type = "checkbox";
  } else if (type === "input") {
    control = el("input"); control.type = "text";
  } else {
    control = el("textarea"); control.rows = 3;
  }
  control.setAttribute("aria-label", labelText);
  authoringFocus(control, state, `${focusScope ? `${focusScope}:` : ""}${key}:${field}`);
  if (placeholder) control.placeholder = placeholder;
  const stored = buffer.value[field];
  if (type === "checkbox") control.checked = Boolean(stored);
  else control.value = stored == null ? "" : String(stored);
  const update = () => authoringFormChanged(
    state, key, buffer, field, type === "checkbox" ? control.checked : control.value,
  );
  control.addEventListener(type === "checkbox" || options ? "change" : "input", update);
  return control;
}

async function authoringSubmitForm(state, key, buffer, submit, { reset = null, token = null } = {}) {
  const submitted = structuredClone(buffer.value);
  const generation = buffer.generation;
  let result;
  try { result = await submit(submitted); }
  catch (error) {
    if (error.status === 409) {
      const request = Object.entries(state.authoringRequests || {}).find(([, payload]) => payload.request_id === error.payload?.request_id);
      state.authoringConflict = { key: request?.[0] || key, bufferKey: key, message: error.message };
      workspacePersist(state);
    }
    throw error;
  }
  const unchanged = buffer.generation === generation
    && JSON.stringify(buffer.value) === JSON.stringify(submitted);
  if (unchanged) {
    if (reset) buffer.value = structuredClone(reset);
    buffer.dirty = false;
  }
  if (unchanged) buffer.base = state.authoring?.base || buffer.base;
  if (token !== null && unchanged) buffer.token = token(result);
  authoringPersistBuffer(state, key, buffer);
  return result;
}

async function authoringSubmitScalar(state, key, buffer, submitted, submit, { value = submitted } = {}) {
  const generation = Number(buffer.generation || 0);
  const result = await submit();
  if (buffer.value === submitted && Number(buffer.generation || 0) === generation) {
    buffer.value = value;
    buffer.dirty = false;
    buffer.base = state.authoring?.base || buffer.base;
  } else if (!buffer.dirty) buffer.base = state.authoring?.base || buffer.base;
  authoringPersistBuffer(state, key, buffer);
  return result;
}

async function workspaceSaveQuestionAnswer(task, state, question, buffer, field) {
  const submitted = field.value;
  const generation = Number(buffer.generation || 0);
  const result = await authoringAction(task, state, "answer", {
    question: question.id, mode: "answer", answer: submitted, source: null, ref: null,
  }, { expected: buffer.base, key: `answer:${question.id}` });
  const current = state.authoring?.questions?.find(row => row.id === question.id);
  if (buffer.generation !== generation || buffer.value !== submitted) {
    const target = state.authoring?.revision_targets?.find(row => row.target === `question-response:${question.id}`);
    const key = `record-revision:question-response:${question.id}`;
    const revision = state.authoringBuffers?.[key] || {
      value: { mode: "answer", answer: buffer.value, source: null, ref: null },
      dirty: true, generation: buffer.generation,
    };
    revision.value = { mode: "answer", answer: buffer.value, source: null, ref: null };
    revision.dirty = true;
    revision.generation = buffer.generation;
    revision.base = buffer.base;
    revision.token = target?.revision;
    state.authoringBuffers[key] = revision;
    authoringPersistBuffer(state, key, revision);
  } else {
    buffer.value = current?.answer || submitted;
    buffer.dirty = false;
    buffer.base = state.authoring?.base || buffer.base;
    authoringPersistBuffer(state, `answer:${question.id}`, buffer);
  }
  return result;
}

async function workspaceSaveQuestionDeferral(task, state, question, key, buffer) {
  const submitted = structuredClone(buffer.value);
  const generation = Number(buffer.generation || 0);
  const result = await authoringAction(task, state, "answer", {
    question: question.id, mode: "defer", answer: "", source: submitted.source, ref: submitted.ref,
  }, { expected: buffer.base, key: `defer:${question.id}` });
  if (buffer.generation !== generation || JSON.stringify(buffer.value) !== JSON.stringify(submitted)) {
    const target = state.authoring?.revision_targets?.find(row => row.target === `question-response:${question.id}`);
    const correctionKey = `record-revision:question-response:${question.id}`;
    const correction = state.authoringBuffers?.[correctionKey] || {
      value: { mode: "defer", answer: "", source: buffer.value.source, ref: buffer.value.ref }, dirty: true,
    };
    correction.value = { mode: "defer", answer: "", source: buffer.value.source, ref: buffer.value.ref };
    correction.dirty = true;
    correction.generation = buffer.generation;
    correction.base = buffer.base;
    correction.token = target?.revision;
    state.authoringBuffers[correctionKey] = correction;
    authoringPersistBuffer(state, correctionKey, correction);
  } else {
    buffer.value = { source: question.skip_source || submitted.source, ref: question.skip_reason || submitted.ref };
    buffer.dirty = false;
    buffer.base = state.authoring?.base || buffer.base;
    authoringPersistBuffer(state, key, buffer);
  }
  return result;
}

function authoringPersistBuffer(state, key, buffer) {
  const storageKey = `myharness-authoring:${state.projectId}:${state.task}:${key}`;
  try {
    localStorage.setItem(storageKey, JSON.stringify({ ...buffer, projectId: state.projectId, task: state.task }));
    state.storageAvailable = true;
    state.storageError = "";
  } catch (_) {
    state.storageAvailable = false;
    state.storageError = "Browser storage is unavailable. This draft stays in this page until saved.";
  }
}

function workspaceAuthoringStatus(task, state) {
  const authoring = state.authoring || task.authoring;
  if (!authoring) return null;
  const bar = el("div");
  bar.className = "authoring-statusbar";
  bar.setAttribute("role", "status");
  const phase = authoring.phase || task.phase;
  const jobs = authoring.jobs || [];
  const latest = jobs.at(-1);
  const active = latest
    ? `${latest.kind === "investigate" ? "Context preparation" : "Interview model"}: ${latest.state}${latest.error ? ` · ${latest.error}` : ""}`
    : "No authoring job recorded";
  bar.append(el("span", `Project ${authoring.project?.repository || "configured project"} · ${phase}`), el("span", active));
  if (state.storageError) bar.append(el("span", state.storageError));
  return bar;
}

async function workspaceAuthoringRefresh(task, state, { hosts = false } = {}) {
  state.authoringReadSequence = (state.authoringReadSequence || 0) + 1;
  const requestSequence = state.authoringReadSequence;
  const url = `/api/authoring?task=${encodeURIComponent(task.id)}${hosts ? "&hosts=1" : ""}`;
  const response = await fetch(url, { cache: "no-store" });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  const canApply = requestSequence === state.authoringReadSequence
    && Number(data.base?.generation ?? -1) >= Number(state.authoring?.base?.generation ?? -1);
  if (canApply) workspaceAcceptAuthoringSnapshot(state, data);
  else if (hosts && state.authoring) state.authoring = { ...state.authoring, hosts: data.hosts || [] };
  if (workspaceData?.task?.id === task.id) {
    workspaceData.task.authoring = state.authoring || data;
  }
  return state.authoring || data;
}

async function workspaceAuthoringPost(task, state, action, fields, options = {}) {
  const { key = action, expected = state.authoring?.base, retry = true, useExisting = false } = options;
  state.authoringRequests ||= {};
  let payload = state.authoringRequests[key];
  const requested = { task: task.id, expected, ...fields };
  if (payload && useExisting) {
    // Replay the exact prior request after an unknown outcome.
  } else if (payload && retry && JSON.stringify(Object.fromEntries(Object.entries(payload).filter(([name]) => name !== "request_id"))) !== JSON.stringify(requested)) {
    throw new Error("A previous request with different text is unresolved. Retry that exact request or explicitly start a new one.");
  } else if (!payload || !retry) {
    payload = { ...requested, request_id: authoringRequestId() };
    state.authoringRequests[key] = payload;
    workspacePersist(state);
  }
  const response = await fetch(`/api/authoring/${action}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) throw Object.assign(new Error(result.error || `HTTP ${response.status}`), { status: response.status, result, payload });
  await workspaceAuthoringRefresh(task, state);
  if (workspaceData?.task?.id === task.id) await refreshDashboard();
  delete state.authoringRequests[key];
  delete state.authoringRequestActions?.[key];
  delete state.authoringRequestForms?.[key];
  workspacePersist(state);
  return result;
}

function authoringStatus(state, text, isError = false) {
  state.authoringMessage = { text, error: isError };
  workspacePersist(state);
}

function authoringButton(task, state, label, action, fields, opts = {}) {
  const button = workspaceButton(label, async () => {
    button.disabled = true;
    authoringStatus(state, `${label}…`);
    try {
      const result = await action(task, state, fields);
      state.authoringConflict = null;
      authoringStatus(state, result?.job ? "Request queued. Waiting for a persisted worker result." : "Saved.");
    } catch (error) {
      authoringStatus(state, `${error.message}. Your edit and request details are retained.`, true);
      if (error.status === 409 && opts.recovery) {
        state.authoringConflict = { ...opts.recovery, message: error.message, key: opts.key || opts.recovery.key };
        workspacePersist(state);
      }
    }
    workspaceRerender();
  });
  return button;
}

function authoringConflictRecovery(task, state) {
  const conflict = state.authoringConflict;
  if (!conflict) return null;
  const section = el("section"); section.className = "authoring-conflict-recovery";
  section.setAttribute("role", "alert");
  section.append(el("p", `This save was rejected because the server changed: ${conflict.message}. Your draft and rejected request are kept.`));
  section.append(workspaceButton("Refresh latest state and keep my text", async event => {
    try {
      const latest = await workspaceAuthoringRefresh(task, state);
      const buffer = state.authoringBuffers?.[conflict.bufferKey];
      if (buffer) {
        buffer.base = latest.base;
        const typedPrefix = ["record-revision:", "revision-contribution:"].find(prefix => conflict.bufferKey.startsWith(prefix));
        if (buffer.token && typedPrefix) {
          const targetId = conflict.bufferKey.slice(typedPrefix.length);
          buffer.token = latest.revision_targets?.find(item => item.target === targetId)?.revision || buffer.token;
        }
        if (conflict.questionId && conflict.bufferKey.startsWith("question-wording:"))
          buffer.targetRevision = latest.question_revisions?.[String(conflict.questionId)];
        authoringPersistBuffer(state, conflict.bufferKey, buffer);
      }
      const rejected = state.authoringRequests?.[conflict.key];
      if (rejected) {
        state.authoringRequestHistory ||= [];
        state.authoringRequestHistory.push({ action: state.authoringRequestActions?.[conflict.key], key: conflict.key, payload: rejected, outcome: "rejected-stale-base" });
        delete state.authoringRequests[conflict.key];
        delete state.authoringRequestActions?.[conflict.key];
      }
      state.authoringConflict = null;
      state.authoringMessage = { text: "Latest server state loaded. Your text is preserved; saving it now creates a new request against the refreshed revision." };
      workspacePersist(state);
    } catch (error) {
      state.authoringMessage = { text: `Could not refresh the latest state: ${error.message}. Your draft and rejected request remain available.`, error: true };
    }
    workspaceRerender();
  }));
  return section;
}

function authoringAction(task, state, action, fields, options = {}) {
  state.authoringRequestActions ||= {};
  state.authoringRequestActions[options.key || action] = action;
  workspacePersist(state);
  return workspaceAuthoringPost(task, state, action, fields, options);
}

function authoringRetry(task, state, action, key) {
  const saved = state.authoringRequests?.[key];
  if (!saved) return Promise.reject(new Error("There is no saved request to retry."));
  const requestForm = state.authoringRequestForms?.[key];
  const fields = { ...saved }; delete fields.task; delete fields.expected; delete fields.request_id;
  return workspaceAuthoringPost(task, state, action, fields, {
    expected: saved.expected, key, useExisting: true,
  }).then(async result => {
    if (action === "save-spec") {
      const buffer = state.authoringBuffers?.["spec:working"];
      if (buffer) {
        const unchanged = buffer.value === saved.body
          && Number(buffer.generation || 0) === Number(requestForm?.generation ?? buffer.generation ?? 0);
        if (unchanged) {
          buffer.dirty = false;
          buffer.value = state.authoring.working?.body ?? saved.body;
          buffer.base = state.authoring.base;
        }
        authoringPersistBuffer(state, "spec:working", buffer);
      }
    }
    if (action === "followup") {
      const buffer = state.authoringBuffers?.followup;
      if (buffer) {
        const form = requestForm;
        if (form && buffer.value === form.value && buffer.generation === form.generation) { buffer.value = ""; buffer.dirty = false; buffer.base = state.authoring.base; }
        else if (!buffer.dirty) buffer.base = state.authoring.base;
        authoringPersistBuffer(state, "followup", buffer);
      }
      const continuation = state.authoringRequestContinuations?.[key];
      if (continuation?.type === "send") {
        const regenerateKey = `regenerate-after-followup:${saved.request_id}`;
        continuation.regenerateKey = regenerateKey;
        workspacePersist(state);
        const generated = await authoringAction(task, state, "regenerate", { host: continuation.host }, { key: regenerateKey });
        if (generated) delete state.authoringRequestContinuations?.[key];
        workspacePersist(state);
        return generated || result;
      }
    } else if (action === "followup" || key === "followup-only") {
      const buffer = state.authoringBuffers?.followup;
      if (buffer) {
        const form = requestForm;
        if (form && buffer.value === form.value && buffer.generation === form.generation) { buffer.value = ""; buffer.dirty = false; buffer.base = state.authoring.base; }
        else if (!buffer.dirty) buffer.base = state.authoring.base;
        authoringPersistBuffer(state, "followup", buffer);
      }
    }
    for (const [followupKey, continuation] of Object.entries(state.authoringRequestContinuations || {})) {
      if (continuation.regenerateKey === key) {
        delete state.authoringRequestContinuations[followupKey];
        workspacePersist(state);
      }
    }
    return result;
  });
}

function authoringMessage(state) {
  if (!state.authoringMessage) return null;
  const node = el("p", state.authoringMessage.text);
  node.className = state.authoringMessage.error ? "authoring-error" : "authoring-feedback";
  node.setAttribute(state.authoringMessage.error ? "role" : "aria-live", state.authoringMessage.error ? "alert" : "polite");
  const box = el("div"); box.className = "authoring-action-message"; box.append(node);
  const recovery = authoringConflictRecovery({ id: state.task }, state);
  if (recovery) box.append(recovery);
  for (const [key, payload] of Object.entries(state.authoringRequests || {})) {
    const action = state.authoringRequestActions?.[key];
    if (!action || !payload || state.authoringConflict?.key === key) continue;
    const retry = workspaceButton("Retry exact saved request", async () => {
      retry.disabled = true;
      try {
        await authoringRetry({ id: state.task }, state, action, key);
        authoringStatus(state, "The exact saved request and its server result were confirmed.");
      } catch (error) {
        authoringStatus(state, `${error.message}. The exact request remains available for retry.`, true);
      }
      workspaceRerender();
    });
    box.append(retry);
  }
  return box;
}

function workspaceProjectStart(data) {
  const root = el("section"); root.id = "project-start"; root.className = "workspace-empty";
  root.append(el("p", data.project?.repository || "Configured project"), el("h2", "Start an interview"),
    el("p", "Create a task and build its specification here. The selected native host uses Harness’s existing sign-in."));
  const form = el("form"); form.className = "authoring-form";
  const request = el("textarea"); request.required = true; request.maxLength = 16000; request.rows = 5;
  request.setAttribute("aria-label", "What would you like to build?"); request.placeholder = "Describe the task you want to plan";
  const hostLabel = el("label", "Interview model");
  const host = el("select"); host.setAttribute("aria-label", "Interview model");
  for (const [value, label] of [["codex", "Codex"], ["claude", "Claude"]]) { const option = el("option", label); option.value = value; host.append(option); }
  const button = el("button", "Start task and interview"); button.type = "submit";
  const status = el("p"); status.setAttribute("role", "status"); status.className = "authoring-feedback";
  const storageKey = `myharness-start:${data.project?.id || "unknown-project"}`;
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(storageKey)); } catch (_) { status.textContent = "Browser storage is unavailable; keep this page open until the task is created."; }
  if (saved?.request) request.value = saved.request;
  if (saved?.host) host.value = saved.host;
  const persistDraft = () => {
    if (saved?.request_id) return;
    saved = { request: request.value, host: host.value };
    try { localStorage.setItem(storageKey, JSON.stringify(saved)); } catch (_) { /* draft remains in the form */ }
  };
  request.addEventListener("input", persistDraft);
  host.addEventListener("change", persistDraft);
  const next = el("button", "Start a different task instead"); next.type = "button"; next.hidden = !saved;
  next.addEventListener("click", () => { saved = null; try { localStorage.removeItem(storageKey); } catch (_) { /* local draft can be replaced in this form */ } request.value = ""; host.value = "codex"; button.disabled = false; next.hidden = true; status.textContent = "A new task will use a new request identity."; });
  hostLabel.append(host); form.append(request, hostLabel, button, status);
  form.addEventListener("submit", async event => {
    event.preventDefault(); button.disabled = true; status.textContent = "Reserving task and interview…";
    if (saved && (saved.request !== request.value || saved.host !== host.value)) {
      status.textContent = "This page has an unresolved startup request. Retry the exact request or choose ‘Start a different task instead’.";
      button.disabled = false; next.hidden = false; return;
    }
    const payload = saved?.request_id ? saved : { request_id: authoringRequestId(), request: request.value, host: host.value };
    saved = payload;
    try { localStorage.setItem(storageKey, JSON.stringify(payload)); } catch (_) { status.textContent = "Browser storage unavailable; keeping the exact request in this page."; }
    try {
      const response = await fetch("/api/authoring/new", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
      try { localStorage.removeItem(storageKey); } catch (_) { /* task is already reserved server-side */ }
      location.href = `/?task=${encodeURIComponent(result.task)}&tab=interview`;
    } catch (error) { status.textContent = `${error.message}. Retry preserves the exact request identity.`; button.disabled = false; next.hidden = false; }
  });
  form.append(next); root.append(form);
  return root;
}

function workspaceNewTaskDialog(data, state, trigger) {
  const dialog = el("dialog"); dialog.className = "authoring-drawer authoring-new-task-dialog";
  dialog.setAttribute("aria-labelledby", "new-interview-title");
  const header = el("header"); header.className = "authoring-drawer-header";
  const title = el("h2", "Start a new interview"); title.id = "new-interview-title";
  const close = el("button", "Close"); close.type = "button";
  header.append(title, close); dialog.append(header);
  const formRoot = workspaceProjectStart(data);
  formRoot.classList.add("authoring-new-task-form");
  dialog.append(formRoot);
  const closeDialog = () => {
    state.newTaskOpen = false; workspacePersist(state); dialog.close();
    const key = state.newTaskFocusReturnKey;
    setTimeout(() => {
      const focusTarget = key && document.querySelector(`[data-workspace-focus="${CSS.escape(key)}"]`);
      (focusTarget || trigger)?.focus({ preventScroll: true });
    }, 0);
  };
  close.addEventListener("click", closeDialog);
  dialog.addEventListener("cancel", event => { event.preventDefault(); closeDialog(); });
  dialog.addEventListener("close", () => {
    if (state.newTaskReplacing) return;
    state.newTaskOpen = false; workspacePersist(state);
  });
  return dialog;
}

function workspaceEmptyProject(data) {
  const root = el("section"); root.id = "empty-project"; root.className = "workspace-empty";
  if (new URLSearchParams(location.search).has("task") && data.notice) {
    const notice = el("section"); notice.id = "notice"; notice.append(el("p", data.notice));
    root.append(notice);
  }
  root.append(el("p", data.project?.repository || "Configured project"), el("h2", "Interview & Spec"));
  if (data.tasks?.length) root.append(el("p", "Choose a task or start a new interview."));
  root.append(workspaceProjectStart(data));
  return root;
}

function workspaceStartOrResume(task, state) {
  const box = el("section"); box.className = "authoring-card";
  box.append(el("h2", state.authoring?.initialized ? "Interview workspace" : "Start or resume"),
    el("p", state.authoring?.initialized ? "Conversation and drafts are saved for this task." : "This existing task has not started an Interview & Spec workspace."));
  const host = el("select"); host.setAttribute("aria-label", "Interview model");
  for (const [value, label] of [["codex", "Codex"], ["claude", "Claude"]]) { const option = el("option", label); option.value = value; host.append(option); }
  const hostKey = "start-host";
  const hostBuffer = authoringFormBuffer(state, hostKey, { host: "codex" }, state.authoring?.base);
  host.value = hostBuffer.value.host;
  host.addEventListener("change", () => authoringFormChanged(state, hostKey, hostBuffer, "host", host.value));
  const hosts = state.authoring?.hosts || [];
  if (!state.hostsLoaded) {
    state.hostsLoaded = true;
    workspaceAuthoringRefresh(task, state, { hosts: true }).then(data => { state.authoring = data; workspaceRerender(); }).catch(() => { state.hostError = "Host availability could not be checked."; workspaceRerender(); });
  }
  box.append(host);
  for (const row of hosts) {
    const line = el("p", `${row.host}: ${row.available ? `profile verified · ${row.version}` : row.error || "unavailable"}`);
    line.dataset.hostStatus = row.host; box.append(line);
  }
  if (state.hostError) box.append(el("p", state.hostError));
  box.append(authoringButton(task, state, "Start / resume interview", async (t, s) => {
    const selectedHost = hostBuffer.value.host;
    const result = await authoringAction(t, s, "start", { host: selectedHost }, { expected: hostBuffer.base, key: `start:${selectedHost}` });
    if (result.job) s.authoringMessage = { text: "Context preparation queued. The interview will continue after it is recorded." };
    return result;
  }, null));
  return box;
}

function workspaceContributionCard(task, state, item) {
  const card = el("article"); card.className = "authoring-message";
  const actor = item.actor === "developer" ? "You" : "Interview model";
  card.append(el("small", `${actor} · ${item.kind.replaceAll("_", " ")} · ${item.created_at}`));
  const payload = item.payload || {};
  if (payload.body) card.append(el("p", payload.body));
  if (payload.answer) card.append(el("p", payload.answer));
  if (payload.source) card.append(el("p", `Deferral type: ${payload.source}${payload.ref ? ` · ${payload.ref}` : ""}`));
  if (payload.text && item.kind !== "assistant") card.append(el("p", payload.text));
  if (payload.original?.text && item.kind === "question_revision") card.append(el("p", `Earlier wording: ${payload.original.text}`));
  if (payload.status) card.append(el("p", `Decision recorded: ${payload.status}${payload.response ? ` · ${payload.response}` : ""}`));
  if (payload.text && item.kind === "assistant") card.append(el("p", payload.text));
  for (const question of payload.questions || []) {
    const suggestion = el("details"); suggestion.append(el("summary", question.topic), el("p", question.text));
    for (const answer of question.suggestions || []) suggestion.append(el("p", `Suggested answer: ${answer}`));
    card.append(suggestion);
  }
  for (const recommendation of payload.recommendations || []) {
    const note = el("p", `Recommendation: ${recommendation.recommendation} · ${recommendation.rationale}`);
    card.append(note);
  }
  const contributions = workspaceAllContributions(state);
  const superseded = contributions.some(record => record.supersedes === item.id);
  const isHead = !superseded;
  if (item.actor === "developer" && ["answer", "followup", "revision"].includes(item.kind) && isHead) {
    const details = el("details"); details.append(el("summary", "Revise this contribution"));
    const key = `contribution:${item.id}`;
    const buffer = authoringBuffer(state, key, payload.answer || payload.body || payload.text || "", state.authoring.base);
    const field = authoringFocus(el("textarea"), state, `contribution:${item.id}`); field.value = buffer.value; field.rows = 3;
    field.setAttribute("aria-label", "Revised contribution");
    field.addEventListener("input", () => authoringScalarChanged(state, key, buffer, field.value));
    details.append(field, authoringButton(task, state, "Save revision to history", (t, s) => authoringAction(t, s, "revise", {
      target: item.id, target_revision: item.id, body: field.value,
    }, { expected: buffer.base, key: `revise:${item.id}` }), undefined, { recovery: { key: `revise:${item.id}`, bufferKey: key } }));
    card.append(details);
  }
  return card;
}

function workspaceAllContributions(state) {
  const byId = new Map();
  for (const row of state.authoringHistory?.records || []) byId.set(row.id, row);
  for (const row of state.authoring?.contributions || []) byId.set(row.id, { ...byId.get(row.id), ...row });
  const rows = [...byId.values()];
  if (rows.some(row => Number.isFinite(row.cursor))) rows.sort((a, b) => (a.cursor || 0) - (b.cursor || 0));
  return rows;
}

async function workspaceLoadOlderConversation(task, state) {
  state.authoringHistory ||= { records: [], nextBefore: null, loaded: false, loading: false, error: "" };
  const history = state.authoringHistory;
  if (history.loading) return;
  history.loading = true; history.error = ""; workspacePersist(state); workspaceRerender();
  const mergePage = page => {
    const rows = new Map((history.records || []).map(row => [row.id, row]));
    for (const row of page.records || []) rows.set(row.id, row);
    history.records = [...rows.values()].sort((a, b) => a.cursor - b.cursor);
    history.nextBefore = page.next_before;
  };
  try {
    if (!history.loaded) {
      const latest = await fetch(`/api/authoring/history?task=${encodeURIComponent(task.id)}&limit=200`, { cache: "no-store" });
      const latestPage = await latest.json();
      if (!latest.ok) throw new Error(latestPage.error || `HTTP ${latest.status}`);
      // The current snapshot already holds the latest 200. Fetch the next page
      // immediately so the first action reveals genuinely older conversation.
      if (latestPage.next_before) {
        const older = await fetch(`/api/authoring/history?task=${encodeURIComponent(task.id)}&before=${encodeURIComponent(latestPage.next_before)}&limit=50`, { cache: "no-store" });
        const olderPage = await older.json();
        if (!older.ok) throw new Error(olderPage.error || `HTTP ${older.status}`);
        mergePage(latestPage);
        mergePage(olderPage);
      } else mergePage(latestPage);
      history.loaded = true;
    } else if (history.nextBefore) {
      const older = await fetch(`/api/authoring/history?task=${encodeURIComponent(task.id)}&before=${encodeURIComponent(history.nextBefore)}&limit=50`, { cache: "no-store" });
      const page = await older.json();
      if (!older.ok) throw new Error(page.error || `HTTP ${older.status}`);
      mergePage(page);
    }
  } catch (error) { history.error = error.message; }
  // This async page belongs to the task that started it. The state object is
  // task-scoped and cached, but explicitly guard against future reroute changes.
  if (workspaceStates.get(task.id) !== state || state.task !== task.id) return;
  history.loading = false;
  workspacePersist(state); workspaceRerender();
}

function workspaceConversationHistoryPanel(task, state) {
  const section = el("section"); section.className = "authoring-conversation-history";
  section.append(el("h2", "Interview conversation history"));
  const contributions = workspaceAllContributions(state);
  for (const item of [...contributions].reverse()) section.append(workspaceContributionCard(task, state, item));
  const history = state.authoringHistory ||= { records: [], nextBefore: null, loaded: false, loading: false, error: "" };
  if (state.authoring?.history_truncated && !history?.loaded)
    section.append(el("p", "Earlier contributions are available from the task’s saved history."));
  if ((state.authoring?.history_truncated && !history?.loaded)
      || (history?.nextBefore !== null && history?.nextBefore !== undefined))
    section.append(workspaceButton(history.loading ? "Loading earlier conversation…" : "Load earlier conversation", () => workspaceLoadOlderConversation(task, state)));
  if (history?.error) { const error = el("p", history.error); error.className = "authoring-error"; error.setAttribute("role", "alert"); section.append(error); }
  const status = el("p", `${contributions.length} conversation records loaded for this task.`); status.setAttribute("role", "status"); section.append(status);
  return section;
}

function questionAnswerHistory(state, questionId) {
  const contributions = workspaceAllContributions(state);
  const history = contributions.filter(item => item.kind === "answer" && item.payload?.question === questionId);
  const ids = new Set(history.map(item => item.id));
  let changed = true;
  while (changed) {
    changed = false;
    for (const item of contributions) {
      if (item.kind === "revision" && ids.has(item.payload?.target) && !ids.has(item.id)) {
        history.push(item); ids.add(item.id); changed = true;
      }
    }
  }
  return history;
}

function workspaceRevisionTargetCard(task, state, target) {
  const card = el("article"); card.className = "authoring-record authoring-revision-target";
  const original = target.original || {};
  const kind = target.kind;
  const label = kind === "question_response" ? `Question Q${original.id} response`
    : kind === "question_wording" ? `Question Q${original.id} wording`
      : kind === "recommendation_decision" ? `Recommendation P${original.id} decision`
        : `Contribution ${target.target}`;
  card.append(el("h3", label));
  card.append(el("p", `Record status: ${target.status}${target.applied ? " · applied" : ""}`));
  if (target.provenance) {
    const provenance = target.provenance;
    card.append(el("p", `Source: ${provenance.kind}${provenance.created_at ? ` · ${provenance.created_at}` : ""}`));
  }
  const valueText = value => typeof value === "string" ? value : JSON.stringify(value, null, 2);
  card.append(el("p", `Recorded value: ${valueText(target.original?.answer ?? target.original?.text ?? target.original?.recommendation ?? target.original?.status ?? target.original)}`));
  const effectiveLabel = target.status === "pending" && target.contribution
    ? "Saved proposed correction (not applied)"
    : "Current effective value";
  card.append(el("p", `${effectiveLabel}: ${valueText(target.effective)}`));
  if (!target.editable) {
    const retired = el("p", `This is a retired record. Its current successor is ${target.current_target || "unavailable"}.`);
    retired.className = "authoring-retired-record"; card.append(retired);
    if (target.current_target) {
      const successor = (state.authoring?.revision_targets || []).find(row => row.target === target.current_target);
      if (successor) card.append(workspaceButton("Open current successor", () => {
        state.revisionTargetFocus = successor.target;
        workspacePersist(state);
        workspaceRerender();
        setTimeout(() => document.querySelector(`[data-revision-target="${CSS.escape(successor.target)}"]`)?.scrollIntoView({ block: "center" }), 0);
      }));
    }
    return card;
  }
  card.dataset.revisionTarget = target.target;
  const effective = target.effective;
  if (kind === "contribution") {
    const body = effective?.body || effective?.answer || effective?.text;
    if (typeof body !== "string") return card;
    const key = `revision-contribution:${target.target}`;
    const buffer = authoringFormBuffer(state, key, { body }, state.authoring?.base, target.revision);
    const field = authoringFormControl(state, key, buffer, "body", "Revised contribution", { placeholder: "Your correction" });
    const save = authoringButton(task, state, "Save revision to history", () => authoringSubmitForm(state, key, buffer, values => authoringAction(task, state, "revise", {
      target: target.target, target_revision: buffer.token, body: values.body,
    }, { expected: buffer.base, key: `revise:${target.target}` }), { token: result => result?.contribution || buffer.token }));
    save.dataset.authoringFormKey = key;
    save.disabled = !buffer.dirty;
    card.append(field, save);
    return card;
  }
  const initial = kind === "question_wording" ? { text: effective }
    : kind === "question_response" ? { mode: effective.mode || "unknown", answer: effective.answer || "", source: effective.source || "decision", ref: effective.ref || "" }
      : kind === "recommendation_decision" ? { status: effective.status || "pending", response: effective.response || "" }
        : null;
  if (!initial) return card;
  const key = `record-revision:${target.target}`;
  const buffer = authoringFormBuffer(state, key, initial, state.authoring?.base, target.revision);
  const status = target.status;
  if (status === "pending" && target.contribution) {
    card.append(el("p", "A correction is saved in history and still needs explicit application."));
    card.append(authoringButton(task, state, "Apply this saved correction", async () => {
      const result = await authoringAction(task, state, "planning", {
        operation: { op: "revision-apply", target: target.target, target_revision: target.revision },
      }, { key: `apply-record-revision:${target.target}:${target.revision}` });
      return result;
    }));
  }
  const form = el("div"); form.className = "authoring-form";
  if (kind === "question_wording") {
    form.append(authoringFormControl(state, key, buffer, "text", "Corrected question wording", { placeholder: "Question wording" }));
  } else if (kind === "question_response") {
    const conditional = el("div"); conditional.className = "authoring-response-fields";
    const renderResponseFields = () => {
      conditional.replaceChildren();
      if (buffer.value.mode === "answer") conditional.append(authoringFormControl(state, key, buffer, "answer", "Corrected answer", { placeholder: "Answer" }));
      if (buffer.value.mode === "defer") {
        conditional.append(authoringFormControl(state, key, buffer, "source", "Deferral type", { options: [["decision", "Approved decision"], ["codebase", "Codebase reference"], ["not_relevant", "Not relevant"]] }));
        conditional.append(authoringFormControl(state, key, buffer, "ref", "Deferral reason or reference", { placeholder: "Reason, code reference, or approved DEC-ID" }));
      }
    };
    const mode = authoringFormControl(state, key, buffer, "mode", "Response type", { options: [["answer", "Answer"], ["unknown", "Unknown for now"], ["defer", "Defer with a reason"]] });
    mode.addEventListener("change", renderResponseFields);
    form.append(mode, conditional);
    renderResponseFields();
  } else {
    form.append(authoringFormControl(state, key, buffer, "status", "Recommendation decision", { options: [["accepted", "Accept"], ["rejected", "Reject"], ["modified", "Modify"]] }));
    form.append(authoringFormControl(state, key, buffer, "response", "Decision response", { placeholder: "Required when modifying" }));
  }
  const save = authoringButton(task, state, "Save correction to history", () => authoringSubmitForm(state, key, buffer, async values => {
    const value = kind === "question_wording" ? values.text
      : kind === "question_response" ? {
        mode: values.mode,
        answer: values.mode === "answer" ? values.answer : "",
        source: values.mode === "defer" ? values.source : null,
        ref: values.mode === "defer" ? values.ref : null,
      }
        : { status: values.status, response: values.response };
    if (kind === "question_wording") {
      return authoringAction(task, state, "revise", {
        target: target.target, target_revision: buffer.token, body: values.text,
      }, { expected: buffer.base, key: `revise:${target.target}` });
    }
    return authoringAction(task, state, "revise-record", {
      target: target.target, target_revision: buffer.token, value,
    }, { expected: buffer.base, key: `record-revision:${target.target}` });
  }, { token: result => {
    const current = (state.authoring?.revision_targets || []).find(row => row.target === target.target);
    return current?.revision || result?.contribution || buffer.token;
  } }));
  save.dataset.authoringFormKey = key;
  save.disabled = !buffer.dirty;
  form.append(save); card.append(form);
  return card;
}

function workspaceRevisionTargets(task, state) {
  const section = el("section"); section.className = "authoring-revision-targets";
  section.append(el("h2", "Recorded answers and decision history"));
  const targets = state.authoring?.revision_targets || [];
  const editableRecords = targets.filter(item => ["question_wording", "question_response", "recommendation_decision", "contribution"].includes(item.kind));
  if (!editableRecords.length) section.append(el("p", "No recorded answer or recommendation decisions yet."));
  for (const item of editableRecords) section.append(workspaceRevisionTargetCard(task, state, item));
  return section;
}

async function workspaceSaveQuestionWording(task, state, question, buffer, field) {
  const submitted = field.value;
  const generation = Number(buffer.generation || 0);
  const result = await authoringAction(task, state, "revise", {
    target: `question:${question.id}`, target_revision: buffer.targetRevision, body: submitted,
  }, { expected: buffer.base, key: `question-wording:${question.id}` });
  // A successful save advances the base/token even if the developer kept
  // typing while it was in flight; the newer draft remains dirty and can be
  // saved as the next revision of this confirmed head.
  buffer.targetRevision = result.contribution;
  buffer.base = state.authoring.base;
  buffer.dirty = true;
  if (buffer.generation === generation && buffer.value === submitted) buffer.value = submitted;
  authoringPersistBuffer(state, `question-wording:${question.id}`, buffer);
  return result;
}

function workspaceQuestionCard(task, state, question, surface = "interview") {
  const focus = (node, key) => authoringFocus(node, state, `${surface}:${key}`);
  const card = el("article"); card.className = "authoring-question"; card.dataset.questionId = question.id;
  const heading = el("div"); heading.className = "authoring-question-heading";
  heading.append(el("small", `Q${question.id} · ${question.kind || "question"} · ${question.status || "open"}`), el("h3", question.text));
  card.append(heading);
  const history = workspaceAllContributions(state).filter(item => item.payload?.original?.id === question.id || item.payload?.target === `question:${question.id}`);
  if (history.length) {
    const details = authoringDisclosure(el("details"), state, `${surface}:question:${question.id}:history`);
    details.append(el("summary", "Wording and answer history"));
    for (const item of history) details.append(workspaceContributionCard(task, state, item));
    card.append(details);
  }
  if (question.answer) card.append(el("p", `Your recorded answer: ${question.answer}`));
  if (question.skip_source) card.append(el("p", `Your recorded deferral: ${question.skip_source}${question.skip_reason ? ` · ${question.skip_reason}` : ""}`));
  if (question.answer || question.skip_source) {
    const prior = questionAnswerHistory(state, question.id);
    for (const item of prior) card.append(workspaceContributionCard(task, state, item));
    if (question.answer) {
      const key = `answer:${question.id}`;
      const buffer = authoringBuffer(state, key, question.answer, state.authoring?.base);
      const revise = authoringDisclosure(el("details"), state, `${surface}:question:${question.id}:answer-revision`);
      revise.append(el("summary", "Revise your answer"));
      const field = focus(el("textarea"), `answer:${question.id}`); field.rows = 3; field.value = buffer.value; field.setAttribute("aria-label", `Revised answer for question ${question.id}`);
      field.addEventListener("input", () => authoringScalarChanged(state, key, buffer, field.value));
      const current = prior.find(item => !prior.some(other => other.supersedes === item.id));
      revise.append(field);
      if (current) revise.append(authoringButton(task, state, "Save answer revision to history", () => authoringSubmitScalar(state, key, buffer, field.value,
        () => authoringAction(task, state, "revise", { target: current.id, target_revision: current.id, body: field.value }, {
          expected: buffer.base, key: `answer-revision:${question.id}`,
        }), { value: field.value }), undefined, { recovery: { key: `answer-revision:${question.id}`, bufferKey: key } }));
      else revise.append(el("p", "This answer predates editable contribution history. Its recorded decision remains available for review."));
      card.append(revise);
    }
    if (question.skip_source) {
      const key = `defer-reason:${question.id}`;
      const buffer = authoringFormBuffer(state, key, { source: question.skip_source, reason: question.skip_reason || question.skip_ref || "" }, state.authoring?.base);
      const revise = authoringDisclosure(el("details"), state, `${surface}:question:${question.id}:deferral-revision`);
      revise.append(el("summary", "Revise this deferral"));
      const source = authoringFormControl(state, key, buffer, "source", "Updated deferral type", { options: [["decision", "Approved decision"], ["codebase", "Codebase reference"], ["not_relevant", "Not relevant"]], focusScope: surface });
      const reason = authoringFormControl(state, key, buffer, "reason", "Updated deferral reason", { placeholder: "Reason, code reference, or approved DEC-ID", focusScope: surface });
      const current = prior.find(item => !prior.some(other => other.supersedes === item.id));
      revise.append(source, reason);
      if (current) revise.append(authoringButton(task, state, "Save deferral revision to history", () => authoringSubmitForm(state, key, buffer, values => authoringAction(task, state, "revise", {
        target: current.id, target_revision: current.id, body: `${values.source}: ${values.reason}`,
      }, { expected: buffer.base, key: `deferral-revision:${question.id}` }), { token: result => result?.contribution || buffer.token }), undefined, { recovery: { key: `deferral-revision:${question.id}`, bufferKey: key } }));
      else revise.append(el("p", "This deferral predates editable contribution history; it remains visible above."));
      card.append(revise);
    }
  } else {
  const answerKey = `answer:${question.id}`;
  const answerBuffer = authoringBuffer(state, answerKey, "", state.authoring?.base);
  const answer = focus(el("textarea"), answerKey); answer.rows = 3; answer.placeholder = "Your answer"; answer.value = answerBuffer.value;
  answer.setAttribute("aria-label", `Answer question ${question.id}`);
  answer.addEventListener("input", () => authoringScalarChanged(state, answerKey, answerBuffer, answer.value));
  const suggestions = workspaceAllContributions(state).flatMap(item => item.payload?.questions || [])
    .find(item => item.text === question.text)?.suggestions || [];
  if (suggestions.length) {
    const label = el("label", "Suggested answer"); const choice = el("select");
    const empty = el("option", "Choose a suggestion or write your own"); empty.value = ""; choice.append(empty);
    for (const text of suggestions) { const option = el("option", text); option.value = text; choice.append(option); }
    choice.addEventListener("change", () => {
      if (choice.value) { answer.value = choice.value; authoringScalarChanged(state, answerKey, answerBuffer, choice.value); }
    });
    label.append(choice); card.append(label);
    const rationale = (state.authoring?.recommendations || []).find(item => item.question_id === question.id);
    if (rationale) card.append(el("p", `Model recommendation: ${rationale.recommendation}. ${rationale.rationale}`));
  }
  card.append(answer);
  const buttons = el("div"); buttons.className = "authoring-actions";
  buttons.append(authoringButton(task, state, "Save answer", () => workspaceSaveQuestionAnswer(task, state, question, answerBuffer, answer), undefined, { recovery: { key: `answer:${question.id}`, bufferKey: answerKey } }));
  buttons.append(authoringButton(task, state, "I don’t know yet", (t, s) => authoringAction(t, s, "answer", {
    question: question.id, mode: "unknown", answer: "", source: null, ref: null,
  }, { expected: answerBuffer.base, key: `answer-unknown:${question.id}` })));
  const defer = authoringDisclosure(el("details"), state, `${surface}:question:${question.id}:new-deferral`);
  defer.append(el("summary", "Defer with a reason"));
  const deferKey = `defer-reason:${question.id}`;
  const deferBuffer = authoringFormBuffer(state, deferKey, { source: "decision", ref: "" }, state.authoring?.base);
  const source = authoringFormControl(state, deferKey, deferBuffer, "source", "Reason type", { options: [["decision", "Approved decision"], ["codebase", "Codebase reference"], ["not_relevant", "Not relevant"]], focusScope: surface });
  const reason = authoringFormControl(state, deferKey, deferBuffer, "ref", "Deferral reason", { placeholder: "Reason, code reference, or approved DEC-ID", focusScope: surface });
  defer.append(source, reason, authoringButton(task, state, "Save deferral", () => workspaceSaveQuestionDeferral(task, state, question, deferKey, deferBuffer), undefined, { recovery: { key: `defer:${question.id}`, bufferKey: deferKey } }));
  buttons.append(defer);
  card.append(buttons);
  }
  const wordKey = `question-wording:${question.id}`;
  const wordingBuffer = authoringBuffer(state, wordKey, question.text, state.authoring?.base);
  if (!wordingBuffer.dirty || !wordingBuffer.targetRevision)
    wordingBuffer.targetRevision = state.authoring?.question_revisions?.[String(question.id)];
  const reviseWording = authoringDisclosure(el("details"), state, `${surface}:question:${question.id}:wording`);
  reviseWording.append(el("summary", "Revise question wording"));
  const wording = focus(el("textarea"), wordKey); wording.value = wordingBuffer.value; wording.rows = 2; wording.setAttribute("aria-label", `Revised wording for question ${question.id}`);
  wording.addEventListener("input", () => authoringScalarChanged(state, wordKey, wordingBuffer, wording.value));
  reviseWording.append(wording, authoringButton(task, state, "Save wording revision", (t, s) => workspaceSaveQuestionWording(t, s, question, wordingBuffer, wording), undefined, { recovery: { key: `question-wording:${question.id}`, bufferKey: wordKey, questionId: question.id } }));
  if (state.authoring?.phase === "interview" && wordingBuffer.value !== question.text) {
    reviseWording.append(authoringButton(task, state, "Apply this wording in the interview", async (t, s) => {
      const submitted = wordingBuffer.value;
      const submittedGeneration = Number(wordingBuffer.generation || 0);
      const result = await authoringAction(t, s, "planning", {
        operation: { op: "question-wording", question_id: question.id, text: submitted },
      }, { expected: wordingBuffer.base, key: `apply-question-wording:${question.id}` });
      if (wordingBuffer.value === submitted && wordingBuffer.generation === submittedGeneration) { wordingBuffer.dirty = false; wordingBuffer.value = submitted; }
      else wordingBuffer.dirty = true;
      wordingBuffer.targetRevision = s.authoring.question_revisions[String(question.id)];
      wordingBuffer.base = s.authoring.base; authoringPersistBuffer(s, wordKey, wordingBuffer); return result;
    }));
  }
  card.append(reviseWording);
  return card;
}

function workspaceInterviewDrawer(task, state, focusReturn) {
  const dialog = el("dialog"); dialog.className = "authoring-drawer"; dialog.setAttribute("aria-labelledby", "interview-drawer-title");
  const header = el("div"); header.className = "authoring-drawer-header";
  const title = el("h2", "Interview question"); title.id = "interview-drawer-title";
  const restoreFocus = () => {
    const targets = [...document.querySelectorAll("[data-workspace-focus]")];
    const target = targets.find(node => node.dataset.workspaceFocus === state.drawerFocusReturnKey)
      || targets.find(node => node.dataset.workspaceFocus === `interview-drawer:${task.id}`)
      || focusReturn;
    target?.focus({ preventScroll: true });
  };
  const close = el("button", "Close interview"); close.type = "button"; close.addEventListener("click", () => {
    state.drawerReplacing = false; state.drawerOpen = false; workspacePersist(state);
    dialog.close(); setTimeout(restoreFocus, 0);
  });
  const questions = state.authoring?.questions || [];
  const question = questions.find(item => item.id === state.drawerQuestionId)
    || questions.find(item => !item.answer && !item.skip_source);
  header.append(title, close); dialog.append(header);
  if (question) dialog.append(workspaceQuestionCard(task, state, question, "drawer"));
  else dialog.append(el("p", "No unanswered question is currently recorded."));
  const message = authoringMessage(state); if (message) dialog.append(message);
  dialog.addEventListener("close", () => {
    if (state.drawerReplacing) return;
    state.drawerOpen = false; workspacePersist(state);
    restoreFocus();
  });
  dialog.addEventListener("click", event => { if (event.target === dialog) dialog.close(); });
  return dialog;
}

function workspaceInterviewPanel(task, state, inspector) {
  const panel = el("section"); panel.append(el("h2", "Interview"), el("p", "Your conversation and answers are saved against this task. Model suggestions remain separate from your decisions."));
  panel.append(workspaceStartOrResume(task, state));
  const questions = state.authoring?.questions || [];
  const pending = questions.find(item => !item.answer && !item.skip_source);
  if (pending) {
    const alert = el("div"); alert.className = "authoring-question-alert"; alert.setAttribute("role", "status");
    alert.append(el("span", `Question Q${pending.id} needs your answer: ${pending.text}`));
    const open = el("button", "Open question drawer"); open.type = "button";
    open.addEventListener("click", () => { state.drawerOpen = true; workspacePersist(state); workspaceRerender(); });
    alert.append(open); panel.append(alert);
  }
  for (const question of questions) {
    const card = workspaceQuestionCard(task, state, question);
    if (!question.answer && !question.skip_source) {
      const open = el("button", `Open Q${question.id} in drawer`); open.type = "button";
      open.dataset.workspaceFocus = `question-drawer:${task.id}:${question.id}`;
      open.setAttribute("aria-haspopup", "dialog");
      open.addEventListener("click", () => {
        state.drawerQuestionId = question.id; state.drawerFocusReturnKey = open.dataset.workspaceFocus;
        state.drawerOpen = true; workspacePersist(state); workspaceRerender();
      });
      card.insertBefore(open, card.children[1] || null);
    }
    panel.append(card);
  }
  const recommendations = state.authoring?.recommendations || [];
  if (recommendations.length) {
    panel.append(el("h3", "Model recommendations and your decisions"));
    for (const proposal of recommendations) {
      const card = el("article"); card.className = "authoring-card";
      card.append(el("h4", proposal.title), el("p", proposal.recommendation), el("p", `Rationale: ${proposal.rationale}`));
      if (proposal.impact) card.append(el("p", `Impact: ${proposal.impact}`));
      card.append(el("p", `Decision status: ${proposal.status}`));
      if (proposal.status === "pending") {
        const key = `recommendation-decision:${proposal.id}`;
        const buffer = authoringFormBuffer(state, key, { status: "accepted", response: "" }, state.authoring?.base, proposal.id);
        const response = authoringFormControl(state, key, buffer, "response", `Response to ${proposal.title}`, { placeholder: "Required if modifying" });
        const choices = el("div"); choices.className = "authoring-actions";
        for (const status of ["accepted", "rejected", "modified"]) choices.append(authoringButton(task, state, `${status[0].toUpperCase()}${status.slice(1)} recommendation`, () => authoringSubmitForm(state, key, buffer, values => authoringAction(task, state, "decide", {
          proposal: proposal.id, status, response: values.response,
        }, { expected: buffer.base, key: `decision:${proposal.id}:${status}` }), { reset: { status: "accepted", response: "" } })));
        card.append(response, choices);
      } else if (proposal.response) card.append(el("p", `Your recorded response: ${proposal.response}`));
      panel.append(card);
    }
  }
  const followup = el("section"); followup.className = "authoring-card"; followup.append(el("h3", "Continue the conversation"));
  const buffer = authoringBuffer(state, "followup", "", state.authoring?.base);
  const input = authoringFocus(el("textarea"), state, "followup"); input.rows = 3; input.value = buffer.value; input.placeholder = "Ask a follow-up or add context"; input.setAttribute("aria-label", "Follow-up message");
  input.addEventListener("input", () => authoringScalarChanged(state, "followup", buffer, input.value));
  followup.append(input, authoringButton(task, state, "Send to the interview model", async (t, s) => {
    const submitted = input.value;
    const submittedGeneration = buffer.generation;
    s.authoringRequests ||= {}; s.authoringRequestActions ||= {}; s.authoringRequestContinuations ||= {};
    const hasSavedSend = Boolean(s.authoringRequests.followup);
    if (!hasSavedSend) s.authoringRequests.followup = {
      task: t.id, expected: buffer.base, body: submitted, request_id: authoringRequestId(),
    };
    const followupRequestId = s.authoringRequests.followup.request_id;
    s.authoringRequestActions.followup = "followup";
    if (!hasSavedSend) {
      s.authoringRequestContinuations.followup = { type: "send", host: host.value, requestId: followupRequestId };
      s.authoringRequestForms ||= {};
      s.authoringRequestForms.followup = { key: "followup", value: submitted, generation: submittedGeneration };
    }
    workspacePersist(s);
    const sent = await authoringAction(t, s, "followup", { body: submitted }, { expected: buffer.base, key: "followup" });
    if (buffer.value === submitted && buffer.generation === submittedGeneration) { buffer.value = ""; buffer.dirty = false; buffer.base = s.authoring.base; }
    else if (!buffer.dirty) buffer.base = s.authoring.base;
    authoringPersistBuffer(s, "followup", buffer);
    const continuation = s.authoringRequestContinuations.followup;
    const regenerateKey = continuation?.regenerateKey || `regenerate-after-followup:${followupRequestId}`;
    if (continuation) continuation.regenerateKey = regenerateKey;
    workspacePersist(s);
    const generated = await authoringAction(t, s, "regenerate", { host: continuation?.host || host.value }, { key: regenerateKey });
    delete s.authoringRequestContinuations?.followup;
    workspacePersist(s);
    return generated || sent;
  }, undefined, { recovery: { key: "followup", bufferKey: "followup" } }));
  followup.append(authoringButton(task, state, "Save follow-up only", async (t, s) => {
    const submitted = input.value;
    const submittedGeneration = buffer.generation;
    s.authoringRequestForms ||= {};
    s.authoringRequestForms["followup-only"] = { key: "followup", value: submitted, generation: submittedGeneration };
    const result = await authoringAction(t, s, "followup", { body: submitted }, { expected: buffer.base, key: "followup-only" });
    if (buffer.value === submitted && buffer.generation === submittedGeneration) { buffer.value = ""; buffer.dirty = false; buffer.base = s.authoring.base; }
    else if (!buffer.dirty) buffer.base = s.authoring.base;
    authoringPersistBuffer(s, "followup", buffer); return result;
  }, undefined, { recovery: { key: "followup-only", bufferKey: "followup" } }));
  const host = el("select"); host.setAttribute("aria-label", "Model for the next response");
  authoringFocus(host, state, "followup-host:host");
  for (const [value, label] of [["codex", "Codex"], ["claude", "Claude"]]) { const option = el("option", label); option.value = value; host.append(option); }
  const hostKey = "followup-host";
  const hostBuffer = authoringFormBuffer(state, hostKey, { host: "codex" }, state.authoring?.base);
  host.value = hostBuffer.value.host;
  host.addEventListener("change", () => authoringFormChanged(state, hostKey, hostBuffer, "host", host.value));
  const hostStatus = (state.authoring?.hosts || []).find(row => row.host === hostBuffer.value.host);
  followup.append(el("p", `Selected response host: ${hostBuffer.value.host}${hostStatus ? hostStatus.available ? ` · available${hostStatus.version ? ` · ${hostStatus.version}` : ""}` : ` · unavailable: ${hostStatus.error || "not verified"}` : " · availability not checked"}. Requests use this host.`));
  followup.append(host, authoringButton(
    task,
    state,
    "Request model response",
    (t, s) => authoringAction(t, s, "regenerate", { host: host.value }, { key: "regenerate" }),
    undefined,
    { recovery: { key: "regenerate", bufferKey: hostKey } },
  ));
  panel.append(followup);
  panel.append(workspaceConversationHistoryPanel(task, state));
  panel.append(workspaceRevisionTargets(task, state));
  if (state.authoring?.jobs?.length) {
    panel.append(el("h3", "Model activity"));
    for (const job of [...state.authoring.jobs].reverse()) {
      const row = el("article", `${job.kind} · ${job.host} · ${job.state}${job.settlement === "unknown" ? " · outcome unknown" : ""}${job.error ? ` · ${job.error}` : ""}`);
      row.className = "authoring-job";
      row.dataset.jobId = job.id;
      if (["queued", "running"].includes(job.state)) row.append(authoringButton(task, state, "Cancel request", (t, s) => authoringAction(t, s, "cancel-job", { job: job.id }, { key: `cancel:${job.id}` })));
      if (["failed", "stale", "cancelled", "interrupted"].includes(job.state)) {
        const retryForm = el("div");
        let acknowledge = null;
        if (job.settlement === "unknown") {
          const ackLabel = el("label", "I understand the previous provider outcome is unknown and may already have completed.");
          acknowledge = el("input"); acknowledge.type = "checkbox";
          const storedAck = state.authoringRetryAcknowledgements?.[job.id];
          acknowledge.checked = storedAck?.settlement === "unknown" && storedAck?.job === job.id
            && storedAck?.attempt === job.attempt && storedAck?.claim === job.claim;
          acknowledge.addEventListener("change", () => {
            state.authoringRetryAcknowledgements ||= {};
            if (acknowledge.checked) state.authoringRetryAcknowledgements[job.id] = {
              settlement: "unknown", job: job.id, attempt: job.attempt, claim: job.claim,
            };
            else delete state.authoringRetryAcknowledgements[job.id];
            workspacePersist(state);
          });
          ackLabel.prepend(acknowledge);
          retryForm.append(ackLabel);
        }
        const retry = authoringButton(task, state, "Retry this request", (t, s) => authoringAction(t, s, "retry-job", {
          job: job.id, acknowledge_unknown: job.settlement === "unknown" && acknowledge.checked,
        }, { key: `retry:${job.id}` }));
        retry.disabled = job.settlement === "unknown" && !acknowledge.checked;
        acknowledge?.addEventListener("change", () => { retry.disabled = !acknowledge.checked; });
        retryForm.append(retry); row.append(retryForm);
      }
      panel.append(row);
    }
  }
  const message = authoringMessage(state); if (message) panel.append(message);
  inspector.append(el("h2", "Interview context"), el("p", `Task ${task.id} · ${task.phase}`), el("p", `Project ${state.authoring?.project?.repository || "configured project"}`));
  return panel;
}

function workspaceRequirementsPanel(task, state, inspector) {
  const panel = workspaceRequirements(task, state, inspector);
  const questions = state.authoring?.questions || [];
  const controls = workspacePlanningControls(task, state, { interview: true });
  panel.append(controls);
  panel.append(el("h2", "Recorded questions and decisions"));
  for (const question of questions) {
    const row = el("article"); row.className = "authoring-record";
    row.append(el("h3", `Q${question.id} · ${question.topic}`), el("p", question.text));
    if (question.answer) row.append(el("p", `Developer answer: ${question.answer}`));
    if (question.skip_source) row.append(el("p", `Deferred by developer · ${question.skip_source}${question.skip_ref ? ` · ${question.skip_ref}` : ""}`));
    const revise = el("details"); revise.append(el("summary", "Revise question wording"));
    const key = `question-wording:${question.id}`;
    const wordingBuffer = authoringBuffer(state, key, question.text, state.authoring.base);
    if (!wordingBuffer.dirty || !wordingBuffer.targetRevision) wordingBuffer.targetRevision = state.authoring.question_revisions[String(question.id)];
    const wording = authoringFocus(el("textarea"), state, key); wording.value = wordingBuffer.value; wording.rows = 2; wording.setAttribute("aria-label", `Revised wording for question ${question.id}`);
    wording.addEventListener("input", () => authoringScalarChanged(state, key, wordingBuffer, wording.value));
    revise.append(wording, authoringButton(task, state, "Save wording revision", (t, s) => workspaceSaveQuestionWording(t, s, question, wordingBuffer, wording), undefined, { recovery: { key, bufferKey: key, questionId: question.id } }));
    row.append(revise);
    panel.append(row);
  }
  panel.append(el("h2", "Unresolved requirements"));
  if (state.authoring?.findings?.length) for (const finding of state.authoring.findings) panel.append(el("p", finding));
  else panel.append(el("p", "No authoring findings currently reported."));
  panel.append(workspaceRevisionTargets(task, state));
  return panel;
}

function workspacePlanningControls(task, state, options = {}) {
  const section = el("section"); section.className = "authoring-planning"; section.append(el("h2", "Planning controls"));
  const current = state.authoring?.phase || task.phase;
  section.append(el("p", `Current phase: ${current}. These controls use the existing Harness phase and validation rules.`));
  const advance = authoringButton(
    task,
    state,
    "Continue to next phase",
    (t, s) => authoringAction(
      t, s, "planning", { operation: { op: "advance" } }, { key: "phase-advance" },
    ),
    undefined,
    { recovery: { key: "phase-advance" } },
  );
  const backForm = el("details"); backForm.append(el("summary", "Return to an earlier phase"));
  const backKey = "phase-back-form";
  const backBuffer = authoringFormBuffer(state, backKey, { phase: "investigation", reason: "" }, state.authoring?.base);
  const phase = authoringFormControl(state, backKey, backBuffer, "phase", "Earlier phase", { options: ["investigation", "interview", "design", "test_plan", "final_review"].map(value => [value, value.replaceAll("_", " ")]) });
  const reason = authoringFormControl(state, backKey, backBuffer, "reason", "Reason for returning", { placeholder: "Why should this task return to that phase?" });
  backForm.append(phase, reason, authoringButton(task, state, "Return with this reason", () => authoringSubmitForm(state, backKey, backBuffer, values => authoringAction(task, state, "planning", { operation: { op: "back", phase: values.phase, reason: values.reason } }, { expected: backBuffer.base, key: "phase-back" }), { reset: { phase: "investigation", reason: "" } })));
  section.append(advance, backForm);
  if (options.interview || current === "interview") {
    const ask = el("details"); ask.open = true; ask.append(el("summary", "Add an interview question"));
    const addKey = "question-add-form";
    const addBuffer = authoringFormBuffer(state, addKey, { topic: "", text: "", decision: false }, state.authoring?.base);
    const topic = authoringFormControl(state, addKey, addBuffer, "topic", "Question topic", { type: "input", placeholder: "Topic" });
    const text = authoringFormControl(state, addKey, addBuffer, "text", "Question wording", { placeholder: "Question wording" });
    const decisionLabel = el("label", "Developer decision required"); const decision = authoringFormControl(state, addKey, addBuffer, "decision", "Developer decision required", { type: "checkbox" }); decisionLabel.prepend(decision);
    ask.append(topic, text, decisionLabel, authoringButton(task, state, "Add question", () => authoringSubmitForm(state, addKey, addBuffer, values => authoringAction(task, state, "planning", {
      operation: { op: "question-ask", topic: values.topic, text: values.text, decision: values.decision },
    }, { expected: addBuffer.base, key: "question-add" }), { reset: { topic: "", text: "", decision: false } })));
    section.append(ask);
  }
  const planning = state.authoring?.planning;
  if (current === "design") {
    const design = el("details"); design.open = true; design.append(el("summary", "Add design sections"));
    const designSections = ["components", "responsibilities", "interactions", "data_flow", "interfaces", "configuration", "failure_handling", "required_behaviour"];
    const designKey = "design-add-form";
    const designBuffer = authoringFormBuffer(state, designKey, { section: designSections[0], text: "", delegated: false }, state.authoring?.base);
    const sectionName = authoringFormControl(state, designKey, designBuffer, "section", "Design section", { options: designSections.map(value => [value, value.replaceAll("_", " ")]) });
    const text = authoringFormControl(state, designKey, designBuffer, "text", "Design description", { placeholder: "Describe this part of the design" });
    const delegatedLabel = el("label", "Delegate this design entry"); const delegated = authoringFormControl(state, designKey, designBuffer, "delegated", "Delegate this design entry", { type: "checkbox" }); delegatedLabel.prepend(delegated);
    design.append(sectionName, text, delegatedLabel, authoringButton(task, state, "Add design entry", () => authoringSubmitForm(state, designKey, designBuffer, values => authoringAction(task, state, "planning", {
      operation: { op: "design-add", section: values.section, text: values.text, delegated: values.delegated },
    }, { expected: designBuffer.base, key: "design-add" }), { reset: { section: designSections[0], text: "", delegated: false } })));
    const none = el("details"); none.append(el("summary", "Mark a design section intentionally empty"));
    const noneKey = "design-none-form";
    const noneBuffer = authoringFormBuffer(state, noneKey, { section: designSections[0], reason: "" }, state.authoring?.base);
    const noneSection = authoringFormControl(state, noneKey, noneBuffer, "section", "Design section marked empty", { options: designSections.map(value => [value, value.replaceAll("_", " ")]) });
    const noneReason = authoringFormControl(state, noneKey, noneBuffer, "reason", "Reason design section is empty", { placeholder: "Reason" });
    none.append(noneSection, noneReason, authoringButton(task, state, "Record no design needed", () => authoringSubmitForm(state, noneKey, noneBuffer, values => authoringAction(task, state, "planning", {
      operation: { op: "design-none", section: values.section, reason: values.reason },
    }, { expected: noneBuffer.base, key: "design-none" }), { reset: { section: designSections[0], reason: "" } })));
    design.append(none); section.append(design);
    for (const item of planning?.design || []) {
      const record = el("article"); record.className = "authoring-record"; record.append(el("h3", `${item.section} · ${item.status}`), el("p", item.text));
      if (["pending", "proposed", "awaiting"].includes(item.status)) {
        const key = `design-decision:${item.id}`;
        const buffer = authoringFormBuffer(state, key, { status: "accepted", response: "" }, state.authoring?.base, item.id);
        const status = authoringFormControl(state, key, buffer, "status", "Design decision", { options: [["accepted", "Accept"], ["rejected", "Reject"], ["modified", "Modify"]] });
        const response = authoringFormControl(state, key, buffer, "response", "Decision response", { placeholder: "Required for modified" });
        record.append(status, response, authoringButton(task, state, "Record design decision", () => authoringSubmitForm(state, key, buffer, values => authoringAction(task, state, "planning", {
          operation: { op: "design-decide", entry_id: item.id, status: values.status, response: values.response || null },
        }, { expected: buffer.base, key: `design-decide:${item.id}` }), { reset: { status: "accepted", response: "" } })));
      }
      section.append(record);
    }
  }
  if (current === "test_plan") {
    const scenario = el("details"); scenario.open = true; scenario.append(el("summary", "Add a test scenario"));
    const scenarioKey = "scenario-add-form";
    const scenarioInitial = { category: "unit", text: "", covers: "", mock: "", mock_reason: "" };
    const scenarioBuffer = authoringFormBuffer(state, scenarioKey, scenarioInitial, state.authoring?.base);
    const category = authoringFormControl(state, scenarioKey, scenarioBuffer, "category", "Test category", { options: ["unit", "integration", "e2e", "regression", "edge_case", "failure"].map(value => [value, value.replaceAll("_", " ")]) });
    const text = authoringFormControl(state, scenarioKey, scenarioBuffer, "text", "Scenario", { placeholder: "Scenario and expected behavior" });
    const covers = authoringFormControl(state, scenarioKey, scenarioBuffer, "covers", "Coverage references", { type: "input", placeholder: "Q1, E1" });
    const mock = authoringFormControl(state, scenarioKey, scenarioBuffer, "mock", "Mocking approach", { options: [["", "No mock selected"], ["mock", "Use a mock"], ["real", "Use the real dependency"]] });
    const mockReason = authoringFormControl(state, scenarioKey, scenarioBuffer, "mock_reason", "Mocking reason", { type: "input", placeholder: "Why this mocking approach is appropriate" });
    const coverage = el("p", "Use recorded references such as Q1 or E1; Harness validates them.");
    scenario.append(category, text, covers, mock, mockReason, coverage, authoringButton(task, state, "Add scenario", () => authoringSubmitForm(state, scenarioKey, scenarioBuffer, values => authoringAction(task, state, "planning", {
      operation: { op: "scenario-add", category: values.category, text: values.text,
        covers: values.covers.split(",").map(value => value.trim()).filter(Boolean),
        mock: values.mock || null, mock_reason: values.mock_reason.trim() || null },
    }, { expected: scenarioBuffer.base, key: "scenario-add" }), { reset: scenarioInitial })));
    section.append(scenario);
    for (const item of planning?.scenarios || []) {
      const record = el("article"); record.className = "authoring-record"; record.append(el("h3", `${item.category} · ${item.status}`), el("p", item.text || item.statement || ""));
      if (item.covers?.length) record.append(el("p", `Covers: ${item.covers.join(", ")}`));
      if (item.mock || item.mock_justification) record.append(el("p", `Mocking: ${item.mock || "unspecified"}${item.mock_justification ? ` · ${item.mock_justification}` : ""}`));
      if (item.status === "pending") {
        const key = `scenario-decision:${item.id}`;
        const buffer = authoringFormBuffer(state, key, { status: "accepted", response: "" }, state.authoring?.base, item.id);
        const decision = authoringFormControl(state, key, buffer, "status", `Decision for scenario ${item.id}`, { options: [["accepted", "Accept"], ["rejected", "Reject"], ["modified", "Modify"]] });
        const response = authoringFormControl(state, key, buffer, "response", `Decision response for scenario ${item.id}`, { placeholder: "Required when modifying" });
        record.append(decision, response, authoringButton(task, state, "Record scenario decision", () => authoringSubmitForm(state, key, buffer, values => authoringAction(task, state, "planning", {
          operation: { op: "scenario-decide", scenario_id: item.id, status: values.status, response: values.response || null },
        }, { expected: buffer.base, key: `scenario-decide:${item.id}` }), { reset: { status: "accepted", response: "" } })));
      }
      section.append(record);
    }
    const testPlan = planning?.test_plan;
    if (testPlan) {
      section.append(el("p", testPlan.approved ? `Test plan approved · ${testPlan.approved.id || "recorded"}` : "Test plan is not approved."));
      if (testPlan.review?.missing_categories?.length)
        section.append(el("p", `Before approval, add a scenario for each required category: ${testPlan.review.missing_categories.join(", ")}.`));
      if (testPlan.review?.unresolved?.length) for (const finding of testPlan.review.unresolved) section.append(el("p", `Unresolved review finding: ${finding.ref} · ${finding.state}${finding.text ? ` · ${finding.text}` : ""}`));
      if (!testPlan.approved) {
        const approve = authoringButton(task, state, "Approve test plan", (t, s) => authoringAction(t, s, "planning", { operation: { op: "test-plan-approve" } }, { key: "test-plan-approve" }));
        const unresolved = testPlan.review?.unresolved || [];
        approve.disabled = Boolean(testPlan.review?.missing_categories?.length)
          || unresolved.some(finding => ["awaiting_decision", "mock_flagged"].includes(finding.state));
        section.append(approve);
      }
    }
  }
  const message = authoringMessage(state); if (message) section.append(message);
  return section;
}

function workspaceReviewPanel(task, state, inspector) {
  const panel = workspaceReview(task, state, inspector);
  inspector.append(el("h2", "Approval and implementation controls"), renderApprovalCenter(task));
  panel.append(workspaceReconciliation(task, state));
  return panel;
}
