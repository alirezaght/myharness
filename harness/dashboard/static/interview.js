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
  const session = task.session?.snapshot;
  const active = session?.mode === "attached"
    ? session.state === "clock_unavailable"
      ? `Shared session needs host clock access · ${session.clockUnavailable?.reason || "unavailable"}`
      : `${session.providerLabel || "Provider"} session · ${session.state.replaceAll("_", " ")}`
    : latest
    ? `${latest.kind === "source_investigation" ? "Source investigation" : latest.kind === "jira_reconnect" ? "Jira reconnect" : latest.kind === "investigate" ? "Context preparation" : "Interview model"}: ${latest.state}${latest.error ? ` · ${latest.error}` : ""}`
    : "No authoring job recorded";
  bar.append(el("span", `Project ${workspaceProjectName(authoring.project?.repository)} · ${phase}`), el("span", active));
  if (state.storageError) bar.append(el("span", state.storageError));
  return bar;
}

function workspaceProjectName(repository) {
  const path = String(repository || "").replace(/[\\/]+$/, "");
  return path.split(/[\\/]/).filter(Boolean).at(-1) || "configured project";
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
  if (action === "answer" && state.authoring?.phase === "interview") {
    if (state.drawerQuestionId === fields.question) state.drawerQuestionId = null;
    result.acknowledgement = fields.mode === "unknown"
      ? `Saved Q${fields.question} as not known yet. It remains unresolved; the next question is shown.`
      : fields.mode === "defer" ? `Deferral for Q${fields.question} saved. Review remains required.`
        : `Answer for Q${fields.question} saved. The next question is shown.`;
  }
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
      authoringStatus(state, result?.acknowledgement || (result?.job
        ? result.state && !["queued", "running"].includes(result.state)
          ? `Existing request is ${result.state}. No new model response was queued; use the recovery controls below.`
          : "Request queued. Waiting for a persisted worker result."
        : "Saved."));
    } catch (error) {
      authoringStatus(state, error.followupSaved
        ? `Your follow-up was saved as context. ${error.message}. The model request details are retained; structured questions are unchanged.`
        : `${error.message}. Your edit and request details are retained.`, true);
      if (error.status === 409 && opts.recovery && error.result?.code !== "non_answer") {
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
    box.append(el("p", `Saved ${action} request${payload.host ? ` on ${payload.host}` : payload.question ? ` for Q${payload.question}` : ""}. Retry preserves its original details.`), retry);
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

function workspaceCurrentQuestion(state) {
  const id = state.authoring?.interview?.current_question_id;
  return (state.authoring?.questions || []).find(question => question.id === id) || null;
}

function workspaceInterviewRecovery(task, state) {
  if (task.session?.snapshot?.mode === "attached") return null;
  const recovery = state.authoring?.model_recovery;
  if (!recovery) return null;
  const box = el("section"); box.className = "authoring-card authoring-recovery"; box.setAttribute("role", "alert");
  box.append(el("strong", `Interview model ${recovery.host}: ${recovery.state}`), el("p", recovery.message),
    el("p", "Your manual answers and drafts remain usable. Choose the host explicitly for a NEW response; failed history is retained."));
  const key = "recovery-host";
  const buffer = authoringFormBuffer(state, key, { host: recovery.host }, state.authoring.base);
  box.append(authoringFormControl(state, key, buffer, "host", "Recovery interview model", {
    options: [["codex", "Codex"], ["claude", "Claude"]], type: "input",
  }));
  box.append(authoringButton(task, state, "Request a new response on selected host", (t, s) =>
    authoringAction(t, s, "regenerate", { host: buffer.value.host }, { key: `recovery-response:${buffer.value.host}` })));
  return box;
}

function investigationStageLabel(investigation) {
  if (investigation.state === "queued") return "Queued";
  if (investigation.state === "running" && !investigation.stage) return "Running";
  const labels = {
    acquiring_ticket: "Reading the selected Jira issue",
    inspecting_repository: "Inspecting bounded repository sources",
    synthesizing: "Preparing source-backed findings",
    ready: "Source review is ready",
    authentication_completed: "Sign-in completed; read access is not qualified",
    failed: "Investigation needs recovery",
    stale: "Investigation result is stale",
    unavailable: "Source acquisition is unavailable here",
  };
  return labels[investigation.stage] || labels[investigation.state] || investigation.state || "Not started";
}

function investigationSourceCard(source) {
  const card = el("details"); card.className = "investigation-source";
  const meta = source.metadata || {};
  const summary = `${source.kind === "ticket" ? "Jira issue" : "Repository file"} · ${source.locator}`;
  card.append(el("summary", summary));
  if (source.sha256) card.append(el("p", `SHA-256 ${source.sha256}`));
  if (meta.updated) card.append(el("p", `Jira last updated · ${meta.updated}`));
  if (source.kind === "repository") {
    card.append(el("p", `Read version ${meta.version || "not reported"} · ${meta.hash_status || "hash not reported"}`));
    if (meta.truncated) card.append(el("p", `Partial file read · offset ${meta.offset} · next ${meta.next_offset}`));
  }
  card.append(el("pre", source.content || "No source text was recorded."));
  return card;
}

function investigationReviewPackageView(content, packageSha256) {
  const section = el("section"); section.className = "investigation-review-package";
  section.append(el("h3", "Exact investigation review package"));
  section.append(el("p", content.complete
    ? "Review the source evidence, proposed specification, and diagram below. Findings remain proposals until you accept this exact package."
    : "This package is incomplete; acceptance is unavailable."));

  const proposed = el("details"); proposed.open = true;
  proposed.append(el("summary", "Complete proposed specification"), authoringMarkdown(content.proposed_spec || "No proposed specification was recorded."));
  section.append(proposed);

  const graph = content.graph?.value || content.graph || {};
  const diagram = el("details");
  diagram.append(el("summary", `Proposed logical diagram · ${(graph.nodes || []).length} components · ${(graph.edges || []).length} relationships`));
  const nodeLabels = new Map((graph.nodes || []).map(node => [node.id, node.label || node.id]));
  for (const node of graph.nodes || []) {
    const row = el("article"); row.className = "investigation-evidence-row";
    row.append(el("h4", node.label || node.id), el("p", `${node.kind || "component"} · ${node.status || "status not reported"}`));
    if (node.assumption) row.append(el("p", `Proposal rationale: ${node.assumption}`));
    if (node.sources?.length) row.append(el("p", `Evidence references: ${node.sources.join(", ")}`));
    diagram.append(row);
  }
  for (const edge of graph.edges || []) {
    const row = el("article"); row.className = "investigation-evidence-row";
    row.append(el("h4", `${nodeLabels.get(edge.from) || edge.from} → ${nodeLabels.get(edge.to) || edge.to}`), el("p", `${edge.label || "Relationship"} · ${edge.status || "status not reported"}`));
    if (edge.assumption) row.append(el("p", `Proposal rationale: ${edge.assumption}`));
    if (edge.sources?.length) row.append(el("p", `Evidence references: ${edge.sources.join(", ")}`));
    diagram.append(row);
  }
  section.append(diagram);

  const sourceDetails = el("details");
  sourceDetails.append(el("summary", `All acquired sources · ${(content.sources || []).length}`));
  for (const source of content.sources || []) sourceDetails.append(investigationSourceCard(source));
  section.append(sourceDetails);

  const coverage = content.evidence || {};
  const findings = el("section"); findings.className = "investigation-findings";
  findings.append(el("h4", "Source coverage and exact citations"));
  for (const row of coverage.coverage || []) {
    const card = el("article"); card.className = "investigation-evidence-row";
    card.append(el("h5", `${row.key} · ${row.disposition.replaceAll("_", " ")}`), el("p", row.statement), el("p", `Reasoning: ${row.rationale}`));
    for (const citation of row.citations || []) {
      const source = (content.sources || []).find(item => item.id === citation.source);
      const quote = el("blockquote", citation.quote);
      card.append(el("p", `${source?.locator || citation.source} · characters ${citation.start}–${citation.end} · source SHA-256 ${citation.sha256}`), quote);
    }
    findings.append(card);
  }
  const decisions = el("section"); decisions.className = "investigation-findings";
  decisions.append(el("h4", "Genuine decisions for you"));
  const decisionRows = coverage.decisions || [];
  if (!decisionRows.length) decisions.append(el("p", "The source review proposed no additional developer decisions."));
  for (const row of decisionRows) {
    const card = el("article"); card.className = "investigation-evidence-row";
    card.append(el("h5", row.concern), el("p", row.text), el("p", row.reason));
    if (row.supersedes) card.append(el("p", `Proposes a successor to question Q${row.supersedes}; prior responses remain in history.`));
    for (const citation of row.citations || []) {
      const source = (content.sources || []).find(item => item.id === citation.source);
      card.append(el("p", `Evidence from ${source?.locator || citation.source}:`), el("blockquote", citation.quote));
    }
    decisions.append(card);
  }
  findings.append(decisions); section.append(findings);

  const questions = el("details");
  questions.append(el("summary", `Current questions and recommendations · ${(content.questions || []).length} questions · ${(content.proposals || []).length} recommendations`));
  for (const question of content.questions || []) {
    const row = el("article"); row.append(el("h5", `Q${question.id} · ${question.topic} · ${question.status}`), el("p", question.text));
    if (question.answer) row.append(el("p", `Recorded answer: ${question.answer}`));
    if (question.skip_source) row.append(el("p", `Recorded deferral: ${question.skip_source} · ${question.skip_reason || question.skip_ref || ""}`));
    questions.append(row);
  }
  for (const proposal of content.proposals || []) {
    const row = el("article"); row.append(el("h5", `P${proposal.id} · ${proposal.status}`), el("p", proposal.recommendation), el("p", proposal.rationale));
    if (proposal.response) row.append(el("p", `Developer response: ${proposal.response}`));
    questions.append(row);
  }
  section.append(questions);

  const sourceHistory = content.source_lineage || {};
  const sourceRelationships = Array.isArray(sourceHistory.relationships)
    ? sourceHistory.relationships : [];
  if (sourceRelationships.length) {
    const historical = new Map((content.historical_questions || []).map(question => [
      Number(question.id), question,
    ]));
    const current = new Map((content.questions || []).map(question => [Number(question.id), question]));
    const sourceDetails = el("details");
    sourceDetails.className = "investigation-source-history";
    sourceDetails.append(el("summary", `Earlier source decisions · ${sourceRelationships.length} retained links`));
    sourceDetails.append(el("p", "These decisions were retired when their source premise changed. Their recorded answers remain historical and do not answer the current successor."));
    for (const relationship of sourceRelationships) {
      const oldId = Number(relationship.question);
      const newId = Number(relationship.current_question);
      const oldQuestion = historical.get(oldId);
      const currentQuestion = current.get(newId);
      const row = el("article"); row.className = "investigation-evidence-row source-history-row";
      row.append(el("h4", `Q${oldId} · retired source decision`));
      if (oldQuestion?.text) row.append(el("p", oldQuestion.text));
      if (oldQuestion?.answer) row.append(el("p", `Recorded answer: ${oldQuestion.answer}`));
      if (oldQuestion?.skip_source) {
        row.append(el("p", `Recorded deferral: ${oldQuestion.skip_source}${oldQuestion.skip_reason ? ` · ${oldQuestion.skip_reason}` : ""}`));
      }
      row.append(el("p", `Current successor Q${newId}: ${currentQuestion?.text || "not present in the current package"}`));
      sourceDetails.append(row);
    }
    section.append(sourceDetails);
  }

  const basis = content.basis || {};
  const lineage = el("details");
  const effectiveLineage = content.effective_lineage || {};
  const lineageApplications = Array.isArray(effectiveLineage.applications) ? effectiveLineage.applications : [];
  const lineageCount = lineageApplications.length
    + Object.values(effectiveLineage.question_successors || {}).length
    + Object.values(effectiveLineage.proposal_successors || {}).length;
  lineage.append(el("summary", `Review basis and decision lineage · ${lineageCount} recorded links`));
  for (const [label, value] of [
    ["Source identity", basis.semantic], ["Selected body", basis.body],
    ["Selected diagram", basis.graph], ["Selected revision", basis.revision],
  ]) {
    const row = el("p"); row.append(el("strong", `${label}: `), el("code", value || "not reported"));
    lineage.append(row);
  }
  for (const [label, value] of [
    ["Package SHA-256", packageSha256], ["Source bundle", content.bundle],
    ["Working revision", content.working_revision], ["Adopted revision", content.adopted_revision],
  ]) {
    const row = el("p"); row.append(el("strong", `${label}: `), el("code", value || "none"));
    lineage.append(row);
  }
  const selection = content.selection || {};
  const selectedSource = el("p");
  selectedSource.append(el("strong", "Selected Jira issue: "), el("code", selection.issue || "not reported"));
  if (selection.site?.url) selectedSource.append(el("span", ` · ${selection.site.url}`));
  selectedSource.append(el("span", ` · fields ${selection.fields?.join(", ") || "not reported"}`));
  lineage.append(selectedSource);
  const lineageMaps = [
    ["Question successors", effectiveLineage.question_successors, "Q"],
    ["Recommendation successors", effectiveLineage.proposal_successors, "P"],
    ["Question roots", effectiveLineage.question_roots, "Q"],
    ["Recommendation roots", effectiveLineage.proposal_roots, "P"],
  ];
  for (const [label, mapping, prefix] of lineageMaps) {
    for (const [record, linked] of Object.entries(mapping || {})) {
      lineage.append(el("p", `${label}: ${prefix}${record} → ${prefix}${linked}`));
    }
  }
  for (const item of lineageApplications) {
    const originalId = item.original?.id ?? "identity not reported";
    lineage.append(el("p", `Applied ${item.target || "record"} · contribution ${item.contribution || "not reported"} · original record ${originalId}`));
  }
  for (const [record, binding] of Object.entries(effectiveLineage.registry || {})) {
    lineage.append(el("p", `P${record} registry decision ${binding.id || "not reported"} · version ${binding.version ?? "not reported"} · ${binding.status || "status not reported"} · ${binding.path || "path not reported"}`));
  }
  section.append(lineage);
  for (const limitation of content.limitations || []) section.append(el("p", `Investigation limitation: ${limitation}`));
  return section;
}

function workspaceInvestigationReviewControls(task, state, investigation) {
  const box = el("section"); box.className = "authoring-card investigation-controls";
  box.append(el("h3", "Review and adopt source-backed work"), el("p", "The complete review package shows the specification, diagram, acquired sources, evidence mappings, limitations, and current question lineage. Adoption and source acceptance are separate explicit steps; source acceptance does not approve implementation."));
  if (!investigation.review_available) {
    box.append(el("p", "A complete source bundle is not available yet. Resolve the investigation status above before reviewing source findings."));
    return box;
  }
  const reviewButton = authoringButton(task, state, "Load exact investigation review", async (t, s) => {
    const result = await authoringAction(t, s, "investigation-review", {}, { key: "investigation-review" });
    state.investigationReview = result.review;
    workspacePersist(state);
    return { acknowledgement: `Loaded review package ${result.review.sha256}.` };
  });
  box.append(reviewButton);
  const review = state.investigationReview;
  if (!review) {
    box.append(el("p", "Load the current server package before adopting or accepting source coverage."));
    return box;
  }
  const content = review.content;
  const authoring = state.authoring || {};
  const current = Boolean(content.bundle === investigation.bundle
    && content.basis?.semantic === authoring.base?.source
    && content.working_revision === authoring.working?.id
    && content.adopted_revision === (authoring.adopted?.id || null));
  const hash = el("p", current
    ? "This review package matches the task’s current sources, saved specification, and adopted revision."
    : "This review package is stale; reload the exact current package before continuing.");
  hash.className = current ? "investigation-package-current" : "authoring-error";
  box.append(hash, investigationReviewPackageView(content, review.sha256));
  const adopted = Boolean(content.adopted_revision && content.adopted_revision === authoring.adopted?.id);
  if (!adopted) {
    box.append(el("p", "Adopt the selected specification and diagram candidate through the guarded Review tab before accepting this source package."));
    box.append(workspaceButton("Open guarded candidate adoption", () => {
      workspaceSetTab(state, "review"); workspacePersist(state); workspaceRerender();
    }));
  }
  const decisionIds = new Set((investigation.coverage?.decision_question_ids || []).map(Number));
  const decisionQuestions = (authoring.questions || []).filter(question => decisionIds.has(question.id));
  const unknownIds = new Set((authoring.interview?.unknown_question_ids || []).map(Number));
  const openDecisionQuestions = decisionQuestions.filter(question =>
    question.status === "open" || unknownIds.has(question.id));
  const hasOpenDecision = openDecisionQuestions.length > 0;
  const hasPendingRecommendation = (content.proposals || []).some(proposal => proposal.status === "pending");
  const effectiveConcerns = investigation.coverage?.concerns || [];
  const coverageCanBeReviewed = effectiveConcerns.length > 0 && effectiveConcerns.every(row =>
    ["pending_source_review", "accepted_source_evidence"].includes(row.state));
  const canAcceptCoverage = !hasOpenDecision && !hasPendingRecommendation && coverageCanBeReviewed
    && (content.evidence?.coverage || []).every(row => !["operational_gap", "conflict"].includes(row.disposition));
  if (investigation.coverage?.accepted) {
    box.append(el("p", `Exact source coverage was accepted as ${investigation.coverage.acceptance}. Fresh design review and explicit approval are still required.`));
  } else if (content.complete && current && adopted && canAcceptCoverage) {
    const provenanceKey = "investigation-acceptance-provenance";
    const provenanceBuffer = authoringBuffer(state, provenanceKey, "", state.authoring?.base);
    const provenance = authoringFocus(el("textarea"), state, provenanceKey);
    provenance.rows = 3; provenance.value = provenanceBuffer.value;
    provenance.placeholder = "Optional: add a note about accepting this exact package";
    provenance.setAttribute("aria-label", "Optional source review acceptance note");
    provenance.addEventListener("input", () => authoringScalarChanged(state, provenanceKey, provenanceBuffer, provenance.value));
    const confirmation = el("label", "I reviewed the complete source text, citations, proposed specification, diagram, decisions, and limitations.");
    const checked = el("input"); checked.type = "checkbox"; confirmation.prepend(checked);
    const accept = authoringButton(task, state, "Accept this exact source review", (t, s) => authoringAction(t, s, "accept-investigation", {
      approved_sha256: review.sha256,
      provenance: provenanceBuffer.value.trim()
        || "Developer explicitly accepted this exact source review package through the confirmation control.",
    }, { key: `accept-investigation:${review.sha256}` }));
    accept.disabled = true;
    const update = () => { accept.disabled = !checked.checked; };
    checked.addEventListener("change", update);
    provenance.addEventListener("input", update);
    box.append(provenance, confirmation, accept);
  } else {
    box.append(el("p", content.complete ? "Review package is stale or still contains source conflicts/operational gaps." : "An incomplete package cannot be accepted."));
  }
  return box;
}

function workspaceInvestigationPanel(task, state) {
  const attached = task.session?.snapshot?.mode === "attached";
  const investigation = state.authoring?.investigation;
  const section = el("section"); section.className = "authoring-card investigation-panel";
  section.append(el("h2", "Task investigation"), el("p", "Gather cited ticket and repository evidence first. Source findings are proposals; only your explicit responses count as developer decisions."));
  if (!investigation?.enabled) {
    section.append(el("p", "No source investigation is attached to this task. You can request an investigation for a Jira issue, or continue with manual questions and specification editing."));
    if (attached) {
      section.lastChild.textContent = "The connected agent gathers authorized sources and publishes findings here. Manual answers and specification editing remain available.";
      return section;
    }
    const hostKey = "investigation-host";
    const buffer = authoringFormBuffer(state, hostKey, { host: state.authoring?.jobs?.at(-1)?.host || "codex" }, state.authoring?.base);
    section.append(authoringFormControl(state, hostKey, buffer, "host", "Interview model for source review", { options: [["codex", "Codex"], ["claude", "Claude"]] }));
    section.append(authoringButton(task, state, "Investigate this task", (t, s) => authoringAction(t, s, "investigate", {
      host: buffer.value.host, site: null,
    }, { expected: buffer.base, key: "investigate" })));
    return section;
  }

  const stateLabel = investigationStageLabel(investigation);
  const status = el("p", `${stateLabel}${investigation.attempt ? ` · attempt ${investigation.attempt}` : ""}`);
  status.setAttribute("role", investigation.recovery || investigation.state === "failed" ? "alert" : "status");
  section.append(status);
  const selection = investigation.selection || {};
  const site = selection.site?.id || null;
  section.append(el("p", `Jira issue ${selection.issue || "not identified"} · repository ${selection.repository || "not reported"} · selected interview model ${(investigation.job && state.authoring?.jobs?.find(job => job.id === investigation.job)?.host) || state.authoring?.jobs?.at(-1)?.host || "not reported"}`));
  if (investigation.completed_stages?.length) section.append(el("p", `Completed stages: ${investigation.completed_stages.map(stage => investigationStageLabel({ stage, state: "running" })).join(" · ")}`));
  if (investigation.recovery) {
    const recovery = el("div"); recovery.className = "authoring-recovery"; recovery.setAttribute("role", "alert");
    recovery.append(el("strong", `Source recovery · ${investigation.recovery.reason || "needs attention"}`), el("p", investigation.recovery.message || "The source investigation did not complete."));
    if (investigation.recovery.site_choices?.length) {
      const siteKey = "investigation-site";
      const siteBuffer = authoringFormBuffer(state, siteKey, { site: investigation.recovery.site_choices[0].id }, state.authoring?.base);
      recovery.append(authoringFormControl(state, siteKey, siteBuffer, "site", "Verified Jira site", { options: investigation.recovery.site_choices.map(site => [site.id, site.url]) }));
      if (!attached) {
        const hostKey = "investigation-host";
        const hostBuffer = authoringFormBuffer(state, hostKey, { host: investigation.job && state.authoring?.jobs?.find(job => job.id === investigation.job)?.host || "codex" }, state.authoring?.base);
        recovery.append(authoringFormControl(state, hostKey, hostBuffer, "host", "Interview model for source review", { options: [["codex", "Codex"], ["claude", "Claude"]] }));
        recovery.append(authoringButton(task, state, "Continue with this verified site", (t, s) => authoringAction(t, s, "investigate", {
          host: hostBuffer.value.host, site: siteBuffer.value.site,
        }, { expected: state.authoring.base, key: "investigate-site" })));
      }
    }
  }
  let hostBuffer;
  if (!attached) {
    const sourceHost = el("select"); sourceHost.setAttribute("aria-label", "Source investigation interview model");
    for (const [value, label] of [["codex", "Codex"], ["claude", "Claude"]]) { const option = el("option", label); option.value = value; sourceHost.append(option); }
    const hostKey = "investigation-host";
    hostBuffer = authoringFormBuffer(state, hostKey, { host: state.authoring?.jobs?.find(job => job.id === investigation.job)?.host || "codex" }, state.authoring?.base);
    sourceHost.value = hostBuffer.value.host;
    sourceHost.addEventListener("change", () => authoringFormChanged(state, hostKey, hostBuffer, "host", sourceHost.value));
    section.append(sourceHost);
  }

  const job = state.authoring?.jobs?.find(item => item.id === investigation.job);
  if (attached) {
    section.append(el("p", "Source work is coordinated through the attached agent. No standalone provider job is started from this task."));
  } else if (job && ["queued", "running"].includes(job.state)) {
    section.append(authoringButton(task, state, "Cancel source investigation", (t, s) => authoringAction(t, s, "cancel-job", {
      job: job.id,
    }, { key: `cancel-investigation:${job.id}` })));
  } else {
    section.append(authoringButton(task, state, investigation.bundle ? "Refresh ticket and repository sources" : "Retry source investigation", (t, s) => authoringAction(t, s, "investigate", {
      host: hostBuffer.value.host, site,
    }, { expected: state.authoring.base, key: `investigate:${investigation.bundle || "initial"}` })));
  }
  if (!attached && investigation.reconnect?.available) {
    const disclosure = el("details");
    disclosure.append(el("summary", "Reconnect Jira using explicit native consent"), el("p", investigation.reconnect.disclosure || "This uses the existing native sign-in and may update account authentication."), el("p", `Requested scope: ${investigation.reconnect.scope?.join(", ") || "not reported"}`));
    const reconnect = authoringButton(task, state, "Start Jira reconnect", (t, s) => authoringAction(t, s, "reconnect-jira", {
      host: hostBuffer.value.host, site,
    }, { expected: state.authoring.base, key: "reconnect-jira" }));
    disclosure.append(reconnect);
    if (job?.kind === "jira_reconnect" && job.state === "running") {
      disclosure.append(authoringButton(task, state, "Open Jira authorization page", async (t, s) => {
        const popup = window.open("about:blank", "_blank");
        try {
          const result = await authoringAction(t, s, "jira-handoff", { job: job.id }, { expected: state.authoring.base, key: `jira-handoff:${job.id}` });
          if (!result.handoff) throw new Error("The native authorization page is no longer available.");
          if (popup) popup.location.replace(result.handoff.authorization_url);
          else window.location.assign(result.handoff.authorization_url);
          return { acknowledgement: "Opened the native authorization page. The URL is held only for this click." };
        } catch (error) {
          popup?.close();
          throw error;
        }
      }));
    }
    section.append(disclosure);
  } else if (!attached && investigation.reconnect?.reason) {
    section.append(el("p", "Jira reconnect is unavailable until the installed native connection has separate bootstrap, consent, and same-account qualification. No automatic sign-in was attempted."));
  }

  if (investigation.recovery?.reason === "site_selection_required" && investigation.recovery.site_choices?.length) {
    // The verified-site choice is already presented in the recovery control above.
  } else if (!selection.site && !investigation.recovery?.site_choices?.length) {
    section.append(el("p", "A verified Jira site has not been selected. The server will offer only qualified sites; this form does not accept a site URL."));
  }
  if (investigation.sources?.length) {
    const sources = el("details"); sources.append(el("summary", `Acquired source text · ${investigation.sources.length} records`));
    for (const source of investigation.sources) sources.append(investigationSourceCard(source));
    section.append(sources);
  }
  if (investigation.attempt_observations) {
    const observation = investigation.attempt_observations;
    const details = el("details"); details.append(el("summary", "Repository inspection progress"));
    details.append(el("p", `Inspected ${observation.repository?.counts?.files || 0} bounded file reads · ${observation.repository?.counts?.bytes || 0} text bytes · ${observation.repository?.counts?.entries || 0} entries.`));
    for (const path of observation.repository?.searched_paths || []) details.append(el("code", path));
    for (const limitation of observation.repository?.limitations || []) details.append(el("p", `Limitation: ${limitation}`));
    section.append(details);
  }
  const coverage = investigation.coverage || {};
  if (coverage.concerns?.length) {
    const findings = el("section"); findings.className = "investigation-findings";
    findings.append(el("h3", "What Harness found"), el("p", "Each row is a proposed mapping grounded in the acquired source. It is not a developer answer or approval."));
    for (const concern of coverage.concerns) {
      const row = el("article"); row.className = `investigation-evidence-row state-${concern.state}`;
      row.append(el("h4", concern.text), el("p", `Status · ${concern.state.replaceAll("_", " ")} · proposed classification ${concern.disposition.replaceAll("_", " ")}`), el("p", concern.statement), el("p", `Reasoning: ${concern.rationale}`));
      for (const citation of concern.citations || []) {
        const source = (investigation.sources || []).find(item => item.id === citation.source);
        row.append(el("p", `${source?.locator || citation.source} · characters ${citation.start}–${citation.end}`), el("blockquote", citation.quote));
      }
      findings.append(row);
    }
    for (const limitation of coverage.limitations || investigation.limitations || []) findings.append(el("p", `Coverage limitation: ${limitation}`));
    section.append(findings);
  }
  const decisionIds = new Set((investigation.coverage?.decision_question_ids || []).map(Number));
  const decisionQuestions = (state.authoring?.questions || []).filter(question => decisionIds.has(question.id));
  const unknownIds = new Set((state.authoring?.interview?.unknown_question_ids || []).map(Number));
  const currentId = Number(state.authoring?.interview?.current_question_id || 0);
  const currentDecision = decisionQuestions.find(question => question.id === currentId
    && question.status === "open" && !question.skip_source && !unknownIds.has(question.id));
  const answeredDecisions = decisionQuestions.filter(question => question.status === "answered");
  const unknownDecisions = decisionQuestions.filter(question => unknownIds.has(question.id));
  const deferredDecisions = decisionQuestions.filter(question => question.status === "open" && question.skip_source);
  const otherOpenDecisions = decisionQuestions.filter(question => question.status === "open"
    && !unknownIds.has(question.id) && !question.skip_source && question.id !== currentDecision?.id);
  const hasOpenDecision = Boolean(currentDecision || otherOpenDecisions.length || unknownDecisions.length || deferredDecisions.length);
  if (decisionQuestions.length || currentDecision) {
    const decisions = el("section"); decisions.className = "investigation-decisions";
    decisions.append(el("h3", "Developer decision status"));
    if (currentDecision) decisions.append(el("p", `Current decision Q${currentDecision.id}: ${currentDecision.display_text || currentDecision.text}`));
    for (const question of otherOpenDecisions) decisions.append(el("p", `Still open Q${question.id}: ${question.display_text || question.text}`));
    for (const question of unknownDecisions) decisions.append(el("p", `Not known yet Q${question.id}: ${question.display_text || question.text}`));
    for (const question of deferredDecisions) decisions.append(el("p", `Deferred Q${question.id} (${question.skip_source}): ${question.display_text || question.text}`));
    if (answeredDecisions.length) {
      const history = el("details"); history.append(el("summary", `Answered decision history · ${answeredDecisions.length}`));
      for (const question of answeredDecisions) history.append(el("p", `Answered Q${question.id}: ${question.display_text || question.text}`));
      decisions.append(history);
    }
    if (!currentDecision && !hasOpenDecision) decisions.append(el("p", "No immediate developer decision is waiting. Review the source findings below."));
    if (currentDecision) decisions.append(workspaceButton("Open current decision", () => {
      state.drawerQuestionId = null; state.drawerOpen = true; workspacePersist(state); workspaceRerender();
    }));
    section.append(decisions);
  }
  section.append(workspaceInvestigationReviewControls(task, state, investigation));
  const message = authoringMessage(state); if (message) section.append(message);
  return section;
}

function workspaceStartOrResume(task, state) {
  const session = task.session?.snapshot;
  if (session?.mode === "attached" || (session?.epoch > 0 && session.mode === "manual")) return null;
  const box = el("section"); box.className = "authoring-card";
  box.append(el("h2", state.authoring?.initialized ? "Interview workspace" : "Start or resume"),
    el("p", state.authoring?.initialized ? "Conversation and drafts are saved for this task." : "This existing task has not started an Interview & Spec workspace."));
  const host = el("select"); host.setAttribute("aria-label", "Interview model");
  for (const [value, label] of [["codex", "Codex"], ["claude", "Claude"]]) { const option = el("option", label); option.value = value; host.append(option); }
  const hostKey = "start-host";
  const hostBuffer = authoringFormBuffer(state, hostKey, { host: state.authoring?.jobs?.at(-1)?.host || "codex" }, state.authoring?.base);
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
    if (result.job && (!result.state || ["queued", "running"].includes(result.state))) result.acknowledgement = "Context preparation is queued or running. Model availability is checked before generating a response.";
    return result;
  }, null));
  const recovery = workspaceInterviewRecovery(task, state); if (recovery) box.append(recovery);
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
  const sourceHistory = workspaceSourceLineageHistory(task, state);
  if (sourceHistory) section.append(sourceHistory);
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

function workspaceSourceLineageHistory(task, state) {
  const authoring = state.authoring || task.authoring || {};
  const lineage = authoring.source_lineage || {};
  const relationships = Array.isArray(lineage.relationships) ? lineage.relationships : [];
  if (!relationships.length) return null;

  const questions = new Map([
    ...(authoring.historical?.questions || []),
    ...(authoring.questions || []),
  ].map(question => [Number(question.id), question]));
  const currentQuestions = new Map((authoring.questions || []).map(question => [Number(question.id), question]));
  const section = el("section");
  section.className = "authoring-source-lineage-history";
  section.append(
    el("h3", `Source-replaced decisions · ${relationships.length} retained links`),
    el("p", "Earlier questions and recorded answers remain in history. A changed source premise retires the old target; continue from its current successor instead of editing the retired record."),
  );

  for (const relationship of relationships) {
    const retiredId = Number(relationship.question);
    const successorId = Number(relationship.current_question);
    const retired = questions.get(retiredId);
    const successor = currentQuestions.get(successorId);
    const card = el("article");
    card.className = "authoring-record authoring-source-lineage-record";
    card.dataset.retiredQuestionId = retiredId;
    card.append(el("h4", `Q${retiredId} · retired source decision`));
    if (retired?.text) card.append(el("p", retired.display_text || retired.text));
    if (retired?.answer) card.append(el("p", `Recorded answer: ${retired.answer}`));
    if (retired?.skip_source) {
      card.append(el("p", `Recorded deferral: ${retired.skip_source}${retired.skip_reason ? ` · ${retired.skip_reason}` : ""}`));
    }
    if (!retired?.answer && !retired?.skip_source) {
      card.append(el("p", `Recorded response status: ${retired?.status || "not available"}`));
    }
    const reason = relationship.reason === "source_premise_changed"
      ? "The source premise changed. The earlier response is retained for history and does not answer the replacement decision."
      : `This decision was retired by the recorded source relationship (${relationship.reason || "reason not reported"}).`;
    card.append(el("p", reason));
    if (successor) {
      card.append(el("p", `Current successor Q${successorId}: ${successor.display_text || successor.text}`));
      card.append(workspaceButton(`Open current successor Q${successorId}`, () => {
        state.drawerQuestionId = successorId;
        state.drawerOpen = true;
        workspacePersist(state);
        workspaceRerender();
      }));
    } else {
      card.append(el("p", `Current successor Q${successorId} is not present in the current question projection. Refresh the task before continuing.`));
    }
    section.append(card);
  }
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
  heading.append(el("small", `Q${question.id} · ${question.kind || "question"} · ${question.status || "open"}`), el("h3", question.display_text || question.text));
  card.append(heading);
  if (question.discovery_note) card.append(el("p", question.discovery_note));
  if ((state.authoring?.interview?.unknown_question_ids || []).includes(question.id))
    card.append(el("p", "Saved: I don’t know yet. This requirement remains unresolved and can be revisited."));
  const group = state.authoring?.interview;
  if (group?.scope_response_question_id && group.related_scope_question_ids?.includes(question.id)
      && group.scope_response_question_id !== question.id)
    card.append(el("p", `Related scanner note: context was saved on Q${group.scope_response_question_id}. Technical discovery remains unresolved; this original note is retained for review.`));
  if (question.display_text && question.display_text !== question.text) {
    const original = el("details"); original.append(el("summary", "Original scanner wording"), el("p", question.text)); card.append(original);
  }
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
    || workspaceCurrentQuestion(state);
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

function workspaceSessionEventSummary(event) {
  const payload = event.payload || {};
  const labels = {
    "session.started": "Shared session started",
    "session.attached": "Agent connected",
    "session.pause": "Shared session paused",
    "session.request-reattach": "Reconnect requested",
    "session.interview_prepared": "Interview context prepared",
    "session.choose-manual": "Dashboard mode selected",
    "session.choose-standalone": "Standalone mode selected",
    "session.detached": "Agent disconnected",
  };
  if (event.kind === "session.user_response")
    return `Your response to ${payload.target || "the current item"}: ${payload.text || ""}`;
  if (event.kind === "session.task_message") return payload.text || "Agent message";
  if (event.kind === "session.progress") return `${payload.stage || "Progress"}: ${payload.text || ""}`;
  if (event.kind === "session.candidate") return `Candidate revision ${payload.revision || "recorded"}`;
  if (event.kind === "session.source_bundle") return "Source observations and candidate were published for review.";
  if (event.kind === "session.question_proposal") return payload.text || "A question was proposed.";
  if (payload.workspace_event) return `${event.kind.replaceAll("_", " ")} · saved through the task workspace`;
  return labels[event.kind] || event.kind.replace(/^session\./, "").replaceAll("_", " ").replaceAll("-", " ");
}

function workspaceSessionEventText(event) {
  const payload = event.payload || {};
  if (typeof payload.text === "string") return payload.text;
  if (typeof payload.body === "string") return payload.body;
  if (event.kind === "session.source_bundle") return "Repository and ticket findings with a specification and diagram are ready for review.";
  if (event.kind === "session.candidate") return "A specification and diagram proposal is ready for review.";
  return "";
}

function workspaceSessionEventAuthor(event, session) {
  if (event.kind === "session.user_response") return "You";
  if (event.origin === "browser" || event.origin === "tui_user") return "You";
  if (event.origin === "attached_agent") return session.providerLabel || "Invoking agent";
  if (event.origin === "system") return "Harness";
  return event.origin || "Task history";
}

function workspaceSessionEventLabel(event) {
  const state = event.deliveryState || "saved";
  if (!event.requiresResponse) {
    if (event.origin === "attached_agent") return "Published by the invoking agent";
    if (event.origin === "browser" || event.origin === "tui_user") return "Saved by you";
    return state === "applied" ? "Applied to the task" : "Recorded in task history";
  }
  return state === "applied" ? "Applied to the task"
    : state === "received" ? "Received by the agent · awaiting committed response"
    : "Saved · not yet received by the agent";
}

function workspaceSessionEvents(state, session) {
  const known = new Map((state.sessionHistoryEvents || []).map(event => [event.id, event]));
  for (const event of session.events || []) known.set(event.id, event);
  state.sessionHistoryEvents = [...known.values()].sort((a, b) => a.sequence - b.sequence);
  if (!Object.hasOwn(state, "sessionHistoryBefore")) state.sessionHistoryBefore = session.nextBefore;
  const list = el("ol"); list.className = "session-event-list";
  for (const event of state.sessionHistoryEvents) {
    const row = el("li"); row.className = "session-event";
    const visibleText = workspaceSessionEventText(event);
    if (visibleText) {
      row.classList.add("session-conversation-message");
      row.append(el("strong", workspaceSessionEventAuthor(event, session)));
      row.append(el("p", visibleText));
      if (event.payload?.target) row.append(el("small", `Response to ${event.payload.target.split(":")[0]}`));
      row.append(el("small", workspaceSessionEventLabel(event)));
    }
    const details = el("details");
    details.dataset.workspaceDisclosure = `session-event:${event.id}`;
    const title = visibleText
      ? `Message details · sequence ${event.sequence}`
      : `${workspaceSessionEventSummary(event)} · ${workspaceSessionEventLabel(event)}`;
    details.append(el("summary", title));
    const meta = el("p", `Sequence ${event.sequence} · ${event.origin || "system"} · epoch ${event.epoch ?? "not attached"}`);
    meta.className = "session-event-meta";
    details.append(meta, el("p", `Event reference ${event.id}`));
    const payload = el("details"); payload.append(el("summary", "Recorded event details"));
    const pre = el("pre"); pre.textContent = JSON.stringify(event.payload || {}, null, 2); payload.append(pre);
    details.append(payload); row.append(details); list.append(row);
  }
  return list;
}

async function workspacePostSessionAction(task, state, action) {
  if (state.sessionActionRequest && state.sessionActionRequest.action !== action)
    throw new Error("Resolve the saved session action before creating a different action.");
  if (!state.sessionActionRequest) {
    const snapshot = task.session?.snapshot;
    if (!snapshot || !task.session?.base) throw new Error("Session status is unavailable; reload the task before changing its mode.");
    state.sessionActionRequest = {
      task: task.id,
      request_id: authoringRequestId(),
      expected: task.session.base,
      epoch: snapshot.epoch,
      action,
    };
    workspacePersist(state);
  }
  try {
    const response = await fetch("/api/session/action", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.sessionActionRequest),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(result.error || `Session action failed (${response.status})`);
      error.status = response.status; error.payload = result; throw error;
    }
    state.sessionActionRequest = null;
    state.sessionActionMessage = { text: action === "request-reattach"
      ? "Reconnect request saved. The invoking agent must receive it in the original session."
      : action === "pause" ? "Shared session paused. Your saved answers and task history remain."
        : action === "choose-standalone" ? "Standalone mode selected. No model was started by this change."
          : "Dashboard manual mode selected. Your task history and saved content remain." };
    workspacePersist(state);
    await refreshDashboard();
    return result;
  } catch (error) {
    state.sessionActionMessage = { text: error.status === 409
      ? `${error.message}. Reload the latest session status before deciding whether to create a new action.`
      : `The action outcome may be unknown. Retry the exact saved request or reload status before creating another action. ${error.message}`,
      error: true };
    workspacePersist(state);
    workspaceRerender();
    throw error;
  }
}

function workspaceSessionHistory(task, state) {
  const session = task.session?.snapshot;
  if (!session) return null;
  const section = el("section"); section.className = "authoring-conversation-history";
  section.append(el("h3", "Conversation history"), workspaceSessionEvents(state, session));
  if (state.sessionHistoryError) section.append(el("p", state.sessionHistoryError));
  if (state.sessionHistoryBefore) {
    const older = el("button", "Load earlier conversation events"); older.type = "button";
    older.addEventListener("click", async () => {
      older.disabled = true; state.sessionHistoryError = "Loading earlier events…";
      try {
        const url = `/api/session/events?task=${encodeURIComponent(task.id)}&before=${encodeURIComponent(state.sessionHistoryBefore)}&limit=50`;
        const response = await fetch(url, { cache: "no-store" });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Could not load earlier events.");
        const known = new Map((state.sessionHistoryEvents || []).map(event => [event.id, event]));
        for (const event of data.session?.events || []) known.set(event.id, event);
        state.sessionHistoryEvents = [...known.values()].sort((a, b) => a.sequence - b.sequence);
        state.sessionHistoryBefore = data.session?.nextBefore ?? null;
        state.sessionHistoryError = "";
        workspacePersist(state);
      } catch (error) { state.sessionHistoryError = `Earlier events are unavailable: ${error.message}`; }
      workspaceRerender();
    });
    section.append(older);
  }
  return section;
}

function workspaceSharedSessionPanel(task, state) {
  const session = task.session?.snapshot;
  if (!session || !(session.epoch > 0 || session.attachmentId)) return null;
  const panel = el("section"); panel.className = "authoring-card shared-session-panel";
  const attached = session.mode === "attached";
  const sessionState = String(session.state || "unknown");
  const stateLabel = attached ? sessionState.replaceAll("_", " ") : session.mode;
  const liveAttached = attached && ["busy", "listening"].includes(sessionState);
  panel.append(el("h3", attached ? "Conversation with your invoking agent" : "Task conversation"));
  panel.append(el("p", attached
    ? sessionState === "clock_unavailable"
      ? `The ${session.providerLabel || "invoking"} session is attached, but Harness cannot verify its lease because the host boot identity is unavailable (${session.clockUnavailable?.reason || "unknown reason"}).`
      : liveAttached
        ? `${(session.providerLabel || "Provider").replace(/^./, letter => letter.toUpperCase())} agent ${sessionState === "busy" ? "is processing" : "is connected and listening"} · Browser messages are saved to this task and delivered to the same conversation.`
        : `The ${(session.providerLabel || "invoking").replace(/^./, letter => letter.toUpperCase())} session is ${stateLabel}. Browser messages remain saved on this task; delivery resumes only after the same agent reconnects.`
    : `This task is in ${stateLabel} mode. Existing conversation, answers and planning content remain on the task.`));
  if (attached && session.clockUnavailable) {
    const guidance = session.clockUnavailable.reason === "permission_denied"
      ? "Ask the invoking provider to run the same foreground Harness command with host permission, then retry the unchanged request with the same caller identity and capability. Do not create a new session or switch modes."
      : "Resolve the reported host clock prerequisite, then retry the unchanged foreground command. Do not create a new session or switch modes.";
    const warning = el("p", guidance); warning.className = "authoring-error"; warning.setAttribute("role", "alert"); panel.append(warning);
  }
  if (attached) {
    const current = workspaceCurrentQuestion(state);
    if (current) {
      const prompt = el("div"); prompt.className = "session-current-question";
      prompt.setAttribute("role", "status");
      prompt.append(el("strong", "Current question"), el("p", `Q${current.id} · ${current.display_text || current.text}`));
      prompt.append(workspaceButton(`Open Q${current.id}`, () => {
        state.drawerQuestionId = current.id; state.drawerOpen = true; workspacePersist(state); workspaceRerender();
      }));
      panel.append(prompt);
    }
  }
  if (state.sessionActionMessage) {
    const message = el("p", state.sessionActionMessage.text);
    message.className = state.sessionActionMessage.error ? "authoring-error" : "authoring-feedback";
    message.setAttribute("role", state.sessionActionMessage.error ? "alert" : "status"); panel.append(message);
  }
  const retry = state.sessionActionRequest;
  const actions = el("div"); actions.className = "authoring-actions";
  const canUseSessionActions = !session.clockUnavailable;
  if (retry && canUseSessionActions) {
    const retryButton = el("button", "Retry exact session action"); retryButton.type = "button";
    retryButton.addEventListener("click", () => workspacePostSessionAction(task, state, retry.action).catch(() => {}));
    actions.append(retryButton);
  } else if (attached && canUseSessionActions) {
    if (session.actions?.canRequestReattach) actions.append(workspaceButton("Save a reconnect request", () => workspacePostSessionAction(task, state, "request-reattach").catch(() => {})));
    if (session.actions?.canPause) actions.append(workspaceButton("Pause shared session", () => workspacePostSessionAction(task, state, "pause").catch(() => {})));
    if (session.actions?.canChooseStandalone) actions.append(workspaceButton("Choose standalone mode", () => workspacePostSessionAction(task, state, "choose-standalone").catch(() => {})));
    if (session.actions?.canChooseStandalone) actions.append(workspaceButton("Use dashboard manually", () => workspacePostSessionAction(task, state, "choose-manual").catch(() => {})));
  }
  const details = el("details"); details.dataset.workspaceDisclosure = `session-details:${task.id}`;
  details.append(el("summary", attached ? "Session controls and technical details" : "Session identity and recovery details"));
  if (attached) details.append(el("p", `${session.pendingCount} saved item(s) awaiting receipt · ${session.receivedCount} received · ${session.appliedCount} applied in a committed response.`));
  if (actions.childElementCount) details.append(actions);
  details.append(el("p", `Protocol ${session.protocolVersion} · task ${task.id} · epoch ${session.epoch} · attachment ${session.attachmentId || "none"}`));
  details.append(el("p", `Last contact ${session.lastContact || "not reported"} · status revision ${session.statusRevision} · input revision ${session.inputRevision}`));
  const context = el("pre"); context.textContent = JSON.stringify(session.contextCommand || [], null, 2); details.append(context);
  if (session.recovery) {
    const recovery = el("pre"); recovery.textContent = JSON.stringify(session.recovery, null, 2); details.append(recovery);
  }
  panel.append(details, workspaceSessionHistory(task, state));
  return panel;
}

function workspaceInterviewPanel(task, state, inspector) {
  const panel = el("section"); panel.append(el("h2", "Interview"), el("p", "Your conversation and answers are saved against this task. Model suggestions remain separate from your decisions."));
  const sharedSession = workspaceSharedSessionPanel(task, state);
  if (sharedSession) panel.append(sharedSession);
  panel.append(workspaceInvestigationPanel(task, state));
  const session = task.session?.snapshot;
  const sessionOwner = Boolean(session && (session.epoch > 0 || session.attachmentId));
  if (!(sessionOwner && session.mode !== "standalone")) panel.append(workspaceStartOrResume(task, state));
  const questions = state.authoring?.questions || [];
  const pending = workspaceCurrentQuestion(state);
  if (pending) {
    const alert = el("div"); alert.className = "authoring-question-alert"; alert.setAttribute("role", "status");
    alert.append(el("span", `Question Q${pending.id} needs your answer: ${pending.display_text || pending.text}`));
    const open = el("button", "Open question drawer"); open.type = "button";
    open.addEventListener("click", () => { state.drawerQuestionId = null; state.drawerOpen = true; workspacePersist(state); workspaceRerender(); });
    alert.append(open); panel.append(alert);
  }
  for (const question of questions) {
    const card = workspaceQuestionCard(task, state, question);
    {
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
  const followup = el("section"); followup.className = "authoring-card";
  const followupSession = task.session?.snapshot;
  const attachedProvider = followupSession?.mode === "attached" ? followupSession.providerLabel : null;
  const attachedProviderName = attachedProvider?.replace(/^./, letter => letter.toUpperCase());
  const deliveryUnavailable = followupSession?.mode === "attached"
    && !["busy", "listening"].includes(followupSession.state);
  const sessionStateLabel = String(followupSession?.state || "unknown").replaceAll("_", " ");
  const sendLabel = attachedProvider
    ? deliveryUnavailable ? `Send after ${attachedProviderName} session recovery` : `Send to your connected ${attachedProviderName} conversation`
    : "Send to the interview model";
  const responseLabel = attachedProvider
    ? deliveryUnavailable ? `Request ${attachedProviderName} response after session recovery` : `Request a response from the connected ${attachedProviderName} agent`
    : "Request model response";
  followup.append(el("h3", "Continue the conversation"), el("p", attachedProvider
    ? deliveryUnavailable
      ? `This message can be saved on the task, but the ${attachedProviderName} agent cannot receive it while the attached session is ${sessionStateLabel}. Use Save follow-up only until the same agent reconnects.`
      : `Messages go to this task's connected ${attachedProviderName} conversation. Use Save answer on a question to answer that structured question.`
    : "A follow-up adds context; use Save answer on a question to answer that structured question."));
  const buffer = authoringBuffer(state, "followup", "", state.authoring?.base);
  const input = authoringFocus(el("textarea"), state, "followup"); input.rows = 3; input.value = buffer.value; input.placeholder = "Ask a follow-up or add context"; input.setAttribute("aria-label", "Follow-up message");
  input.addEventListener("input", () => authoringScalarChanged(state, "followup", buffer, input.value));
  const sendFollowup = authoringButton(task, state, sendLabel, async (t, s) => {
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
      s.authoringRequestContinuations.followup = { type: "send", host: attachedProvider || host.value, requestId: followupRequestId };
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
    let generated;
    try { generated = await authoringAction(t, s, "regenerate", { host: continuation?.host || attachedProvider || host.value }, { key: regenerateKey }); }
    catch (error) { error.followupSaved = true; throw error; }
    delete s.authoringRequestContinuations?.followup;
    workspacePersist(s);
    return generated || sent;
  }, undefined, { recovery: { key: "followup", bufferKey: "followup" } });
  sendFollowup.disabled = deliveryUnavailable;
  followup.append(input, sendFollowup);
  followup.append(authoringButton(task, state, "Save follow-up only", async (t, s) => {
    const submitted = input.value;
    const submittedGeneration = buffer.generation;
    s.authoringRequestForms ||= {};
    s.authoringRequestForms["followup-only"] = { key: "followup", value: submitted, generation: submittedGeneration };
    const result = await authoringAction(t, s, "followup", { body: submitted }, { expected: buffer.base, key: "followup-only" });
    if (buffer.value === submitted && buffer.generation === submittedGeneration) { buffer.value = ""; buffer.dirty = false; buffer.base = s.authoring.base; }
    else if (!buffer.dirty) buffer.base = s.authoring.base;
    authoringPersistBuffer(s, "followup", buffer);
    result.acknowledgement = "Follow-up saved as context. Structured questions are unchanged."; return result;
  }, undefined, { recovery: { key: "followup-only", bufferKey: "followup" } }));
  const host = el("select"); host.setAttribute("aria-label", "Model for the next response");
  for (const [value, label] of [["codex", "Codex"], ["claude", "Claude"]]) { const option = el("option", label); option.value = value; host.append(option); }
  const hostKey = "followup-host";
  const hostBuffer = attachedProvider
    ? { value: { host: attachedProvider }, base: state.authoring?.base }
    : authoringFormBuffer(state, hostKey, { host: state.authoring?.jobs?.at(-1)?.host || "codex" }, state.authoring?.base);
  host.value = hostBuffer.value.host;
  if (attachedProvider) {
    const connected = el("p", deliveryUnavailable
      ? `Response host · ${attachedProviderName} session ${sessionStateLabel}; delivery paused`
      : `Response host · connected ${attachedProviderName} conversation`);
    connected.className = "authoring-feedback"; followup.append(connected);
  } else {
    authoringFocus(host, state, "followup-host:host");
    host.addEventListener("change", () => authoringFormChanged(state, hostKey, hostBuffer, "host", host.value));
    const hostStatus = (state.authoring?.hosts || []).find(row => row.host === hostBuffer.value.host);
    followup.append(el("p", `Selected response host: ${hostBuffer.value.host}${hostStatus ? hostStatus.available ? ` · available${hostStatus.version ? ` · ${hostStatus.version}` : ""}` : ` · unavailable: ${hostStatus.error || "not verified"}` : " · availability not checked"}. Requests use this host.`));
    followup.append(host);
  }
  const requestResponse = authoringButton(
    task,
    state,
    responseLabel,
    (t, s) => authoringAction(t, s, "regenerate", { host: attachedProvider || host.value }, { key: "regenerate" }),
    undefined,
    attachedProvider ? undefined : { recovery: { key: "regenerate", bufferKey: hostKey } },
  );
  requestResponse.disabled = deliveryUnavailable;
  followup.append(requestResponse);
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
  inspector.append(el("h2", "Interview context"), el("p", `Task ${task.id} · ${task.phase}`), el("p", `Project ${workspaceProjectName(state.authoring?.project?.repository)}`));
  if (state.authoring?.project?.repository) {
    const projectDetails = el("details");
    projectDetails.append(el("summary", "Repository location"), el("code", state.authoring.project.repository));
    inspector.append(projectDetails);
  }
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
