"use strict";

const TIMELINE_VIEWS = new Map();
const TIMELINE_KINDS = [
  "operation.started", "operation.returned", "operation.denied", "operation.failed",
  "process.launch_pending", "process.started", "process.launch_failed", "process.cancel_requested",
  "process.timeout_observed", "process.exited", "process.cleanup_unconfirmed", "semantic.agent",
  "semantic.task", "semantic.check", "semantic.decision", "semantic.output", "semantic.run",
  "retrieval.bundle", "policy.allowed", "policy.denied", "policy.started", "policy.settled", "policy.unresolved", "policy.capability_issued", "policy.capability_revoked",
];

function timelineState(execution) {
  const identity = executionIdentity(execution);
  if (!TIMELINE_VIEWS.has(identity)) TIMELINE_VIEWS.set(identity, {
    identity, kind: "", principal: "", items: [], cursor: null, trace: null, coverage: null,
    measurements: null, listToken: 0, detailToken: 0, loading: false, detailLoading: false,
    error: null, detailError: null, spanId: null, selectedId: null, detail: null, open: true,
    newer: false, advanced: false,
  });
  while (TIMELINE_VIEWS.size > 8) TIMELINE_VIEWS.delete(TIMELINE_VIEWS.keys().next().value);
  return TIMELINE_VIEWS.get(identity);
}

function timelineSection(execution, view) {
  const section = document.getElementById("execution-timeline");
  return TIMELINE_VIEWS.get(view.identity) === view
    && currentExecution(execution) && executionIdentity(execution) === view.identity
    && section?.dataset.viewIdentity === view.identity ? section : null;
}

function rememberTimeline(view, section) {
  const details = section?.querySelector("details[data-timeline-span]");
  if (details?.dataset.timelineSpan === view.spanId) view.open = details.open;
}

function clearTimelineDetail(view) {
  view.detailToken += 1;
  view.spanId = null; view.selectedId = null; view.detail = null;
  view.detailLoading = false; view.detailError = null;
}

function timelineParams(execution, view) {
  const params = new URLSearchParams({ task: execution.task_id || executionTaskId,
    execution: execution.id, epoch: execution.epoch_id, limit: "25" });
  if (view.kind) params.set("kind", view.kind);
  if (view.principal) params.set("principal", view.principal);
  return params;
}

async function loadTimeline(execution, view, section, { older = false, reset = false } = {}) {
  if (view.loading && !reset) return;
  const token = ++view.listToken;
  if (reset) {
    view.items = []; view.cursor = null; view.newer = false; view.advanced = false;
    clearTimelineDetail(view);
  }
  view.loading = true; view.error = null;
  renderTimelineContent(execution, view, section);
  try {
    const params = timelineParams(execution, view);
    if (older && view.cursor) for (const [key, value] of Object.entries(view.cursor)) params.set(key, value);
    const response = await fetch(`/api/execution/timeline?${params}`, { cache: "no-store" });
    const data = await response.json();
    if (token !== view.listToken || !timelineSection(execution, view)) return;
    if (!response.ok) throw new Error("unavailable");
    const incoming = data.items || [];
    const existing = new Set(view.items.map((item) => item.item_id));
    const fresh = new Map(incoming.map((item) => [item.item_id, item]));
    const retained = view.items.map((item) => fresh.get(item.item_id) || item);
    const added = incoming.filter((item) => !existing.has(item.item_id));
    if (older || !existing.size) {
      const combined = retained.concat(added);
      if (combined.length > 100) view.advanced = true;
      view.items = combined.slice(-100);
      view.cursor = data.next_cursor;
      if (view.selectedId && !view.items.some((item) => item.item_id === view.selectedId)) {
        clearTimelineDetail(view);
      }
    } else {
      view.items = retained;
      if (added.length) view.newer = true;
    }
    view.trace = data.trace_id;
    view.coverage = data.coverage;
    view.measurements = data.measurement_availability;
  } catch (_error) {
    if (token === view.listToken && timelineSection(execution, view)) {
      view.error = "Timeline unavailable for this execution scope.";
    }
  } finally {
    if (token === view.listToken) {
      view.loading = false;
      const current = timelineSection(execution, view);
      if (current) renderTimelineContent(execution, view, current);
    }
  }
}

async function loadTimelineSpan(execution, view, section, item, { refresh = false } = {}) {
  if (refresh && view.detailLoading) return;
  const token = ++view.detailToken;
  if (view.spanId !== item.span_id) view.open = true;
  view.spanId = item.span_id; view.selectedId = item.item_id;
  if (!refresh) view.detail = null;
  view.detailError = null; view.detailLoading = true;
  renderTimelineContent(execution, view, section);
  try {
    const params = new URLSearchParams({task: execution.task_id || executionTaskId,
      execution: execution.id, epoch: execution.epoch_id, span: item.span_id});
    const response = await fetch(`/api/execution/timeline/span?${params}`, {cache: "no-store"});
    const data = await response.json();
    if (token !== view.detailToken || !timelineSection(execution, view)) return;
    if (!response.ok || data.span?.span_id !== item.span_id) throw new Error("unavailable");
    view.detail = data;
  } catch (_error) {
    if (token === view.detailToken && timelineSection(execution, view)) {
      view.detailError = "Span evidence unavailable for this execution scope.";
    }
  } finally {
    if (token === view.detailToken) {
      view.detailLoading = false;
      const current = timelineSection(execution, view);
      if (current) renderTimelineContent(execution, view, current);
    }
  }
}

function timelineEvidence(view) {
  const span = view.detail.span;
  const details = document.createElement("details");
  details.dataset.timelineSpan = span.span_id;
  details.dataset.viewDisclosure = `timeline:${span.span_id}`;
  details.open = view.open;
  const summary = el("summary", "Scoped process evidence");
  summary.id = `timeline-summary-${span.span_id}`;
  details.append(summary);
  details.addEventListener("toggle", () => {
    if (details.isConnected && view.spanId === span.span_id) view.open = details.open;
  });
  const fields = el("dl");
  for (const key of ["span_id", "attempt_id", "trace_id", "execution_id", "epoch_id",
    "authority_generation", "principal_id", "role", "parent_observation_id", "task_id", "revision",
    "launch_state", "terminal_state", "started_at", "spawned_at", "finished_at", "duration_ms",
    "returncode", "stop_reason", "cleanup_status"]) {
    fields.append(executionField(key.replaceAll("_", " "), span[key] ?? "unavailable"));
  }
  details.append(fields, el("p", "Process exit does not prove check publication, descendant cleanup, or acceptance."));
  return details;
}

function renderTimelineContent(execution, view, section) {
  rememberTimeline(view, section);
  const content = el("div"); content.className = "timeline-content";
  content.append(el("p", view.trace ? `Trace ${view.trace}` : "Historical correlation unavailable."));
  content.append(el("p", "Runtime observations and validated semantic reports are distinct; neither grants approval authority."));
  content.append(el("p", "Tokens, cost, and native child observations: unavailable. Durations use a monotonic clock when observed."));
  if (view.coverage) content.append(el("p", "Coverage v1: dispatch, owned processes, validated semantic links, authorized retrieval references. No durable recovery or historical backfill. Comparisons are not qualified."));
  for (const message of [view.error, view.detailError]) if (message) {
    const node = el("p", message); node.setAttribute("role", "alert"); content.append(node);
  }
  if (view.loading && !view.items.length) {
    const status = el("p", "Loading timeline…"); status.setAttribute("role", "status"); content.append(status);
  }
  if (view.detailLoading && !view.detail) content.append(el("p", "Loading span evidence…"));
  if (view.detail) content.append(timelineEvidence(view));
  if (!view.items.length && !view.loading && !view.error) content.append(el("p", "No recorded timeline entries in this scope."));
  const groups = new Map();
  for (const item of view.items) {
    const principal = item.principal_id || "unattributed semantic report";
    if (!groups.has(principal)) {
      const group = el("section"); group.className = "timeline-principal";
      group.append(el("h4", item.principal_id ? `Authenticated principal ${principal}` : principal));
      groups.set(principal, group); content.append(group);
    }
    const article = el("article"); article.className = "timeline-item";
    article.dataset.itemId = item.item_id;
    article.append(el("h5", item.event_kind), el("p", `${item.at} · ${item.provenance}`));
    article.append(el("p", `Source ${item.source_kind}: ${item.source_id}`));
    if (item.parent_observation_id) article.append(el("p", `Parent operation ${item.parent_observation_id}`));
    if (item.incident_id) article.append(el("p", `Operation incident ${item.incident_id}`));
    if (item.bundle_hash) article.append(el("p", `Authorized bundle ${item.bundle_hash}; receipt ${item.receipt_id || "unavailable"}`));
    if (item.reference_id) article.append(el("p", `Semantic reference ${item.reference_id}`));
    if (item.duration_ms !== null) article.append(el("p", `Measured duration ${item.duration_ms.toFixed(1)} ms`));
    if (item.returncode !== null) article.append(el("p", `Process exit code ${item.returncode}`));
    if (item.stop_reason) article.append(el("p", `Stop observation: ${item.stop_reason}`));
    if (item.cleanup_status) article.append(el("p", `Cleanup: ${item.cleanup_status}`));
    if (item.span_id) {
      const button = el("button", view.spanId === item.span_id ? "Hide process evidence" : "View process evidence");
      button.id = `timeline-detail-${item.item_id}`; button.type = "button";
      button.setAttribute("aria-expanded", String(view.spanId === item.span_id));
      button.addEventListener("click", () => {
        if (view.spanId === item.span_id) { clearTimelineDetail(view); renderTimelineContent(execution, view, section); }
        else loadTimelineSpan(execution, view, section, item);
      });
      article.append(button);
    }
    groups.get(principal).append(article);
  }
  if (view.cursor) {
    const older = el("button", "Older timeline entries"); older.id = "timeline-older"; older.type = "button";
    older.disabled = view.loading;
    older.addEventListener("click", () => loadTimeline(execution, view, section, {older: true}));
    content.append(older);
  }
  if (view.newer || view.advanced) {
    content.append(el("p", view.newer ? "Newer timeline entries are available; investigation window retained."
      : "Older navigation slides a window of at most 100 entries through recorded history."));
    const latest = el("button", "Latest timeline entries"); latest.id = "timeline-latest"; latest.type = "button";
    latest.addEventListener("click", () => loadTimeline(execution, view, section, {reset: true}));
    content.append(latest);
  }
  const previous = section.querySelector(".timeline-content"), active = document.activeElement;
  const anchor = previous?.contains(active) && active.id ? active.getBoundingClientRect().top : null;
  withExecutionFocus(() => { if (previous) previous.replaceWith(content); else section.append(content); });
  if (anchor !== null && document.activeElement.id === active.id) {
    window.scrollBy(0, document.activeElement.getBoundingClientRect().top - anchor);
  }
}

function renderExecutionTimeline(execution) {
  const section = el("section"); section.id = "execution-timeline"; section.className = "execution-timeline";
  section.dataset.viewIdentity = executionIdentity(execution);
  section.append(el("h3", "Execution timeline"));
  const view = timelineState(execution);
  rememberTimeline(view, timelineSection(execution, view));
  if (!execution.epoch_id) { section.append(el("p", "An explicit epoch is required.")); return section; }
  const form = el("form"); form.className = "timeline-filters";
  const kindLabel = el("label", "Event kind"), kind = el("select"); kind.id = "timeline-kind";
  for (const value of ["", ...TIMELINE_KINDS]) { const option = el("option", value || "All kinds"); option.value = value; kind.append(option); }
  kind.value = view.kind; kindLabel.htmlFor = kind.id; kindLabel.append(kind);
  const principalLabel = el("label", "Authenticated principal ID"), principal = el("input");
  principal.id = "timeline-principal"; principal.maxLength = 128; principal.value = view.principal;
  principalLabel.htmlFor = principal.id; principalLabel.append(principal);
  kind.addEventListener("change", () => { view.kind = kind.value; loadTimeline(execution, view, section, {reset: true}); });
  principal.addEventListener("input", () => { view.principal = principal.value; loadTimeline(execution, view, section, {reset: true}); });
  form.addEventListener("submit", (event) => event.preventDefault());
  form.append(kindLabel, principalLabel); section.append(form);
  renderTimelineContent(execution, view, section);
  reliabilityRefetch(view, "list", execution, {
    failed: Boolean(view.error),
    busy: () => view.loading,
    mounted: () => Boolean(timelineSection(execution, view)),
    load: () => loadTimeline(execution, view, timelineSection(execution, view) || section),
  });
  if (view.spanId && !view.detailLoading && (view.detail || !view.detailError)) {
    const selected = view.items.find((item) => item.item_id === view.selectedId);
    if (selected && !view.detail) {
      reliabilityMark(view, "span", execution);
      loadTimelineSpan(execution, view, section, selected, { refresh: false });
    }
    else if (selected) reliabilityRefetch(view, "span", execution, {
      busy: () => view.detailLoading,
      mounted: () => Boolean(timelineSection(execution, view)),
      load: () => loadTimelineSpan(execution, view, timelineSection(execution, view) || section, selected, { refresh: true }),
    });
  }
  return section;
}
