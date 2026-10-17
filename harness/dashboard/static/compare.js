"use strict";

// P2-06: two diagram versions side by side with added, removed and changed elements marked
// (text and line pattern, not colour alone). The pair defaults to the version before the
// current one against the current one and follows a new current version until the developer
// picks a pair; a picked pair stays while both versions exist. Stored versions are immutable,
// so a loaded comparison is cached. Built with createElement(NS) and textContent only.
// Loaded after diagrams.js, comments.js and comment_actions.js, before app.js.

const COMPARE_API = "/api/diagrams/compare";
const CHANGE_KINDS = ["removed", "changed", "added"];
const LEGEND = { added: "added: solid outline", removed: "removed: dashed outline",
  changed: "changed: dotted outline" };
const compareCache = new Map();
const compareState = { taskId: null, diagrams: null, from: null, to: null, chosen: false,
  loading: null, failure: null };

function compareSelect(id, label, versions, value, disabledWhen, onPick) {
  const box = el("span");
  const caption = el("label", label);
  caption.htmlFor = id;
  const picker = el("select");
  picker.id = id;
  for (const version of versions) {
    const option = el("option", `version ${version}`);
    option.value = String(version);
    option.disabled = disabledWhen(version);
    picker.append(option);
  }
  picker.value = String(value);
  picker.addEventListener("change", () => onPick(Number(picker.value)));
  box.append(caption, " ", picker);
  return box;
}

function pickPair(from, to) {
  compareState.from = from;
  compareState.to = to;
  compareState.chosen = true;
  rerenderComparison();
}

function renderComparison(taskId, diagrams) {
  if (taskId !== compareState.taskId) {
    Object.assign(compareState, { taskId, from: null, to: null, chosen: false, loading: null,
      failure: null });
  }
  compareState.diagrams = diagrams;
  const section = el("section");
  section.id = "diagram-compare";
  section.append(el("h3", "Compare versions"));
  const versions = diagrams.history.map((entry) => entry.version);
  if (versions.length < 2) {
    section.append(el("p", "no earlier version to compare with"));
    return section;
  }
  const { from, to, chosen } = compareState;
  const valid = chosen && versions.includes(from) && versions.includes(to) && from < to;
  if (!valid) {
    Object.assign(compareState, { from: versions[versions.length - 2],
      to: versions[versions.length - 1], chosen: false });
  }
  section.dataset.from = String(compareState.from);
  section.dataset.to = String(compareState.to);
  const controls = el("div");
  controls.className = "compare-controls";
  controls.append(
    compareSelect("compare-from", "From", versions, compareState.from,
      (v) => v >= compareState.to, (v) => pickPair(v, compareState.to)),
    compareSelect("compare-to", "To", versions, compareState.to,
      (v) => v <= compareState.from, (v) => pickPair(compareState.from, v)),
  );
  section.append(controls);
  const key = `${taskId} ${compareState.from} ${compareState.to}`;
  const cached = compareCache.get(key);
  const failure = compareState.failure;
  if (failure && failure.key === key) {
    section.append(alertText(`comparison could not be loaded: ${failure.message}`));
    const retry = el("button", "Retry");
    retry.type = "button";
    retry.id = "compare-retry";
    retry.addEventListener("click", () => {
      compareState.failure = null;
      rerenderComparison();
    });
    section.append(retry);
  } else if (!cached) {
    if (compareState.loading !== key) {
      fetchComparison(taskId, compareState.from, compareState.to, key);
    }
    section.append(el("p", `loading versions ${compareState.from} and ${compareState.to}…`));
  } else {
    section.append(...renderCompareResult(cached, diagrams));
  }
  return section;
}

async function fetchComparison(taskId, from, to, key) {
  compareState.loading = key;
  let outcome;
  try {
    const url = `${COMPARE_API}?task=${encodeURIComponent(taskId)}&from=${from}&to=${to}`;
    const response = await fetch(url, { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || response.statusText);
    outcome = { data };
  } catch (error) {
    outcome = { failure: { key, message: error.message } };
  }
  if (taskId !== compareState.taskId || compareState.loading !== key) return;
  compareState.loading = null;
  if (outcome.data) compareCache.set(key, outcome.data);
  else compareState.failure = outcome.failure;
  rerenderComparison();
}

function compareLegend() {
  const legend = el("ul");
  legend.className = "compare-legend";
  for (const change of ["added", "removed", "changed"]) {
    const item = el("li");
    const swatch = el("span");
    swatch.className = `legend-swatch change-${change}`;
    item.append(swatch, LEGEND[change]);
    legend.append(item);
  }
  return legend;
}

function comparePane(side, meta, marks, title) {
  const pane = el("div");
  pane.className = "compare-pane";
  pane.dataset.side = side;
  pane.append(el("h4", title));
  if (!meta.diagrams.length) pane.append(el("p", `no diagrams in version ${meta.version}`));
  for (const scene of meta.diagrams) {
    pane.append(renderScene(scene, null, { markerPrefix: `cmp-${side}-`,
      idAttr: "data-compare-element", marks, onSelect: (id) => openInDiagrams(meta.version, id) }));
  }
  return pane;
}

function changeHref(version, id, cur) {
  const element = `element=${encodeURIComponent(id)}`;
  return version === cur ? `#${element}` : `#version=${version}&${element}`;
}

function renderCompareResult(data, diagrams) {
  const cur = diagrams.current ? diagrams.current.version : null;
  const a = data.from.version;
  const b = data.to.version;
  const grid = el("div");
  grid.className = "compare-grid";
  grid.append(
    comparePane("from", data.from, data.marks.from, `version ${a} (previous)`),
    comparePane("to", data.to, data.marks.to,
      `version ${b} (${b === cur ? "current" : "not current"})`),
  );
  const counts = data.counts;
  const heading = el("h4", `Changes (removed ${counts.removed} · changed ${counts.changed}`
    + ` · added ${counts.added})`);
  const parts = [compareLegend(), grid, heading];
  if (!data.changes.length) {
    parts.push(el("p", `no diagram changes between version ${a} and ${b}`));
    return parts;
  }
  const list = el("ol");
  list.className = "compare-changes";
  for (const change of data.changes) {
    if (!CHANGE_KINDS.includes(change.change)) continue;
    const item = el("li");
    item.dataset.change = change.change;
    const version = change.change === "removed" ? a : b;
    const link = el("a", `${change.change}: ${change.kind} ${change.label} (${change.id})`);
    link.href = changeHref(version, change.id, cur);
    item.append(link);
    list.append(item);
  }
  parts.push(list);
  return parts;
}

function openInDiagrams(version, id) {
  const cur = lastDiagrams && lastDiagrams.current ? lastDiagrams.current.version : null;
  writeHash(id, version === cur ? null : version);
  diagramHashChanged();
  const section = document.getElementById("diagrams");
  if (section) section.scrollIntoView({ block: "start" });
}

function rerenderComparison() {
  const section = document.getElementById("diagram-compare");
  if (!section || !compareState.diagrams) return;
  withCommentFocus(() => section.replaceWith(renderComparison(compareState.taskId,
    compareState.diagrams)));
}

function showComparison(from, to) {
  pickPair(from, to);
  const section = document.getElementById("diagram-compare");
  if (section) section.scrollIntoView({ block: "start" });
}
