"use strict";

const API = "/api/dashboard";
const STREAM = "/api/stream";
const RETRY_MS = 2000;
const MAX_RETRY_MS = 30000;
const NOTHING_TO_RESUME = "execution completed; no work to resume";

let rendered = false;
let source = null;
let attempts = 0;

function el(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
}

function withCode(label, code) {
  const node = el("p", label);
  node.append(el("code", code));
  return node;
}

function query() {
  const task = new URLSearchParams(window.location.search).get("task");
  return task ? `?task=${encodeURIComponent(task)}` : "";
}

function pad(n) {
  return String(n).padStart(2, "0");
}

function formatElapsed(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${pad(s % 60)}s`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${pad(m % 60)}m`;
  return `${Math.floor(h / 24)}d ${pad(h % 24)}h`;
}

function tick() {
  for (const node of document.querySelectorAll("time[data-since]")) {
    node.textContent = formatElapsed(Date.now() - Date.parse(node.dataset.since));
  }
}

function specText(spec) {
  const parts = spec.versions.map((v) => `v${v.version} ${v.status}`);
  if (spec.draft) parts.push("draft");
  return parts.length ? parts.join(", ") : "not drafted yet";
}

function activityText(activity) {
  return activity ? `${activity.kind} at ${activity.at}` : "none";
}

function renderNav(tasks, selected) {
  const nav = el("nav");
  nav.id = "tasks";
  nav.setAttribute("aria-label", "Tasks");
  nav.append(el("h2", "Tasks"));
  if (!tasks.length) {
    nav.append(el("p", "no tasks"));
    return nav;
  }
  const list = el("ul");
  for (const t of tasks) {
    const item = el("li");
    const link = el("a", t.id);
    link.href = `/?task=${encodeURIComponent(t.id)}`;
    if (t.id === selected) link.setAttribute("aria-current", "page");
    const request = el("p", t.request);
    request.className = "task-request";
    request.title = t.request;
    item.append(link, el("p", `${t.phase} · ${t.status}`), request);
    if (t.next) item.append(withCode("next: ", t.next));
    list.append(item);
  }
  nav.append(list);
  return nav;
}

function renderResume(resume) {
  const section = el("section");
  section.id = "resume";
  section.append(el("h3", "Resume"));
  section.append(resume.next ? withCode("next: ", resume.next) : el("p", NOTHING_TO_RESUME));
  const question = resume.pending_question;
  if (question) {
    section.append(el("p", `pending question: Q${question.id} [${question.topic}] ${question.text}`));
  }
  const checkpoint = resume.checkpoint;
  section.append(
    el(
      "p",
      checkpoint
        ? `checkpoint: ${checkpoint.label} ${checkpoint.created_at} (#${checkpoint.id})`
        : "checkpoint: none in this phase",
    ),
  );
  if (resume.approval) section.append(el("p", `approval: ${resume.approval}`));
  for (const note of resume.notes) section.append(el("p", `note: ${note}`));
  return section;
}

const openDiffs = new Set();

function appendSpans(node, spans) {
  for (const span of spans) {
    if (span.style === "code" || span.style === "strong") node.append(el(span.style, span.text));
    else node.append(document.createTextNode(span.text));
  }
}

function appendLines(node, lines) {
  lines.forEach((spans, index) => {
    if (index) node.append(el("br"));
    appendSpans(node, spans);
  });
  return node;
}

function renderBlocks(blocks) {
  const article = el("article");
  article.id = "spec-document";
  for (const block of blocks) {
    if (block.type === "heading") {
      const heading = el(`h${Math.min(block.level + 3, 6)}`);
      appendSpans(heading, block.spans);
      article.append(heading);
    } else if (block.type === "list") {
      const list = el("ul");
      for (const item of block.items) list.append(appendLines(el("li"), item.lines));
      article.append(list);
    } else if (block.type === "paragraph") {
      article.append(appendLines(el("p"), block.lines));
    }
  }
  return article;
}

function alertText(text) {
  const node = el("p", text);
  node.setAttribute("role", "alert");
  return node;
}

function renderSpec(spec, phase) {
  const section = el("section");
  section.id = "spec";
  section.append(el("h3", "Specification"));
  if (spec.unavailable) section.append(alertText(`specification unavailable: ${spec.unavailable}`));
  const shown = spec.shown;
  if (shown) {
    section.append(el("p", `showing: ${shown.label} (${shown.status})`));
    section.append(withCode("file: ", shown.path), renderBlocks(shown.blocks));
  } else if (!spec.unavailable) {
    section.append(el("p", "not drafted yet"), el("p", `phase: ${phase}`));
  }
  return section;
}

function historySummary(entry) {
  const superseded = entry.superseded_by ? ` (superseded by v${entry.superseded_by})` : "";
  const revision = entry.revision ? ` · revision ${entry.revision}` : "";
  return `${entry.label} ${entry.status}${superseded} · ${entry.timestamp}${revision}`;
}

function renderDiff(taskId, entry) {
  const diff = entry.diff;
  const key = `${taskId} ${entry.label}`;
  const details = el("details");
  details.dataset.key = key;
  details.open = openDiffs.has(key);
  details.addEventListener("toggle", () => {
    if (details.open) openDiffs.add(key);
    else openDiffs.delete(key);
  });
  details.append(el("summary", `· +${diff.added} -${diff.removed} against ${diff.from}`));
  if (!diff.lines.length) {
    details.append(el("p", "no changes to the body"));
    return details;
  }
  const pre = el("pre");
  pre.className = "diff";
  for (const line of diff.lines) {
    const span = el("span", line.text);
    span.dataset.kind = line.kind;
    pre.append(span);
  }
  details.append(pre);
  return details;
}

function renderHistory(taskId, history) {
  const section = el("section");
  section.id = "history";
  section.append(el("h3", "Version history"));
  if (!history.length) {
    section.append(el("p", "no versions yet"));
    return section;
  }
  const list = el("ol");
  for (const entry of history) {
    const item = el("li");
    item.append(el("p", historySummary(entry)));
    if (entry.diff) item.append(renderDiff(taskId, entry));
    else if (entry.version === null) item.append(el("p", "· no version to compare against"));
    else item.append(el("p", "· first version"));
    list.append(item);
  }
  section.append(list);
  return section;
}

function approvedDetail(record) {
  const parts = [`scope: ${record.scope}`, record.timestamp];
  if (record.supersedes) parts.push(`supersedes ${record.supersedes}`);
  if (record.recorded_by_task) parts.push("recorded by this task");
  return parts.join(" · ");
}

function renderApproved(decisions) {
  const section = el("section");
  section.id = "approved-decisions";
  section.append(el("h4", `Approved decisions (${decisions.approved.length})`));
  if (decisions.unavailable) {
    section.append(alertText(`decisions unavailable: ${decisions.unavailable}`));
  }
  if (!decisions.approved.length) {
    if (!decisions.unavailable) section.append(el("p", "none"));
    return section;
  }
  const list = el("ul");
  for (const record of decisions.approved) {
    const item = el("li");
    item.append(el("p", `${record.id} v${record.version} — ${record.title}`));
    item.append(el("p", approvedDetail(record)));
    const text = el("p", record.decision);
    text.className = "decision-text";
    item.append(text, withCode("file: ", record.path));
    list.append(item);
  }
  section.append(list);
  return section;
}

function renderOpen(items) {
  const section = el("section");
  section.id = "open-decisions";
  section.append(el("h4", `Open decisions (${items.length})`));
  if (!items.length) {
    section.append(el("p", "none"));
    return section;
  }
  const list = el("ul");
  for (const item of items) {
    const entry = el("li");
    entry.append(el("p", `${item.ref} · ${item.area} · ${item.state}: ${item.text}`));
    if (item.recommendation) entry.append(el("p", `recommendation: ${item.recommendation}`));
    if (item.command) entry.append(withCode("decide: ", item.command));
    list.append(entry);
  }
  section.append(list);
  return section;
}

function renderDecisions(decisions) {
  const section = el("section");
  section.id = "decisions";
  section.append(el("h3", "Decisions"));
  section.append(withCode("registry: ", decisions.directory));
  section.append(renderApproved(decisions), renderOpen(decisions.open));
  return section;
}

function renderTask(task) {
  const section = el("section");
  section.id = "task";
  section.append(el("h2", `Task ${task.id}`));
  const title = el("p", task.request.length > 110 ? `${task.request.slice(0, 107)}…` : task.request);
  title.className = "task-purpose";
  title.title = task.request;
  section.append(title);
  if (task.execution) {
    section.append(renderExecution(task.id, task.execution));
    const statusDetails = document.createElement("details");
    statusDetails.className = "execution-detail-panel";
    statusDetails.dataset.viewDisclosure = "task-status-details";
    statusDetails.append(el("summary", "Status and next-step details"), renderStatusSummary(task));
    section.append(statusDetails);
  } else section.append(renderStatusSummary(task));
  if (task.execution && task.approval_center?.final?.blockers?.some(item => item.code === "approved")) {
    const approved = document.createElement("details");
    approved.dataset.viewDisclosure = "approved-specification-record";
    approved.className = "execution-detail-panel";
    approved.append(el("summary", "Approved specification and approval record"), renderApprovalCenter(task));
    section.append(approved);
  } else section.append(renderApprovalCenter(task));
  const list = el("dl");
  const rows = [
    ["Description", task.request],
    ["Phase", task.phase],
    ["Status", task.status],
  ];
  for (const [label, value] of rows) list.append(el("dt", label), el("dd", value));
  const elapsed = el("time", formatElapsed(Date.now() - Date.parse(task.created_at)));
  elapsed.dateTime = task.created_at;
  elapsed.dataset.since = task.created_at;
  const since = el("dd");
  since.append(elapsed);
  list.append(el("dt", "Elapsed"), since);
  list.append(el("dt", "Complexity"), el("dd", `${task.complexity} (${task.complexity_source})`));
  list.append(el("dt", "Specification"), el("dd", specText(task.spec)));
  const directory = el("dd");
  directory.append(el("code", task.spec.directory));
  list.append(el("dt", "Spec directory"), directory);
  list.append(el("dt", "Latest activity"), el("dd", activityText(task.latest_activity)));
  const metadata = document.createElement("details");
  metadata.className = "task-metadata";
  metadata.dataset.viewDisclosure = "task-details";
  metadata.append(el("summary", "Task details"), list);
  section.append(metadata);
  if (task.execution) {
    if (task.execution.delivery) section.append(renderDelivery(task.execution.delivery));
  }
  section.append(renderResume(task.resume));
  section.append(
    renderSpec(task.spec, task.phase),
    renderDecisions(task.decisions),
    renderHistory(task.id, task.spec.history),
  );
  rememberComments(task);
  section.append(renderDiagrams(task.id, task.diagrams));
  section.append(renderComparison(task.id, task.diagrams));
  section.append(renderCommentsSection(task.comments, task.diagrams));
  return section;
}

function renderStatusSummary(task) {
  const summary = el("section");
  summary.id = "current-status";
  summary.className = "status-summary";
  summary.setAttribute("aria-label", "Current status and next step");
  const execution = task.execution;
  const openBlockers = execution ? developerDecisions(execution).filter((item) => item.state !== "Resolved") : [];
  const checks = execution?.readiness?.checks || [];
  const failedChecks = checks.filter((item) => item?.state === "permission_denied");
  const final = task.approval_center?.final;
  const approvedSpec = String(final?.status || "").toLowerCase().includes("approved")
    || task.spec?.versions?.some((version) => version.status === "approved");
  const approvalBlockers = approvedSpec
    ? []
    : (final?.blockers || []).filter((item) => item.code !== "approved");
  const current = el("article");
  current.append(el("h3", "Current status"));
  const states = [];
  if (approvedSpec) states.push("Specification approved");
  if (!approvedSpec) states.push(`${task.status} · ${task.phase}`);
  if (execution && implementationLabel(execution)) states.push(implementationLabel(execution));
  if (execution) states.push(`execution ${execution.status}`);
  current.append(el("p", states.join(" · ")));
  const why = el("article");
  const pendingVerification = execution?.verification?.status === "incomplete";
  const technicalRepair = execution?.implementation?.status === "technically_unavailable";
  why.append(el("h3", openBlockers.length ? (execution?.developer_decisions ? "Developer decision" : "Recorded blocker") : technicalRepair ? "Tool repair" : pendingVerification ? "Checks pending" : "Attention"));
  if (openBlockers.length) {
    for (const blocker of openBlockers) {
      why.append(el("p", `${blocker.id}: ${blocker.question}`));
      if (blocker.recommendation) why.append(el("p", `Recommendation: ${blocker.recommendation}`));
    }
  } else if (failedChecks.length) {
    for (const check of failedChecks) why.append(el("p", `${check.capability}: ${check.message || "operator permission is unavailable"}`));
  } else if (approvalBlockers.length) {
    for (const blocker of approvalBlockers) why.append(el("p", blocker.text));
  } else if (pendingVerification) {
    why.append(el("p", "Some checks or discovery are incomplete. Defined, authorized implementation can continue."));
  } else if (execution?.status_explanation) {
    why.append(el("p", execution.status_explanation));
  } else {
    why.append(el("p", "No open execution blocker is recorded."));
  }
  const next = el("article");
  next.append(el("h3", "Next step"));
  let guidance;
  if (failedChecks.length) {
    guidance = `${execution?.readiness?.message || "Restart the dashboard from a trusted terminal with access to the existing project state."} Continuing a native session does not repair operator permissions.`;
  } else if (openBlockers.length) {
    const blocker = openBlockers[0];
    guidance = blocker.recommendation || `Resolve the open blocker ${blocker.id} before continuing.`;
    if (execution?.native_id) guidance += " Continuing a native session does not resolve this blocker.";
  } else if (approvalBlockers.length) {
    guidance = approvalBlockers[0].text;
  } else if (technicalRepair) {
    const issues = Object.values(execution.operational_issues || {}).filter((issue) => issue.state !== "Resolved");
    guidance = issues[0]?.next_action || "Repair the recorded technical capability and continue authorized work.";
  } else if (pendingVerification && ACTIVE_EXECUTION_STATUSES.has(execution?.status)) {
    guidance = "Implementation continues while agents repair tooling and finish the remaining checks.";
  } else if (execution?.status_explanation) {
    guidance = "Review the recorded result before choosing whether to continue the existing native session.";
  } else {
    guidance = task.next || task.resume?.next || execution?.readiness?.message;
  }
  next.append(el("p", guidance || "Review the specification and recorded task activity."));
  summary.append(current, why, next);
  return summary;
}

function renderNotice(notice) {
  const section = el("section");
  section.id = "notice";
  section.append(el("p", notice));
  return section;
}

function viewIdentity(taskId, execution) {
  return JSON.stringify([taskId, execution?.id ?? null, execution?.al_session_id ?? null,
    execution?.al_run_id ?? null, execution?.epoch_id ?? null, execution?.authority_generation ?? null]);
}

function captureView(root) {
  if (!root) return null;
  return {
    identity: root.dataset.viewIdentity,
    disclosures: new Map([...root.querySelectorAll("details[data-view-disclosure]")]
      .map((node) => [node.dataset.viewDisclosure, node.open])),
    logs: new Map([...root.querySelectorAll("[data-view-log]")]
      .map((node) => [node.dataset.viewLog, { text: node.textContent, top: node.scrollTop }])),
    x: window.scrollX, y: window.scrollY,
  };
}

function restoreView(root, saved) {
  if (!saved || root.dataset.viewIdentity !== saved.identity) return false;
  for (const node of root.querySelectorAll("details[data-view-disclosure]")) {
    if (saved.disclosures.has(node.dataset.viewDisclosure)) node.open = saved.disclosures.get(node.dataset.viewDisclosure);
  }
  for (const node of root.querySelectorAll("[data-view-log]")) {
    const log = saved.logs.get(node.dataset.viewLog);
    if (log) { node.textContent = log.text; node.scrollTop = log.top; }
  }
  return true;
}

function render(data) {
  rendered = true;
  const main = document.getElementById("main");
  const selected = data.task ? data.task.id : null;
  const saved = captureView(document.getElementById("task"));
  const identity = viewIdentity(selected, data.task?.execution);
  if (saved && saved.identity !== identity) approvalPreviews.delete(selected);
  const content = data.task ? renderTask(data.task) : renderNotice(data.notice);
  content.dataset.viewIdentity = identity;
  const preview = content.querySelector("#approve-preview");
  if (preview) preview.dataset.viewDisclosure = JSON.stringify(["approval-preview", data.task.approval_center.final.spec_hash]);
  for (const node of content.querySelectorAll("[data-delivery-disclosure]")) {
    node.dataset.viewDisclosure = JSON.stringify(["delivery", node.dataset.deliveryDisclosure]);
  }
  const same = restoreView(content, saved);
  const replace = () => main.replaceChildren(renderNav(data.tasks, selected), content);
  if (same) {
    withApprovalFocus(() => withCommentFocus(() => withExecutionFocus(() => withDeliveryFocus(replace))));
    window.scrollTo(saved.x, saved.y);
  } else replace();
}

function renderError(message) {
  rendered = false;
  const section = el("section");
  section.id = "error";
  const alert = el("p", `Dashboard data unavailable: ${message}`);
  alert.setAttribute("role", "alert");
  const retry = el("button", "Retry");
  retry.type = "button";
  retry.addEventListener("click", load);
  section.append(alert, retry);
  document.getElementById("main").replaceChildren(section);
}

function setLive(state, text) {
  const live = document.getElementById("live");
  live.dataset.state = state;
  live.textContent = text;
  const main = document.getElementById("main");
  if (state === "stale") main.dataset.stale = "true";
  else delete main.dataset.stale;
}

function connect() {
  if (source) source.close();
  const stream = new EventSource(STREAM + query());
  source = stream;
  stream.onopen = () => {
    attempts = 0;
    setLive("live", "live");
  };
  stream.onmessage = (event) => {
    render(JSON.parse(event.data));
    setLive("live", "live");
  };
  stream.addEventListener("failure", (event) => {
    const { error } = JSON.parse(event.data);
    const kept = rendered ? "; showing the last loaded data" : "";
    if (!rendered) renderError(error);
    setLive("stale", `data unavailable: ${error}${kept}`);
  });
  stream.onerror = () => {
    if (source !== stream) return;
    setLive("stale", "live connection lost; reconnecting…");
    if (stream.readyState === EventSource.CLOSED) {
      const delay = Math.min(RETRY_MS * 2 ** attempts, MAX_RETRY_MS);
      attempts += 1;
      setTimeout(() => {
        if (source === stream) connect();
      }, delay);
    }
  };
}

async function load() {
  try {
    const response = await fetch(API + query(), { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || response.statusText);
    render(data);
  } catch (error) {
    renderError(error.message);
  }
  connect();
}

async function refreshDashboard() {
  try {
    const response = await fetch(API + query(), { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || response.statusText);
    render(data);
  } catch (error) {
    setLive("stale", `data unavailable: ${error.message}; showing the last loaded data`);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  window.addEventListener("hashchange", diagramHashChanged);
  load();
  setInterval(tick, 1000);
});
