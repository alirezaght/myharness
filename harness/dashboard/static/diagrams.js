"use strict";

// P2-04: the task's diagrams as SVG, built with createElementNS and textContent only.
// Every element is addressable by its stable id: click, Enter or Space selects it, and so does
// the URL hash "#element=<id>" (optionally "version=<n>&" to show a stored older version).
// Loaded before comments.js and app.js; el() and alertText() come from app.js, the comment
// panel of the selected element from comments.js; they are only called after load.

const SVG_NS = "http://www.w3.org/2000/svg";
const DIAGRAMS_API = "/api/diagrams";
const VERSION_RE = /^[1-9][0-9]*$/;
const OUTDATED = "the design changed since the latest diagram version; the next update records it";

let lastTaskId = null;
let lastDiagrams = null;
let shownVersion = null;
let pinned = null;
let pending = null;
let failed = null;

function svg(tag, attrs, text) {
  const made = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attrs || {})) made.setAttribute(name, String(value));
  if (text !== undefined) made.textContent = text;
  return made;
}

function readHash() {
  const params = new URLSearchParams(window.location.hash.slice(1));
  const version = params.get("version");
  return {
    element: params.get("element") || null,
    version: version && VERSION_RE.test(version) ? Number(version) : null,
  };
}

function writeHash(element, version) {
  const parts = [];
  if (version !== null && version !== undefined) parts.push(`version=${version}`);
  if (element) parts.push(`element=${encodeURIComponent(element)}`);
  const { pathname, search } = window.location;
  history.replaceState(null, "", pathname + search + (parts.length ? `#${parts.join("&")}` : ""));
}

function points(list) {
  return list.map(([x, y]) => `${x},${y}`).join(" ");
}

function centred(item) {
  return svg("text", { x: item.x + item.width / 2, y: item.y + item.height / 2, class: "label" },
    item.label);
}

function shapeOf(item, marker) {
  const group = [];
  if (item.shape === "box") {
    group.push(svg("rect", { x: item.x, y: item.y, width: item.width, height: item.height, rx: 4 }));
    group.push(centred(item));
  } else if (item.shape === "frame") {
    group.push(svg("rect", { x: item.x, y: item.y, width: item.width, height: item.height,
      class: "frame" }));
    group.push(svg("text", { x: item.x + 8, y: item.y + 18, class: "frame-label" }, item.label));
  } else if (item.shape === "lifeline") {
    const cx = item.x + item.width / 2;
    group.push(svg("line", { x1: cx, y1: item.y + item.height, x2: cx, y2: item.bottom,
      class: "lifeline" }));
    group.push(svg("rect", { x: item.x, y: item.y, width: item.width, height: item.height, rx: 4 }));
    group.push(centred(item));
  } else if (item.shape === "arrow") {
    group.push(svg("polyline", { points: points(item.points), class: "hit" }));
    group.push(svg("polyline", { points: points(item.points), class: "edge",
      "marker-end": `url(#${marker})` }));
    if (item.text && item.text_at) {
      group.push(svg("text", { x: item.text_at[0], y: item.text_at[1], class: "edge-label" },
        item.text));
    }
  }
  return group;
}

// options (P2-06 comparison): idAttr names the id attribute, marks maps id -> added, removed
// or changed, onSelect replaces select(). Without options the markup is unchanged.
function renderItem(item, marker, selected, options = {}) {
  const undeclared = item.detail && item.detail.declared === false;
  const marks = options.marks || {};
  const change = Object.hasOwn(marks, item.id) ? marks[item.id] : null;
  const classes = ["diagram-element", undeclared && "undeclared", change && `change-${change}`];
  const group = svg("g", {
    class: classes.filter(Boolean).join(" "),
    [options.idAttr || "data-element-id"]: item.id,
    "data-kind": item.kind,
    tabindex: 0,
    role: "button",
    "aria-label": `${item.kind} ${item.label}${change ? ` (${change})` : ""}`,
    "aria-pressed": item.id === selected ? "true" : "false",
  });
  if (change) {
    group.setAttribute("data-change", change);
    group.append(svg("title", {}, `${change}: ${item.kind} ${item.label}`));
  }
  group.append(...shapeOf(item, marker));
  const choose = options.onSelect || select;
  group.addEventListener("click", () => choose(item.id));
  group.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      choose(item.id);
    }
  });
  return group;
}

function renderScene(scene, selected, options = {}) {
  const figure = el("figure");
  figure.className = "diagram";
  figure.dataset.diagram = scene.type;
  figure.append(el("figcaption", scene.title));
  const canvas = el("div");
  canvas.className = "diagram-canvas";
  const marker = `${options.markerPrefix || ""}arrow-${scene.type}`;
  const drawing = svg("svg", {
    viewBox: `0 0 ${scene.width} ${scene.height}`,
    width: scene.width,
    height: scene.height,
    role: "group",
    "aria-label": scene.title,
  });
  const defs = svg("defs");
  const head = svg("marker", { id: marker, viewBox: "0 0 10 10", refX: 10, refY: 5,
    markerWidth: 8, markerHeight: 8, orient: "auto-start-reverse" });
  if (options.marks) {
    // Marked edges are thicker; a fixed-size head keeps arrowheads from growing with them.
    head.setAttribute("markerUnits", "userSpaceOnUse");
    head.setAttribute("markerWidth", 10);
    head.setAttribute("markerHeight", 10);
  }
  head.append(svg("path", { d: "M 0 0 L 10 5 L 0 10 z", class: "arrowhead" }));
  defs.append(head);
  drawing.append(defs);
  for (const item of scene.items) drawing.append(renderItem(item, marker, selected, options));
  canvas.append(drawing);
  figure.append(canvas);
  return figure;
}

function detailText(value) {
  if (Array.isArray(value)) return value.length ? value.join("; ") : "none";
  return String(value);
}

function fillSelection(panel, view, element) {
  panel.replaceChildren(el("h4", "Selected element"));
  if (!element) {
    panel.append(el("p", "select an element to see its details"));
    return;
  }
  const items = view ? view.diagrams.flatMap((scene) => scene.items) : [];
  const item = items.find((candidate) => candidate.id === element);
  if (!item) {
    const where = view ? `version ${view.version}` : "the shown version";
    panel.append(el("p", `element ${element} is not in ${where}`));
    return;
  }
  const list = el("dl");
  const rows = [["id", item.id], ["kind", item.kind], ["label", item.label],
    ["version", `version ${view.version}`], ["sources", detailText(item.sources)]];
  for (const [name, value] of Object.entries(item.detail || {})) rows.push([name, detailText(value)]);
  for (const [name, value] of rows) list.append(el("dt", name), el("dd", value));
  panel.append(list);
  const cur = lastDiagrams && lastDiagrams.current;
  const writable = !!cur && view.version === cur.version && !lastDiagrams.outdated
    && !lastDiagrams.unavailable;
  panel.append(renderElementComments(view, item, writable));
}

function currentView() {
  const hash = readHash();
  const cur = lastDiagrams ? lastDiagrams.current : null;
  const wanted = hash.version !== null ? hash.version : shownVersion;
  if (wanted === null || !cur || wanted === cur.version) return { view: cur, wanted: null };
  const key = `${lastTaskId} ${wanted}`;
  if (pinned && pinned.key === key) return { view: pinned.data, wanted };
  if (failed && failed.key === key) return { view: cur, wanted, error: failed.message };
  if (pending !== key) fetchVersion(lastTaskId, wanted, key);
  return { view: null, wanted, loading: true };
}

async function fetchVersion(taskId, version, key) {
  pending = key;
  let outcome;
  try {
    const url = `${DIAGRAMS_API}?task=${encodeURIComponent(taskId)}&version=${version}`;
    const response = await fetch(url, { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || response.statusText);
    outcome = { pinned: { key, data } };
  } catch (error) {
    outcome = { failed: { key, message: error.message } };
  }
  if (taskId !== lastTaskId || pending !== key) return;
  pending = null;
  if (outcome.pinned) pinned = outcome.pinned;
  else failed = outcome.failed;
  rerenderDiagrams();
}

function rerenderDiagrams() {
  const section = document.getElementById("diagrams");
  if (!section || !lastDiagrams) return;
  withCommentFocus(() => section.replaceWith(renderDiagrams(lastTaskId, lastDiagrams)));
}

function showVersion(version) {
  const hash = readHash();
  shownVersion = version;
  if (version === null) pinned = null;
  writeHash(hash.element, version);
  rerenderDiagrams();
}

function renderHistoryList(diagrams, displayed) {
  const section = el("section");
  section.id = "diagram-history";
  section.append(el("h4", "Diagram versions"));
  if (!diagrams.history.length) {
    section.append(el("p", "no versions yet"));
    return section;
  }
  const list = el("ol");
  for (const entry of diagrams.history) {
    const item = el("li");
    const types = entry.types.length ? entry.types.join(", ") : "no diagrams";
    item.append(el("span", `version ${entry.version} · ${entry.created_at} · ${types}`));
    if (entry.version === displayed) {
      item.setAttribute("aria-current", "true");
    } else {
      const show = el("button", "show");
      show.type = "button";
      show.addEventListener("click", () => showVersion(entry.current ? null : entry.version));
      item.append(" ", show);
    }
    list.append(item);
  }
  section.append(list);
  return section;
}

function renderDiagrams(taskId, diagrams) {
  if (taskId !== lastTaskId) {
    shownVersion = null;
    pinned = null;
    pending = null;
    failed = null;
  }
  lastTaskId = taskId;
  lastDiagrams = diagrams;
  const section = el("section");
  section.id = "diagrams";
  section.append(el("h3", "Diagrams"));
  if (diagrams.state) {
    const state = el("p", `Design state: ${diagrams.state.replaceAll("_", " ")}`);
    state.className = "diagram-provenance";
    section.append(state);
  }
  if (diagrams.provenance) section.append(el("p", diagrams.provenance));
  if (diagrams.state === "prose_only") {
    section.append(el("p", "The design contains prose but no structured notation. Diagram data does not wait for AL planning."));
  }
  if (diagrams.proposals?.length) {
    const proposals = el("section");
    proposals.className = "diagram-proposals";
    proposals.append(el("h4", `Pending diagram proposals (${diagrams.proposals.length})`));
    proposals.append(el("p", "These proposals are not accepted design versions and cannot be selected as current diagrams."));
    const list = el("ul");
    for (const proposal of diagrams.proposals) {
      const item = el("li");
      const kinds = proposal.diagrams.map((diagram) => `${diagram.type}: ${diagram.title}`).join(", ") || "notation needs review";
      item.append(el("p", `${proposal.ref} · pending acceptance · ${kinds}`));
      if (proposal.issues?.length) item.append(el("p", `${proposal.issues.length} notation issue(s) need review.`));
      list.append(item);
    }
    proposals.append(list);
    section.append(proposals);
  }
  if (diagrams.guidance) {
    const guidance = el("p", diagrams.guidance);
    guidance.className = "diagram-guidance";
    section.append(guidance);
  }
  if (diagrams.unavailable) section.append(alertText(`diagrams unavailable: ${diagrams.unavailable}`));
  if (diagrams.outdated) {
    section.append(el("p", OUTDATED));
  }
  if (diagrams.issues.length) {
    const list = el("ul");
    list.className = "diagram-issues";
    for (const issue of diagrams.issues) {
      const item = el("li", `${issue.ref} line ${issue.line}: ${issue.problem} — `);
      item.append(el("code", issue.text));
      list.append(item);
    }
    section.append(list);
  }
  const hash = readHash();
  const { view, wanted, loading, error } = currentView();
  if (error) section.append(alertText(`version ${wanted} could not be loaded: ${error}`));
  if (loading) section.append(el("p", `loading version ${wanted}…`));
  if (!view || !view.diagrams.length) {
    if (!loading) {
      section.append(el("p", diagrams.notice || "no diagrams yet"));
      if (diagrams.hint) section.append(el("p", diagrams.hint));
    }
  }
  const cur = diagrams.current;
  if (view) {
    const isCurrent = cur !== null && view.version === cur.version;
    const status = el("p", `showing: version ${view.version} (${isCurrent ? "current" : "not current"})`);
    status.className = "diagram-status";
    if (!isCurrent) {
      const back = el("button", "show current");
      back.type = "button";
      back.addEventListener("click", () => showVersion(null));
      status.append(" ", back);
    }
    section.append(status);
    section.dataset.version = String(view.version);
    for (const scene of view.diagrams) section.append(renderScene(scene, hash.element));
  }
  const panel = el("div");
  panel.id = "diagram-selection";
  panel.setAttribute("aria-live", "polite");
  fillSelection(panel, view, hash.element);
  section.append(panel, renderHistoryList(diagrams, view ? view.version : null));
  return section;
}

function refreshSelection(element) {
  for (const group of document.querySelectorAll("#diagrams [data-element-id]")) {
    const pressed = group.getAttribute("data-element-id") === element;
    group.setAttribute("aria-pressed", pressed ? "true" : "false");
  }
  const panel = document.getElementById("diagram-selection");
  if (panel) fillSelection(panel, currentView().view, element);
}

function select(element) {
  const hash = readHash();
  writeHash(element, hash.version !== null ? hash.version : shownVersion);
  refreshSelection(element);
}

function diagramHashChanged() {
  const hash = readHash();
  const section = document.getElementById("diagrams");
  const shown = section ? section.dataset.version : undefined;
  const displayed = shown ? Number(shown) : null;
  const cur = lastDiagrams && lastDiagrams.current ? lastDiagrams.current.version : null;
  const wanted = hash.version !== null ? hash.version : cur;
  if (wanted !== displayed) {
    shownVersion = hash.version;
    rerenderDiagrams();
  } else {
    refreshSelection(hash.element);
  }
}
