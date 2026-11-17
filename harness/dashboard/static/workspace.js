"use strict";

const workspaceStates = new Map();
let workspaceData = null;
let workspaceDragging = false;
let workspaceRenderPending = false;

function workspaceLegacyRoute(hash) {
  const target = (hash || "").slice(1).split(/[?&]/, 1)[0];
  const params = new URLSearchParams((hash || "").slice(1));
  if (target === "diagrams" || target === "diagram-compare" || target === "comments"
      || params.has("version") || params.has("element") || /(?:^|&)element=/.test((hash || "").slice(1))) {
    return { mode: "design", tab: "diagram" };
  }
  if (target === "spec") return { mode: "design", tab: "requirements" };
  if (target === "delivery") return { mode: "run", tab: "delivery" };
  if (target === "history") return { mode: "run", tab: "history" };
  if (["approvals", "execution", "execution-decisions", "current-status", "resume"].includes(target)) {
    return { mode: "run", tab: "overview" };
  }
  return null;
}

function workspacePersist(state) {
  try {
    localStorage.setItem(`myharness-workspace:${state.task}`, JSON.stringify({ base: state.base, source: state.source,
      operations: state.operations, fields: state.fields, mode: state.mode, tab: state.tab, selection: state.selection,
      retry: state.retry, status: state.status, savedId: state.savedId }));
  } catch (_) { /* The current page still retains the draft when storage is unavailable. */ }
}

function workspaceState(task) {
  let state = workspaceStates.get(task.id);
  if (!state) {
    let stored = null;
    try { stored = JSON.parse(localStorage.getItem(`myharness-workspace:${task.id}`)); } catch (_) { /* absent */ }
    const latest = task.workspace.proposals.at(-1);
    const runSurface = Boolean(task.execution || task.guidance?.target === "start"
      || ["approval", "execution"].includes(task.phase));
    state = { task: task.id, base: task.workspace.base, source: task.workspace.source, operations: [], fields: {},
      mode: runSurface ? "run" : "design", tab: runSurface ? "overview" : "requirements", selection: null,
      status: "No proposal edits", saving: false, retry: null, savedId: null, ...stored };
    if (state.retry && state.status === "Saving proposal…") {
      state.status = "Save outcome unknown — retry to confirm.";
    }
    if (!stored && latest && !latest.stale) {
      state.operations = latest.operations;
      state.savedId = latest.id;
      state.status = "Saved proposal";
    }
    const route = new URLSearchParams(location.search);
    const requestedMode = route.get("workspace");
    if (["design", "run"].includes(requestedMode)) state.mode = requestedMode;
    const legacyRoute = workspaceLegacyRoute(location.hash);
    if (legacyRoute) {
      state.mode = legacyRoute.mode;
      state.tab = legacyRoute.tab;
    }
    const allowedTabs = state.mode === "design"
      ? ["requirements", "plan", "diagram", "review"]
      : ["overview", "changes", "checks", "delivery", "history"];
    if (!legacyRoute && allowedTabs.includes(route.get("tab"))) state.tab = route.get("tab");
    workspaceStates.set(task.id, state);
  }
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
  state.mode = route.mode;
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

function workspaceRerender() { if (workspaceData) render(workspaceData); }

function workspaceTabs(state, names, label) {
  const tabs = el("div");
  tabs.className = "workspace-tabs";
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", label);
  for (const [name, title] of names) {
    const button = workspaceButton(title, () => {
      state.tab = name; workspacePersist(state); workspaceRerender();
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
      state.tab = names[index][0]; workspacePersist(state); workspaceRerender();
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
  if (state.tab === "overview") {
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
    diagnostics.append(workspaceSummary(state, diagnostics, "Design notation details"), renderDiagrams(task.id, task.diagrams));
    panel.append(diagnostics);
    if (execution?.delivery) {
      const delivery = el("details");
      delivery.dataset.viewDisclosure = "overview-delivery-record";
      delivery.append(workspaceSummary(state, delivery, "Delivery record"), renderDelivery(execution.delivery));
      panel.append(delivery);
    }
    panel.append(workspaceTaskDetails(task));
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
  } else if (state.tab === "changes") {
    panel.append(el("h2", "Changes in this run"));
    if (task.diagrams.implementation) panel.append(renderChangeIllustration(task.diagrams.implementation));
    else panel.append(el("p", "No recorded execution change references available."));
    panel.append(el("p", "A source diff is unavailable unless recorded by the runtime. Design proposals are reviewed in Plan & Design → Review."));
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
  const meta = el("p", state.mode === "design"
    ? `Specification · ${specText(task.spec)}`
    : workActivity ? `Work update · ${workActivity}` : "No recorded run activity");
  meta.className = "workspace-muted"; heading.append(meta);
  const switcher = el("div"); switcher.className = "workspace-switch";
  switcher.setAttribute("aria-label", "Workspace");
  for (const [mode, title] of [["design", "Plan & Design"], ["run", "Live Run"]]) {
    const button = workspaceButton(title, () => {
      state.mode = mode; state.tab = mode === "design" ? "requirements" : "overview";
      workspacePersist(state); workspaceRerender();
    });
    button.dataset.workspaceFocus = `mode:${mode}`;
    button.setAttribute("aria-pressed", String(state.mode === mode)); switcher.append(button);
  }
  top.append(heading, switcher); root.append(top);
  const names = state.mode === "design" ? [["requirements", "Requirements"], ["plan", "Plan"], ["diagram", "Diagram"], ["review", `Review${state.operations.length ? ` (${state.operations.length})` : ""}`]]
    : [["overview", "Overview"], ["changes", "Changes"], ["checks", "Checks"], ["delivery", "Delivery"], ["history", "History"]];
  if (!names.some(([name]) => name === state.tab)) state.tab = names[0][0];
  root.append(workspaceTabs(state, names, state.mode === "design" ? "Plan and design" : "Live run"));
  if (state.mode === "design") {
    const bar = workspaceSaveBar(task, state);
    if (bar) root.append(bar);
  }
  const layout = el("div"); layout.className = "workspace-layout";
  const inspector = el("aside"); inspector.className = "workspace-inspector";
  inspector.setAttribute("aria-label", "Contextual inspector"); inspector.dataset.workspaceScroll = "inspector";
  let panel;
  if (state.mode === "run") panel = workspaceRunPanel(task, state, inspector, existingKnowledge);
  else panel = ({ requirements: workspaceRequirements, plan: workspacePlan, diagram: workspaceDiagram, review: workspaceReview })[state.tab](task, state, inspector);
  panel.id = "workspace-panel"; panel.classList.add("workspace-panel"); panel.setAttribute("role", "tabpanel");
  panel.setAttribute("aria-labelledby", `workspace-tab-${state.tab}`);
  layout.append(panel, inspector); root.append(layout);
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
  const key = node?.dataset.workspaceFocus;
  const selectedNode = node?.dataset.nodeId;
  const selectedEdge = node?.dataset.edgeId;
  const editors = new Map([...document.querySelectorAll("input[data-workspace-focus],textarea[data-workspace-focus]")]
    .filter(item => typeof item.selectionStart === "number")
    .map(item => [item.dataset.workspaceFocus, [item.selectionStart, item.selectionEnd, item.selectionDirection]]));
  const scroll = new Map([...document.querySelectorAll("[data-workspace-scroll]")].map(item => [item.dataset.workspaceScroll, [item.scrollLeft, item.scrollTop]]));
  replace();
  const target = key ? [...document.querySelectorAll("[data-workspace-focus]")].find(item => item.dataset.workspaceFocus === key)
    : selectedNode ? [...document.querySelectorAll("[data-node-id]")].find(item => item.dataset.nodeId === selectedNode)
    : selectedEdge ? [...document.querySelectorAll("[data-edge-id]")].find(item => item.dataset.edgeId === selectedEdge) : null;
  for (const item of document.querySelectorAll("input[data-workspace-focus],textarea[data-workspace-focus]")) {
    const saved = editors.get(item.dataset.workspaceFocus);
    if (saved && typeof item.selectionStart === "number" && typeof item.setSelectionRange === "function") item.setSelectionRange(...saved);
  }
  target?.focus({ preventScroll: true });
  for (const item of [...document.querySelectorAll("[data-workspace-scroll]")].reverse()) {
    const saved = scroll.get(item.dataset.workspaceScroll); if (saved) { item.scrollLeft = saved[0]; item.scrollTop = saved[1]; }
  }
}
