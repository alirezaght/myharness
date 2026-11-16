"use strict";

let knowledgeGraphSequence = 0;

function kEl(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderKnowledgePanel(taskId) {
  const section = kEl("section");
  section.id = "knowledge";
  section.className = "knowledge-panel";
  section.dataset.task = taskId;
  section.append(kEl("h3", "Project knowledge graph"));
  section.append(kEl("p", "Sourced context for this task’s investigated repository. Relationships are knowledge observations, not approved architecture or execution authority."));
  const status = kEl("div");
  status.className = "knowledge-status";
  status.setAttribute("role", "status");
  status.append(kEl("p", "Loading index status…"));
  section.append(status);
  const refresh = kEl("button", "Refresh project knowledge");
  refresh.type = "button";
  refresh.className = "knowledge-refresh";
  refresh.addEventListener("click", async () => {
    await refreshKnowledge(section, taskId);
  });
  section.append(refresh);
  const form = document.createElement("form");
  form.className = "knowledge-query-form";
  const input = document.createElement("input");
  input.type = "search";
  input.maxLength = 4096;
  input.setAttribute("aria-label", "Search project knowledge");
  input.placeholder = "Search components, decisions, or relationships";
  const submit = kEl("button", "Search");
  submit.type = "submit";
  form.append(input, submit);
  const results = kEl("div");
  results.className = "knowledge-results";
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!input.value.trim()) return;
    results.replaceChildren(kEl("p", "Searching the current snapshot…"));
    const result = await postJson("/api/knowledge/query", { task: taskId, query: input.value });
    if (!result.res?.ok) {
      results.replaceChildren(kEl("p", `Query failed: ${result.data.error || result.netErr || result.res?.statusText || "No response"}`));
      return;
    }
    renderKnowledgeResults(results, taskId, result.data);
  });
  section.append(form, results);
  loadKnowledgeStatus(section, taskId);
  return section;
}

async function loadKnowledgeStatus(section, taskId) {
  const status = section.querySelector(".knowledge-status");
  const refresh = section.querySelector(".knowledge-refresh");
  try {
    const params = new URLSearchParams({ task: taskId });
    const response = await fetch(`/api/knowledge?${params}`, { cache: "no-store" });
    const data = await response.json();
    if (!section.isConnected) return;
    if (!response.ok) throw new Error(data.error || response.statusText);
    renderKnowledgeStatus(status, data);
    return data;
  } catch (error) {
    if (!section.isConnected) return;
    status.replaceChildren(kEl("p", `Index status unavailable: ${error.message}`));
    return null;
  }
}

function renderCoverage(container, label, coverage) {
  if (!coverage?.length) return;
  const details = document.createElement("details");
  details.className = "knowledge-coverage";
  details.append(kEl("summary", `${label} (${coverage.length})`));
  for (const item of coverage.slice(0, 30)) {
    const line = typeof item === "string" ? item : [
      item.status || "coverage note",
      item.reason,
      item.locator || item.path,
      item.message,
      item.limit ? `limit ${item.limit} bytes` : null,
      item.observed_bytes ? `observed ${item.observed_bytes} bytes` : null,
      item.read_limit && item.read_limit !== item.limit ? `read limit ${item.read_limit} bytes` : null,
    ].filter(Boolean).join(" · ");
    details.append(kEl("p", line));
  }
  if (coverage.length > 30) details.append(kEl("p", `${coverage.length - 30} additional coverage items omitted.`));
  container.append(details);
}

function renderKnowledgeStatus(container, data) {
  const hasSnapshot = Boolean(data.active_snapshot_id);
  const failed = data.latest_refresh?.status === "incomplete";
  let summary;
  if (!hasSnapshot && failed) summary = "No knowledge snapshot yet. Refresh incomplete.";
  else if (!hasSnapshot) summary = "No knowledge snapshot yet. Refresh to build one from eligible repository sources.";
  else if (failed) summary = "Showing the last complete snapshot. Latest refresh incomplete.";
  else if (data.latest_refresh?.status === "published") summary = "Index ready — latest refresh published.";
  else summary = "A complete knowledge snapshot is available; latest refresh status is unknown.";
  container.replaceChildren(kEl("p", summary));
  if (hasSnapshot) {
    container.append(kEl("p", `Snapshot ${data.active_snapshot_id} · ${data.source_count} sources · ${data.entity_count} entities · ${data.relationship_count} observations`));
    renderCoverage(container, "Coverage of last complete snapshot", data.snapshot_coverage);
  }
  if (data.latest_refresh) {
    const latest = data.latest_refresh;
    container.append(kEl("p", `Latest refresh: ${latest.status}${latest.created_at ? ` · ${latest.created_at}` : ""}`));
    renderCoverage(container, "Latest refresh capture diagnostics", latest.coverage);
  }
}

async function refreshKnowledge(section, taskId) {
  const status = section.querySelector(".knowledge-status");
  const refresh = section.querySelector(".knowledge-refresh");
  if (!refresh || refresh.disabled) return;
  refresh.disabled = true;
  status.replaceChildren(kEl("p", "Refreshing eligible sources…"));
  try {
    const result = await postJson("/api/knowledge/refresh", { task: taskId });
    if (!result.res?.ok) {
      status.replaceChildren(kEl("p", `Refresh failed: ${result.data.error || result.netErr || result.res?.statusText || "No response"}`));
      return;
    }
    if (result.data.status === "incomplete") {
      const message = kEl("p", "Refresh incomplete. The previous complete snapshot, if any, remains active.");
      message.className = "knowledge-refresh-incomplete";
      status.replaceChildren(message);
      renderCoverage(status, "This refresh capture diagnostics", result.data.coverage);
    } else if (result.data.status === "published") {
      status.replaceChildren(kEl("p", "Refresh published a complete snapshot."));
    } else {
      status.replaceChildren(kEl("p", `Refresh returned status ${String(result.data.status || "unknown")}.`));
    }
    const refreshed = await loadKnowledgeStatus(section, taskId);
    if (!refreshed && result.data.status === "incomplete") {
      const retained = kEl("p", "The refresh was incomplete; its capture diagnostics are retained below. The status reload also failed.");
      retained.className = "knowledge-refresh-incomplete";
      status.prepend(retained);
      renderCoverage(status, "This refresh capture diagnostics", result.data.coverage);
    }
  } catch (error) {
    status.replaceChildren(kEl("p", `Refresh failed: ${error.message}`));
    try { await loadKnowledgeStatus(section, taskId); } catch (_reloadError) { /* Status already carries the refresh error. */ }
  } finally {
    refresh.disabled = false;
  }
}

function renderKnowledgeResults(container, taskId, bundle) {
  container.replaceChildren();
  container.append(kEl("p", `Bundle ${bundle.bundle_hash} · snapshot ${bundle.snapshot_id}`));
  container.append(kEl("p", `Evidence budget: ${bundle.budget.serialized_bytes}/${bundle.budget.max_bytes} bytes · ${bundle.assertions.length}/${bundle.budget.max_assertions} observations${bundle.truncation ? " · results truncated" : ""}`));
  const notice = kEl("p", bundle.notice || "Context grants no execution or approval authority.");
  notice.className = "knowledge-notice";
  container.append(notice);
  if (!bundle.assertions.length) {
    container.append(kEl("p", "No matching observations in this snapshot. No result does not prove that a dependency or decision is absent."));
    return;
  }
  if (bundle.coverage?.length) {
    const details = document.createElement("details");
    details.append(kEl("summary", "Index coverage and limitations"));
    for (const gap of bundle.coverage.slice(0, 20)) details.append(kEl("p", typeof gap === "string" ? gap : JSON.stringify(gap)));
    container.append(details);
  }
  if (bundle.conflicts?.length) container.append(kEl("p", `${bundle.conflicts.length} sourced value conflict(s) are reported in this result.`));
  if (bundle.staleness?.length) container.append(kEl("p", `${bundle.staleness.length} observation(s) were withheld or marked stale.`));
  const graph = renderKnowledgeGraph(bundle);
  container.append(graph);
  const selection = kEl("p", "Select a graph node to focus its cited observations.");
  selection.className = "knowledge-graph-selection";
  selection.setAttribute("aria-live", "polite");
  container.append(selection);
  for (const assertion of bundle.assertions) {
    const article = kEl("article");
    article.className = "knowledge-assertion";
    article.id = `knowledge-evidence-${assertion.assertion_id}`;
    article.tabIndex = -1;
    article.append(kEl("h4", `${assertion.subject} · ${assertion.predicate}`));
    article.append(kEl("p", assertion.object ? `Related to ${assertion.object}` : assertion.text));
    article.append(kEl("p", `${assertion.verification} · ${assertion.origin}${assertion.historical ? " · historical" : ""}${assertion.stale ? " · stale" : ""}`));
    const explain = kEl("button", "Explain evidence");
    explain.type = "button";
    const evidence = kEl("div");
    explain.addEventListener("click", async () => {
      explain.disabled = true;
      evidence.replaceChildren(kEl("p", "Checking cited source evidence…"));
      const result = await postJson("/api/knowledge/explain", {
        task: taskId, bundle_hash: bundle.bundle_hash, assertion_id: assertion.assertion_id,
      });
      explain.disabled = false;
      if (!result.res?.ok) {
        evidence.replaceChildren(kEl("p", `Evidence unavailable: ${result.data.error || result.netErr || result.res?.statusText || "No response"}`));
        return;
      }
      evidence.replaceChildren();
      evidence.append(kEl("p", result.data.authority));
      for (const citation of result.data.citations || []) {
        const row = kEl("div");
        row.append(kEl("p", `${citation.locator} · bytes ${citation.start_byte}–${citation.end_byte} · sha256 ${citation.span_sha256}`));
        const sourceButton = kEl("button", "View cited source text");
        sourceButton.type = "button";
        const source = kEl("pre");
        source.className = "knowledge-source-text";
        sourceButton.addEventListener("click", async () => {
          sourceButton.disabled = true;
          source.textContent = "Revalidating cited source…";
          const params = new URLSearchParams({ task: taskId, bundle_hash: bundle.bundle_hash,
            assertion_id: assertion.assertion_id });
          try {
            const response = await fetch(`/api/knowledge/evidence?${params}`, { cache: "no-store" });
            const data = await response.json();
            source.textContent = response.ok
              ? (data.citations || []).map(item => `${item.locator}\n${item.text}${item.truncated ? "\n[excerpt truncated at 4096 bytes]" : ""}`).join("\n\n")
              : `Evidence unavailable: ${data.error || response.statusText}`;
          } catch (error) { source.textContent = `Evidence unavailable: ${error.message}`; }
          sourceButton.disabled = false;
        });
        row.append(sourceButton, source);
        evidence.append(row);
      }
      if (!result.data.citations?.length) evidence.append(kEl("p", "No citation was returned."));
    });
    article.append(explain, evidence);
    container.append(article);
  }
  graph.querySelectorAll("[data-knowledge-node]").forEach(node => {
    const activate = () => {
      const label = node.dataset.knowledgeNode;
      const related = bundle.assertions.filter(item => item.subject === label || item.object === label);
      selection.textContent = `${label}: ${related.length} returned observation(s) link to this node.`;
      const article = related.length && document.getElementById(`knowledge-evidence-${related[0].assertion_id}`);
      if (article) {
        article.focus({ preventScroll: true });
        article.scrollIntoView({ block: "nearest" });
      }
    };
    node.addEventListener("click", activate);
    node.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activate();
      }
    });
  });
}

function renderKnowledgeGraph(bundle) {
  const section = kEl("section");
  section.className = "knowledge-graph";
  section.setAttribute("aria-label", "Selected knowledge relationship graph");
  section.append(kEl("h4", "Selected relationships"));
  section.append(kEl("p", "Sourced knowledge observations only; this is separate from approved architecture and AL execution diagrams."));
  const all = bundle.assertions || [];
  const edges = all.filter(item => item.object && item.subject).slice(0, 16);
  const labels = [];
  for (const edge of edges) {
    for (const label of [edge.subject, edge.object]) {
      if (!labels.includes(label) && labels.length < 12) labels.push(label);
    }
  }
  if (!labels.length) {
    for (const item of all.slice(0, 12)) if (!labels.includes(item.subject)) labels.push(item.subject);
  }
  if (!labels.length) {
    section.append(kEl("p", "No relationship nodes were selected within the bounded result."));
    return section;
  }
  const nodeIndex = new Map(labels.map((label, index) => [label, index]));
  const graphEdges = edges.filter(edge => nodeIndex.has(edge.subject) && nodeIndex.has(edge.object));
  const rows = Math.ceil(labels.length / 3);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("knowledge-graph-svg");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `Knowledge graph with ${labels.length} nodes and ${graphEdges.length} sourced relationships`);
  svg.setAttribute("viewBox", `0 0 850 ${Math.max(170, rows * 145 + 20)}`);
  svg.setAttribute("width", "850");
  svg.setAttribute("height", String(Math.max(170, rows * 145 + 20)));
  const title = document.createElementNS(svg.namespaceURI, "title");
  title.textContent = "Bounded sourced knowledge graph";
  svg.append(title);
  const defs = document.createElementNS(svg.namespaceURI, "defs");
  const marker = document.createElementNS(svg.namespaceURI, "marker");
  marker.setAttribute("id", `knowledge-arrow-${++knowledgeGraphSequence}`);
  marker.setAttribute("markerWidth", "8");
  marker.setAttribute("markerHeight", "8");
  marker.setAttribute("refX", "7");
  marker.setAttribute("refY", "4");
  marker.setAttribute("orient", "auto");
  const arrowHead = document.createElementNS(svg.namespaceURI, "path");
  arrowHead.setAttribute("d", "M0,0 L8,4 L0,8 z");
  arrowHead.setAttribute("class", "knowledge-arrowhead");
  marker.append(arrowHead);
  defs.append(marker);
  svg.append(defs);
  const markerId = marker.id;
  for (const edge of graphEdges) {
    const sourceIndex = nodeIndex.get(edge.subject);
    const targetIndex = nodeIndex.get(edge.object);
    const source = { x: 35 + (sourceIndex % 3) * 275 + 200, y: 35 + Math.floor(sourceIndex / 3) * 145 + 42 };
    const target = { x: 35 + (targetIndex % 3) * 275, y: 35 + Math.floor(targetIndex / 3) * 145 + 42 };
    const line = document.createElementNS(svg.namespaceURI, "line");
    line.setAttribute("x1", String(source.x));
    line.setAttribute("y1", String(source.y));
    line.setAttribute("x2", String(target.x));
    line.setAttribute("y2", String(target.y));
    line.setAttribute("marker-end", `url(#${markerId})`);
    line.setAttribute("class", "knowledge-arrow");
    const edgeTitle = document.createElementNS(svg.namespaceURI, "title");
    edgeTitle.textContent = `${edge.predicate} · ${edge.verification} · ${edge.origin}${edge.historical ? " · historical" : ""}`;
    line.append(edgeTitle);
    svg.append(line);
  }
  labels.forEach((label, index) => {
    const group = document.createElementNS(svg.namespaceURI, "g");
    group.classList.add("knowledge-node");
    group.setAttribute("tabindex", "0");
    group.setAttribute("role", "button");
    group.setAttribute("aria-label", `${label}. Select to focus supporting evidence.`);
    group.dataset.knowledgeNode = label;
    const x = 35 + (index % 3) * 275;
    const y = 35 + Math.floor(index / 3) * 145;
    const rect = document.createElementNS(svg.namespaceURI, "rect");
    rect.setAttribute("x", String(x));
    rect.setAttribute("y", String(y));
    rect.setAttribute("width", "200");
    rect.setAttribute("height", "84");
    rect.setAttribute("rx", "10");
    group.append(rect);
    const fullTitle = document.createElementNS(svg.namespaceURI, "title");
    fullTitle.textContent = label;
    group.append(fullTitle);
    const text = document.createElementNS(svg.namespaceURI, "text");
    text.setAttribute("x", String(x + 12));
    text.setAttribute("y", String(y + 32));
    text.textContent = label.length > 25 ? `${label.slice(0, 22)}…` : label;
    group.append(text);
    const trust = all.filter(item => item.subject === label || item.object === label)
      .slice(0, 3).map(item => `${item.verification}${item.historical ? " · historical" : ""}`);
    const state = document.createElementNS(svg.namespaceURI, "text");
    state.setAttribute("x", String(x + 12));
    state.setAttribute("y", String(y + 57));
    state.classList.add("knowledge-node-state");
    state.textContent = trust.join(" / ") || "no returned assertion";
    group.append(state);
    svg.append(group);
  });
  const scroll = kEl("div");
  scroll.className = "knowledge-graph-scroll";
  scroll.append(svg);
  section.append(scroll);
  if (all.filter(item => item.object && item.subject).length > graphEdges.length || labels.length >= 12) {
    section.append(kEl("p", "Graph view is bounded to 12 nodes and 16 returned relationships; the evidence list retains the full returned bundle."));
  }
  return section;
}
