"use strict";

const API = "/api/dashboard";

function el(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
}

function specText(spec) {
  const parts = spec.versions.map((v) => `v${v.version} ${v.status}`);
  if (spec.draft) parts.push("draft");
  return parts.length ? parts.join(", ") : "not drafted yet";
}

function renderTask(task) {
  const section = el("section");
  section.id = "task";
  section.append(el("h2", `Task ${task.id}`));
  const list = el("dl");
  const rows = [
    ["Request", task.request],
    ["Phase", task.phase],
    ["Complexity", `${task.complexity} (${task.complexity_source})`],
    ["Specification", specText(task.spec)],
  ];
  for (const [label, value] of rows) {
    list.append(el("dt", label), el("dd", value));
  }
  const directory = el("dd");
  directory.append(el("code", task.spec.directory));
  list.append(el("dt", "Spec directory"), directory);
  section.append(list);
  return section;
}

function renderTasks(data) {
  const section = el("section");
  section.id = "tasks";
  section.append(el("p", data.notice));
  if (!data.tasks.length) {
    section.append(el("p", "no tasks"));
    return section;
  }
  const list = el("ul");
  for (const t of data.tasks) {
    list.append(el("li", `${t.id} · phase ${t.phase} · ${t.request}`));
  }
  section.append(list);
  return section;
}

function render(data) {
  const main = document.getElementById("main");
  main.replaceChildren(data.task ? renderTask(data.task) : renderTasks(data));
}

function renderError(message) {
  const main = document.getElementById("main");
  const alert = el("p", `Dashboard data unavailable: ${message}`);
  alert.setAttribute("role", "alert");
  const retry = el("button", "Retry");
  retry.type = "button";
  retry.addEventListener("click", load);
  main.replaceChildren(alert, retry);
}

async function load() {
  try {
    const response = await fetch(API, { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || response.statusText);
    render(data);
  } catch (error) {
    renderError(error.message);
  }
}

document.addEventListener("DOMContentLoaded", load);
