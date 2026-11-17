"use strict";

function workspaceSvg(tag, attributes = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  return node;
}

function workspaceEdgeGeometry(from, to) {
  const dx = to.x - from.x, dy = to.y - from.y;
  const fraction = Math.min(85 / Math.max(1, Math.abs(dx)), 32 / Math.max(1, Math.abs(dy)), .45);
  return { x1: from.x + 85 + dx * fraction, y1: from.y + 32 + dy * fraction,
    x2: to.x + 85 - dx * fraction, y2: to.y + 32 - dy * fraction };
}

function workspaceEdgeCurve(from, to, offset = 0) {
  if (from.x === to.x && from.y === to.y) {
    const control = { x: from.x + 230 + offset, y: from.y - 85 - offset };
    return {
      d: `M ${from.x + 100} ${from.y} Q ${control.x} ${control.y} ${from.x + 170} ${from.y + 25}`,
      labelX: from.x + 180 + offset / 2,
      labelY: from.y - 16 - offset / 2,
      bounds: { minX: from.x + 100, minY: control.y, maxX: control.x, maxY: from.y + 25 },
    };
  }
  const points = workspaceEdgeGeometry(from, to);
  const dx = points.x2 - points.x1, dy = points.y2 - points.y1;
  const length = Math.max(1, Math.hypot(dx, dy));
  const control = {
    x: (points.x1 + points.x2) / 2 - dy / length * offset,
    y: (points.y1 + points.y2) / 2 + dx / length * offset,
  };
  return {
    d: `M ${points.x1} ${points.y1} Q ${control.x} ${control.y} ${points.x2} ${points.y2}`,
    labelX: (points.x1 + 2 * control.x + points.x2) / 4,
    labelY: (points.y1 + 2 * control.y + points.y2) / 4 - 7,
    bounds: { minX: Math.min(points.x1, points.x2, control.x), minY: Math.min(points.y1, points.y2, control.y),
      maxX: Math.max(points.x1, points.x2, control.x), maxY: Math.max(points.y1, points.y2, control.y) },
  };
}

function workspaceEdgeOffset(edge, edges) {
  const parallel = edges.filter(item => item.from === edge.from && item.to === edge.to)
    .sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  const index = parallel.findIndex(item => item.id === edge.id);
  if (edge.from === edge.to) return index * 64;
  const reverse = edges.some(item => item.from === edge.to && item.to === edge.from);
  return reverse ? 34 + index * 64 : (index - (parallel.length - 1) / 2) * 64;
}

function workspaceDiagram(task, state, inspector) {
  const panel = el("section");
  panel.className = "workspace-diagram-panel";
  panel.append(el("h2", "Design diagram"), el("p", "Select a component or connection. Drag to arrange; double-click a component to edit its label. All edits are proposals."));
  const graph = workspaceResult(state).graph;
  if (!graph.nodes.length) panel.append(el("p", "No structured diagram notation recorded. Design prose is available in Plan; existing notation guidance appears below."));
  else {
    const select = workspaceChoice("Select component or connection", [...graph.nodes, ...graph.edges.map(edge => ({ ...edge, label: `${edge.from} → ${edge.to}: ${edge.label}` }))], state.selection, "diagram-selection", state);
    select.input.disabled = false;
    select.input.addEventListener("change", () => { workspaceSelect(state, select.input.value); workspaceRerender(); });
    panel.append(select.wrap);
    const canvas = el("div");
    canvas.className = "workspace-canvas";
    canvas.dataset.workspaceScroll = "diagram";
    const nodeById = new Map(graph.nodes.map(node => [node.id, node]));
    const routes = graph.edges.filter(edge => nodeById.has(edge.from) && nodeById.has(edge.to))
      .map(edge => workspaceEdgeCurve(nodeById.get(edge.from), nodeById.get(edge.to), workspaceEdgeOffset(edge, graph.edges)));
    const minX = Math.min(0, ...graph.nodes.map(node => node.x - 50), ...routes.map(route => route.bounds.minX - 30));
    const minY = Math.min(0, ...graph.nodes.map(node => node.y - 50), ...routes.map(route => route.bounds.minY - 30));
    const width = Math.max(760, ...graph.nodes.map(node => node.x + 230), ...routes.map(route => route.bounds.maxX + 30)) - minX;
    const height = Math.max(330, ...graph.nodes.map(node => node.y + 130), ...routes.map(route => route.bounds.maxY + 30)) - minY;
    const svg = workspaceSvg("svg", { role: "group", "aria-label": "Proposed design diagram", viewBox: `${minX} ${minY} ${width} ${height}`, width, height });
    const draw = () => {
      svg.replaceChildren();
      const defs = workspaceSvg("defs");
      const marker = workspaceSvg("marker", { id: "workspace-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: "auto-start-reverse" });
      marker.append(workspaceSvg("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "context-stroke" }));
      defs.append(marker); svg.append(defs);
      const nodes = new Map(graph.nodes.map(node => [node.id, node]));
      for (const edge of graph.edges) {
        const from = nodes.get(edge.from), to = nodes.get(edge.to);
        if (!from || !to) continue;
        const group = workspaceSvg("g", { tabindex: 0, role: "button", "aria-label": `Connection ${edge.label || edge.id}`, "data-selected": state.selection === edge.id, "data-edge-id": edge.id });
        group.classList.add("workspace-edge");
        if (state.operations.some(op => op.target === edge.id)) group.classList.add("proposed");
        const curve = workspaceEdgeCurve(from, to, workspaceEdgeOffset(edge, graph.edges));
        group.append(workspaceSvg("path", { d: curve.d, "marker-end": "url(#workspace-arrow)" }));
        const label = workspaceSvg("text", { x: curve.labelX, y: curve.labelY });
        label.textContent = edge.label || "→";
        group.append(label);
        const choose = () => { workspaceSelect(state, edge.id); workspaceRerender(); };
        group.addEventListener("click", choose);
        group.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); choose(); } });
        svg.append(group);
      }
      for (const node of graph.nodes) {
        const group = workspaceSvg("g", { tabindex: 0, role: "button", "aria-label": `Component ${node.label}`, transform: `translate(${node.x} ${node.y})`, "data-node-id": node.id, "data-selected": state.selection === node.id });
        group.classList.add("workspace-node");
        if (state.operations.some(op => op.target === node.id)) group.classList.add("proposed");
        group.append(workspaceSvg("rect", { width: 170, height: 64, rx: 10 }));
        const label = workspaceSvg("text", { x: 85, y: 35 });
        label.textContent = node.label.length > 22 ? `${node.label.slice(0, 21)}…` : node.label;
        const title = workspaceSvg("title"); title.textContent = node.label;
        group.append(label, title);
        if (state.inlineNode === node.id) {
          const editor = workspaceSvg("foreignObject", { x: 5, y: 12, width: 160, height: 45 });
          const field = workspaceField("Edit component label", node.label, `inline-label:${node.id}`, state);
          field.input.setAttribute("aria-label", "Edit component label on diagram");
          field.input.maxLength = 500;
          field.input.className = "workspace-inline-label";
          field.input.addEventListener("click", event => event.stopPropagation());
          field.input.addEventListener("dblclick", event => event.stopPropagation());
          field.input.addEventListener("pointerdown", event => event.stopPropagation());
          field.input.addEventListener("keydown", event => {
            event.stopPropagation();
            if (event.key === "Enter") {
              event.preventDefault();
              if (state.stale || state.saving) return;
              state.inlineNode = null;
              workspaceEdit(state, { type: "node_label", target: node.id, label: field.input.value });
            } else if (event.key === "Escape") {
              event.preventDefault(); state.inlineNode = null;
              delete state.fields[`inline-label:${node.id}`]; workspacePersist(state); workspaceRerender();
            }
          });
          editor.append(field.input); group.append(editor);
        }
        const choose = (focusLabel = false) => {
          if (!group.isConnected) return;
          workspaceSelect(state, node.id);
          if (focusLabel) {
            state.inlineNode = node.id;
            workspaceRerender();
            document.querySelector('[data-workspace-focus^="inline-label:"]')?.focus();
          } else {
            for (const item of svg.querySelectorAll("[data-node-id], [data-edge-id]")) item.dataset.selected = String((item.dataset.nodeId || item.dataset.edgeId) === node.id);
            select.input.value = node.id;
            inspector.replaceChildren();
            workspaceDiagramInspector(state, graph, node, inspector);
          }
        };
        group.addEventListener("click", () => choose());
        group.addEventListener("dblclick", () => choose(true));
        group.addEventListener("keydown", event => {
          if (event.key === "Enter" || event.key === "F2") { event.preventDefault(); choose(true); }
          const offsets = { ArrowLeft: [-20, 0], ArrowRight: [20, 0], ArrowUp: [0, -20], ArrowDown: [0, 20] };
          if (offsets[event.key]) {
            event.preventDefault();
            const [x, y] = offsets[event.key];
            workspaceSelect(state, node.id);
            workspaceEdit(state, { type: "node_position", target: node.id, x: node.x + x, y: node.y + y });
          }
        });
        group.addEventListener("pointerdown", event => {
          if (state.stale || state.saving || event.button !== 0) return;
          workspaceDragging = true;
          const start = { x: event.clientX, y: event.clientY, nx: node.x, ny: node.y };
          let moved = false;
          group.setPointerCapture(event.pointerId);
          const move = e => {
            const scale = svg.viewBox.baseVal.width / svg.getBoundingClientRect().width;
            if (Math.abs(e.clientX - start.x) + Math.abs(e.clientY - start.y) < 4) return;
            moved = true;
            node.x = Math.max(-10000, Math.min(10000, start.nx + (e.clientX - start.x) * scale));
            node.y = Math.max(-10000, Math.min(10000, start.ny + (e.clientY - start.y) * scale));
            group.setAttribute("transform", `translate(${node.x} ${node.y})`);
            for (const edgeGroup of svg.querySelectorAll("[data-edge-id]")) {
              const edge = graph.edges.find(item => item.id === edgeGroup.dataset.edgeId);
              const a = nodes.get(edge.from), b = nodes.get(edge.to);
              const curve = workspaceEdgeCurve(a, b, workspaceEdgeOffset(edge, graph.edges));
              edgeGroup.querySelector("path").setAttribute("d", curve.d);
              const text = edgeGroup.querySelector("text");
              text.setAttribute("x", curve.labelX); text.setAttribute("y", curve.labelY);
            }
          };
          const finish = e => {
            workspaceDragging = false;
            group.removeEventListener("pointermove", move);
            group.removeEventListener("pointerup", finish);
            group.removeEventListener("pointercancel", cancel);
            if (moved) {
              state.selection = node.id;
              workspaceEdit(state, { type: "node_position", target: node.id, x: node.x, y: node.y });
            }
            if (workspaceRenderPending) {
              workspaceSelect(state, node.id);
              workspaceRerender();
            }
          };
          const cancel = () => {
            const pending = workspaceRenderPending;
            node.x = start.nx; node.y = start.ny; moved = false;
            finish();
            if (!pending) workspaceRerender();
          };
          group.addEventListener("pointermove", move);
          group.addEventListener("pointerup", finish);
          group.addEventListener("pointercancel", cancel);
        });
        svg.append(group);
      }
    };
    draw();
    canvas.append(svg);
    panel.append(canvas);
    const selected = graph.nodes.find(node => node.id === state.selection) || graph.edges.find(edge => edge.id === state.selection) || graph.nodes[0];
    if (selected) workspaceDiagramInspector(state, graph, selected, inspector);
  }
  const notation = el("details");
  notation.dataset.viewDisclosure = "workspace-notation";
    const legacyHash = location.hash.slice(1);
    const legacyParams = new URLSearchParams(legacyHash);
    notation.open = legacyHash === "diagrams" || legacyHash === "diagram-compare"
      || legacyParams.has("version") || legacyParams.has("element");
  notation.append(workspaceSummary(state, notation, "Source notation, diagram history and execution evidence"), renderDiagrams(task.id, task.diagrams), renderComparison(task.id, task.diagrams));
  panel.append(notation);
  rememberComments(task);
  const comments = el("details");
  comments.dataset.viewDisclosure = "workspace-comments";
    comments.open = legacyHash === "comments";
  comments.append(workspaceSummary(state, comments, "Diagram comments"), renderCommentsSection(task.comments, task.diagrams));
  panel.append(comments);
  return panel;
}

function workspaceDiagramInspector(state, graph, selected, inspector) {
  const isNode = "x" in selected;
  inspector.append(el("small", isNode ? "Component inspector" : "Connection inspector"), el("h2", selected.label || selected.id), el("code", selected.id),
    el("h3", "Design sources"), el("p", selected.sources.join(", ") || "Proposed connection; no approved design source"));
  if (isNode) {
    const label = workspaceField("Component label", selected.label, `node-label:${selected.id}`, state);
    label.input.maxLength = 500;
    const commit = () => {
      if (state.stale || state.saving) return;
      workspaceEdit(state, { type: "node_label", target: selected.id, label: label.input.value });
    };
    label.input.addEventListener("keydown", event => {
      if (event.key === "Enter") { event.preventDefault(); commit(); }
      if (event.key === "Escape") { delete state.fields[`node-label:${selected.id}`]; workspacePersist(state); workspaceRerender(); }
    });
    const x = workspaceField("Horizontal position", selected.x, `node-x:${selected.id}`, state);
    const y = workspaceField("Vertical position", selected.y, `node-y:${selected.id}`, state);
    for (const field of [x, y]) { field.input.type = "number"; field.input.min = -10000; field.input.max = 10000; field.input.step = "any"; }
    inspector.append(label.wrap, workspaceActionButton(state, selected.id, "node-label", "Propose label", commit), x.wrap, y.wrap, workspaceActionButton(state, selected.id, "node-position", "Propose position", () => {
      workspaceEdit(state, { type: "node_position", target: selected.id, x: Number(x.input.value), y: Number(y.input.value) });
    }), workspaceTraceability(state, selected.id));
  }
  inspector.append(el("h3", isNode ? "Add a connection" : "Edit connection"));
  const from = workspaceChoice("From component", graph.nodes, isNode ? selected.id : selected.from, `edge-from:${selected.id}`, state);
  const to = workspaceChoice("To component", graph.nodes, isNode ? graph.nodes.find(node => node.id !== selected.id)?.id : selected.to, `edge-to:${selected.id}`, state);
  const label = workspaceField("Connection label", isNode ? "" : selected.label, `edge-label:${selected.id}`, state);
  label.input.maxLength = 500;
  inspector.append(from.wrap, to.wrap, label.wrap, workspaceActionButton(state, selected.id, isNode ? "connection-create" : "connection-change", isNode ? "Propose connection" : "Propose connection change", () => {
    workspaceEdit(state, { type: isNode ? "connection_add" : selected.proposed ? "connection_add" : "connection_change",
      target: isNode ? `proposed:${crypto.randomUUID()}` : selected.id, from: from.input.value, to: to.input.value, label: label.input.value }, selected.id);
  }));
  if (!isNode) inspector.append(workspaceActionButton(state, selected.id, "connection-remove", "Remove connection in proposal", () => {
    if (state.stale || state.saving) return;
    if (selected.proposed) {
      state.operations = state.operations.filter(op => op.target !== selected.id);
      state.status = "Unsaved"; state.retry = null; workspacePersist(state); workspaceRerender();
    } else workspaceEdit(state, { type: "connection_remove", target: selected.id });
  }));
  const links = workspaceResult(state).proposed_links.filter(link => link.target === selected.id);
  if (links.length) inspector.append(el("p", `Proposed links: ${links.map(link => link.requirement).join(", ")}`));
}
