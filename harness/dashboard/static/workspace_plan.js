"use strict";

function workspaceButton(label, action, className = "") {
  const button = el("button", label);
  button.type = "button";
  button.className = className;
  button.addEventListener("click", action);
  return button;
}

function workspaceFocusKey(task, target, action) {
  return JSON.stringify(["workspace", task, target, action]);
}

function workspaceActionButton(state, target, action, label, handler) {
  const button = workspaceButton(label, handler);
  button.dataset.workspaceFocus = workspaceFocusKey(state.task, target, action);
  return button;
}

function workspaceSummary(state, details, label) {
  const summary = el("summary", label);
  summary.dataset.workspaceFocus = workspaceFocusKey(state.task, details.dataset.viewDisclosure, "disclosure");
  return summary;
}

function workspaceField(label, value, key, state, multiline = false) {
  const wrap = el("label", label);
  const input = el(multiline ? "textarea" : "input");
  input.dataset.workspaceFocus = key;
  input.dataset.workspaceScroll = JSON.stringify(["workspace-editor", state.task, key]);
  input.value = state.fields[key] ?? value ?? "";
  input.disabled = state.saving;
  input.readOnly = state.stale;
  input.addEventListener("input", () => { state.fields[key] = input.value; workspacePersist(state); workspaceUpdateEditorStatus(state); });
  wrap.append(input);
  return { wrap, input };
}

function workspaceChoice(label, values, value, key, state) {
  const wrap = el("label", label);
  const select = el("select");
  select.dataset.workspaceFocus = key;
  for (const row of values) {
    const option = el("option", row.label || row.id || row.key);
    option.value = row.id || row.key;
    select.append(option);
  }
  select.value = state.fields[key] ?? value ?? values[0]?.id ?? values[0]?.key ?? "";
  select.disabled = state.saving || state.stale;
  select.addEventListener("change", () => {
    if (key === "diagram-selection") return;
    state.fields[key] = select.value; workspacePersist(state); workspaceUpdateEditorStatus(state);
  });
  wrap.append(select);
  return { wrap, input: select };
}

function workspaceRefinement(task, state, row, type) {
  const inspector = el("div");
  const kind = row.kind === "request" ? "Original request" : (row.kind || "Design entry").replaceAll("_", " ");
  const status = (row.status || "Recorded").replaceAll("_", " ");
  inspector.append(el("small", `${kind} · ${status}`), el("h2", row.label), el("p", row.text));
  const provenance = typeof row.provenance === "string" ? row.provenance : JSON.stringify(row.provenance);
  const source = el("details");
  source.dataset.viewDisclosure = JSON.stringify(["workspace-source-record", task.id, type, row.key]);
  source.append(workspaceSummary(state, source, "Source record details"), el("p", `Source: ${provenance}`), el("code", row.key), el("code", row.hash));
  if (row.quote) source.append(el("p", `Source quote: ${row.quote}`));
  source.append(el("p", `Requirement links: ${row.links.length ? row.links.join(", ") : "No explicit requirement link recorded."}`));
  inspector.append(source);
  const field = workspaceField("Proposed refinement", workspaceResult(state)[type === "plan" ? "plan" : "requirements"].find(item => item.key === row.key)?.text,
    `refine:${row.key}`, state, true);
  field.input.maxLength = 16000;
  inspector.append(field.wrap, workspaceActionButton(state, row.key, `refine:${type}`, "Add refinement to proposal", () => {
    workspaceEdit(state, { type, target: row.key, text: field.input.value });
  }));
  inspector.append(el("p", "This proposes a revision. The source statement and approval remain unchanged."));
  if (type === "plan") inspector.append(workspaceTraceability(state, row.key));
  return inspector;
}

function workspaceTraceability(state, target) {
  const box = el("div");
  const choice = workspaceChoice("Proposed requirement link", state.source.requirements, workspaceResult(state).proposed_links.find(link => link.target === target)?.requirement || "request", `link:${target}`, state);
  box.append(choice.wrap, workspaceActionButton(state, target, "traceability", "Propose link", () => workspaceEdit(state,
    { type: "traceability", target, requirement: choice.input.value })));
  return box;
}

function workspaceRequirements(task, state, inspector) {
  const panel = el("section");
  panel.append(el("h2", "Understand the requirements"), el("p", "Exact source statements, acceptance criteria and unresolved questions."));
  const rows = state.source.requirements;
  for (const row of rows) {
    const button = workspaceButton(row.label, () => { workspaceSelect(state, row.key); workspaceRerender(); }, "workspace-source-card");
    button.dataset.workspaceFocus = JSON.stringify(["workspace-source-card", task.id, row.kind, row.key]);
    button.dataset.selected = String(state.selection === row.key);
    button.append(el("small", `${row.key} · ${row.kind} · ${row.status}`), el("p", row.text));
    panel.append(button);
  }
  const selected = rows.find(row => row.key === state.selection) || rows[0];
  if (selected) inspector.append(workspaceRefinement(task, state, selected, "requirement"));
  const unresolved = el("section");
  unresolved.append(el("h3", "Unresolved questions"));
  if (!state.source.unresolved.length) unresolved.append(el("p", "No unresolved interview items recorded."));
  for (const item of state.source.unresolved) {
    const question = el("article");
    question.className = "workspace-question";
    question.append(el("p", item.text), el("small", item.state.replaceAll("_", " ")));
    const details = el("details");
    details.dataset.viewDisclosure = JSON.stringify(["workspace-question-record", task.id, item.ref, item.topic]);
    details.append(workspaceSummary(state, details, "Question record details"), el("code", item.ref));
    question.append(details);
    unresolved.append(question);
  }
  panel.append(unresolved);
  const spec = el("details");
  spec.dataset.viewDisclosure = "workspace-source-spec";
  spec.open = workspaceLegacyRoute(location.hash)?.tab === "requirements";
  spec.append(workspaceSummary(state, spec, "Specification source"), renderSpec(task.spec, task.phase));
  panel.append(spec);
  if (!rows.some(row => row.kind === "criteria")) panel.append(el("p", "No separately identified acceptance criteria recorded in accepted context. Any specification acceptance text remains visible in its source above."));
  return panel;
}

function workspacePlan(task, state, inspector) {
  const panel = el("section");
  panel.append(el("h2", "Harness design plan"), el("p", "Recorded design sections and responsibilities. These entries do not define AL execution order or assigned agents."));
  const effective = state.source.plan.filter(row => row.effective);
  if (!effective.length) panel.append(el("p", "No effective design entries recorded yet."));
  for (const row of effective) {
    const button = workspaceButton(`${row.key} · ${row.label}`, () => { workspaceSelect(state, row.key); workspaceRerender(); }, "workspace-source-card");
    button.dataset.workspaceFocus = JSON.stringify(["workspace-source-card", task.id, row.kind, row.key]);
    button.dataset.selected = String(state.selection === row.key);
    button.append(el("small", `${row.status} · ${row.provenance}`), el("p", row.text));
    panel.append(button);
  }
  const selected = effective.find(row => row.key === state.selection) || effective[0];
  if (selected) inspector.append(workspaceRefinement(task, state, selected, "plan"));
  const pending = state.source.plan.filter(row => !row.effective);
  if (pending.length) {
    panel.append(el("h3", "Pending and other design records"));
    for (const row of pending) panel.append(el("p", `${row.key} · ${row.status}: ${row.text}`));
  }
  panel.append(el("h3", "Recorded AL implementation plan"),
    el("p", "Runtime task records show the implementation work that AL actually registered. They are execution evidence; design refinements above remain proposals."));
  if (task.execution?.tasks?.length) panel.append(renderGraph(task.execution));
  else panel.append(el("p", "No AL implementation tasks have been recorded for this task."));
  panel.append(el("h3", "AL planning records"));
  const reports = (task.execution?.reports || []).filter(row => /architect|plan/i.test(`${row.role} ${row.name}`));
  if (!reports.length) panel.append(el("p", "No AL planning report references available in recorded execution state."));
  for (const report of reports) panel.append(el("p", `${report.name} · ${report.role}${report.ref ? ` · ${report.ref}` : ""}`));
  return panel;
}

function workspaceReview(task, state, inspector) {
  const panel = el("section");
  panel.append(el("h2", "Review proposed revisions"), el("p", "Saving records proposals only. Review grants no approval or execution authority."));
  inspector.append(el("h2", "Proposal boundary"), el("p", "The stored base snapshot remains attached to each batch. Changed source or runtime identity makes older batches stale."));
  const base = el("details");
  base.dataset.viewDisclosure = JSON.stringify(["workspace-proposal-base", state.task, state.base]);
  base.append(workspaceSummary(state, base, "Proposal base identity"), el("code", state.base));
  inspector.append(base);
  const show = (title, source, operations, result) => {
    const section = el("article");
    section.className = "workspace-review-batch";
    section.append(el("h3", title));
    for (const operation of operations) {
      const before = workspaceBefore(source, operation);
      const row = el("div");
      row.className = "workspace-review-edit";
      row.append(el("h4", `${workspaceOperationTitle(operation.type)} · ${workspaceTargetLabel(source, operation)}`),
        el("small", `Source identity: ${operation.target}`), el("small", "Before"), el("pre", before),
        el("small", "Proposed result"), el("pre", workspaceAfter(source, operation)));
      section.append(row);
    }
    if (!operations.length) section.append(el("p", "No local edits yet."));
    panel.append(section);
  };
  show("Current proposal overlay", state.source, state.operations, workspaceResult(state));
  for (const proposal of [...task.workspace.proposals].reverse()) {
    show(`Saved ${proposal.created_at}${proposal.stale ? " · STALE — source changed" : ""}`, proposal.source, proposal.operations, proposal.result);
  }
  return panel;
}

function workspaceBefore(source, operation) {
  if (operation.type === "connection_add") return "No connection in base";
  if (operation.type === "traceability") return "No explicit association added by this proposal in base";
  const rows = operation.type === "requirement" ? source.requirements : operation.type === "plan" ? source.plan : operation.type.startsWith("node_") ? source.graph.nodes : source.graph.edges;
  const row = rows.find(item => (item.key || item.id) === operation.target);
  if (!row) return "Absent";
  if (operation.type === "node_label") return row.label;
  if (operation.type === "node_position") return `Horizontal ${row.x}, vertical ${row.y}`;
  if (row.from && row.to) return `${workspaceNodeLabel(source, row.from)} → ${workspaceNodeLabel(source, row.to)} · ${row.label || "unlabeled"}`;
  return row.text || row.label || "Absent";
}

function workspaceAfter(source, operation) {
  if (operation.type === "connection_remove") return "Connection removed in proposal";
  if (operation.type === "connection_add" || operation.type === "connection_change") {
    return `${workspaceNodeLabel(source, operation.from)} → ${workspaceNodeLabel(source, operation.to)} · ${operation.label || "unlabeled"}`;
  }
  if (operation.type === "node_label") return operation.label;
  if (operation.type === "node_position") return `Horizontal ${operation.x}, vertical ${operation.y}`;
  if (operation.type === "traceability") return `Proposed link to ${workspaceTargetLabel(source, { target: operation.requirement })}`;
  return operation.text;
}

function workspaceNodeLabel(source, id) {
  return source.graph.nodes.find(node => node.id === id)?.label || id;
}

function workspaceTargetLabel(source, operation) {
  const row = [...source.requirements, ...source.plan, ...source.graph.nodes, ...source.graph.edges]
    .find(item => (item.key || item.id) === operation.target);
  if (!row) return operation.target;
  if (row.from && row.to) return `${workspaceNodeLabel(source, row.from)} → ${workspaceNodeLabel(source, row.to)}`;
  return row.label || row.text?.split("\n", 1)[0] || row.key || row.id;
}

function workspaceOperationTitle(type) {
  return ({ requirement: "Requirement refinement", plan: "Plan refinement", node_label: "Component label",
    node_position: "Component position", connection_add: "Connection added", connection_change: "Connection changed",
    connection_remove: "Connection removed", traceability: "Proposed requirement link" })[type] || type;
}
