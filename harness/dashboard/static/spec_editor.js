"use strict";

function authoringMarkdown(text) {
  const article = el("article"); article.className = "authoring-preview";
  let paragraph = [];
  const flush = () => { if (paragraph.length) { article.append(el("p", paragraph.join(" "))); paragraph = []; } };
  for (const line of String(text || "").split(/\r?\n/)) {
    if (!line.trim()) { flush(); continue; }
    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) { flush(); article.append(el(`h${heading[1].length}`, heading[2])); continue; }
    const list = /^\s*[-*+]\s+(.+)$/.exec(line);
    if (list) {
      flush();
      let ul = article.lastElementChild?.tagName === "UL" ? article.lastElementChild : null;
      if (!ul) { ul = el("ul"); article.append(ul); }
      ul.append(el("li", list[1])); continue;
    }
    paragraph.push(line);
  }
  flush();
  return article;
}

function workspaceSpecPanel(task, state, inspector) {
  const authoring = state.authoring || task.authoring || {};
  const working = authoring.working;
  const key = "spec:working";
  const initial = working?.body ?? authoring.effective_body ?? "";
  const buffer = authoringBuffer(state, key, initial, authoring.base);
  const panel = el("section"); panel.className = "spec-workspace";
  const head = el("div"); head.className = "spec-workspace-head";
  head.append(el("h2", "Complete specification"));
  head.append(el("p", "Edit the full working body here. Saving keeps a separate draft. Adoption and approval happen in explicit guarded steps."));
  panel.append(head);
  if (!authoring.initialized) panel.append(workspaceStartOrResume(task, state));
  const editor = authoringFocus(el("textarea"), state, key); editor.className = "spec-editor"; editor.rows = 24;
  editor.setAttribute("aria-label", "Full working specification"); editor.spellcheck = true;
  editor.value = buffer.value;
  editor.addEventListener("input", () => {
    buffer.value = editor.value; buffer.dirty = editor.value !== initial;
    buffer.generation = Number(buffer.generation || 0) + 1;
    authoringPersistBuffer(state, key, buffer);
    if (save) save.disabled = !buffer.dirty || !(buffer.base || authoring.base);
    workspaceSpecStatus(state, buffer, panel);
    preview.replaceChildren(el("h3", "Rendered working preview"), authoringMarkdown(buffer.value));
  });
  const toolbar = el("div"); toolbar.className = "spec-toolbar";
  const save = workspaceButton("Save working specification", async () => {
    save.disabled = true;
    const submitted = buffer.value;
    const submittedGeneration = Number(buffer.generation || 0);
    const requestKey = "save-spec:working";
    state.authoringRequestForms ||= {};
    state.authoringRequestForms[requestKey] = { key, value: submitted, generation: submittedGeneration, base: buffer.base || authoring.base };
    workspacePersist(state);
    try {
      await authoringAction(task, state, "save-spec", { body: submitted }, {
        expected: buffer.base || authoring.base, key: requestKey,
      });
      if (state.authoringConflict?.key === requestKey) state.authoringConflict = null;
      if (buffer.value === submitted && Number(buffer.generation || 0) === submittedGeneration) {
        buffer.dirty = false;
        buffer.value = state.authoring.working?.body ?? submitted;
        buffer.base = state.authoring.base;
      } else {
        buffer.dirty = true;
      }
      authoringPersistBuffer(state, key, buffer);
      state.authoringMessage = { text: "Working specification saved. The approved version and active run remain unchanged." };
    } catch (error) {
      if (error.status === 409) {
        state.authoringConflict = { key: requestKey, bufferKey: key, message: error.message };
        workspacePersist(state);
      }
      state.authoringMessage = { text: `${error.message.replace(/[.\s]+$/, "")}. Your text and original base are retained.`, error: true };
    }
    workspaceRerender();
  });
  // Manual specification editing is available for existing tasks even before
  // an interview workspace/model is initialized. A current base still guards
  // the save against task changes.
  save.disabled = !buffer.dirty || !(buffer.base || authoring.base);
  toolbar.append(save);
  const changedBase = buffer.base && state.authoring?.base && JSON.stringify(buffer.base) !== JSON.stringify(state.authoring.base);
  const rebase = workspaceButton("Keep my text and save against the current revision", () => {
    buffer.base = state.authoring.base; buffer.dirty = true;
    if (state.authoringRequests) delete state.authoringRequests["save-spec:working"];
    authoringResolveConflict(state, "save-spec:working");
    state.authoringMessage = { text: "Your text now targets the current revision. Save it to submit a new request." };
    authoringPersistBuffer(state, key, buffer); workspaceRerender();
  });
  if (changedBase) toolbar.append(rebase);
  const savedRequest = state.authoringRequests?.["save-spec:working"];
  if (savedRequest) {
    const retry = workspaceButton("Retry exact saved request", async () => {
      try {
        const submitted = savedRequest.body;
        const submittedGeneration = Number(buffer.generation || 0);
        await authoringRetry(task, state, "save-spec", "save-spec:working");
        if (state.authoringConflict?.key === "save-spec:working") state.authoringConflict = null;
        if (buffer.value === submitted && Number(buffer.generation || 0) === submittedGeneration) {
          buffer.dirty = false; buffer.value = state.authoring.working?.body ?? submitted;
          buffer.base = state.authoring.base;
        }
        authoringPersistBuffer(state, key, buffer);
        state.authoringMessage = { text: "Exact specification save result confirmed." };
      } catch (error) { state.authoringMessage = { text: `${error.message} Your text is retained.`, error: true }; }
      workspaceRerender();
    });
    toolbar.append(retry);
    if (buffer.value !== savedRequest.body) toolbar.append(workspaceButton("Save current text as a new revision", () => {
      delete state.authoringRequests["save-spec:working"];
      authoringResolveConflict(state, "save-spec:working");
      state.authoringMessage = { text: "Your text now targets the current revision. Save it to submit a new request." };
      buffer.base = state.authoring.base; buffer.dirty = true; authoringPersistBuffer(state, key, buffer); workspaceRerender();
    }));
  }
  if (!state.storageAvailable) toolbar.append(el("span", state.storageError || "Browser storage unavailable; save to Harness before leaving."));
  panel.append(toolbar);
  workspaceSpecStatus(state, buffer, panel);
  panel.append(editor);
  const preview = el("section"); preview.className = "spec-preview";
  preview.append(el("h3", "Rendered working preview"), authoringMarkdown(buffer.value));
  panel.append(preview);
  if (authoring.findings?.length) {
    const findings = el("section"); findings.className = "authoring-findings"; findings.append(el("h3", "Validation and reconciliation findings"));
    for (const finding of authoring.findings) findings.append(el("p", finding));
    panel.append(findings);
  }
  const message = authoringMessage(state); if (message) panel.append(message);
  const approved = authoring.approved;
  const adopted = authoring.adopted;
  const execution = authoring.execution;
  inspector.append(el("h2", "Specification versions"));
  inspector.append(el("p", adopted ? `Adopted revision ${adopted.id} · ${adopted.body_hash}` : "No authoring revision adopted. The current effective spec is still generated from existing task state."));
  if (approved) {
    inspector.append(el("h3", `Approved version ${approved.version}`), authoringMarkdown(approved.body), el("small", `Sealed hash ${approved.hash}`));
  } else inspector.append(el("p", "No approved snapshot recorded."));
  if (execution) inspector.append(el("p", `Execution ${execution.id} · ${execution.status} · bound to approval ${execution.approval_id ?? "not reported"}`));
  if (working?.obligations?.length) {
    inspector.append(el("h3", "Open revision obligations"));
    for (const obligation of working.obligations) inspector.append(el("p", obligation));
  }
  return panel;
}

function workspaceSpecStatus(state, buffer, panel) {
  const status = el("p"); status.className = "spec-draft-status"; status.setAttribute("role", "status");
  if (!buffer.base) status.textContent = "Draft is local until the interview workspace is started.";
  else if (JSON.stringify(buffer.base) !== JSON.stringify(state.authoring?.base))
    status.textContent = "The server revision changed since this editor loaded. Your text and original base are preserved; review the current saved version before rebasing.";
  else status.textContent = buffer.dirty ? "Unsaved local edits · saved body remains available in Harness." : "Working body is saved in Harness.";
  const existing = panel.querySelector(".spec-draft-status");
  if (existing) existing.replaceWith(status); else panel.append(status);
}

function workspaceRenderCandidateInspection(target, current, revision) {
  target.replaceChildren(el("h3", "Candidate " + revision.attribution.replaceAll("_", " ")),
    el("p", "Specification " + revision.body_hash + " · graph " + (revision.graph_id || "none")),
    el("h4", "Current adopted/effective specification"), el("pre", current),
    el("h4", "Selected candidate specification"), el("pre", revision.body));
  const graph = el("details"); graph.append(el("summary", "Candidate logical diagram"));
  if (revision.graph?.value) {
    graph.append(el("p", revision.graph.value.nodes.length + " proposed nodes · "
      + revision.graph.value.edges.length + " proposed relationships"));
    for (const node of revision.graph.value.nodes) graph.append(el("p",
      node.kind + " · " + node.status + " · " + node.label + " · sources: " + node.sources.join(", ")));
    for (const edge of revision.graph.value.edges) graph.append(el("p",
      edge.from + " → " + edge.to + " · " + edge.label + " · " + edge.status));
  } else graph.append(el("p", "No concrete graph attached to this revision."));
  target.append(graph);
}

function workspaceReconciliation(task, state) {
  const authoring = state.authoring || task.authoring || {};
  const identity = JSON.stringify([state.projectId, task.id]);
  const section = el("section"); section.className = "authoring-reconcile";
  section.append(el("h2", "Review a specification and diagram candidate"),
    el("p", "Candidates stay separate from your saved working text. Adoption applies the exact selected revision and requires a fresh review before approval."));
  const candidates = new Map();
  if (authoring.working) candidates.set(authoring.working.id, { id: authoring.working.id, attribution: authoring.working.attribution, body_hash: authoring.working.body_hash });
  for (const revision of authoring.revisions || []) if (revision.attribution === "model_proposal" || revision.attribution === "developer") candidates.set(revision.id, revision);
  const select = el("select"); select.setAttribute("aria-label", "Candidate revision");
  for (const revision of [...candidates.values()].reverse()) {
    const option = el("option", `${revision.attribution.replaceAll("_", " ")} · ${revision.id.slice(0, 8)}${revision.id === authoring.adopted?.id ? " · adopted" : ""}`);
    option.value = revision.id; select.append(option);
  }
  const savedChoice = state.candidateChoice;
  const selectedCandidate = savedChoice?.identity === identity && candidates.has(savedChoice.id)
    ? savedChoice.id : authoring.working?.id || candidates.keys().next().value || "";
  state.candidateChoice = { identity, id: selectedCandidate };
  select.value = selectedCandidate;
  const inspect = el("button", "Load candidate diff"); inspect.type = "button";
  const compare = el("div"); compare.className = "candidate-diff";
  const previewBox = el("div"); previewBox.className = "reconcile-preview";
  const current = authoring.adopted?.body ?? authoring.effective_body ?? "";
  const inspection = state.candidateInspection;
  state.selectedRevision = null;
  if (inspection?.identity === identity && inspection.id === select.value
      && candidates.has(inspection.id)) {
    workspaceRenderCandidateInspection(compare, current, inspection.revision);
    state.selectedRevision = inspection.id;
  } else if (state.candidateLoadError?.identity === identity
      && state.candidateLoadError.id === select.value) {
    compare.replaceChildren(el("p", state.candidateLoadError.message));
  } else {
    compare.replaceChildren(el("p", "Load the selected candidate before previewing adoption."));
  }
  inspect.disabled = state.candidateLoad?.identity === identity
    && state.candidateLoad.id === select.value;
  select.addEventListener("change", () => {
    state.selectedRevision = null;
    state.candidateChoice = { identity, id: select.value };
    state.candidateInspection = null;
    state.candidateLoadError = null;
    state.candidateLoad = null;
    state.reconcilePreviewRequestSequence = (state.reconcilePreviewRequestSequence || 0) + 1;
    state.reconcilePreviewRequest = null;
    state.reconcilePreview = null;
    confirmGraph.checked = false;
    acknowledged.checked = false;
    compare.replaceChildren(el("p", "Load the selected candidate before previewing adoption."));
    previewBox.replaceChildren();
    inspect.disabled = false;
    workspacePersist(state);
  });
  const load = async () => {
    const id = select.value; if (!id) return;
    const request = (state.candidateLoadSequence || 0) + 1;
    state.candidateLoadSequence = request;
    state.candidateChoice = { identity, id };
    state.candidateInspection = null;
    state.candidateLoadError = null;
    state.candidateLoad = { identity, id, request };
    state.reconcilePreviewRequestSequence = (state.reconcilePreviewRequestSequence || 0) + 1;
    state.reconcilePreviewRequest = null;
    state.selectedRevision = null;
    state.reconcilePreview = null;
    inspect.disabled = true;
    try {
      const response = await fetch(`/api/authoring/revision?task=${encodeURIComponent(task.id)}&revision=${encodeURIComponent(id)}`, { cache: "no-store" });
      const revision = await response.json(); if (!response.ok) throw new Error(revision.error || "Could not load candidate");
      if (state.candidateChoice?.identity !== identity || state.candidateChoice.id !== id
          || state.candidateLoad?.request !== request) return;
      state.candidateInspection = { identity, id, revision };
      state.candidateLoadError = null;
      state.candidateLoad = null;
      state.selectedRevision = id;
      if (compare.isConnected) workspaceRenderCandidateInspection(compare, current, revision);
      else if (workspaceData?.task?.id === task.id && state.tab === "review") workspaceRerender();
    } catch (error) {
      if (state.candidateChoice?.identity !== identity || state.candidateChoice.id !== id
          || state.candidateLoad?.request !== request) return;
      state.candidateLoadError = { identity, id, message: error.message };
      state.candidateLoad = null;
      if (compare.isConnected) compare.replaceChildren(el("p", error.message));
      else if (workspaceData?.task?.id === task.id && state.tab === "review") workspaceRerender();
    } finally {
      if (state.candidateLoad?.request === request) state.candidateLoad = null;
      if (inspect.isConnected && !state.candidateLoad) inspect.disabled = false;
    }
  };
  inspect.addEventListener("click", load);
  section.append(select, inspect, compare);
  const confirm = el("label", "I reviewed the selected logical graph and its relationship to this specification.");
  const confirmGraph = el("input"); confirmGraph.type = "checkbox"; confirm.prepend(confirmGraph);
  const acknowledge = el("label", "I acknowledge the listed source changes, unresolved requirements and downstream review work.");
  const acknowledged = el("input"); acknowledged.type = "checkbox"; acknowledge.prepend(acknowledged);
  section.append(confirm, acknowledge);
  const adopt = el("button", "Preview explicit adoption"); adopt.type = "button";
  adopt.addEventListener("click", async () => {
    const revision = state.selectedRevision;
    if (!revision || revision !== select.value || state.candidateChoice?.identity !== identity
        || state.candidateChoice.id !== revision || state.candidateInspection?.identity !== identity
        || state.candidateInspection.id !== revision) {
      previewBox.textContent = "Load the selected candidate before previewing adoption.";
      return;
    }
    const request = (state.reconcilePreviewRequestSequence || 0) + 1;
    state.reconcilePreviewRequestSequence = request;
    state.reconcilePreviewRequest = { identity, revision, request };
    state.reconcilePreview = null;
    try {
      const response = await fetch("/api/authoring/reconcile-preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ task: task.id, revision }) });
      const preview = await response.json();
      const currentRequest = state.reconcilePreviewRequest;
      if (currentRequest?.identity !== identity || currentRequest.revision !== revision
          || currentRequest.request !== request || !select.isConnected || select.value !== revision
          || state.selectedRevision !== revision || state.candidateChoice?.identity !== identity
          || state.candidateChoice.id !== revision || state.candidateInspection?.identity !== identity
          || state.candidateInspection.id !== revision || !previewBox.isConnected) return;
      if (!response.ok) throw new Error(preview.error || "Preview unavailable");
      state.reconcilePreview = preview;
      state.reconcilePreviewRevision = { identity, revision, request };
      previewBox.replaceChildren(el("h3", "Adoption preview"), el("p", `Exact body hash ${preview.body_hash}`), el("p", `Current source ${preview.base.source}${preview.source_changed ? " · source has changed" : ""}`));
      for (const finding of preview.findings || []) previewBox.append(el("p", finding));
      for (const item of preview.downstream || []) previewBox.append(el("p", item));
      const material = workspaceMaterialHandlingForm(state, preview, revision);
      if (material.node) previewBox.append(material.node);
      const apply = el("button", "Adopt this revision and request review"); apply.type = "button";
      const executionUnsettled = Boolean(authoring.execution && !["Completed", "Failed"].includes(authoring.execution.status));
      const update = () => { apply.disabled = !(confirmGraph.checked && acknowledged.checked)
        || executionUnsettled || !material.valid(); };
      update();
      confirmGraph.addEventListener("change", update); acknowledged.addEventListener("change", update);
      material.onChange(update);
      apply.addEventListener("click", async () => {
        if (!select.isConnected || select.value !== revision || state.selectedRevision !== revision
            || state.candidateChoice?.identity !== identity || state.candidateChoice.id !== revision
            || state.candidateInspection?.identity !== identity
            || state.candidateInspection.id !== revision || state.reconcilePreview !== preview
            || state.reconcilePreviewRevision?.identity !== identity
            || state.reconcilePreviewRevision.revision !== revision) {
          previewBox.replaceChildren(el("p", "The selected candidate changed. Load and preview it again before adopting."));
          return;
        }
        apply.disabled = true;
        try {
          await authoringAction(task, state, "reconcile", { revision, token: preview.token,
            confirm_graph: confirmGraph.checked, acknowledge: acknowledged.checked, actions: [],
            handling: material.entries() }, { key: `reconcile:${revision}` });
          state.authoringMessage = { text: "Revision adopted. Harness requires a fresh design review and explicit approval." };
          workspaceRerender();
        } catch (error) {
          previewBox.append(el("p", `${error.message}. Your candidate remains available.`));
          if (error.status === 409) {
            state.authoringConflict = {
              key: `reconcile:${revision}`,
              bufferKey: `session-material-handling:${revision}`,
              message: error.message,
            };
            workspacePersist(state);
            const recovery = authoringConflictRecovery(task, state);
            if (recovery) previewBox.append(recovery);
          }
          apply.disabled = false;
        }
      });
      previewBox.append(apply);
    } catch (error) {
      const currentRequest = state.reconcilePreviewRequest;
      if (currentRequest?.identity === identity && currentRequest.revision === revision
          && currentRequest.request === request && select.isConnected && select.value === revision
          && state.selectedRevision === revision && previewBox.isConnected)
        previewBox.replaceChildren(el("p", error.message));
    } finally {
      if (state.reconcilePreviewRequest?.request === request) state.reconcilePreviewRequest = null;
    }
  });
  section.append(adopt, previewBox);
  if (authoring.execution && !["Completed", "Failed"].includes(authoring.execution.status))
    section.append(el("p", "Adoption is unavailable while this execution is active or unsettled. Your candidate remains editable."));
  const message = authoringMessage(state); if (message) section.append(message);
  return section;
}

function workspaceMaterialInputLabel(input) {
  const payload = input.payload || {};
  if (payload.text) return payload.text;
  if (payload.answer) return payload.answer;
  if (payload.body) return payload.body;
  const nested = payload.input || payload.payload || {};
  if (nested.text || nested.answer || nested.body) return nested.text || nested.answer || nested.body;
  if (payload.workspace_event) return `${input.kind.replaceAll("_", " ")} was saved in the task workspace.`;
  return `${input.kind.replaceAll("_", " ")} was recorded for this task.`;
}

function workspaceMaterialHandlingForm(state, preview, revision) {
  const inputs = preview.material?.inputs || [];
  if (!inputs.length) return { node: null, valid: () => true, entries: () => [], onChange: () => {} };
  const section = el("section"); section.className = "session-material-review";
  section.append(el("h4", `Review ${inputs.length} new conversation or planning item(s)`),
    el("p", "Choose which items to reconcile now. Unselected items stay pending for a later review. For each selected item, choose whether it changes this candidate, explain why, and select supporting current content."));
  const key = `session-material-handling:${revision}`;
  const initial = {};
  for (const item of inputs) initial[item.input_id] = { include: false, disposition: "", rationale: "", references: [] };
  const buffer = authoringFormBuffer(state, key, initial, preview.base);
  const refOptions = [`revision:${revision}`];
  if (preview.graph) refOptions.push(`graph:${preview.graph}`);
  for (const target of preview.decision_lineage || []) refOptions.push(`target:${target.target}@${target.revision}`);
  const listeners = [];
  const entryNodes = [];
  for (const item of inputs) {
    if (!buffer.value[item.input_id]) buffer.value[item.input_id] = { include: false, disposition: "", rationale: "", references: [] };
    const value = buffer.value[item.input_id];
    const kind = item.kind || "task input";
    const origin = item.origin || (kind.startsWith("authoring.") ? "task workspace" : "session history");
    const card = el("article"); card.className = "session-material-item";
    card.append(el("h5", `${kind.replaceAll("_", " ")} · ${origin.replaceAll("_", " ")}`), el("p", workspaceMaterialInputLabel(item)));
    const includeLabel = el("label", "Include this item in this reconciliation");
    const include = el("input"); include.type = "checkbox"; include.checked = Boolean(value.include);
    include.setAttribute("aria-label", `Include event ${item.input_id} in this reconciliation`);
    includeLabel.prepend(include); card.append(includeLabel);
    const details = el("details"); details.append(el("summary", "Recorded event details"));
    const raw = el("pre"); raw.textContent = JSON.stringify(item.payload || {}, null, 2); details.append(raw); card.append(details);
    const dispositionLabel = el("label", "How does this item relate to the selected candidate?");
    const disposition = el("select"); disposition.setAttribute("aria-label", `Handling for event ${item.input_id}`);
    for (const [optionValue, label] of [["", "Choose a handling decision"], ["incorporated", "Incorporated into this candidate"], ["no_change", "Reviewed; no change to this candidate"]]) {
      const option = el("option", label); option.value = optionValue; disposition.append(option);
    }
    disposition.value = value.disposition;
    const reasonLabel = el("label", "Reason for this decision");
    const rationale = el("textarea"); rationale.rows = 3; rationale.value = value.rationale;
    rationale.setAttribute("aria-label", `Reason for handling event ${item.input_id}`);
    rationale.placeholder = "Explain the effect on this specification or why it does not change it.";
    const referencesLabel = el("label", "Supporting current content references (select one or more)");
    const references = el("select"); references.multiple = true;
    references.setAttribute("aria-label", `Content references for event ${item.input_id}`);
    for (const ref of refOptions) {
      const option = el("option", ref.startsWith("revision:") ? "Selected specification" : ref.startsWith("graph:") ? "Selected diagram" : ref.replace(/^target:/, "Current decision "));
      option.value = ref; option.selected = value.references.includes(ref); references.append(option);
    }
    const update = () => {
      value.include = include.checked;
      value.disposition = disposition.value;
      value.rationale = rationale.value;
      value.references = [...references.selectedOptions].map(option => option.value);
      authoringFormChanged(state, key, buffer, item.input_id, value);
      listeners.forEach(listener => listener());
    };
    include.addEventListener("change", update);
    disposition.addEventListener("change", update);
    rationale.addEventListener("input", update);
    references.addEventListener("change", update);
    dispositionLabel.append(disposition); reasonLabel.append(rationale); referencesLabel.append(references);
    card.append(dispositionLabel, reasonLabel, referencesLabel); section.append(card);
    entryNodes.push(item.input_id);
  }
  const valid = () => entryNodes.every(id => {
    const entry = buffer.value[id];
    if (!entry?.include) return true;
    return entry && ["incorporated", "no_change"].includes(entry.disposition)
      && typeof entry.rationale === "string" && entry.rationale.trim().length >= 20
      && Array.isArray(entry.references) && entry.references.length > 0;
  }) && entryNodes.some(id => buffer.value[id]?.include);
  return {
    node: section,
    valid,
    entries: () => entryNodes.filter(input_id => buffer.value[input_id]?.include).map(input_id => {
      const { disposition, rationale, references } = structuredClone(buffer.value[input_id]);
      return { input_id, disposition, rationale, references };
    }),
    onChange: callback => listeners.push(callback),
  };
}
