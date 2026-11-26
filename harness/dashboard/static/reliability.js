"use strict";

const INCIDENT_VIEWS = new Map();

// Live repaints re-render the reliability panels on every dashboard change. A panel fetches
// its scoped list again only when the execution's reliability marker (computed from the
// reliability, incident and policy tables) or an explicit dashboard refresh changes, or when
// its last load failed; filters, paging and "Latest" buttons still load directly (#110).
let reliabilityRefreshes = 0;

function noteReliabilityRefresh() {
  reliabilityRefreshes += 1;
}

function reliabilityFetchDue(view, slot, execution, failed = false) {
  const key = `${execution.reliability_revision ?? "unknown"}|${reliabilityRefreshes}`;
  view.fetchedKeys ||= {};
  if (!failed && view.fetchedKeys[slot] === key) return false;
  view.fetchedKeys[slot] = key;
  return true;
}
const INCIDENT_HISTORY_LIMIT = 100;

function incidentView(execution) {
  const identity = viewIdentity(execution.task_id || executionTaskId, execution);
  if (!INCIDENT_VIEWS.has(identity)) {
    INCIDENT_VIEWS.set(identity, {
      identity, query: "", category: "", cursor: null, items: [], pending: [],
      pendingTruncated: false, coverage: null, detailId: null, detail: null,
      error: null, listRequest: 0, loading: false, detailRequest: 0,
      detailLoading: false, detailError: null, detailOpen: true, newerOutsideWindow: false, windowAdvanced: false,
    });
  }
  return INCIDENT_VIEWS.get(identity);
}

function incidentElement(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function incidentField(label, value) {
  const row = incidentElement("div");
  row.className = "incident-field";
  row.append(incidentElement("dt", label), incidentElement("dd", value ?? "unknown"));
  return row;
}

function incidentQuery(execution, view, cursor = null) {
  const params = new URLSearchParams({
    task: execution.task_id || executionTaskId,
    execution: execution.id,
    epoch: execution.epoch_id,
    limit: "25",
  });
  if (view.query) params.set("q", view.query);
  if (view.category) params.set("category", view.category);
  if (cursor) {
    params.set("before_time", cursor.before_time);
    params.set("before_id", cursor.before_id);
  }
  return params;
}

function incidentDetailQuery(execution, incidentId) {
  return new URLSearchParams({
    task: execution.task_id || executionTaskId,
    execution: execution.id,
    epoch: execution.epoch_id,
    incident: incidentId,
  });
}

function currentIncidentSection(execution, view) {
  const section = document.querySelector("#operation-incidents");
  return view.identity === viewIdentity(execution.task_id || executionTaskId, execution)
    && document.querySelector("#execution")?.dataset.viewIdentity === view.identity
    && section?.dataset.viewIdentity === view.identity ? section : null;
}

function cancelIncidentDetail(view) {
  view.detailRequest += 1;
  view.detailId = null;
  view.detail = null;
  view.detailLoading = false;
  view.detailError = null;
}

function rememberIncidentDisclosure(view, section) {
  const details = section?.querySelector("details[data-incident-id]");
  if (details?.dataset.incidentId === view.detailId) view.detailOpen = details.open;
}

async function loadIncidentList(execution, view, section, { older = false, reset = false } = {}) {
  // A parent repaint can reuse an in-flight page; filters explicitly supersede it.
  if (view.loading && !reset) return;
  const request = ++view.listRequest;
  if (reset) {
    view.items = [];
    view.cursor = null;
    view.newerOutsideWindow = false;
    view.windowAdvanced = false;
    cancelIncidentDetail(view);
  }
  view.loading = true;
  view.error = null;
  renderIncidentContent(execution, view, section);
  try {
    const params = incidentQuery(execution, view, older ? view.cursor : null);
    const response = await fetch(`/api/execution/incidents?${params}`, { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (!currentIncidentSection(execution, view) || request !== view.listRequest) return;
    if (!response.ok) throw new Error("Incident history is unavailable.");
    const incoming = data.items || [];
    const previous = new Map(view.items.map((item) => [item.incident_id, item]));
    const updated = new Map(incoming.map((item) => [item.incident_id, item]));
    const added = incoming.filter((item) => !previous.has(item.incident_id));
    const retained = view.items.map((item) => updated.get(item.incident_id) || item);
    if (older || previous.size === 0) {
      const combined = retained.concat(added);
      if (combined.length > INCIDENT_HISTORY_LIMIT) view.windowAdvanced = true;
      view.items = combined.slice(-INCIDENT_HISTORY_LIMIT);
      view.cursor = data.next_cursor || null;
      if (view.detailId && !view.items.some((item) => item.incident_id === view.detailId)) {
        cancelIncidentDetail(view);
      }
    } else {
      // A latest page cannot bridge an arbitrary arrival burst to the saved cursor.
      view.items = retained;
      if (added.length) view.newerOutsideWindow = true;
    }
    view.pending = data.pending || [];
    view.pendingTruncated = Boolean(data.pending_truncated);
    view.coverage = data.coverage || null;
    view.error = null;
  } catch (_error) {
    if (!currentIncidentSection(execution, view) || request !== view.listRequest) return;
    view.error = "Incident history is unavailable for this execution scope.";
  } finally {
    if (request === view.listRequest) {
      view.loading = false;
      const current = currentIncidentSection(execution, view);
      if (current) renderIncidentContent(execution, view, current);
    }
  }
}

async function loadIncidentDetail(execution, view, section, incidentId) {
  const request = ++view.detailRequest;
  if (view.detailId !== incidentId) view.detailOpen = true;
  view.detailId = incidentId;
  view.detail = null;
  view.detailLoading = true;
  view.detailError = null;
  renderIncidentContent(execution, view, section);
  try {
    const params = incidentDetailQuery(execution, incidentId);
    const response = await fetch(`/api/execution/incidents/detail?${params}`, { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (!currentIncidentSection(execution, view) || request !== view.detailRequest) return;
    if (!response.ok || data.item?.incident_id !== incidentId) throw new Error("detail unavailable");
    view.detail = data.item;
  } catch (_error) {
    if (!currentIncidentSection(execution, view) || request !== view.detailRequest) return;
    view.detailError = "Incident details are unavailable for this execution scope.";
  } finally {
    if (request === view.detailRequest) {
      view.detailLoading = false;
      const current = currentIncidentSection(execution, view);
      if (current) renderIncidentContent(execution, view, current);
    }
  }
}

function renderIncidentDetail(item, view) {
  const details = document.createElement("details");
  details.open = view.detailOpen;
  details.dataset.viewDisclosure = `incident:${item.incident_id}`;
  details.dataset.incidentId = item.incident_id;
  const summary = incidentElement("summary", "Scoped evidence details");
  summary.id = `incident-summary-${item.incident_id}`;
  details.append(summary);
  details.addEventListener("toggle", () => {
    if (details.isConnected && view.detailId === item.incident_id) view.detailOpen = details.open;
  });
  const fields = document.createElement("dl");
  for (const [label, value] of [
    ["Incident", item.incident_id], ["Observation", item.observation_id],
    ["Source", item.source_id], ["Execution", item.execution_id], ["Task", item.task_id],
    ["Epoch", item.epoch_id], ["Authority generation", item.authority_generation],
    ["Principal", item.principal_id], ["Role", item.role], ["Tool", item.tool],
    ["Policy digest", item.policy_digest], ["Specification version", item.spec_version],
    ["Approval", item.approval_id], ["Started", item.started_at], ["Finished", item.finished_at],
    ["State", item.state], ["Code", item.code], ["Provenance", item.provenance],
    ["Root cause", item.root_cause],
  ]) fields.append(incidentField(label, value));
  details.append(fields);
  return details;
}

function renderIncidentContent(execution, view, section) {
  rememberIncidentDisclosure(view, section);
  const content = document.createElement("div");
  content.className = "incident-content";
  if (view.error) {
    const error = incidentElement("p", view.error);
    error.setAttribute("role", "alert");
    content.append(error);
  }
  if (view.loading && !view.items.length) {
    const status = incidentElement("p", "Loading recorded outcomes…");
    status.setAttribute("role", "status");
    content.append(status);
  }
  if (view.coverage) {
    const coverage = incidentElement("p", "Observed operation outcomes; no root-cause assessment or approval authority.");
    coverage.className = "incident-authority-note";
    content.append(coverage);
    content.append(incidentElement("p", "Coverage: operation dispatch only. Early transport, epoch rejection, assignment wait, historical backfill, and asynchronous completion are not observed here."));
  }
  if (view.detailError) {
    const error = incidentElement("p", view.detailError);
    error.setAttribute("role", "alert");
    content.append(error);
  }
  if (view.detail) content.append(renderIncidentDetail(view.detail, view));
  else if (view.detailLoading) content.append(incidentElement("p", "Loading incident details…"));

  const results = document.createElement("div");
  results.className = "incident-results";
  results.setAttribute("aria-live", "polite");
  if (!view.items.length && !view.loading && !view.error) results.append(incidentElement("p", "No recorded incidents in this scope."));
  for (const item of view.items) {
    const article = incidentElement("article");
    article.className = "incident-item";
    const heading = incidentElement("h5", item.category === "operation_denied" ? "Operation denied" : "Operation failed");
    article.append(heading);
    article.append(incidentElement("p", item.symptom));
    article.append(incidentElement("p", `${item.created_at} · ${item.tool || "unknown tool"}`));
    const button = incidentElement("button", view.detailId === item.incident_id ? "Hide evidence" : "View evidence");
    button.type = "button";
    button.id = `incident-detail-${item.incident_id}`;
    button.setAttribute("aria-expanded", String(view.detailId === item.incident_id));
    button.addEventListener("click", () => {
      if (view.detailId === item.incident_id) {
        cancelIncidentDetail(view);
        renderIncidentContent(execution, view, section);
      } else loadIncidentDetail(execution, view, section, item.incident_id);
    });
    article.append(button);
    results.append(article);
  }
  if (view.cursor) {
    const older = incidentElement("button", view.loading ? "Loading older…" : "Older incidents");
    older.type = "button";
    older.id = "incident-older";
    older.disabled = view.loading;
    older.addEventListener("click", () => loadIncidentList(execution, view, section, { older: true }));
    results.append(older);
  }
  if (view.newerOutsideWindow || view.windowAdvanced) {
    results.append(incidentElement("p", view.newerOutsideWindow
      ? "Newer incidents are available. The loaded history window is retained."
      : "Older navigation moves a window of up to 100 incidents through recorded history."));
    const latest = incidentElement("button", "Latest incidents");
    latest.type = "button";
    latest.id = "incident-latest";
    latest.addEventListener("click", () => loadIncidentList(execution, view, section, { reset: true }));
    results.append(latest);
  }
  content.append(results);

  const pending = document.createElement("section");
  pending.className = "incident-pending";
  pending.append(incidentElement("h4", "Outcome not recorded"));
  if (!view.pending.length) pending.append(incidentElement("p", "No pending outcomes."));
  const pendingList = document.createElement("ul");
  for (const item of view.pending) {
    pendingList.append(incidentElement("li", `${item.started_at} · ${item.tool || "unknown tool"} · Outcome not recorded`));
  }
  if (view.pending.length) pending.append(pendingList);
  if (view.pendingTruncated) pending.append(incidentElement("p", "Additional pending outcomes are not shown."));
  content.append(pending);
  const previous = section.querySelector(".incident-content");
  const active = document.activeElement;
  const anchor = previous?.contains(active) && active.id ? active.getBoundingClientRect().top : null;
  withExecutionFocus(() => {
    if (previous) previous.replaceWith(content);
    else section.append(content);
  });
  if (anchor !== null && document.activeElement.id === active.id) {
    window.scrollBy(0, document.activeElement.getBoundingClientRect().top - anchor);
  }
}

function renderOperationIncidents(execution) {
  const section = incidentElement("section");
  section.className = "operation-incidents";
  section.id = "operation-incidents";
  const identity = viewIdentity(execution.task_id || executionTaskId, execution);
  section.dataset.viewIdentity = identity;
  section.append(incidentElement("h3", "Operation incidents"));
  const view = incidentView(execution);
  rememberIncidentDisclosure(view, currentIncidentSection(execution, view));
  if (!execution.epoch_id) {
    section.append(incidentElement("p", "Incident history requires an explicit runtime epoch."));
    section.append(renderIncidentIntelligence(execution));
    return section;
  }

  const form = document.createElement("form");
  form.className = "incident-filters";
  const searchLabel = incidentElement("label", "Search incidents");
  searchLabel.htmlFor = "incident-search";
  const search = incidentElement("input");
  search.type = "search";
  search.id = "incident-search";
  search.maxLength = 128;
  search.value = view.query;
  search.autocomplete = "off";
  search.addEventListener("input", () => {
    view.query = search.value;
    loadIncidentList(execution, view, section, { reset: true });
  });
  searchLabel.append(search);
  const categoryLabel = incidentElement("label", "Category");
  categoryLabel.htmlFor = "incident-category";
  const category = incidentElement("select");
  category.id = "incident-category";
  for (const [value, label] of [["", "All categories"], ["operation_denied", "Operation denied"], ["operation_failed", "Operation failed"]]) {
    const option = incidentElement("option", label);
    option.value = value;
    category.append(option);
  }
  category.value = view.category;
  category.addEventListener("change", () => {
    view.category = category.value;
    loadIncidentList(execution, view, section, { reset: true });
  });
  categoryLabel.append(category);
  form.append(searchLabel, categoryLabel);
  form.addEventListener("submit", (event) => event.preventDefault());
  section.append(form);
  renderIncidentContent(execution, view, section);
  if (reliabilityFetchDue(view, "list", execution, Boolean(view.error))) {
    loadIncidentList(execution, view, section);
  }
  if (view.detailId && !view.detail && !view.detailLoading && !view.detailError) {
    loadIncidentDetail(execution, view, section, view.detailId);
  }
  section.append(renderIncidentIntelligence(execution));
  return section;
}

// R5a Incident Intelligence is an additive operator view. The older operation-only
// projection above remains intact and keeps its own request/window state.
const INCIDENT_INTELLIGENCE_VIEWS = new Map();
const INCIDENT_INTELLIGENCE_LIMIT = 100;

function iiScopeKey(execution, mode) {
  return JSON.stringify([
    execution.task_id || executionTaskId,
    mode,
    mode === "execution" ? execution.id : null,
    mode === "execution" ? execution.epoch_id : null,
  ]);
}

function iiView(execution, mode) {
  const key = iiScopeKey(execution, mode);
  if (!INCIDENT_INTELLIGENCE_VIEWS.has(key)) {
    INCIDENT_INTELLIGENCE_VIEWS.set(key, {
      key, mode, query: "", category: "", sourceKind: "", items: [], nextCursor: null,
      listRequest: 0, loading: false, error: null, windowAdvanced: false,
      selectedId: null, detail: null, detailRequest: 0, detailLoading: false,
      detailError: null, history: [], historyCursor: null, historyRequest: 0,
      historyLoading: false, groups: [], groupsCursor: null, groupListRequest: 0,
      groupsLoading: false, groupsError: null, selectedGroupId: null, selectedGroup: null,
      groupHistory: [], groupHistoryCursor: null, groupRequest: 0,
      projection: null, projectionRequest: 0, outputs: [], outputsCursor: null,
      outputsRequest: 0, outputsLoaded: false, selectedOutputId: null, outputError: null, actionError: null,
      actionNotice: null, editor: { cause: "unknown", confidence: "unassessed", supporting: "",
        contradicting: "", explanation: "", status: "open", rationale: "evidence_updated",
        evidence: "", component: "", componentVersion: "", componentSnapshot: "",
        componentVerification: "unverified", controlKind: "dispatch", controlState: "unknown",
        controlEvidence: "", action: "investigate", correctionField: "hypotheses",
        correctionValue: "unknown", correctionRationale: "interpretation_corrected",
        correctionEvidence: "", split: "", targetGroup: "", groupRationale: "unrelated_symptoms",
        groupEvidence: "", verdictRationale: "evidence_updated", verdictExplanation: "",
        verdictEvidence: "", requestId: null, projectRequestId: null,
        groupRequestId: null, verdictRequestId: null, includeControl: false,
        includeAction: false, includeHypothesis: false, supportRefs: [], contradictRefs: [], rationaleRefs: [],
        controlRefs: [], correctionRefs: [], groupRefs: [], verdictRefs: [],
        splitLeftRefs: [], splitRightRefs: [] },
      listOwner: null, detailOwner: null, historyOwner: null,
    });
  }
  return INCIDENT_INTELLIGENCE_VIEWS.get(key);
}

function iiScope(execution, view) {
  return view.mode === "task"
    ? { task: execution.task_id || executionTaskId }
    : { task: execution.task_id || executionTaskId, execution: execution.id, epoch: execution.epoch_id };
}

function iiIsCurrent(execution, view, section) {
  return Boolean(section?.isConnected && section.dataset.intelligenceKey === view.key
    && view.key === iiScopeKey(execution, view.mode)
    && document.querySelector("#execution")?.dataset.viewIdentity
      === viewIdentity(execution.task_id || executionTaskId, execution));
}

function iiUrl(execution, view, endpoint, extra = {}) {
  const params = new URLSearchParams(iiScope(execution, view));
  for (const [key, value] of Object.entries(extra)) {
    if (value !== null && value !== undefined && value !== "") params.set(key, String(value));
  }
  return `/api/reliability/${endpoint}?${params}`;
}

function iiRequestId() {
  return window.crypto?.randomUUID?.() || `incident-${Date.now()}-${Math.random()}`;
}

function iiEl(tag, text) {
  return incidentElement(tag, text);
}

function iiLabel(label, control) {
  const node = iiEl("label", label);
  node.append(control);
  return node;
}

function iiInput(id, value, type = "text") {
  const node = iiEl("input");
  node.id = id;
  node.type = type;
  node.value = value ?? "";
  return node;
}

function iiSelect(id, value, entries) {
  const node = iiEl("select");
  node.id = id;
  for (const [optionValue, label] of entries) {
    const option = iiEl("option", label);
    option.value = optionValue;
    node.append(option);
  }
  node.value = value;
  return node;
}

function iiButton(label, id, onClick, className = "") {
  const button = iiEl("button", label);
  button.type = "button";
  button.id = id;
  if (className) button.className = className;
  button.addEventListener("click", onClick);
  return button;
}

function iiPreserveFocus(container, update) {
  const active = document.activeElement;
  const id = container?.contains(active) ? active.id : "";
  const selection = id && typeof active.selectionStart === "number"
    ? [active.selectionStart, active.selectionEnd] : null;
  const top = id ? active.getBoundingClientRect().top : null;
  withExecutionFocus(update);
  if (id) {
    const replacement = document.getElementById(id);
    if (replacement) {
      replacement.focus({ preventScroll: true });
      if (selection && typeof replacement.setSelectionRange === "function") {
        replacement.setSelectionRange(...selection);
      }
      if (top !== null) window.scrollBy(0, replacement.getBoundingClientRect().top - top);
    }
  }
}

function iiMessage(view, message, error = false) {
  view.actionNotice = error ? null : message;
  view.actionError = error ? message : null;
}

function iiIsConflict(error) {
  return error.status === 409 && /revision or request conflict/i.test(error.message || "");
}

async function iiGet(execution, view, endpoint, extra = {}) {
  const response = await fetch(iiUrl(execution, view, endpoint, extra), { cache: "no-store" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Incident service is unavailable.");
  return data;
}

async function iiPost(execution, view, endpoint, payload) {
  const response = await fetch(`/api/reliability/${endpoint}`, {
    method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ schema_version: 1, scope: iiScope(execution, view), ...payload }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || "Incident action could not be saved.");
    error.status = response.status;
    throw error;
  }
  return data;
}

function iiTokens(value) {
  return value.split(/[\s,]+/).map((part) => part.trim()).filter(Boolean);
}

function iiEvidenceOptions(view, selected, detailItem = null) {
  const candidates = new Map(view.items.map((item) => [item.incident_id, item]));
  if (detailItem) candidates.set(detailItem.incident_id, detailItem);
  for (const incidentId of selected) {
    if (!candidates.has(incidentId)) {
      candidates.set(incidentId, {
        incident_id: incidentId, created_at: "", category: "selected reference",
        symptom_code: "outside current incident window", source_kind: "",
        execution_id: null,
      });
    }
  }
  return [...candidates.values()].map((item) => {
    const option = iiEl("option");
    option.value = item.incident_id;
    const execution = item.execution_id || "task history";
    const time = String(item.created_at || "").slice(11, 19) || "time unknown";
    const description = [time, item.category, item.symptom_code, item.source_kind, execution].filter(Boolean).join(" · ");
    option.textContent = `#${item.incident_id.slice(0, 8)} · ${description}`;
    option.selected = selected.includes(item.incident_id);
    return option;
  });
}

function iiRefreshEvidencePickers(view, section) {
  const pickers = [
    ["ii-supporting", view.editor.supportRefs, true],
    ["ii-contradicting", view.editor.contradictRefs, true],
    ["ii-evidence", view.editor.rationaleRefs, true],
    ["ii-control-evidence", view.editor.controlRefs, true],
    ["ii-correction-evidence", view.editor.correctionRefs, true],
    ["ii-group-evidence", view.editor.groupRefs, false],
    ["ii-verdict-evidence", view.editor.verdictRefs, false],
  ];
  iiPreserveFocus(section, () => {
    for (const [id, selected, includeDetail] of pickers) {
      const picker = section.querySelector(`#${id}`);
      if (!picker) continue;
      picker.replaceChildren(...iiEvidenceOptions(view, selected, includeDetail ? view.detail?.item : null));
    }
  });
}

function iiEvidencePicker(id, view, selected, onChange, detailItem = null) {
  const picker = iiEl("select");
  picker.id = id;
  picker.multiple = true;
  picker.size = 4;
  picker.replaceChildren(...iiEvidenceOptions(view, selected, detailItem));
  picker.addEventListener("change", () => onChange([...picker.selectedOptions].map((option) => option.value)));
  return picker;
}

function iiMemberPicker(id, group, selected, onChange) {
  const picker = iiEl("select");
  picker.id = id;
  picker.multiple = true;
  picker.size = Math.min(4, Math.max(2, group.members.length));
  for (const incidentId of group.members) {
    const meta = group.member_metadata?.[incidentId] || {};
    const option = iiEl("option");
    option.value = incidentId;
    option.textContent = `#${incidentId.slice(0, 8)} · ${meta.category || "unknown"} · ${meta.symptom_code || "unknown"} · ${meta.execution_id || "unknown execution"}`;
    option.selected = selected.includes(incidentId);
    picker.append(option);
  }
  picker.addEventListener("change", () => onChange([...picker.selectedOptions].map((option) => option.value)));
  return picker;
}

function iiCheckbox(id, label, checked, onChange) {
  const input = iiInput(id, "", "checkbox");
  input.checked = checked;
  input.addEventListener("change", () => onChange(input.checked));
  const node = iiLabel(label, input);
  node.className = "ii-checkbox-label";
  return node;
}

function iiAddFactFields(parent, item) {
  const fields = iiEl("dl");
  fields.className = "ii-facts";
  for (const [label, value] of [
    ["Incident", item.incident_id], ["Source kind", item.source_kind], ["Category", item.category],
    ["Classification version", item.classification_version], ["Symptom", item.symptom_code],
    ["Created", item.created_at], ["Task", item.task_id], ["Execution", item.execution_id],
    ["Epoch", item.epoch_id], ["Indexing", item.indexing], ["Provenance", item.provenance],
  ]) fields.append(incidentField(label, value));
  parent.append(fields);
}

function iiRenderSource(parent, source) {
  const box = iiEl("details");
  box.dataset.viewDisclosure = `ii-source:${source.source_kind}`;
  box.append(iiEl("summary", "Source provenance"));
  const fields = iiEl("dl");
  fields.className = "ii-facts";
  fields.append(incidentField("Source kind", source.source_kind));
  for (const [key, value] of Object.entries(source.reference || {})) {
    if (key === "accepted_source" && value && typeof value === "object") {
      fields.append(incidentField("Accepted source kind", value.kind));
      fields.append(incidentField("Accepted source ID", value.id));
      fields.append(incidentField("Accepted source digest", value.sha256));
    } else if (value !== null && typeof value !== "object") {
      fields.append(incidentField(key.replaceAll("_", " "), value));
    }
  }
  box.append(fields);
  parent.append(box);
}

function iiRenderDocument(parent, revision, view) {
  const documentValue = revision?.document;
  if (!documentValue) return;
  const history = iiEl("details");
  history.open = true;
  history.dataset.viewDisclosure = `ii-revision:${revision.revision}`;
  history.append(iiEl("summary", `Investigation revision ${revision.revision} · ${revision.actor_kind || "initial view"}`));
  history.append(iiEl("p", "This is an attributed interpretation. It does not change the immutable incident fact or grant execution, approval, verification, or deployment authority."));
  const status = iiEl("p", `Status: ${documentValue.status || "open"} · confidence is ordinal, not a probability.`);
  status.className = "ii-status";
  history.append(status);
  const facts = iiEl("dl");
  facts.className = "ii-facts";
  for (const [label, value] of [
    ["Fact IDs", (documentValue.facts || []).join(", ")],
    ["Original classification", JSON.stringify(documentValue.classification || {})],
    ["Rationale", JSON.stringify(documentValue.rationale || null)],
  ]) facts.append(incidentField(label, value));
  history.append(facts);
  const hypotheses = iiEl("section");
  hypotheses.className = "ii-subsection";
  hypotheses.append(iiEl("h5", "Hypotheses"));
  if (!(documentValue.hypotheses || []).length) hypotheses.append(iiEl("p", "No hypothesis recorded."));
  for (const [index, value] of (documentValue.hypotheses || []).entries()) {
    const block = iiEl("article");
    block.className = "ii-record";
    block.append(iiEl("strong", `${value.cause} · ${value.confidence} confidence`));
    block.append(iiEl("p", `Supporting: ${(value.supporting || []).join(", ") || "none"}`));
    block.append(iiEl("p", `Contradicting: ${(value.contradicting || []).join(", ") || "none"}`));
    if (value.operator_explanation) block.append(iiEl("p", `Operator explanation: ${value.operator_explanation}`));
    block.dataset.hypothesisIndex = String(index);
    hypotheses.append(block);
  }
  history.append(hypotheses);
  for (const [label, values] of [
    ["Controls", documentValue.controls], ["Affected components", documentValue.affected_components],
    ["Corrective actions", documentValue.corrective_actions], ["Corrections", documentValue.corrections],
  ]) {
    const block = iiEl("section");
    block.className = "ii-subsection";
    block.append(iiEl("h5", label));
    const list = iiEl("ul");
    for (const value of values || []) {
      const item = iiEl("li", JSON.stringify(value));
      list.append(item);
    }
    if (!list.childElementCount) block.append(iiEl("p", `No ${label.toLowerCase()} recorded.`));
    else block.append(list);
    history.append(block);
  }
  parent.append(history);
}

function iiRenderDetail(execution, view, section) {
  const target = section.querySelector(".ii-detail");
  if (!target) return;
  const fragment = document.createDocumentFragment();
  if (view.actionError) {
    const error = iiEl("p", view.actionError);
    error.setAttribute("role", "alert");
    fragment.append(error);
  }
  if (view.actionNotice) {
    const notice = iiEl("p", view.actionNotice);
    notice.setAttribute("role", "status");
    fragment.append(notice);
  }
  if (view.detailError) {
    const error = iiEl("p", view.detailError);
    error.setAttribute("role", "alert");
    fragment.append(error);
  }
  if (view.detailLoading) fragment.append(iiEl("p", "Loading incident detail…"));
  const item = view.detail?.item;
  if (item) {
    const facts = iiEl("details");
    facts.open = true;
    facts.dataset.viewDisclosure = `ii-facts:${item.incident_id}`;
    facts.append(iiEl("summary", "Immutable observed fact"));
    iiAddFactFields(facts, item);
    fragment.append(facts);
    if (view.detail.source) iiRenderSource(fragment, view.detail.source);
    if (view.detail.investigation) iiRenderDocument(fragment, view.detail.investigation, view);
    const historyBox = iiEl("section");
    historyBox.className = "ii-subsection";
    historyBox.append(iiEl("h4", "Append-only investigation history"));
    for (const row of view.history) iiRenderDocument(historyBox, row, view);
    if (view.historyLoading) historyBox.append(iiEl("p", "Loading older revisions…"));
    if (view.historyCursor) historyBox.append(iiButton("Older revisions", "ii-history-older", () => {
      iiLoadHistory(execution, view, section, true);
    }));
    fragment.append(historyBox);
    if (item.indexing === "legacy_unindexed") {
      fragment.append(iiEl("p", "This legacy incident is visible read-only. Explicitly project it in the selected epoch before editing."));
    } else {
      fragment.append(iiRenderEditor(execution, view, section));
    }
  } else if (!view.detailLoading && !view.selectedId) {
    fragment.append(iiEl("p", "Select an incident to inspect its immutable source and separate operator interpretation."));
  }
  iiPreserveFocus(target, () => target.replaceChildren(fragment));
}

function iiRenderList(execution, view, section) {
  const target = section.querySelector(".ii-results");
  if (!target) return;
  const fragment = document.createDocumentFragment();
  if (view.error) {
    const error = iiEl("p", view.error);
    error.setAttribute("role", "alert");
    fragment.append(error);
  }
  if (view.loading && !view.items.length) fragment.append(iiEl("p", "Loading canonical incidents…"));
  if (!view.items.length && !view.loading && !view.error) fragment.append(iiEl("p", "No incidents in this scope."));
  for (const item of view.items) {
    const article = iiEl("article");
    article.className = "ii-item";
    article.dataset.incidentId = item.incident_id;
    article.append(iiEl("h5", `${item.category} · ${item.source_kind}`));
    article.append(iiEl("p", `${item.symptom_code} · ${item.created_at}`));
    article.append(iiEl("p", `${item.execution_id || "task history"} · ${item.indexing}`));
    const button = iiButton(view.selectedId === item.incident_id ? "Selected incident" : "Inspect incident",
      `ii-select-${item.incident_id}`, () => iiSelectIncident(execution, view, section, item.incident_id));
    button.setAttribute("aria-pressed", String(view.selectedId === item.incident_id));
    article.append(button);
    fragment.append(article);
  }
  if (view.nextCursor) fragment.append(iiButton("Older incidents", "ii-list-older", () => {
    iiLoadList(execution, view, section, { older: true });
  }));
  if (view.windowAdvanced) fragment.append(iiButton("Latest incidents", "ii-list-latest", () => {
    iiLoadList(execution, view, section, { reset: true });
  }));
  iiPreserveFocus(target, () => target.replaceChildren(fragment));
}

function iiRenderEditor(execution, view, section) {
  const item = view.detail?.item;
  const current = view.detail?.investigation || { revision: 0, document: { hypotheses: [], controls: [], affected_components: [], corrective_actions: [], corrections: [] } };
  const doc = current.document || {};
  const draft = view.editor;
  if (!draft.rationaleRefs.length && item?.incident_id) draft.rationaleRefs = [item.incident_id];
  const wrap = iiEl("section");
  wrap.className = "ii-editor";
  wrap.append(iiEl("h4", "Add an attributed interpretation"));
  const note = iiEl("p", "Observed facts and original classification remain unchanged. Requests are local_dashboard_operator actions; this is not named-user authentication, approval, verification, or deployment authorization.");
  note.className = "ii-authority-note";
  wrap.append(note);
  const form = iiEl("form");
  form.className = "ii-form";
  const cause = iiSelect("ii-cause", draft.cause, [
    ["unknown", "Unknown"], ["incorrect_implementation", "Incorrect implementation"],
    ["architecture_violation", "Architecture violation"], ["specification_drift", "Specification drift"],
    ["unauthorized_operation", "Unauthorized operation"], ["repeated_unsuccessful_edits", "Repeated unsuccessful edits"],
    ["invalid_handoff", "Invalid handoff"], ["hallucinated_dependency", "Hallucinated dependency"],
    ["hallucinated_api", "Hallucinated API"], ["incorrect_tool", "Incorrect tool"],
    ["missing_context", "Missing context"], ["excessive_retries", "Excessive retries"],
    ["budget_exhaustion", "Budget exhaustion"], ["failed_verification", "Failed verification"],
    ["human_rejected_deliverable", "Human-rejected deliverable"],
  ]);
  cause.addEventListener("change", () => { draft.cause = cause.value; });
  const confidence = iiSelect("ii-confidence", draft.confidence,
    [["unassessed", "Unassessed"], ["low", "Low"], ["medium", "Medium"], ["high", "High"]]);
  confidence.addEventListener("change", () => { draft.confidence = confidence.value; });
  const supporting = iiEvidencePicker("ii-supporting", view, draft.supportRefs,
    (values) => { draft.supportRefs = values; draft.requestId = null; }, item);
  const contradicting = iiEvidencePicker("ii-contradicting", view, draft.contradictRefs,
    (values) => { draft.contradictRefs = values; draft.requestId = null; }, item);
  const includeHypothesis = iiCheckbox("ii-include-hypothesis", "Append this hypothesis", draft.includeHypothesis,
    (value) => { draft.includeHypothesis = value; draft.requestId = null; });
  const explanation = iiEl("textarea");
  explanation.id = "ii-explanation";
  explanation.maxLength = 2000;
  explanation.value = draft.explanation;
  explanation.addEventListener("input", () => { draft.explanation = explanation.value; });
  form.append(iiLabel("Hypothesis", cause), iiLabel("Ordinal confidence", confidence),
    iiLabel("Supporting evidence", supporting), iiLabel("Contradicting evidence", contradicting), includeHypothesis,
    iiLabel("Separately authored explanation (up to 2,000 characters)", explanation));
  const status = iiSelect("ii-status", draft.status,
    [["open", "Open"], ["investigating", "Investigating"], ["resolved", "Resolved"], ["dismissed", "Dismissed"]]);
  status.addEventListener("change", () => { draft.status = status.value; });
  const rationale = iiSelect("ii-rationale", draft.rationale,
    ["evidence_updated", "interpretation_corrected", "evidence_sufficient", "not_reproduced",
      "duplicate_symptom", "unrelated_symptoms", "scope_changed"].map((v) => [v, v.replaceAll("_", " ")]));
  rationale.addEventListener("change", () => { draft.rationale = rationale.value; });
  const evidence = iiEvidencePicker("ii-evidence", view, draft.rationaleRefs,
    (values) => { draft.rationaleRefs = values; draft.requestId = null; }, item);
  form.append(iiLabel("Investigation status", status), iiLabel("Rationale code", rationale), iiLabel("Rationale evidence", evidence));

  const controls = iiEl("details");
  controls.id = "ii-advanced-controls";
  controls.className = "ii-advanced-controls";
  controls.dataset.viewDisclosure = "ii-advanced-controls";
  controls.append(iiEl("summary", "Controls, components, corrections, and suggestions"));
  const component = iiInput("ii-component", draft.component);
  component.placeholder = "Repository-relative path";
  component.addEventListener("input", () => { draft.component = component.value; });
  const componentVerification = iiSelect("ii-component-verification", draft.componentVerification,
    [["unverified", "Unverified authored reference"], ["verified", "Check existing Knowledge source membership"]]);
  componentVerification.addEventListener("change", () => { draft.componentVerification = componentVerification.value; });
  const sourceVersion = iiInput("ii-component-version", draft.componentVersion);
  sourceVersion.placeholder = "Existing source version ID";
  sourceVersion.addEventListener("input", () => { draft.componentVersion = sourceVersion.value; });
  const snapshot = iiInput("ii-component-snapshot", draft.componentSnapshot);
  snapshot.placeholder = "Existing snapshot ID";
  snapshot.addEventListener("input", () => { draft.componentSnapshot = snapshot.value; });
  const kind = iiSelect("ii-control-kind", draft.controlKind,
    ["authority", "dispatch", "contract", "budget", "verification", "review"].map((v) => [v, v]));
  kind.addEventListener("change", () => { draft.controlKind = kind.value; });
  const controlState = iiSelect("ii-control-state", draft.controlState,
    [["unknown", "Unknown"], ["held", "Held"], ["failed", "Failed"]]);
  controlState.addEventListener("change", () => { draft.controlState = controlState.value; });
  const controlEvidence = iiEvidencePicker("ii-control-evidence", view, draft.controlRefs,
    (values) => { draft.controlRefs = values; draft.requestId = null; }, item);
  const includeControl = iiCheckbox("ii-include-control", "Append a control assessment", draft.includeControl,
    (value) => { draft.includeControl = value; draft.requestId = null; });
  const includeAction = iiCheckbox("ii-include-action", "Append a corrective-action suggestion", draft.includeAction,
    (value) => { draft.includeAction = value; draft.requestId = null; });
  const action = iiSelect("ii-action", draft.action,
    ["investigate", "add_regression", "review_contract", "review_policy", "review_context", "review_design", "evaluate_change"]
      .map((v) => [v, v.replaceAll("_", " ")]));
  action.addEventListener("change", () => { draft.action = action.value; });
  const correctionField = iiSelect("ii-correction-field", draft.correctionField,
    ["classification", "hypotheses", "affected_components", "controls", "status"].map((v) => [v, v]));
  correctionField.addEventListener("change", () => { draft.correctionField = correctionField.value; });
  const correctionValue = iiSelect("ii-correction-value", draft.correctionValue,
    ["unknown", "incorrect_implementation", "architecture_violation", "specification_drift",
      "unauthorized_operation", "repeated_unsuccessful_edits", "invalid_handoff", "hallucinated_dependency",
      "hallucinated_api", "incorrect_tool", "missing_context", "excessive_retries", "budget_exhaustion",
      "failed_verification", "human_rejected_deliverable", "held", "failed", "unverified"]
      .map((v) => [v, v.replaceAll("_", " ")]));
  correctionValue.addEventListener("change", () => { draft.correctionValue = correctionValue.value; });
  const correctionRationale = iiSelect("ii-correction-rationale", draft.correctionRationale,
    ["evidence_updated", "interpretation_corrected", "evidence_sufficient", "not_reproduced",
      "duplicate_symptom", "unrelated_symptoms", "scope_changed"].map((v) => [v, v.replaceAll("_", " ")]));
  correctionRationale.addEventListener("change", () => { draft.correctionRationale = correctionRationale.value; });
  const correctionEvidence = iiEvidencePicker("ii-correction-evidence", view, draft.correctionRefs,
    (values) => { draft.correctionRefs = values; draft.requestId = null; }, item);
  controls.append(iiLabel("Component reference", component), iiLabel("Verification basis", componentVerification),
    iiLabel("Existing Knowledge source version", sourceVersion), iiLabel("Existing Knowledge snapshot", snapshot),
    includeControl, iiLabel("Control kind", kind), iiLabel("Control state", controlState), iiLabel("Control evidence", controlEvidence),
    includeAction, iiLabel("Suggested corrective action", action), iiLabel("Correction field", correctionField),
    iiLabel("Correction interpretation", correctionValue), iiLabel("Correction rationale", correctionRationale),
    iiLabel("Correction evidence", correctionEvidence));
  controls.addEventListener("input", () => { draft.requestId = null; });
  controls.addEventListener("change", () => { draft.requestId = null; });
  const save = iiButton("Append investigation revision", "ii-save", async () => {
    const changes = {};
    if (draft.includeHypothesis && draft.cause !== "unknown" && !draft.supportRefs.length) {
      iiMessage(view, "Add at least one supporting incident for a non-unknown hypothesis.", true);
      iiRenderDetail(execution, view, section);
      document.getElementById("ii-supporting")?.focus();
      return;
    }
    if (["resolved", "dismissed"].includes(draft.status) && !draft.rationaleRefs.length) {
      iiMessage(view, "Resolution and dismissal require newly supplied incident evidence.", true);
      iiRenderDetail(execution, view, section);
      document.getElementById("ii-evidence")?.focus();
      return;
    }
    if (draft.includeControl && draft.controlState !== "unknown" && !draft.controlRefs.length) {
      iiMessage(view, "A held or failed control needs evidence. Use Unknown when its result is not established.", true);
      iiRenderDetail(execution, view, section);
      document.getElementById("ii-control-evidence")?.focus();
      return;
    }
    if (draft.component.trim() && draft.componentVerification === "verified"
        && (!draft.componentVersion.trim() || !draft.componentSnapshot.trim())) {
      iiMessage(view, "Historical verification requires an existing Knowledge source version and snapshot.", true);
      iiRenderDetail(execution, view, section);
      document.getElementById("ii-component-version")?.focus();
      return;
    }
    const hypothesis = {
      cause: draft.cause, confidence: draft.confidence, supporting: draft.supportRefs,
      contradicting: draft.contradictRefs,
    };
    if (draft.explanation.trim()) hypothesis.operator_explanation = draft.explanation.trim();
    const hasHypothesis = draft.includeHypothesis
      && (hypothesis.cause !== "unknown" || hypothesis.supporting.length || hypothesis.contradicting.length || hypothesis.operator_explanation);
    if (hasHypothesis) {
      changes.hypotheses = [...(doc.hypotheses || []), hypothesis];
    }
    if (draft.status !== (doc.status || "open")) changes.status = draft.status;
    if (draft.rationaleRefs.length && (hasHypothesis || changes.status || draft.includeControl || draft.includeAction)) {
      changes.rationale = { code: draft.rationale, evidence: draft.rationaleRefs };
    }
    if (draft.component.trim()) {
      const componentValue = { reference: draft.component.trim(), verification: draft.componentVerification };
      if (draft.componentVerification === "verified") {
        componentValue.source_version = draft.componentVersion.trim();
        componentValue.snapshot = draft.componentSnapshot.trim();
      }
      changes.affected_components = [...(doc.affected_components || []), componentValue];
    }
    if (draft.includeControl) {
      changes.controls = [...(doc.controls || []), { kind: draft.controlKind, state: draft.controlState, evidence: draft.controlRefs }];
    }
    if (draft.includeAction) changes.corrective_actions = [...(doc.corrective_actions || []), draft.action];
    if (draft.correctionRefs.length) changes.corrections = [...(doc.corrections || []), {
      field: draft.correctionField, interpretation: draft.correctionValue.trim(),
      rationale: { code: draft.correctionRationale, evidence: draft.correctionRefs },
    }];
    if (!Object.keys(changes).length) {
      iiMessage(view, "Choose an interpretation, status, control, correction, or action before saving.", true);
      iiRenderDetail(execution, view, section);
      return;
    }
    const requestId = draft.requestId || (draft.requestId = iiRequestId());
    save.disabled = true;
    try {
      await iiPost(execution, view, "investigate", {
        incident: item.incident_id, expected_revision: current.revision || 0,
        request_id: requestId, changes,
      });
      iiMessage(view, "Investigation revision appended. Original facts remain unchanged.");
      draft.requestId = null;
      draft.includeHypothesis = false;
      draft.includeControl = false;
      draft.includeAction = false;
      draft.component = "";
      draft.componentVersion = "";
      draft.componentSnapshot = "";
      draft.componentVerification = "unverified";
      draft.correctionRefs = [];
      await iiLoadDetail(execution, view, section, item.incident_id, true);
    } catch (error) {
      iiMessage(view, iiIsConflict(error)
        ? "The saved revision changed or this request ID was used with different content. Reload the current incident before editing."
        : "The investigation could not be saved. Check the selected evidence and authored text; rejected content is not echoed.", true);
      iiRenderDetail(execution, view, section);
    } finally { save.disabled = false; }
  });
  form.addEventListener("submit", (event) => event.preventDefault());
  form.addEventListener("input", () => { draft.requestId = null; });
  form.addEventListener("change", () => { draft.requestId = null; });
  form.append(save);
  wrap.append(form);
  const suggestion = iiButton("Suggest groups from this visible incident window", "ii-suggest", () => iiSuggestGroups(execution, view, section));
  controls.append(suggestion);
  wrap.append(controls);
  return wrap;
}

async function iiLoadList(execution, view, section, { older = false, reset = false } = {}) {
  if (view.loading && view.listOwner === section && iiIsCurrent(execution, view, section)) return;
  const request = ++view.listRequest;
  view.listOwner = section;
  if (reset) {
    view.detailRequest += 1;
    view.detailLoading = false;
    view.items = [];
    view.nextCursor = null;
    view.windowAdvanced = false;
    view.selectedId = null;
    view.detail = null;
    view.history = [];
    view.historyCursor = null;
  }
  view.loading = true;
  view.error = null;
  if (iiIsCurrent(execution, view, section)) {
    iiRenderList(execution, view, section);
    if (reset) {
      iiRenderDetail(execution, view, section);
      iiRenderGroups(execution, view, section);
      if (view.mode === "execution") iiRenderOutputs(execution, view, section);
      iiRefreshEvidencePickers(view, section);
    }
  }
  try {
    const data = await iiGet(execution, view, "incidents", {
      limit: 25, q: view.query, category: view.category, source_kind: view.sourceKind,
      before_time: older ? view.nextCursor?.created_at : null,
      before_id: older ? view.nextCursor?.incident_id : null,
    });
    if (!iiIsCurrent(execution, view, section) || request !== view.listRequest) return;
    const incoming = data.items || [];
    const merged = older ? view.items.concat(incoming) : incoming;
    if (merged.length > INCIDENT_INTELLIGENCE_LIMIT) {
      view.items = merged.slice(-INCIDENT_INTELLIGENCE_LIMIT);
      view.windowAdvanced = true;
    } else view.items = merged;
    view.nextCursor = data.next_cursor || null;
    if (view.selectedId && !view.items.some((item) => item.incident_id === view.selectedId)) {
      view.selectedId = null; view.detail = null; view.history = [];
    }
  } catch (_error) {
    if (iiIsCurrent(execution, view, section) && request === view.listRequest) {
      view.error = "Incident history is unavailable for this scope.";
    }
  } finally {
    if (request === view.listRequest) {
      view.loading = false;
      if (iiIsCurrent(execution, view, section)) {
        iiRenderList(execution, view, section);
        iiRefreshEvidencePickers(view, section);
      }
    }
  }
}

async function iiLoadDetail(execution, view, section, incidentId, refreshHistory = false) {
  if (view.detailLoading && view.detailOwner === section && view.selectedId === incidentId) return;
  const request = ++view.detailRequest;
  view.detailOwner = section;
  view.selectedId = incidentId;
  view.detail = null;
  view.detailLoading = true;
  view.detailError = null;
  if (iiIsCurrent(execution, view, section)) iiRenderDetail(execution, view, section);
  try {
    const data = await iiGet(execution, view, "detail", { incident: incidentId });
    if (!iiIsCurrent(execution, view, section) || request !== view.detailRequest || view.selectedId !== incidentId) return;
    view.detail = data;
    if (!refreshHistory) { view.history = []; view.historyCursor = null; }
    await iiLoadHistory(execution, view, section, false);
    if (view.mode === "execution") await iiLoadProjection(execution, view, section);
  } catch (_error) {
    if (iiIsCurrent(execution, view, section) && request === view.detailRequest) {
      view.detailError = "Incident detail or provenance is unavailable for this scope.";
    }
  } finally {
    if (request === view.detailRequest) {
      view.detailLoading = false;
      if (iiIsCurrent(execution, view, section)) iiRenderDetail(execution, view, section);
    }
  }
}

async function iiLoadHistory(execution, view, section, older) {
  if (!view.selectedId || (view.historyLoading && view.historyOwner === section)) return;
  const request = ++view.historyRequest;
  view.historyOwner = section;
  view.historyLoading = true;
  const incidentId = view.selectedId;
  try {
    const data = await iiGet(execution, view, "investigation", {
      incident: incidentId, limit: 25,
      before_revision: older ? view.historyCursor : null,
    });
    if (!iiIsCurrent(execution, view, section) || request !== view.historyRequest || view.selectedId !== incidentId) return;
    const merged = older ? view.history.concat(data.items || []) : (data.items || []);
    view.history = merged.slice(-INCIDENT_INTELLIGENCE_LIMIT);
    view.historyCursor = data.next_revision || null;
  } catch (_error) {
    if (iiIsCurrent(execution, view, section) && request === view.historyRequest) {
      view.actionError = "Investigation history is unavailable for this incident.";
    }
  } finally {
    if (request === view.historyRequest) {
      view.historyLoading = false;
      if (iiIsCurrent(execution, view, section)) iiRenderDetail(execution, view, section);
    }
  }
}

async function iiSelectIncident(execution, view, section, incidentId) {
  view.actionError = null; view.actionNotice = null;
  await iiLoadDetail(execution, view, section, incidentId);
}

async function iiLoadProjection(execution, view, section) {
  const request = ++view.projectionRequest;
  try {
    const data = await iiGet(execution, view, "projection");
    if (!iiIsCurrent(execution, view, section) || request !== view.projectionRequest) return;
    view.projection = data;
    iiRenderProjection(execution, view, section);
  } catch (_error) {
    if (iiIsCurrent(execution, view, section) && request === view.projectionRequest) {
      view.projection = { projection_coverage: "Projection status unavailable." };
      iiRenderProjection(execution, view, section);
    }
  }
}

function iiRenderProjection(execution, view, section) {
  const target = section.querySelector(".ii-projection");
  if (!target) return;
  const box = iiEl("section");
  box.className = "ii-projection-card";
  box.append(iiEl("h4", "Legacy coverage"));
  if (view.mode !== "execution") box.append(iiEl("p", "Projection requires a selected execution and recorded epoch."));
  else {
    box.append(iiEl("p", `Checkpoint revision ${view.projection?.checkpoint_revision ?? "unknown"}; ${view.projection?.unindexed_observed ? "unindexed legacy rows observed" : "no unindexed rows observed"}. Coverage is only as of this read; later old-pin records may appear.`));
    box.append(iiButton("Project up to 100 visible legacy rows", "ii-project", async () => {
      const requestId = view.editor.projectRequestId || (view.editor.projectRequestId = iiRequestId());
      try {
        const data = await iiPost(execution, view, "project", {
          expected_revision: view.projection?.checkpoint_revision ?? 0,
          request_id: requestId, limit: 100,
        });
        view.editor.projectRequestId = null;
        iiMessage(view, `Projection committed as checkpoint ${data.result.checkpoint_revision}; ${data.result.projected_count} rows processed.`);
        iiRenderDetail(execution, view, section);
        await iiLoadProjection(execution, view, section);
        await iiLoadList(execution, view, section, { reset: true });
        if (view.selectedId) await iiLoadDetail(execution, view, section, view.selectedId, true);
      } catch (error) {
        iiMessage(view, error.status === 409 ? "Projection checkpoint changed. Reload coverage before retrying." : "Legacy projection was refused or unavailable.", true);
        iiRenderDetail(execution, view, section);
      }
    }));
  }
  target.replaceChildren(box);
}

async function iiSuggestGroups(execution, view, section, older = false) {
  const request = ++view.groupListRequest;
  view.groupsLoading = true; view.groupsError = null;
  iiRenderGroups(execution, view, section);
  try {
    const data = await iiPost(execution, view, "suggest", {
      limit: 100, cursor: older ? view.groupsCursor : null,
    });
    if (!iiIsCurrent(execution, view, section) || request !== view.groupListRequest) return;
    view.groups = older ? view.groups.concat(data.result.items || []) : (data.result.items || []);
    if (view.groups.length > INCIDENT_INTELLIGENCE_LIMIT) view.groups = view.groups.slice(-INCIDENT_INTELLIGENCE_LIMIT);
    view.groupsCursor = data.result.next_cursor || null;
    view.actionNotice = `Suggestions cover one bounded window; ${data.result.legacy_unindexed_in_window} unindexed legacy rows were skipped.`;
  } catch (_error) {
    if (iiIsCurrent(execution, view, section) && request === view.groupListRequest) view.groupsError = "Group suggestions are unavailable for this incident window.";
  } finally {
    if (request === view.groupListRequest) {
      view.groupsLoading = false;
      if (iiIsCurrent(execution, view, section)) iiRenderGroups(execution, view, section);
    }
  }
}

async function iiSelectGroup(execution, view, section, groupId, older = false) {
  const request = ++view.groupRequest;
  if (view.selectedGroupId !== groupId) {
    view.editor.groupRefs = [];
    view.editor.splitLeftRefs = [];
    view.editor.splitRightRefs = [];
    view.editor.groupRequestId = null;
  }
  view.selectedGroupId = groupId;
  view.selectedGroup = null;
  try {
    const data = await iiGet(execution, view, "group", {
      group: groupId, limit: 25, before_revision: older ? view.groupHistoryCursor : null,
    });
    if (!iiIsCurrent(execution, view, section) || request !== view.groupRequest || view.selectedGroupId !== groupId) return;
    view.selectedGroup = data.group;
    if (!older && !view.editor.groupRefs.length && data.group.members?.length) {
      view.editor.groupRefs = [data.group.members[0]];
    }
    view.groupHistory = older ? view.groupHistory.concat(data.history.items || []) : (data.history.items || []);
    if (view.groupHistory.length > INCIDENT_INTELLIGENCE_LIMIT) view.groupHistory = view.groupHistory.slice(-INCIDENT_INTELLIGENCE_LIMIT);
    view.groupHistoryCursor = data.history.next_revision || null;
  } catch (_error) {
    if (iiIsCurrent(execution, view, section) && request === view.groupRequest) view.groupsError = "Group decision history is unavailable.";
  }
  if (iiIsCurrent(execution, view, section)) iiRenderGroups(execution, view, section);
}

function iiRenderGroups(execution, view, section) {
  const target = section.querySelector(".ii-groups");
  if (!target) return;
  const box = iiEl("section");
  box.className = "ii-groups-card";
  box.append(iiEl("h4", "Related symptom groups"), iiEl("p", "Each suggestion request covers one bounded incident window. Related symptoms do not establish a common cause."));
  if (view.groupsError) { const alert = iiEl("p", view.groupsError); alert.setAttribute("role", "alert"); box.append(alert); }
  if (view.groupsLoading) box.append(iiEl("p", "Preparing bounded suggestions…"));
  for (const group of view.groups) {
    const article = iiEl("article");
    article.className = "ii-group";
    article.append(iiEl("h5", `${group.members.length} related symptoms · ${group.group_id}`));
    article.append(iiEl("p", group.meaning));
    article.append(iiEl("p", `Symptom family: ${group.safe_key.symptom_family}; versions and source differences are retained below.`));
    const members = iiEl("ul");
    for (const id of group.members) {
      const metadata = group.member_metadata?.[id] || {};
      members.append(iiEl("li", `${id} · ${metadata.source_kind || "unknown source"} · ${metadata.category || "unknown category"} · ${metadata.execution_id || "unknown execution"}`));
    }
    article.append(members, iiButton("Inspect group and decisions", `ii-group-${group.group_id}`, () => iiSelectGroup(execution, view, section, group.group_id)));
    box.append(article);
  }
  if (view.selectedGroup) {
    const current = view.selectedGroup;
    const editor = iiEl("details");
    editor.open = true;
    editor.dataset.viewDisclosure = `ii-group-detail:${current.group_id}`;
    editor.append(iiEl("summary", "Append group decision"));
    const revision = current.revision || 0;
    const fields = iiEl("div");
    fields.className = "ii-form";
    const kind = iiSelect("ii-group-action", "accept", [["accept", "Accept suggestion"], ["reject", "Reject suggestion"], ["split", "Split members"], ["supersede", "Supersede with another group"]]);
    const rationale = iiSelect("ii-group-rationale", view.editor.groupRationale,
      ["evidence_updated", "interpretation_corrected", "evidence_sufficient", "not_reproduced", "duplicate_symptom", "unrelated_symptoms", "scope_changed"].map((v) => [v, v.replaceAll("_", " ")]));
    const evidence = iiEvidencePicker("ii-group-evidence", view, view.editor.groupRefs,
      (values) => { view.editor.groupRefs = values; view.editor.groupRequestId = null; });
    const partitionLeft = iiMemberPicker("ii-group-partition-left", current,
      view.editor.splitLeftRefs, (values) => { view.editor.splitLeftRefs = values; view.editor.groupRequestId = null; });
    const partitionRight = iiMemberPicker("ii-group-partition-right", current,
      view.editor.splitRightRefs, (values) => { view.editor.splitRightRefs = values; view.editor.groupRequestId = null; });
    const targetGroup = iiSelect("ii-group-target", view.editor.targetGroup,
      [["", "Choose an existing same-scope suggestion"], ...view.groups
        .filter((group) => group.group_id !== current.group_id)
        .map((group) => [group.group_id, `${group.members.length} symptoms · ${group.group_id}`])]);
    targetGroup.addEventListener("change", () => { view.editor.targetGroup = targetGroup.value; view.editor.groupRequestId = null; });
    rationale.addEventListener("change", () => { view.editor.groupRationale = rationale.value; });
    fields.append(iiLabel("Decision", kind), iiLabel("Rationale", rationale), iiLabel("Supporting incidents", evidence),
      iiLabel("First split partition", partitionLeft), iiLabel("Second split partition", partitionRight),
      iiLabel("Superseding suggestion", targetGroup));
    fields.append(iiButton("Save group decision", "ii-group-save", async () => {
      const decision = { kind: kind.value, rationale: { code: rationale.value, evidence: view.editor.groupRefs } };
      if (kind.value === "split") decision.partitions = [view.editor.splitLeftRefs, view.editor.splitRightRefs].filter((part) => part.length);
      if (kind.value === "supersede") decision.target_group = targetGroup.value;
      const requestId = view.editor.groupRequestId || (view.editor.groupRequestId = iiRequestId());
      try {
        await iiPost(execution, view, "group-decision", {
          group: current.group_id, expected_revision: revision, request_id: requestId, decision,
        });
        view.editor.groupRequestId = null;
        iiMessage(view, "Group decision appended. Source incidents remain unchanged.");
        await iiSelectGroup(execution, view, section, current.group_id);
      } catch (error) {
        iiMessage(view, iiIsConflict(error) ? "Group revision changed. Reload the group before editing." : "Group decision was refused; verify partitions and evidence.", true);
        iiRenderGroups(execution, view, section);
      }
    }));
    editor.append(fields);
    const history = iiEl("ol");
    for (const row of view.groupHistory) history.append(iiEl("li", `Revision ${row.revision} · ${row.actor_kind} · ${JSON.stringify(row.document)}`));
    if (history.childElementCount) editor.append(history);
    if (view.groupHistoryCursor) editor.append(iiButton("Older group decisions", "ii-group-history-older", () => {
      iiSelectGroup(execution, view, section, current.group_id, true);
    }));
    fields.addEventListener("input", () => { view.editor.groupRequestId = null; });
    fields.addEventListener("change", () => { view.editor.groupRequestId = null; });
    box.append(editor);
  }
  if (view.groupsCursor) box.append(iiButton("Suggest groups from the older incident window", "ii-groups-older", () => {
    iiSuggestGroups(execution, view, section, true);
  }));
  iiPreserveFocus(target, () => target.replaceChildren(box));
}

async function iiLoadOutputs(execution, view, section, older = false) {
  if (view.mode !== "execution") return;
  const request = ++view.outputsRequest;
  try {
    const data = await iiGet(execution, view, "outputs", {
      limit: 25, before_id: older ? view.outputsCursor?.before_id : null,
    });
    if (!iiIsCurrent(execution, view, section) || request !== view.outputsRequest) return;
    const merged = older ? view.outputs.concat(data.items || []) : (data.items || []);
    view.outputs = merged.slice(-INCIDENT_INTELLIGENCE_LIMIT);
    view.outputsCursor = data.next_cursor || null;
    view.outputsLoaded = true;
    view.outputError = data.source_available ? null : "No existing recorded output index is available for this epoch. The dashboard did not create or repair one.";
  } catch (_error) {
    if (iiIsCurrent(execution, view, section) && request === view.outputsRequest) {
      view.outputsLoaded = true;
      view.outputError = "Published output descriptors are unavailable for this epoch.";
    }
  }
  if (iiIsCurrent(execution, view, section)) iiRenderOutputs(execution, view, section);
}

function iiRenderOutputs(execution, view, section) {
  const target = section.querySelector(".ii-outputs");
  if (!target) return;
  const box = iiEl("section");
  box.className = "ii-outputs-card";
  box.append(iiEl("h4", "Record an operator verdict"));
  box.append(iiEl("p", "Choose a published agent report or result from this recorded epoch. The list shows metadata only; submitting a verdict independently checks the selected immutable output. This does not approve, verify, deploy, or change execution status."));
  if (view.outputError) { const error = iiEl("p", view.outputError); error.setAttribute("role", "status"); box.append(error); }
  const list = iiEl("div");
  list.className = "ii-output-list";
  for (const output of view.outputs) {
    const item = iiEl("div");
    item.className = "ii-output-item";
    const radio = iiInput(`ii-output-${output.artifact_id}`, view.selectedOutputId === output.artifact_id ? output.artifact_id : "", "radio");
    radio.name = "ii-output";
    radio.value = output.artifact_id;
    radio.checked = view.selectedOutputId === output.artifact_id;
    radio.addEventListener("change", () => { view.selectedOutputId = output.artifact_id; });
    const label = iiEl("label", `${output.name} · ${output.role} · ${output.size} bytes`);
    label.htmlFor = radio.id;
    const provenance = iiEl("details");
    provenance.className = "ii-output-provenance";
    provenance.append(iiEl("summary", "Output provenance and integrity"));
    provenance.append(iiEl("p", `Owner: ${output.owner} · Artifact: ${output.artifact_id} · SHA-256: ${output.sha256} · Graph revision: ${output.graph_revision}`));
    provenance.append(iiEl("p", "The verdict service rechecks the recorded descriptor and output bytes against the immutable artifact identity and digest when the verdict is submitted. The list itself does not read output content."));
    item.append(radio, label, provenance);
    list.append(item);
  }
  if (!view.outputs.length && !view.outputError) list.append(iiEl("p", "No published outputs are available in this page."));
  box.append(list);
  if (view.outputsCursor) box.append(iiButton("Older published outputs", "ii-output-older", () => iiLoadOutputs(execution, view, section, true)));
  const form = iiEl("div");
  form.className = "ii-form";
  const rationale = iiSelect("ii-verdict-rationale", view.editor.verdictRationale,
    ["evidence_updated", "interpretation_corrected", "evidence_sufficient", "not_reproduced", "duplicate_symptom", "unrelated_symptoms", "scope_changed"].map((v) => [v, v.replaceAll("_", " ")]));
  rationale.addEventListener("change", () => { view.editor.verdictRationale = rationale.value; });
  const explanation = iiEl("textarea");
  explanation.id = "ii-verdict-explanation";
  explanation.maxLength = 2000;
  explanation.value = view.editor.verdictExplanation;
  explanation.addEventListener("input", () => { view.editor.verdictExplanation = explanation.value; });
  const evidence = iiEvidencePicker("ii-verdict-evidence", view, view.editor.verdictRefs,
    (values) => { view.editor.verdictRefs = values; view.editor.verdictRequestId = null; });
  form.append(iiLabel("Verdict rationale", rationale), iiLabel("Separate operator explanation", explanation), iiLabel("Supporting incidents", evidence));
  form.addEventListener("input", () => { view.editor.verdictRequestId = null; });
  form.addEventListener("change", () => { view.editor.verdictRequestId = null; });
  form.append(iiButton("Record historical operator verdict", "ii-verdict", async () => {
    const selected = view.outputs.find((item) => item.artifact_id === view.selectedOutputId);
    if (!selected) { view.outputError = "Select a published output first."; iiRenderOutputs(execution, view, section); return; }
    const requestId = view.editor.verdictRequestId || (view.editor.verdictRequestId = iiRequestId());
    const reason = { code: rationale.value, evidence: view.editor.verdictRefs };
    if (explanation.value.trim()) reason.operator_explanation = explanation.value.trim();
    try {
      const data = await iiPost(execution, view, "verdict", {
        request_id: requestId,
        target: { artifact_id: selected.artifact_id, sha256: selected.sha256 },
        reason, evidence: view.editor.verdictRefs,
      });
      view.editor.verdictRequestId = null;
      view.actionNotice = `Historical operator verdict recorded as incident ${data.result.incident_id}.`;
      view.outputError = null;
      await iiLoadList(execution, view, section, { reset: true });
    } catch (error) {
      view.outputError = iiIsConflict(error)
        ? "This verdict request conflicts with a prior request. Refresh outputs and incident history."
        : "The verdict was refused. Check the selected output and rationale; no rejected bytes are shown.";
    }
    iiRenderOutputs(execution, view, section);
    iiRenderDetail(execution, view, section);
  }));
  box.append(form);
  iiPreserveFocus(target, () => target.replaceChildren(box));
}

function renderIncidentIntelligence(execution, existingView = null) {
  const section = iiEl("section");
  section.className = "incident-intelligence";
  section.id = "incident-intelligence";
  const initialMode = execution.epoch_id ? "execution" : "task";
  const view = existingView || iiView(execution, initialMode);
  section.dataset.intelligenceKey = view.key;
  section.append(iiEl("h3", "Incident Intelligence"));
  section.append(iiEl("p", "Search immutable observed incidents, inspect source provenance, and append separate operator interpretations. These controls never change execution authority."));
  const controls = iiEl("form");
  controls.className = "ii-filters";
  controls.id = "ii-filters";
  const scopeSelect = iiSelect("ii-scope", view.mode,
    [["task", "Entire task history"], ["execution", "Selected execution and epoch"]]);
  scopeSelect.disabled = !execution.epoch_id;
  scopeSelect.addEventListener("change", () => {
    const next = iiView(execution, scopeSelect.value);
    const replacement = renderIncidentIntelligence(execution, next);
    section.replaceWith(replacement);
    replacement.querySelector("#ii-scope")?.focus();
  });
  const query = iiInput("ii-query", view.query, "search");
  query.maxLength = 200;
  query.placeholder = "Incident ID, category, symptom, or source kind";
  const category = iiInput("ii-category", view.category);
  category.setAttribute("list", "ii-categories");
  category.placeholder = "Any category";
  const categoryList = iiEl("datalist");
  categoryList.id = "ii-categories";
  for (const value of ["operation_denied", "operation_failed", "verification_failed", "review_finding",
    "architecture_violation", "specification_drift", "incorrect_deliverable", "hallucinated_dependency",
    "hallucinated_api", "incorrect_tool", "missing_context", "invalid_handoff", "budget_exhaustion",
    "human_rejected_deliverable"]) {
    const option = iiEl("option"); option.value = value; categoryList.append(option);
  }
  const sourceKind = iiSelect("ii-source-kind", view.sourceKind,
    [["", "All source kinds"], ["operation_dispatch", "Operation dispatch"], ["al_test_result", "AL test result"], ["al_review_finding", "AL review finding"], ["contract_rejection", "Contract rejection"], ["budget_decision", "Budget decision"], ["operator_verdict", "Operator verdict"]]);
  controls.append(iiLabel("Scope", scopeSelect), iiLabel("Search fields", query), iiLabel("Category", category), iiLabel("Source kind", sourceKind), categoryList);
  controls.addEventListener("submit", (event) => {
    event.preventDefault();
    view.query = query.value; view.category = category.value; view.sourceKind = sourceKind.value;
    view.actionError = null; view.actionNotice = null;
    iiLoadList(execution, view, section, { reset: true });
  });
  const searchButton = iiEl("button", "Search incidents");
  searchButton.type = "submit";
  controls.append(searchButton);
  section.append(controls);
  const coverage = iiEl("details");
  coverage.className = "ii-coverage";
  coverage.append(iiEl("summary", "Coverage and interpretation limits"));
  coverage.append(iiEl("p", "Search uses literal incident ID, category, symptom code, and source kind fields. It is not report or log full-text search. Read-only source provenance describes original acceptance and does not revalidate files."));
  coverage.append(iiEl("p", "A held control describes only the recorded check. Generic dispatch rejection remains unknown; no absence of earlier effects, process settlement, remote CI provenance, current source stability, or proven common cause is inferred."));
  section.append(coverage);
  const body = iiEl("div"); body.className = "ii-body";
  const list = iiEl("div"); list.className = "ii-list-panel";
  list.append(iiEl("h4", "Canonical incident history"));
  const results = iiEl("div"); results.className = "ii-results"; results.setAttribute("aria-live", "polite"); list.append(results);
  const detail = iiEl("div"); detail.className = "ii-detail-panel";
  detail.append(iiEl("h4", "Selected incident"));
  const projection = iiEl("div"); projection.className = "ii-projection"; detail.append(projection);
  const outputs = iiEl("div"); outputs.className = "ii-outputs"; detail.append(outputs);
  const detailBody = iiEl("div"); detailBody.className = "ii-detail"; detail.append(detailBody);
  body.append(list, detail);
  section.append(body);
  const groups = iiEl("div"); groups.className = "ii-groups"; section.append(groups);
  iiRenderList(execution, view, section);
  iiRenderDetail(execution, view, section);
  if (view.mode === "execution") {
    iiRenderProjection(execution, view, section);
    iiRenderOutputs(execution, view, section);
  } else {
    projection.append(iiEl("p", "Projection and recorded-output verdicts require a selected execution and epoch."));
    outputs.append(iiEl("p", "Choose a selected execution and epoch to browse published output descriptors."));
  }
  iiRenderGroups(execution, view, section);
  // A repaint during a load re-issues it for the new section, which owns the results.
  if (reliabilityFetchDue(view, "list", execution, Boolean(view.error)) || view.loading) {
    iiLoadList(execution, view, section);
  }
  if (view.selectedId && (!view.detail || (view.detailLoading && view.detailOwner !== section))) {
    iiLoadDetail(execution, view, section, view.selectedId, true);
  }
  if (view.mode === "execution" && !view.projection) iiLoadProjection(execution, view, section);
  if (view.mode === "execution" && !view.outputsLoaded) iiLoadOutputs(execution, view, section);
  return section;
}
