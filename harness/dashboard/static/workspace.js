"use strict";

const workspaceStates = new Map();
let workspaceData = null;
let workspaceDragging = false;
let workspaceTouching = false;
let workspaceRenderPending = false;
let workspacePendingFocusIntent = null;
let workspaceFocusInteractionEpoch = 0;

document.addEventListener("pointerdown", () => { workspaceFocusInteractionEpoch += 1; }, true);
document.addEventListener("keydown", () => { workspaceFocusInteractionEpoch += 1; }, true);

function workspaceLegacyRoute(hash) {
  const target = (hash || "").slice(1).split(/[?&]/, 1)[0];
  const params = new URLSearchParams((hash || "").slice(1));
  if (target === "diagrams" || target === "diagram-compare" || target === "comments"
      || params.has("version") || params.has("element") || /(?:^|&)element=/.test((hash || "").slice(1))) {
    return { tab: "diagram" };
  }
  if (target === "spec") return { tab: "spec" };
  if (target === "delivery") return { tab: "delivery" };
  if (target === "history") return { tab: "history" };
  if (target === "approvals") return { tab: "review" };
  if (["execution", "execution-decisions", "current-status", "resume"].includes(target)) {
    return { tab: "implementation" };
  }
  return null;
}

function workspacePersist(state) {
  try {
    localStorage.setItem(`myharness-workspace:${state.projectId}:${state.task}`, JSON.stringify({
      projectId: state.projectId, base: state.base, source: state.source,
      operations: state.operations, fields: state.fields, tab: state.tab, selection: state.selection,
      retry: state.retry, status: state.status, savedId: state.savedId,
      authoringRequests: state.authoringRequests,
      authoringRequestActions: state.authoringRequestActions,
      authoringRequestForms: state.authoringRequestForms,
      authoringRetryAcknowledgements: state.authoringRetryAcknowledgements,
      authoringRequestHistory: state.authoringRequestHistory,
      authoringRequestContinuations: state.authoringRequestContinuations,
      authoringHistory: state.authoringHistory,
      authoringConflict: state.authoringConflict, authoringMessage: state.authoringMessage,
      candidateChoice: state.candidateChoice,
      sessionActionRequest: state.sessionActionRequest,
      sessionActionMessage: state.sessionActionMessage,
      sessionHistoryError: state.sessionHistoryError,
      drawerQuestionId: state.drawerQuestionId, drawerFocusReturnKey: state.drawerFocusReturnKey,
      newTaskOpen: state.newTaskOpen, diagramView: state.diagramView,
    }));
    state.storageAvailable = true;
    state.storageError = "";
  } catch (_) {
    state.storageAvailable = false;
    state.storageError = "Browser storage is unavailable. Drafts stay in this page until saved to Harness.";
  }
}

function workspaceAcceptAuthoringSnapshot(state, snapshot) {
  if (!snapshot) return state.authoring;
  const previousGeneration = state.authoring?.base?.generation;
  const nextGeneration = snapshot.base?.generation;
  if (Number.isInteger(previousGeneration) && Number.isInteger(nextGeneration)
      && nextGeneration < previousGeneration) return state.authoring;
  state.authoring = snapshot;
  state.authoringReadSequence = (state.authoringReadSequence || 0) + 1;
  return state.authoring;
}

function workspaceState(task) {
  let state = workspaceStates.get(task.id);
  if (!state) {
    let stored = null;
    const projectId = task.authoring?.project?.id || workspaceData?.project?.id || "unknown-project";
    try {
      stored = JSON.parse(localStorage.getItem(`myharness-workspace:${projectId}:${task.id}`));
      if (!stored) {
        const legacy = JSON.parse(localStorage.getItem(`myharness-workspace:${task.id}`));
        if (legacy && (!legacy.task || legacy.task === task.id)) {
          if (legacy.base === task.workspace.base) stored = legacy;
          else if (legacy.operations?.length || Object.keys(legacy.fields || {}).length) {
            try { localStorage.setItem(`myharness-workspace-recovery:${projectId}:${task.id}`, JSON.stringify(legacy)); } catch (_) { /* the legacy value remains untouched */ }
          }
        }
      }
    } catch (_) { /* absent or unavailable storage */ }
    const latest = task.workspace.proposals.at(-1);
    state = { task: task.id, projectId, base: task.workspace.base, source: task.workspace.source,
      operations: [], fields: {}, tab: task.execution ? "implementation" : "interview", selection: null,
      status: "No proposal edits", saving: false, retry: null, savedId: null,
      authoring: task.authoring || null, ...stored };
    state.projectId = projectId;
    workspaceAcceptAuthoringSnapshot(state, task.authoring);
    state.storageAvailable = true;
    if (state.retry && state.status === "Saving proposal…") {
      state.status = "Save outcome unknown — retry to confirm.";
    }
    if (!stored && latest && !latest.stale) {
      state.operations = latest.operations;
      state.savedId = latest.id;
      state.status = "Saved proposal";
    }
    const route = new URLSearchParams(location.search);
    const legacyRoute = workspaceLegacyRoute(location.hash);
    const aliases = { overview: "implementation", changes: "implementation" };
    const allowedTabs = ["interview", "requirements", "spec", "plan", "diagram", "review",
      "implementation", "checks", "delivery", "history"];
    if (legacyRoute) state.tab = legacyRoute.tab;
    else if (route.get("tab") && allowedTabs.includes(aliases[route.get("tab")] || route.get("tab")))
      state.tab = aliases[route.get("tab")] || route.get("tab");
    else if (route.get("workspace") === "run") state.tab = "implementation";
    else if (route.get("workspace") === "design") state.tab = "requirements";
    if (stored?.mode && !route.has("tab") && !legacyRoute)
      state.tab = stored.mode === "run" ? "implementation" : "requirements";
    workspaceStates.set(task.id, state);
  }
  if (task.authoring) workspaceAcceptAuthoringSnapshot(state, task.authoring);
  task.authoring = state.authoring || task.authoring;
  const pendingFields = workspaceUnappliedFields(state);
  const hasDraft = state.operations.length || pendingFields.length || state.retry;
  if (state.base !== task.workspace.base && !hasDraft) {
    for (const key of Object.keys(state.fields)) if (!pendingFields.includes(key)) delete state.fields[key];
    state.base = task.workspace.base; state.source = task.workspace.source;
  }
  state.stale = state.base !== task.workspace.base;
  return state;
}

window.addEventListener("hashchange", () => {
  const route = workspaceLegacyRoute(location.hash);
  const task = workspaceData?.task;
  const state = task && workspaceStates.get(task.id);
  if (!route || !state) return;
  state.tab = route.tab;
  workspacePersist(state);
  workspaceRerender();
});

function workspaceResult(state) {
  const source = JSON.parse(JSON.stringify(state.source));
  source.proposed_links = [];
  for (const op of state.operations) {
    const rows = op.type === "requirement" ? source.requirements : op.type === "plan" ? source.plan
      : op.type.startsWith("node_") ? source.graph.nodes : source.graph.edges;
    const row = rows.find(item => (item.id || item.key) === op.target);
    if (op.type === "connection_add") source.graph.edges.push({ id: op.target, from: op.from, to: op.to, label: op.label, sources: [], proposed: true });
    else if (op.type === "connection_remove") source.graph.edges = source.graph.edges.filter(item => item.id !== op.target);
    else if (op.type === "traceability") source.proposed_links.push({ target: op.target, requirement: op.requirement });
    else if (row) for (const [key, value] of Object.entries(op)) if (!["type", "target"].includes(key)) row[key] = value;
  }
  return source;
}

function workspaceUnappliedFields(state) {
  const result = workspaceResult(state);
  const nodes = new Map(result.graph.nodes.map(row => [row.id, row]));
  const edges = new Map(result.graph.edges.map(row => [row.id, row]));
  return Object.entries(state.fields).filter(([key, value]) => {
    const split = key.indexOf(":"), kind = key.slice(0, split), target = key.slice(split + 1);
    if (key === "diagram-selection") return false;
    let expected;
    if (kind === "refine") expected = [...result.requirements, ...result.plan].find(row => row.key === target)?.text;
    else if (kind === "node-label" || kind === "inline-label") expected = nodes.get(target)?.label;
    else if (kind === "node-x" || kind === "node-y") {
      expected = nodes.get(target)?.[kind === "node-x" ? "x" : "y"];
      return String(value).trim() === "" || !Number.isFinite(Number(value)) || Number(value) !== expected;
    } else if (kind === "link") expected = result.proposed_links.find(link => link.target === target)?.requirement || "request";
    else if (kind.startsWith("edge-")) {
      const edge = edges.get(target), node = nodes.get(target);
      if (edge) expected = edge[kind.slice(5)];
      else if (node) expected = kind === "edge-label" ? "" : kind === "edge-from" ? target : result.graph.nodes.find(row => row.id !== target)?.id;
    }
    return expected === undefined || String(value) !== String(expected);
  }).map(([key]) => key);
}

function workspaceStatusText(state) {
  const status = state.status.startsWith("Unsaved editor text")
    ? state.operations.length ? "Unsaved proposal" : "No proposal edits" : state.status;
  return state.stale ? "Draft is stale: source or runtime changed. Your edits are retained against their original base."
    : workspaceUnappliedFields(state).length ? `${status}. Unapplied editor changes remain; add them to the proposal to save them.` : status;
}

function workspaceSaveBar(task, state) {
  if (!(state.operations.length || workspaceUnappliedFields(state).length
      || state.stale || state.retry || state.savedId)) return null;
  const bar = el("div"); bar.className = "workspace-savebar";
  const message = el("span", workspaceStatusText(state));
  message.setAttribute("role", "status"); bar.append(message);
  bar.dataset.status = state.stale || workspaceUnappliedFields(state).length || /failed|unsaved/i.test(state.status) ? "attention" : "saved";
  if (state.stale) bar.append(workspaceActionButton(state, state.base, "discard", "Discard draft and reload source", () => {
    state.base = task.workspace.base; state.source = task.workspace.source; state.operations = []; state.fields = {}; state.retry = null; state.stale = false; state.savedId = null; state.status = "Source reloaded";
    workspacePersist(state); workspaceRerender();
  }));
  else {
    const save = workspaceActionButton(state, state.base, "save", state.retry ? "Retry save" : "Save proposal", () => workspaceSave(task, state));
    save.disabled = state.saving || !state.operations.length; bar.append(save);
  }
  return bar;
}

function workspaceUpdateEditorStatus(state) {
  if (workspaceData?.task?.id !== state.task) return;
  const root = document.getElementById("task");
  const bar = root?.querySelector(".workspace-savebar");
  if (!bar) {
    const layout = root?.querySelector(".workspace-layout");
    const added = layout && workspaceSaveBar(workspaceData.task, state);
    if (added) root.insertBefore(added, layout);
    return;
  }
  bar.querySelector('[role="status"]').textContent = workspaceStatusText(state);
  bar.dataset.status = state.stale || workspaceUnappliedFields(state).length || /failed|unsaved/i.test(state.status) ? "attention" : "saved";
}

function workspaceConsumeFields(state, values) {
  for (const [key, value] of Object.entries(values)) {
    if (Object.hasOwn(state.fields, key) && String(state.fields[key]) === String(value)) delete state.fields[key];
  }
}

function workspaceEdit(state, operation, editorTarget = operation.target) {
  if (state.stale || state.saving) return;
  const family = type => type.startsWith("connection_") ? "connection" : type;
  state.operations = state.operations.filter(op => op.target !== operation.target || family(op.type) !== family(operation.type));
  state.operations.push(operation);
  const consumed = {};
  const take = (prefix, value) => { consumed[`${prefix}:${editorTarget}`] = value; };
  if (operation.type === "requirement" || operation.type === "plan") take("refine", operation.text);
  else if (operation.type === "traceability") take("link", operation.requirement);
  else if (operation.type === "node_label") { take("node-label", operation.label); take("inline-label", operation.label); }
  else if (operation.type === "node_position") { take("node-x", operation.x); take("node-y", operation.y); }
  else if (operation.type === "connection_add" || operation.type === "connection_change") {
    take("edge-from", operation.from); take("edge-to", operation.to); take("edge-label", operation.label);
  }
  workspaceConsumeFields(state, consumed);
  state.retry = null;
  state.status = "Unsaved proposal";
  workspacePersist(state);
  workspaceRerender();
}

function workspaceSelect(state, id) {
  state.selection = id;
  delete state.fields["diagram-selection"];
  workspacePersist(state);
}

function workspaceRerender() {
  if (!workspaceData) return;
  const state = workspaceData.task && workspaceStates.get(workspaceData.task.id);
  if (state && document.querySelector(".authoring-drawer:not(.authoring-new-task-dialog)[open]")) {
    state.drawerOpen = true;
    state.drawerReplacing = true;
  }
  if (state && document.querySelector(".authoring-new-task-dialog[open]")) {
    state.newTaskOpen = true; state.newTaskReplacing = true;
  }
  render(workspaceData);
  if (state?.drawerReplacing) setTimeout(() => { state.drawerReplacing = false; }, 0);
  if (state?.newTaskReplacing) setTimeout(() => { state.newTaskReplacing = false; }, 0);
}

function workspaceTouchStart() {
  workspaceTouching = true;
}

function workspaceTouchEnd(event) {
  if (event.touches?.length) return;
  workspaceTouching = false;
  if (workspaceRenderPending && !workspaceDragging) workspaceRerender();
}

function workspaceSetTab(state, name) {
  state.tab = name;
  const url = new URL(location.href);
  url.searchParams.set("tab", name);
  url.searchParams.delete("workspace");
  const legacyHash = workspaceLegacyRoute(url.hash);
  if (legacyHash && legacyHash.tab !== name) url.hash = "";
  history.replaceState(null, "", url);
}

function workspaceTabs(state, names, label) {
  const tabs = el("div");
  tabs.className = "workspace-tabs";
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", label);
  for (const [name, title] of names) {
    const button = workspaceButton(title, () => {
      workspaceSetTab(state, name); workspacePersist(state); workspaceRerender();
    });
    button.id = `workspace-tab-${name}`;
    button.dataset.workspaceFocus = `tab:${name}`;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", String(state.tab === name));
    button.setAttribute("aria-controls", "workspace-panel");
    button.tabIndex = state.tab === name ? 0 : -1;
    button.addEventListener("keydown", event => {
      let index = names.findIndex(([key]) => key === name);
      if (event.key === "ArrowRight") index = (index + 1) % names.length;
      else if (event.key === "ArrowLeft") index = (index - 1 + names.length) % names.length;
      else if (event.key === "Home") index = 0;
      else if (event.key === "End") index = names.length - 1;
      else return;
      event.preventDefault();
      workspaceSetTab(state, names[index][0]); workspacePersist(state); workspaceRerender();
      document.getElementById(`workspace-tab-${state.tab}`)?.focus();
    });
    tabs.append(button);
  }
  return tabs;
}

async function workspaceSave(task, state) {
  if (state.stale || state.saving || !state.operations.length) return;
  state.retry ||= { task: task.id, base: state.base, operations: JSON.parse(JSON.stringify(state.operations)), request_id: crypto.randomUUID() };
  const payload = state.retry;
  state.saving = true; state.status = "Saving proposal…"; workspacePersist(state); workspaceRerender();
  try {
    const response = await fetch("/api/workspace/proposals", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    state.savedId = data.proposal.id; state.retry = null; state.status = "Saved proposal";
  } catch (error) {
    state.status = `Save failed: ${error.message}. Draft retained; retry uses the same request identity.`;
  } finally {
    state.saving = false; workspacePersist(state);
    if (workspaceData?.task?.id === task.id) { workspaceRerender(); await refreshDashboard(); }
  }
}

function workspaceKnowledge(task, state, existingKnowledge) {
  if (existingKnowledge?.querySelector(".knowledge-panel")?.dataset.task === task.id) return existingKnowledge;
  const knowledge = el("details");
  knowledge.dataset.viewDisclosure = "knowledge";
  knowledge.append(workspaceSummary(state, knowledge, "Project knowledge"), renderKnowledgePanel(task.id));
  return knowledge;
}

function workspaceRunPanel(task, state, inspector, existingKnowledge = null) {
  const panel = el("section");
  const execution = task.execution;
  executionTaskId = task.id;
  executionSnapshot = execution;
  if (state.tab === "implementation") {
    panel.append(renderStatusSummary(task));
    if (execution) {
      const checks = execution.activity?.checks || [];
      const checkSummary = workspaceActionButton(state, execution.id, "check-summary", `Checks: ${checks.filter(check => check.status === "PASS").length} passed / ${checks.length} recorded attempts · ${execution.verification?.status || "verification not reported"}`, () => { state.tab = "checks"; workspacePersist(state); workspaceRerender(); });
      panel.append(checkSummary, renderExecution(task.id, execution));
    }
    else panel.append(el("h2", "No execution recorded"), renderResume(task.resume));
    inspector.append(el("small", "Run inspector"), el("h2", "Decisions & controls"), renderApprovalCenter(task));
    inspector.append(workspaceKnowledge(task, state, existingKnowledge));
    const diagnostics = el("details");
    diagnostics.dataset.viewDisclosure = "design-diagnostics";
    const diagnosticDiagrams = { ...task.diagrams, implementation: null };
    diagnostics.append(
      workspaceSummary(state, diagnostics, "Design notation details"),
      renderDiagrams(task.id, diagnosticDiagrams),
    );
    panel.append(diagnostics);
    if (execution?.delivery) {
      const delivery = el("details");
      delivery.dataset.viewDisclosure = "overview-delivery-record";
      delivery.append(workspaceSummary(state, delivery, "Delivery record"), renderDelivery(execution.delivery));
      panel.append(delivery);
    }
    panel.append(workspaceTaskDetails(task));
    panel.append(el("h2", "Changes in this run"));
    if (task.diagrams.implementation) panel.append(renderChangeIllustration(task.diagrams.implementation));
    else panel.append(el("p", "No recorded execution change references available."));
    panel.append(el("p", "A source diff is unavailable unless recorded by the runtime."));
  } else if (state.tab === "checks") {
    panel.append(el("h2", "Recorded checks"));
    if (execution) {
      panel.append(renderVerificationState(execution), renderProgress(execution));
      const checks = execution.activity?.checks || [];
      if (!checks.length) panel.append(el("p", "No individual check results recorded."));
      for (const check of checks) {
        const row = el("article"); row.className = "workspace-check";
        row.append(el("strong", check.area || check.id), el("p", check.status || "Status unknown"), el("small", check.at || "Time not reported"));
        if (check.summary) row.append(el("p", check.summary));
        panel.append(row);
      }
    } else panel.append(el("p", "Checks unavailable until an execution records evidence."));
    inspector.append(el("h2", "Evidence"), el("p", "Missing or unexecuted checks are never counted as passes. Tooling repair is separate from developer decisions."));
  } else if (state.tab === "delivery") {
    panel.append(el("h2", "Delivery"));
    if (execution?.delivery) panel.append(renderDelivery(execution.delivery));
    else panel.append(el("p", "No delivery record available."));
    inspector.append(el("h2", "Delivery authority"), el("p", "Implementation approval does not authorize staging browser actions. The existing browser plan requires its own exact-state approval."));
  } else {
    panel.append(el("h2", "Run history"));
    if (execution) panel.append(renderFeed(execution), renderVerificationState(execution));
    else panel.append(el("p", "No execution history recorded."));
    panel.append(el("h3", "Recent task events"));
    for (const event of [...(task.workspace.events || [])].reverse()) panel.append(el("p", `${event.kind} · ${event.at}`));
    panel.append(renderHistory(task.id, task.spec.history), renderDecisions(task.decisions));
    rememberComments(task);
    const records = el("details");
    records.dataset.viewDisclosure = "workspace-task-records";
    records.append(workspaceSummary(state, records, "Task records and existing tools"), renderResume(task.resume),
      renderSpec(task.spec, task.phase), renderApprovalCenter(task), workspaceKnowledge(task, state, existingKnowledge),
      ...(execution ? [renderExecution(task.id, execution)] : []),
      renderDiagrams(task.id, task.diagrams), renderComparison(task.id, task.diagrams),
      renderCommentsSection(task.comments, task.diagrams));
    records.open = new URLSearchParams(location.search).get("details") === "records";
    if (execution?.delivery) records.append(renderDelivery(execution.delivery));
    panel.append(records);
    panel.append(workspaceTaskDetails(task));
  }
  return panel;
}

function workspaceTaskDetails(task) {
  const metadata = el("details");
  metadata.className = "task-metadata";
  metadata.dataset.viewDisclosure = "task-details";
  const list = el("dl");
  for (const [label, value] of [["Description", task.request], ["Phase", task.phase],
    ["Status", task.status], ["Complexity", `${task.complexity} (${task.complexity_source})`],
    ["Specification", specText(task.spec)], ["Spec directory", task.spec.directory]]) {
    list.append(el("dt", label), el("dd", value));
  }
  const elapsed = el("time", formatElapsed(Date.now() - Date.parse(task.created_at)));
  elapsed.dateTime = task.created_at;
  elapsed.dataset.since = task.created_at;
  list.append(el("dt", "Elapsed"), elapsed, el("dt", "Latest task activity"),
    el("dd", activityText(task.latest_activity)));
  metadata.append(workspaceSummary({ task: task.id }, metadata, "Task details"), list);
  return metadata;
}

function renderWorkspaceTask(task, existingKnowledge = null) {
  const state = workspaceState(task);
  const root = el("section"); root.id = "task"; root.className = "workspace-task";
  const top = el("div"); top.className = "workspace-top";
  const heading = el("div"); heading.className = "workspace-heading";
  heading.append(el("small", `Task ${task.id} · ${task.phase}`), el("h2", task.request), el("p", task.guidance?.status || task.status));
  const workActivity = task.execution?.activity?.last_activity_at;
  const authoring = state.authoring || task.authoring;
  const authoringSpec = authoring?.working
    ? `Working draft saved · ${authoring.working.id.slice(0, 8)}${authoring.adopted ? ` · adopted ${authoring.adopted.id.slice(0, 8)}` : " · not adopted"}`
    : authoring?.approved
      ? `Approved specification v${authoring.approved.version}`
      : authoring?.initialized ? "No working draft saved" : specText(task.spec);
  const meta = el("p", workActivity ? `Last implementation activity · ${workActivity}` : `Specification · ${authoringSpec}`);
  meta.className = "workspace-muted"; heading.append(meta);
  const pendingQuestion = workspaceCurrentQuestion(state);
  const drawerButton = el("button", pendingQuestion ? `Answer Q${pendingQuestion.id}` : "Open interview");
  drawerButton.type = "button"; drawerButton.dataset.workspaceFocus = `interview-drawer:${task.id}`;
  drawerButton.setAttribute("aria-haspopup", "dialog");
  drawerButton.addEventListener("click", () => {
    state.drawerQuestionId = pendingQuestion?.id ?? null;
    state.drawerFocusReturnKey = drawerButton.dataset.workspaceFocus;
    state.drawerOpen = true; workspacePersist(state); workspaceRerender();
  });
  drawerButton.hidden = state.tab === "interview";
  if (authoring?.model_recovery) {
    const recovery = el("div"); recovery.className = "authoring-recovery"; recovery.setAttribute("role", "alert");
    recovery.append(el("span", `Interview model ${authoring.model_recovery.host}: ${authoring.model_recovery.state}. Manual answers are still usable.`));
    recovery.append(workspaceButton("Recover interview", () => { workspaceSetTab(state, "interview"); workspacePersist(state); workspaceRerender(); }));
    heading.append(recovery);
  }
  const newTaskButton = el("button", "New task"); newTaskButton.type = "button";
  newTaskButton.className = "workspace-new-interview";
  newTaskButton.dataset.workspaceFocus = `new-interview:${task.id}`;
  newTaskButton.setAttribute("aria-haspopup", "dialog");
  newTaskButton.addEventListener("click", () => {
    state.newTaskOpen = true; state.newTaskFocusReturnKey = newTaskButton.dataset.workspaceFocus;
    workspacePersist(state); workspaceRerender();
  });
  const topActions = el("div"); topActions.className = "workspace-top-actions";
  topActions.append(drawerButton, newTaskButton);
  top.append(heading, topActions); root.append(top);
  const names = [["interview", "Interview"], ["requirements", "Requirements"], ["spec", "Spec"],
    ["plan", "Plan"], ["diagram", "Diagram"], ["review", `Review${state.operations.length ? ` (${state.operations.length})` : ""}`],
    ["implementation", "Implementation"], ["checks", "Checks"], ["delivery", "Delivery"], ["history", "History"]];
  if (!names.some(([name]) => name === state.tab)) state.tab = names[0][0];
  root.append(workspaceTabs(state, names, "Task workflow"));
  const authoringStatus = workspaceAuthoringStatus(task, state);
  if (authoringStatus) root.append(authoringStatus);
  const bar = workspaceSaveBar(task, state);
  if (bar) root.append(bar);
  const layout = el("div"); layout.className = "workspace-layout";
  const inspector = el("aside"); inspector.className = "workspace-inspector";
  inspector.setAttribute("aria-label", "Contextual inspector"); inspector.dataset.workspaceScroll = "inspector";
  let panel;
  panel = ({
    interview: workspaceInterviewPanel,
    requirements: workspaceRequirementsPanel,
    spec: workspaceSpecPanel,
    plan: workspacePlan,
    diagram: workspaceDiagram,
    review: workspaceReviewPanel,
    implementation: (selectedTask, selectedState, selectedInspector) =>
      workspaceRunPanel(selectedTask, selectedState, selectedInspector, existingKnowledge),
    checks: (selectedTask, selectedState, selectedInspector) =>
      workspaceRunPanel(selectedTask, selectedState, selectedInspector, existingKnowledge),
    delivery: (selectedTask, selectedState, selectedInspector) =>
      workspaceRunPanel(selectedTask, selectedState, selectedInspector, existingKnowledge),
    history: (selectedTask, selectedState, selectedInspector) => {
      const history = workspaceRunPanel(selectedTask, selectedState, selectedInspector, existingKnowledge);
      history.append(workspaceConversationHistoryPanel(selectedTask, selectedState));
      return history;
    },
  })[state.tab](task, state, inspector);
  panel.id = "workspace-panel"; panel.classList.add("workspace-panel"); panel.setAttribute("role", "tabpanel");
  panel.setAttribute("aria-labelledby", `workspace-tab-${state.tab}`);
  layout.append(panel, inspector); root.append(layout);
  const drawer = workspaceInterviewDrawer(task, state, drawerButton);
  root.append(drawer);
  if (state.drawerOpen) setTimeout(() => { if (drawer.isConnected && !drawer.open) drawer.showModal(); }, 0);
  const newTaskDialog = workspaceNewTaskDialog(workspaceData, state, newTaskButton);
  root.append(newTaskDialog);
  if (state.newTaskOpen) setTimeout(() => { if (newTaskDialog.isConnected && !newTaskDialog.open) newTaskDialog.showModal(); }, 0);
  if (state.saving) inspector.querySelectorAll("input,textarea,select,button").forEach(node => { node.disabled = true; });
  else if (state.stale) {
    inspector.querySelectorAll("input,textarea").forEach(node => { node.readOnly = true; });
    inspector.querySelectorAll("select,button").forEach(node => { node.disabled = true; });
  }
  return root;
}

function workspaceTaskGroup(task) {
  const status = task.guidance?.status || "";
  if (/decision|action needed|did not start|checks incomplete/i.test(status)) return "Needs you";
  if (status === "Verified complete") return "Finished";
  if (["Running", "Launching", "WorkerOwned", "Reserved"].includes(task.status)) return "Running";
  if (/awaiting|blocked|failed|recovery|required|interrupted/i.test(task.status || "")) return "Needs you";
  return "Ready / pending";
}

function workspaceNavigation(tasks, selected) {
  const nav = el("nav"); nav.id = "tasks"; nav.setAttribute("aria-label", "Tasks");
  nav.append(el("h2", "Your workspace"));
  const picker = el("select"); picker.dataset.workspaceFocus = workspaceFocusKey(selected, "tasks", "picker"); picker.className = "workspace-mobile-picker"; picker.setAttribute("aria-label", "Select task");
  for (const task of tasks) { const option = el("option", task.request); option.value = task.id; picker.append(option); }
  picker.value = selected || ""; picker.addEventListener("change", () => { window.location.href = `/?task=${encodeURIComponent(picker.value)}`; });
  nav.append(picker);
  const groups = new Map([["Needs you", []], ["Running", []], ["Finished", []], ["Ready / pending", []]]);
  for (const task of tasks) {
    const group = workspaceTaskGroup(task);
    groups.get(group).push(task);
  }
  for (const [group, rows] of groups) {
    if (!rows.length) continue;
    const heading = el("h3", `${group} · ${rows.length}`); heading.className = "workspace-task-group"; nav.append(heading);
    for (const task of rows) {
      const link = el("a", task.request); link.dataset.workspaceFocus = workspaceFocusKey(selected, task.id, "task-link"); link.href = `/?task=${encodeURIComponent(task.id)}`; link.className = "workspace-task-link";
      if (task.id === selected) link.setAttribute("aria-current", "page");
      link.append(el("small", task.guidance?.status || task.status)); nav.append(link);
    }
  }
  if (!tasks.length) nav.append(el("p", "No tasks recorded."));
  const theme = el("select"); theme.dataset.workspaceFocus = workspaceFocusKey(selected, "theme", "select"); theme.setAttribute("aria-label", "Theme");
  for (const value of ["system", "light", "dark"]) { const option = el("option", `${value[0].toUpperCase()}${value.slice(1)} theme`); option.value = value; theme.append(option); }
  let preference = "system";
  try { preference = localStorage.getItem("myharness-theme") || "system"; } catch (_) { /* Theme choice remains usable in memory. */ }
  theme.value = ["system", "light", "dark"].includes(preference) ? preference : "system";
  const apply = () => {
    document.documentElement.dataset.theme = theme.value;
    try { localStorage.setItem("myharness-theme", theme.value); } catch (_) { /* This page keeps the selected theme. */ }
  };
  theme.addEventListener("change", apply); apply(); nav.append(theme);
  return nav;
}

function withWorkspaceFocus(replace) {
  const node = document.activeElement;
  const activeKey = node?.dataset.workspaceFocus;
  const activeDialog = node?.closest("dialog");
  const activeSurface = activeDialog?.classList.contains("authoring-new-task-dialog")
    ? "new-task" : activeDialog?.classList.contains("authoring-drawer") ? "drawer" : null;
  const taskId = workspaceData?.task?.id || null;
  const state = taskId ? workspaceStates.get(taskId) : null;
  let pending = workspacePendingFocusIntent;
  if (pending && (pending.taskId !== taskId || !state
      || !(pending.surface === "drawer" ? state.drawerOpen : state.newTaskOpen))) {
    workspacePendingFocusIntent = null;
    pending = null;
  }
  const canCarryPending = pending && (!activeKey || activeKey === pending.key)
    && (!activeSurface || activeSurface === pending.surface)
    && pending.interactionEpoch === workspaceFocusInteractionEpoch;
  if (pending && !canCarryPending) {
    workspacePendingFocusIntent = null;
    pending = null;
  }
  const key = activeKey || pending?.key || null;
  const surface = activeSurface || pending?.surface || null;
  const selectedNode = node?.dataset.nodeId;
  const selectedEdge = node?.dataset.edgeId;
  const editors = new Map([...document.querySelectorAll("input[data-workspace-focus],textarea[data-workspace-focus]")]
    .filter(item => typeof item.selectionStart === "number")
    .map(item => [item.dataset.workspaceFocus, [item.selectionStart, item.selectionEnd, item.selectionDirection]]));
  if (key && pending?.selection && !editors.has(key)) editors.set(key, pending.selection);
  const disclosures = new Set([...document.querySelectorAll("details[data-workspace-disclosure]")]
    .filter(item => item.open)
    .map(item => item.dataset.workspaceDisclosure));
  const scroll = new Map([...document.querySelectorAll("[data-workspace-scroll]")].map(item => [item.dataset.workspaceScroll, [item.scrollLeft, item.scrollTop]]));
  replace();
  for (const item of document.querySelectorAll("details[data-workspace-disclosure]")) {
    if (disclosures.has(item.dataset.workspaceDisclosure)) item.open = true;
  }
  const focusRoot = surface === "drawer" ? document.querySelector(".authoring-drawer:not(.authoring-new-task-dialog)")
    : surface === "new-task" ? document.querySelector(".authoring-new-task-dialog") : document;
  const target = key ? [...(focusRoot || document).querySelectorAll("[data-workspace-focus]")].find(item => item.dataset.workspaceFocus === key)
    : selectedNode ? [...document.querySelectorAll("[data-node-id]")].find(item => item.dataset.nodeId === selectedNode)
    : selectedEdge ? [...document.querySelectorAll("[data-edge-id]")].find(item => item.dataset.edgeId === selectedEdge) : null;
  for (const item of document.querySelectorAll("input[data-workspace-focus],textarea[data-workspace-focus]")) {
    const saved = editors.get(item.dataset.workspaceFocus);
    if (saved && typeof item.selectionStart === "number" && typeof item.setSelectionRange === "function") item.setSelectionRange(...saved);
  }
  target?.focus({ preventScroll: true });
  const targetDialog = target?.closest("dialog");
  if (target && targetDialog && !targetDialog.open && key && surface && state
      && (surface === "drawer" ? state.drawerOpen : state.newTaskOpen)) {
    const saved = editors.get(key) || pending?.selection || null;
    const intent = { taskId, surface, key, selection: saved, interactionEpoch: workspaceFocusInteractionEpoch };
    workspacePendingFocusIntent = intent;
    setTimeout(() => {
      if (workspacePendingFocusIntent !== intent) return;
      const currentState = workspaceStates.get(taskId);
      if (!currentState || !(surface === "drawer" ? currentState.drawerOpen : currentState.newTaskOpen)
          || workspaceFocusInteractionEpoch !== intent.interactionEpoch) {
        workspacePendingFocusIntent = null;
        return;
      }
      const currentDialog = surface === "drawer"
        ? document.querySelector(".authoring-drawer:not(.authoring-new-task-dialog)")
        : document.querySelector(".authoring-new-task-dialog");
      const currentTarget = currentDialog && [...currentDialog.querySelectorAll("[data-workspace-focus]")]
        .find(item => item.dataset.workspaceFocus === key);
      if (!currentTarget?.isConnected || !currentDialog.open) return;
      const active = document.activeElement;
      if (active?.dataset.workspaceFocus && active.dataset.workspaceFocus !== key) {
        workspacePendingFocusIntent = null;
        return;
      }
      currentTarget.focus({ preventScroll: true });
      if (saved && typeof currentTarget.setSelectionRange === "function") currentTarget.setSelectionRange(...saved);
      workspacePendingFocusIntent = null;
    }, 0);
  } else if (target && targetDialog?.open && key && pending) {
    workspacePendingFocusIntent = null;
  }
  for (const item of [...document.querySelectorAll("[data-workspace-scroll]")].reverse()) {
    const saved = scroll.get(item.dataset.workspaceScroll); if (saved) { item.scrollLeft = saved[0]; item.scrollTop = saved[1]; }
  }
}
