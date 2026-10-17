"use strict";

const API = "/api/dashboard";
const STREAM = "/api/stream";
const RETRY_MS = 2000;
const MAX_RETRY_MS = 30000;
const NOTHING_TO_RESUME = "nothing to resume: approved for implementation (handoff is Phase 3)";

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
    item.append(link, el("p", `${t.phase} · ${t.status}`), el("p", t.request));
    if (t.next) item.append(withCode("next: ", t.next));
    list.append(item);
  }
  nav.append(list);
  return nav;
}

function renderPending(items) {
  const section = el("section");
  section.id = "pending";
  section.append(el("h3", "Pending approvals"));
  if (!items.length) {
    section.append(el("p", "none"));
    return section;
  }
  const list = el("ul");
  for (const item of items) {
    const entry = el("li");
    entry.append(el("p", `${item.ref} · ${item.kind}: ${item.title}`));
    entry.append(withCode("decide: ", item.command));
    list.append(entry);
  }
  section.append(list);
  return section;
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

function renderTask(task) {
  const section = el("section");
  section.id = "task";
  section.append(el("h2", `Task ${task.id}`));
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
  section.append(list, renderPending(task.pending_approvals), renderResume(task.resume));
  return section;
}

function renderNotice(notice) {
  const section = el("section");
  section.id = "notice";
  section.append(el("p", notice));
  return section;
}

function render(data) {
  rendered = true;
  const main = document.getElementById("main");
  const selected = data.task ? data.task.id : null;
  const content = data.task ? renderTask(data.task) : renderNotice(data.notice);
  main.replaceChildren(renderNav(data.tasks, selected), content);
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

document.addEventListener("DOMContentLoaded", () => {
  load();
  setInterval(tick, 1000);
});
