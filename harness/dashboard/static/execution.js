"use strict";

const EXECUTION_DRAFTS = new Map();
const EXECUTION_FOCUS_IDS = new Set(["execution-decision-text", "execution-decision-verdict"]);
let executionTaskId = null;
let executionSnapshot = null;

function executionDraftKey(execution, blocker) {
  return `${execution.id}:${blocker.id}:${blocker.revision}`;
}

function draftFor(execution, blocker) {
  const key = executionDraftKey(execution, blocker);
  if (!EXECUTION_DRAFTS.has(key)) {
    EXECUTION_DRAFTS.set(key, {
      text: "",
      verdict: "accept",
      requestId: window.crypto?.randomUUID?.() || `decision-${Date.now()}-${Math.random()}`,
      error: null,
      busy: false,
    });
  }
  return EXECUTION_DRAFTS.get(key);
}

function saveExecutionDraft(execution, blocker, patch) {
  const key = executionDraftKey(execution, blocker);
  const old = draftFor(execution, blocker);
  const changed = patch.text !== undefined && patch.text !== old.text;
  EXECUTION_DRAFTS.set(key, {
    ...old,
    ...patch,
    requestId: changed
      ? (window.crypto?.randomUUID?.() || `decision-${Date.now()}-${Math.random()}`)
      : old.requestId,
    error: changed ? null : (patch.error === undefined ? old.error : patch.error),
  });
}

function executionField(label, value) {
  const wrap = el("div");
  wrap.className = "execution-field";
  wrap.append(el("dt", label), el("dd", value === null || value === undefined ? "unknown" : String(value)));
  return wrap;
}

function renderExecutionSummary(execution) {
  const section = el("section");
  section.className = "execution-summary";
  section.append(el("h3", "AL execution"));
  const state = el("p", `Execution ${execution.status} · host ${execution.host} · AL events ${execution.cursor}`);
  state.className = "execution-status";
  section.append(state);
  const source = el("p", `AL source: ${execution.source_status || "Unknown"}`);
  source.dataset.sourceStatus = (execution.source_status || "Unknown").toLowerCase();
  section.append(source);
  const identity = document.createElement("dl");
  identity.className = "execution-identities";
  for (const [label, value] of [
    ["Execution", execution.id],
    ["Approval", execution.approval_id],
    ["AL session", execution.al_session_id],
    ["AL run", execution.al_run_id],
    ["Native conversation", execution.native_id],
  ]) identity.append(executionField(label, value).children[0], executionField(label, value).children[1]);
  section.append(identity);
  if (execution.error) section.append(alertText(`Execution issue: ${execution.error}`));
  if (execution.status === "Unavailable" || execution.status === "Interrupted" || execution.status === "RecoveryRequired") {
    section.append(el("p", "Review the recorded host identity and diagnostics before choosing a recovery action."));
  }
  const actions = el("div");
  actions.className = "execution-actions";
  if (execution.status !== "Completed" && execution.status !== "Failed") {
    actions.append(executionActionButton("Reconnect", "reconnect", execution));
    if (execution.native_id) actions.append(executionActionButton("Resume native session", "resume", execution));
    if (execution.status === "RecoveryRequired") actions.append(renderReconcileForm(execution));
  }
  section.append(actions);
  return section;
}

function executionActionButton(label, action, execution) {
  const button = el("button", label);
  button.type = "button";
  button.addEventListener("click", () => sendExecutionAction(action, execution));
  return button;
}

function renderReconcileForm(execution) {
  const form = el("form");
  form.className = "execution-reconcile";
  const select = document.createElement("select");
  select.name = "outcome";
  for (const [value, label] of [["not-started", "Confirmed not started"], ["exited", "Confirmed host exited"]]) {
    const option = el("option", label);
    option.value = value;
    select.append(option);
  }
  const evidence = document.createElement("textarea");
  evidence.name = "evidence";
  evidence.required = true;
  evidence.rows = 2;
  evidence.placeholder = "What did you inspect to establish this outcome?";
  const submit = el("button", "Record reconciliation");
  submit.type = "submit";
  form.append(select, evidence, submit);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await postExecution("reconcile", {
      task: executionTaskId,
      execution: execution.id,
      outcome: select.value,
      evidence: evidence.value,
    });
  });
  return form;
}

function renderProgress(execution) {
  const section = el("section");
  section.className = "execution-progress";
  section.append(el("h4", "Progress"));
  const progress = execution.progress;
  if (!progress) {
    section.append(el("p", "Progress unknown: no measurable task units have been registered."));
  } else {
    section.append(el("p", `${progress.completed} of ${progress.total} ${progress.unit} completed`));
    const bar = document.createElement("progress");
    bar.max = Math.max(1, progress.total);
    bar.value = progress.completed;
    bar.setAttribute("aria-label", "Completed task units");
    section.append(bar);
  }
  if (execution.phase) section.append(el("p", `AL phase: ${execution.phase.phase || execution.phase.name || "unknown"}`));
  return section;
}

function renderAgent(agent) {
  const item = el("article");
  item.className = "execution-agent";
  item.dataset.status = String(agent.status || "Idle").toLowerCase();
  item.append(el("h5", `${agent.name} · ${agent.role}`), el("p", `Status: ${agent.status || "Idle"}`));
  const fields = [
    ["Assigned task", agent.task],
    ["Activity", agent.activity],
    ["Duration", agent.duration === undefined ? null : formatElapsed(agent.duration * 1000)],
    ["Dependencies", (agent.dependencies || []).join(", ") || "none"],
    ["Blockers", (agent.blockers || []).join(", ") || "none"],
    ["Latest output", agent.latest_output],
  ];
  const list = document.createElement("dl");
  for (const [label, value] of fields) {
    const row = executionField(label, value);
    list.append(row.children[0], row.children[1]);
  }
  item.append(list);
  item.append(renderAgentProgress(agent.progress));
  return item;
}

function renderAgentProgress(progress) {
  const node = el("p");
  if (!progress) node.textContent = "Progress: unknown";
  else if (progress.kind === "measured" || ("completed" in progress && "total" in progress)) {
    node.textContent = `Progress: ${progress.completed} of ${progress.total} ${progress.unit}`;
  } else {
    node.textContent = `Estimated progress: ${progress.value}% · ${progress.label} (${progress.source})`;
  }
  return node;
}

function renderAgents(execution) {
  const section = el("section");
  section.className = "execution-agents";
  section.append(el("h4", `Agents (${execution.agents.length})`));
  if (!execution.agents.length) section.append(el("p", "No actual agent invocations have been reported."));
  else {
    const list = el("div");
    list.className = "execution-agent-list";
    for (const agent of execution.agents) list.append(renderAgent(agent));
    section.append(list);
  }
  return section;
}

function renderGraph(execution) {
  const section = el("section");
  section.className = "execution-graph";
  section.append(el("h4", "Task ownership and handoffs"));
  const graph = el("div");
  graph.className = "execution-graph-scroll";
  const byId = new Map(execution.tasks.map((task) => [task.id, task]));
  const edgesByTask = new Map();
  for (const edge of execution.edges || []) {
    const key = edge.source;
    if (!edgesByTask.has(key)) edgesByTask.set(key, []);
    edgesByTask.get(key).push(edge);
  }
  if (!execution.tasks.length) graph.append(el("p", "Task graph unavailable until AL registers tasks."));
  for (const task of execution.tasks) {
    const card = el("article");
    card.className = "execution-task-node";
    card.dataset.status = String(task.status || "Pending").toLowerCase();
    card.append(el("h5", task.title || task.id), el("p", `Status: ${task.status || "Pending"}`));
    card.append(el("p", `Owner files: ${(task.files || []).join(", ") || "none assigned"}`));
    const edges = edgesByTask.get(task.id) || [];
    const relations = new Map();
    const addRelation = (family, source, target) => {
      const relation = [family, source, target].map(String);
      relations.set(JSON.stringify(relation), `${relation[0]}: ${relation[1]} → ${relation[2]}`);
    };
    for (const id of task.dependencies || []) addRelation("dependency", task.id, id);
    for (const edge of edges) addRelation(edge.family || "handoff", edge.source || task.id, edge.target);
    card.append(el("p", `Connections: ${[...relations.values()].join("; ") || "none"}`));
    const assigned = execution.agents.filter((agent) => agent.task === task.id);
    card.append(el("p", `Actual agents: ${assigned.map((agent) => `${agent.name} (${agent.status})`).join(", ") || "none"}`));
    graph.append(card);
  }
  section.append(graph);
  return section;
}

function renderFeed(execution) {
  const section = el("section");
  section.className = "execution-feed";
  section.append(el("h4", "Activity feed"));
  const list = el("ol");
  for (const event of [...(execution.feed || [])].reverse()) {
    const item = el("li");
    const time = el("time");
    const numeric = typeof event.timestamp === "number"
      ? event.timestamp
      : (typeof event.timestamp === "string" && /^-?\d+(?:\.\d+)?$/.test(event.timestamp)
        ? Number(event.timestamp)
        : null);
    const parsed = numeric !== null
      ? new Date(numeric * 1000)
      : (typeof event.timestamp === "string" ? new Date(event.timestamp) : null);
    if (parsed && Number.isFinite(parsed.getTime())) {
      time.textContent = new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium", timeStyle: "medium",
      }).format(parsed);
      time.dateTime = parsed.toISOString();
    } else {
      time.textContent = event.timestamp || "time unknown";
    }
    item.append(time, el("strong", event.kind));
    item.append(el("p", eventSummary(event)));
    list.append(item);
  }
  if (!list.children.length) section.append(el("p", "No semantic activity has been reported."));
  else section.append(list);
  return section;
}

function eventSummary(event) {
  const payload = event.payload || {};
  return payload.name || payload.title || payload.question || payload.activity || payload.status ||
    payload.text || `sequence ${event.sequence}`;
}

function renderLogs(execution) {
  const section = el("section");
  section.className = "execution-logs";
  section.append(el("h4", "Execution diagnostics"));
  if (!execution.logs.length) section.append(el("p", "No diagnostics recorded."));
  for (const log of execution.logs) {
    const details = document.createElement("details");
    const summary = el("summary", `${log.channel} · ${log.created_at}`);
    details.append(summary);
    const load = el("button", "Load stored log");
    load.type = "button";
    const output = el("pre");
    load.addEventListener("click", async () => {
      load.disabled = true;
      const result = await getExecutionLog(execution, log.id);
      output.textContent = result.error || result.text || "Log is empty.";
      load.disabled = false;
    });
    details.append(load, output);
    section.append(details);
  }
  return section;
}

function renderDecision(blocker, execution) {
  const article = el("article");
  article.className = "execution-blocker";
  article.dataset.state = String(blocker.state || "Open").toLowerCase();
  article.append(el("h5", `${blocker.id} · ${blocker.state}`));
  article.append(el("p", `Affected tasks: ${(blocker.affected || blocker.tasks || []).join(", ")}`));
  article.append(el("p", `Question: ${blocker.question}`));
  if (blocker.context) article.append(el("p", `Context: ${blocker.context}`));
  if (blocker.recommendation) article.append(el("p", `Recommendation: ${blocker.recommendation}`));
  if (blocker.alternatives?.length) {
    const alternatives = el("ul");
    for (const alternative of blocker.alternatives) alternatives.append(el("li", alternative));
    article.append(el("p", "Alternatives:"), alternatives);
  }
  const state = blocker.decision_state ? `Decision delivery: ${blocker.decision_state}` : "No developer decision delivered";
  article.append(el("p", state));
  if (blocker.delivery_id) article.append(withCode("delivery: ", blocker.delivery_id));
  const matching = execution.decisions.filter((item) => item.blocker_id === blocker.id);
  for (const decision of matching) {
    const details = el("p", `Request ${decision.id}: ${decision.state}${decision.registry_id ? ` · registry ${decision.registry_id}` : ""}`);
    article.append(details);
    if (decision.error) article.append(alertText(`Decision delivery issue: ${decision.error}`));
  }
  if (blocker.state !== "Resolved" && !["Rejected", "Resolved"].includes(blocker.decision_state)) {
    article.append(renderDecisionForm(blocker, execution));
  }
  return article;
}

function renderDecisionForm(blocker, execution) {
  const draft = draftFor(execution, blocker);
  const form = el("form");
  form.className = "execution-decision-form";
  const verdict = document.createElement("select");
  verdict.id = "execution-decision-verdict";
  verdict.setAttribute("aria-label", "Developer decision");
  for (const [value, label] of [["accept", "Accept recommendation"], ["reject", "Reject recommendation"], ["modify", "Modify with instructions"], ["alternative", "Choose an alternative"]]) {
    const option = el("option", label);
    option.value = value;
    verdict.append(option);
  }
  verdict.value = draft.verdict;
  verdict.addEventListener("change", () => saveExecutionDraft(execution, blocker, { verdict: verdict.value }));
  const text = document.createElement("textarea");
  text.id = "execution-decision-text";
  text.rows = 3;
  text.required = true;
  text.placeholder = "Record your explicit decision and any instructions.";
  text.value = draft.text;
  text.addEventListener("input", () => saveExecutionDraft(execution, blocker, { text: text.value }));
  const submit = el("button", draft.busy ? "Sending decision…" : "Record decision");
  submit.type = "submit";
  submit.disabled = draft.busy;
  form.append(verdict, text, submit);
  if (draft.error) form.append(alertText(draft.error));
  if (draft.requestId) form.append(el("small", `Request ID: ${draft.requestId}`));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await sendDecision(execution, blocker, draftFor(execution, blocker));
  });
  return form;
}

function renderDecisionsExecution(execution) {
  const section = el("section");
  section.className = "execution-decisions";
  section.append(el("h4", "Design blockers and decisions"));
  if (!execution.blockers.length) section.append(el("p", "No task-scoped blockers."));
  for (const blocker of execution.blockers) section.append(renderDecision(blocker, execution));
  return section;
}

function renderExecution(taskId, execution) {
  executionTaskId = taskId;
  executionSnapshot = execution;
  const section = el("section");
  section.id = "execution";
  section.append(
    renderExecutionSummary(execution),
    renderProgress(execution),
    renderAgents(execution),
    renderGraph(execution),
    renderFeed(execution),
    renderDecisionsExecution(execution),
    renderLogs(execution),
  );
  return section;
}

async function postExecution(action, payload) {
  const { res, data, netErr } = await postJson(`/api/execution/${action}`, payload);
  if (res && res.ok) await refreshDashboard();
  else showExecutionError(data.error || netErr || res?.statusText || "No response");
  return { res, data, netErr };
}

function showExecutionError(message) {
  const old = document.querySelector("#execution .execution-error");
  if (old) old.remove();
  const node = alertText(`Execution action failed: ${message}`);
  node.className = "execution-error";
  document.querySelector("#execution .execution-summary")?.append(node);
}

async function sendExecutionAction(action, execution) {
  await postExecution(action, { task: executionTaskId, execution: execution.id });
}

async function sendDecision(execution, blocker, draft) {
  if (draft.busy || !draft.text.trim()) return;
  saveExecutionDraft(execution, blocker, { busy: true, error: null });
  repaintExecution();
  const current = draftFor(execution, blocker);
  const tasks = blocker.affected || blocker.tasks || [];
  const result = await postExecution("decision", {
    task: executionTaskId,
    execution: execution.id,
    blocker: blocker.id,
    revision: blocker.revision,
    request_id: current.requestId,
    verdict: current.verdict,
    text: current.text,
    tasks,
    criteria: [],
    amendment: false,
  });
  if (!result.res?.ok) {
    saveExecutionDraft(execution, blocker, { busy: false, error: result.data.error || result.netErr || "Decision was not delivered." });
    repaintExecution();
  } else {
    EXECUTION_DRAFTS.delete(executionDraftKey(execution, blocker));
  }
}

async function getExecutionLog(execution, logId) {
  const query = new URLSearchParams({ task: executionTaskId, execution: execution.id, log: logId });
  const response = await fetch(`/api/execution/logs?${query}`, { cache: "no-store" });
  const data = await response.json().catch(() => ({}));
  return response.ok ? data : { error: data.error || response.statusText };
}

function repaintExecution() {
  const node = document.getElementById("execution");
  if (node && executionTaskId && executionSnapshot) withExecutionFocus(() => node.replaceWith(renderExecution(executionTaskId, executionSnapshot)));
}

function withExecutionFocus(fn) {
  const active = document.activeElement;
  const id = active && EXECUTION_FOCUS_IDS.has(active.id) ? active.id : null;
  const key = active?.closest?.(".execution-blocker")?.querySelector("textarea")?.value;
  const result = fn();
  if (id) {
    const next = document.getElementById(id);
    if (next) {
      next.focus({ preventScroll: true });
      if (id === "execution-decision-text" && key !== undefined) {
        const start = active.selectionStart;
        const end = active.selectionEnd;
        next.setSelectionRange(start, end);
      }
    }
  }
  return result;
}
