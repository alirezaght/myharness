"use strict";

const EXECUTION_DRAFTS = new Map();
const ACTIVE_EXECUTION_STATUSES = new Set(["Running", "Launching", "WorkerOwned"]);
const EXECUTION_FOCUS_IDS = new Set(["execution-decision-text", "execution-decision-verdict"]);
let executionTaskId = null;
let executionSnapshot = null;
const EXECUTION_ACTIONS = new Map();
const DECISION_RETRIES = new Set();
const DECISION_RETRY_RESULTS = new Map();

function executionIdentity(execution) {
  return viewIdentity(execution.task_id || executionTaskId, execution);
}

function currentExecution(execution) {
  return document.getElementById("execution")?.dataset.viewIdentity === executionIdentity(execution);
}

function executionActionKey(execution, action) {
  return JSON.stringify([executionIdentity(execution), action]);
}

function executionEpochPayload(execution) {
  return execution.epoch_id === undefined ? {} : {
    epoch_id: execution.epoch_id,
    authority_generation: execution.authority_generation,
  };
}

function developerDecisions(execution) {
  if (!Array.isArray(execution.developer_decisions)) return execution.blockers || [];
  return execution.developer_decisions.map((item) => typeof item === "string"
    ? (execution.blockers || []).find((blocker) => blocker.id === item) : item).filter(Boolean);
}

function implementationLabel(execution) {
  return {
    ready: "Implementation ready",
    continuing: "Implementation continuing",
    developer_decision: "Developer decision needed",
    technically_unavailable: "Tool repair in progress",
  }[execution.implementation?.status] || null;
}

function executionDraftKey(execution, blocker) {
  return JSON.stringify([executionIdentity(execution), blocker.id, blocker.revision]);
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
  section.append(renderCurrentWork(execution));
  const identityDetails = document.createElement("details");
  identityDetails.className = "execution-identities-disclosure";
  identityDetails.dataset.viewDisclosure = "execution-identifiers";
  identityDetails.append(el("summary", "Execution identifiers"));
  const identity = document.createElement("dl");
  identity.className = "execution-identities";
  for (const [label, value] of [
    ["Execution", execution.id],
    ["Runtime epoch", execution.epoch_id],
    ["Authority generation", execution.authority_generation],
    ["Approval", execution.approval_id],
    ["AL session", execution.al_session_id],
    ["AL run", execution.al_run_id],
    ["Native conversation", execution.native_id],
  ]) identity.append(executionField(label, value).children[0], executionField(label, value).children[1]);
  identityDetails.append(identity);
  section.append(identityDetails);
  if (execution.error) section.append(alertText(`Execution issue: ${execution.error}`));
  if (execution.status_explanation) section.append(el("p", execution.status_explanation));
  if (execution.authored_work?.kind === "authored_work_recorded_verification_pending") {
    const notice = el("p", execution.authored_work.message);
    notice.className = "authored-work-notice";
    notice.setAttribute("role", "status");
    section.append(notice);
  }
  if (execution.status === "Unavailable" || execution.status === "Interrupted" || execution.status === "RecoveryRequired") {
    section.append(el("p", "Review the recorded host identity and diagnostics before choosing a recovery action."));
  }
  const actions = el("div");
  actions.className = "execution-actions";
  if (execution.status !== "Completed" && execution.status !== "Failed") {
    actions.append(executionActionButton("Refresh recorded status", "reconnect", execution,
      "Refreshes the saved run state and never continues the native agent."));
    if (execution.native_id) actions.append(executionActionButton("Continue agent in existing session", "resume", execution,
      "Explicitly continues the recorded native conversation, subject to current recovery checks."));
    if (execution.status === "RecoveryRequired") actions.append(renderReconcileForm(execution));
  }
  const messages = el("div");
  messages.className = "execution-action-results";
  messages.setAttribute("aria-live", "polite");
  for (const action of ["reconnect", "resume"]) {
    const state = EXECUTION_ACTIONS.get(executionActionKey(execution, action));
    if (state) {
      const result = el("p", state.message);
      result.setAttribute("role", state.error ? "alert" : "status");
      messages.append(result);
    }
  }
  section.append(actions, messages);
  return section;
}

function executionActionButton(label, action, execution, explanation) {
  const wrap = el("div");
  wrap.className = "execution-action";
  const button = el("button", label);
  button.type = "button";
  const readiness = execution.readiness || {};
  const available = readiness.actions?.[action];
  const key = executionActionKey(execution, action);
  const activeResume = action === "resume" && ACTIVE_EXECUTION_STATUSES.has(execution.status);
  button.disabled = EXECUTION_ACTIONS.get(key)?.busy === true || available === false || activeResume;
  button.setAttribute("aria-describedby", `execution-action-help-${action}`);
  button.addEventListener("click", () => sendExecutionAction(action, execution));
  const unavailableReason = activeResume
    ? "The execution is already active. Use Refresh recorded status to observe its progress."
    : readiness.action_reasons?.[action]?.message || readiness.message;
  const hint = el("small", activeResume || available === false
    ? `${unavailableReason} ${explanation}` : explanation);
  hint.id = `execution-action-help-${action}`;
  wrap.append(button, hint);
  return wrap;
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
    ...executionEpochPayload(execution),
      outcome: select.value,
      evidence: evidence.value,
    });
  });
  return form;
}

function renderVerificationState(execution) {
  const section = el("section");
  section.className = "execution-verification";
  const label = implementationLabel(execution);
  if (label) section.append(el("h4", label));
  const verification = execution.verification;
  if (verification) {
    const labels = { incomplete: "Verification incomplete", running: "Checks running", complete: "Required verification complete" };
    section.append(el("p", labels[verification.status] || "Verification status unknown"));
    if (verification.status !== "complete") section.append(el("p",
      "Implementation can continue within its authorized scope. Final approval still requires the remaining checks."));
  }
  const issues = Object.values(execution.operational_issues || {}).filter((issue) => issue.state !== "Resolved");
  const obligations = verification?.obligation_refs || [];
  if (issues.length || obligations.length) {
    const details = document.createElement("details");
    details.dataset.viewDisclosure = "verification-obligations";
    details.append(el("summary", `Tooling and pending checks (${issues.length + obligations.length})`));
    const list = el("ul");
    for (const issue of issues) list.append(el("li", `${issue.capability_or_check_ref || issue.id}: ${issue.state}. ${issue.next_action}`));
    for (const obligation of obligations) list.append(el("li", typeof obligation === "string"
      ? obligation : obligation.message || obligation.id || "Unresolved verification obligation"));
    details.append(list);
    section.append(details);
  }
  const epochs = execution.available_history_epochs || [];
  if (epochs.length) {
    const history = document.createElement("details");
    history.dataset.viewDisclosure = "runtime-history";
    history.append(el("summary", `Previous runtime history (${epochs.length})`));
    const list = el("ul");
    for (const epoch of epochs) list.append(el("li", typeof epoch === "string" ? epoch
      : `${epoch.epoch_id || epoch.id} · ${epoch.status || "Archived"}${epoch.al_session_id ? ` · ${epoch.al_session_id}` : ""}`));
    history.append(el("p", "Previous decisions and failures remain recorded. They do not grant authority to the current runtime."), list);
    section.append(history);
  }
  return section;
}

function renderProgress(execution) {
  const section = el("section");
  section.className = "execution-progress";
  section.append(el("h4", "Verified completion"));
  const progress = execution.progress;
  if (!progress) {
    section.append(el("p", "No measurable task breakdown has been reported."));
  } else {
    section.append(el("p", `${progress.completed} of ${progress.total} ${progress.unit} completed`));
    section.append(el("p", "This counts verified tasks, not the amount of work performed. A percentage estimate has not been inferred."));
    const bar = document.createElement("progress");
    bar.max = Math.max(1, progress.total);
    bar.value = progress.completed;
    bar.setAttribute("aria-label", "Completed task units");
    section.append(bar);
    const authored = (execution.tasks || []).filter((task) => task.authored_output && task.status !== "Completed").length;
    if (authored) section.append(el("p", `Authored output is available for ${authored} task(s). Verified completion is tracked separately.`));
  }
  if (execution.phase) section.append(el("p", `AL phase: ${execution.phase.phase || execution.phase.name || "unknown"}`));
  return section;
}

function activityTime(value) {
  const at = new Date(value);
  if (!value || !Number.isFinite(at.getTime())) return "time not reported";
  const age = Math.max(0, Math.floor((Date.now() - at.getTime()) / 60000));
  const relative = age < 1 ? "less than a minute ago" : age < 60 ? `${age} min ago`
    : age < 1440 ? `${Math.floor(age / 60)} hours ago` : `${Math.floor(age / 1440)} days ago`;
  return `${relative} · ${age >= 1440 ? at.toLocaleString() : at.toLocaleTimeString()}`;
}

function renderCurrentWork(execution) {
  const section = el("section");
  section.className = "execution-current-work";
  section.setAttribute("aria-label", "What is happening");
  section.append(el("h4", "What is happening"));
  const active = (execution.agents || []).filter((agent) => agent.status === "Running");
  const now = el("div");
  now.className = "execution-work-now";
  now.append(el("h5", "Working now"));
  if (!active.length) now.append(el("p", ACTIVE_EXECUTION_STATUSES.has(execution.status)
    ? "The supervisor is active; no running agent has been reported."
    : "No agent is currently reported as running."));
  for (const agent of active) {
    now.append(el("p", agent.activity || "The agent has not described its current work."));
    now.append(el("small", `${agent.name} · ${agent.role}`));
  }
  const activity = execution.activity || {};
  const latest = el("div");
  latest.append(el("h5", "Latest recorded activity"));
  const actions = activity.recent_actions || [];
  for (const action of actions.slice(0, 3)) latest.append(el("p",
    `${action.action} · ${action.agent_name} · ${activityTime(action.at)}${action.outcome ? ` · ${action.outcome}` : ""}`));
  if (!actions.length) latest.append(el("p", activity.last_activity_at
    ? `Last AL work update: ${activityTime(activity.last_activity_at)}`
    : "No work activity has been recorded yet."));
  const last = new Date(activity.last_activity_at);
  if (activity.last_activity_at && Date.now() - last.getTime() > 120000) {
    latest.append(el("p", "No newer work update has been recorded. Progress is unconfirmed until the next agent update."));
  }
  const results = el("div");
  results.append(el("h5", "Recorded results"));
  results.append(el("p", `${activity.finished_agent_runs || 0} agent runs finished · ${activity.report_count ?? execution.reports?.length ?? 0} reports recorded`));
  results.append(el("small", "Agent completion and reports do not mean the feature is verified complete."));
  if (execution.reports?.length) {
    const outputs = document.createElement("details");
    outputs.dataset.viewDisclosure = "current-output-reports";
    outputs.append(el("summary", `View recorded reports (${execution.reports.length})`));
    const list = el("ul");
    for (const report of execution.reports.slice(-5)) list.append(el("li", `${report.name} · ${report.role}`));
    outputs.append(list);
    results.append(outputs);
  }
  for (const check of (activity.checks || []).slice(-3)) results.append(el("p",
    `${check.area || check.id}: ${check.status === "RUNNING" ? "Reported running" : check.status || "Status not reported"} · ${activityTime(check.at)}`));
  section.append(now, latest, results);
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
    ...(agent.latest_output ? [["Latest output", agent.latest_output]] : []),
  ];
  const list = document.createElement("dl");
  for (const [label, value] of fields) {
    const row = executionField(label, value);
    list.append(row.children[0], row.children[1]);
  }
  item.append(list);
  if (agent.progress) item.append(renderAgentProgress(agent.progress));
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
    if (task.authored_output && task.status !== "Completed") card.append(el("p", "Authored output available · verification pending"));
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
  if (event.kind === "writer.updated") return `${payload.role || "Writer"}: ${payload.state || "updated"} · ${(payload.files || []).length} assigned paths${payload.handoffs?.length ? ` · ${payload.handoffs.length} handoffs recorded` : ""}`;
  if (event.kind === "phase.changed") return `Stage: ${payload.phase || "not reported"}`;
  if (event.kind === "test.started" || event.kind === "test.result") return `${payload.area || payload.id || "Check"}: ${payload.status || "updated"}${payload.counts?.passed !== undefined ? ` · ${payload.counts.passed} passed` : ""}`;
  if (event.kind === "issue.updated") return `${payload.capability_or_check_ref || payload.id}: ${payload.state || "updated"}`;
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
    details.dataset.viewDisclosure = JSON.stringify(["log", log.id]);
    const summary = el("summary", `${log.channel} · ${log.created_at}`);
    details.append(summary);
    const load = el("button", "Load stored log");
    load.type = "button";
    const output = el("pre");
    output.dataset.viewLog = JSON.stringify(["log", log.id]);
    load.addEventListener("click", async () => {
      const identity = executionIdentity(execution);
      load.disabled = true;
      const result = await getExecutionLog(execution, log.id);
      const current = document.getElementById("execution");
      if (current?.dataset.viewIdentity !== identity) return;
      const next = [...current.querySelectorAll("[data-view-log]")]
        .find((node) => node.dataset.viewLog === output.dataset.viewLog);
      if (next) {
        next.textContent = result.error || result.text || "Log is empty.";
        next.previousElementSibling.disabled = false;
      }
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
    const statusText = {
      failed: "Saved · delivery failed",
      pending: "Saved · delivery pending",
      awaiting_acknowledgment: "Delivered · awaiting AL acknowledgment",
      acknowledged: "Acknowledged by AL",
      not_delivered: "Retired invalid request · not delivered to AL",
    }[decision.delivery_status] || `Saved · ${decision.state}`;
    const details = document.createElement("details");
    details.className = "saved-decision-details";
    details.dataset.viewDisclosure = JSON.stringify(["decision", blocker.id, decision.id]);
    details.append(el("summary", statusText));
    details.append(withCode("Request ID: ", decision.id));
    if (decision.registry_id) details.append(withCode("Registry record: ", decision.registry_id));
    if (decision.outcome_notice) details.append(el("p", decision.outcome_notice));
    article.append(details);
    const historical = decision.delivery_status === "not_delivered";
    if (decision.error) {
      if (historical) details.append(el("p", `Historical delivery issue: ${decision.error}`));
      else article.append(alertText(`Decision delivery issue: ${decision.error}`));
    }
    if (!historical && decision.retryable && decision.delivery_status === "failed") {
      const retry = el("button", "Retry saved decision delivery");
      retry.type = "button";
      retry.disabled = DECISION_RETRIES.has(executionActionKey(execution, decision.id));
      retry.addEventListener("click", () => retrySavedDecision(execution, decision));
      article.append(retry);
    }
    const retryResult = DECISION_RETRY_RESULTS.get(executionActionKey(execution, decision.id));
    if (retryResult && historical) details.append(el("p", retryResult.message));
    else if (retryResult) {
      const result = el("p", retryResult.message);
      result.setAttribute("role", retryResult.error ? "alert" : "status");
      article.append(result);
    }
  }
  const savedOpenRequest = matching.some((decision) => !["Rejected", "Resolved"].includes(decision.state));
  const actionable = developerDecisions(execution).some((item) => item.id === blocker.id);
  if (actionable && !savedOpenRequest && blocker.state !== "Resolved" && !["Rejected", "Resolved"].includes(blocker.decision_state)) {
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
  section.append(el("h4", "Developer decisions"));
  if (!developerDecisions(execution).length) section.append(el("p", "No developer decision is needed."));
  for (const blocker of execution.blockers || []) section.append(renderDecision(blocker, execution));
  return section;
}

function renderExecution(taskId, execution) {
  executionTaskId = taskId;
  executionSnapshot = execution;
  const section = el("section");
  section.id = "execution";
  section.dataset.viewIdentity = viewIdentity(taskId, execution);
  section.append(
    renderExecutionSummary(execution),
    renderVerificationState(execution),
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
  const identity = executionSnapshot ? executionIdentity(executionSnapshot) : null;
  const { res, data, netErr } = await postJson(`/api/execution/${action}`, payload);
  if (identity && document.getElementById("execution")?.dataset.viewIdentity !== identity) return { res, data, netErr };
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
  if (action === "resume" && ACTIVE_EXECUTION_STATUSES.has(execution.status)) return;
  const key = executionActionKey(execution, action);
  if (EXECUTION_ACTIONS.get(key)?.busy) return;
  EXECUTION_ACTIONS.set(key, {
    busy: true,
    message: action === "reconnect" ? "Refreshing recorded status…" : "Continuing the existing native session…",
  });
  repaintExecution();
  const { res, data, netErr } = await postJson(`/api/execution/${action}`, {
    task: executionTaskId,
    execution: execution.id,
    ...executionEpochPayload(execution),
  });
  if (!currentExecution(execution)) { EXECUTION_ACTIONS.delete(key); return; }
  if (res?.ok) {
    EXECUTION_ACTIONS.set(key, {
      busy: false,
      message: action === "reconnect"
        ? "Recorded status refreshed. No native agent was continued."
        : "The continuation request was accepted for the existing native session. Check recorded status for its result.",
      error: false,
    });
    await refreshDashboard();
  } else {
    EXECUTION_ACTIONS.set(key, {
      busy: false,
      message: `Action failed: ${data.error || netErr || res?.statusText || "No response"}`,
      error: true,
    });
    repaintExecution();
  }
}

async function retrySavedDecision(execution, decision) {
  if (DECISION_RETRIES.has(executionActionKey(execution, decision.id))) return;
  const key = executionActionKey(execution, decision.id);
  DECISION_RETRIES.add(key);
  DECISION_RETRY_RESULTS.set(key, { message: "Retrying the saved decision request…", error: false });
  repaintExecution();
  const { res, data, netErr } = await postJson("/api/execution/decision-retry", {
    task: executionTaskId,
    execution: execution.id,
    ...executionEpochPayload(execution),
    delivery: decision.id,
  });
  DECISION_RETRIES.delete(key);
  if (!currentExecution(execution)) return;
  DECISION_RETRY_RESULTS.set(key, res?.ok
    ? { message: "The saved request was retried with its original text and identity.", error: false }
    : { message: `Retry failed: ${data.error || netErr || res?.statusText || "No response"}`, error: true });
  if (res?.ok) await refreshDashboard();
  else repaintExecution();
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
    ...executionEpochPayload(execution),
    blocker: blocker.id,
    revision: blocker.revision,
    request_id: current.requestId,
    verdict: current.verdict,
    text: current.text,
    tasks,
    criteria: [],
    amendment: false,
  });
  if (!currentExecution(execution)) return;
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
  if (!node || !executionTaskId || !executionSnapshot) return;
  const saved = captureView(node);
  const next = renderExecution(executionTaskId, executionSnapshot);
  const same = restoreView(next, saved);
  withExecutionFocus(() => node.replaceWith(next));
  if (same) window.scrollTo(saved.x, saved.y);
}

function withExecutionFocus(fn) {
  const active = document.activeElement;
  const identity = document.getElementById("execution")?.dataset.viewIdentity;
  const id = active && EXECUTION_FOCUS_IDS.has(active.id) ? active.id : null;
  const key = active?.closest?.(".execution-blocker")?.querySelector("textarea")?.value;
  const result = fn();
  if (identity !== document.getElementById("execution")?.dataset.viewIdentity) return result;
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
