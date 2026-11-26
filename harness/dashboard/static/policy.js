"use strict";

const POLICY_VIEWS = new Map();
function policyState(execution) {
  const identity = executionIdentity(execution);
  if (!POLICY_VIEWS.has(identity)) POLICY_VIEWS.set(identity, { identity, data: null, items: [], cursor: null,
    advanced: false, newer: false, loading: false, listToken: 0, error: null, selected: null,
    detail: null, detailLoading: false, detailToken: 0, detailError: null, open: true,
    capItems: [], capCursor: null, capPageAfter: null, capReady: false, capAdvanced: false,
    capLoading: false, capToken: 0, capError: null, capCoverage: null,
    decision: "", revision: "0", action: "install", submitting: false, actionMessage: "" });
  while (POLICY_VIEWS.size > 8) POLICY_VIEWS.delete(POLICY_VIEWS.keys().next().value);
  return POLICY_VIEWS.get(identity);
}
function policySection(execution, view) {
  const section = document.getElementById("execution-policy");
  return currentExecution(execution) && POLICY_VIEWS.get(view.identity) === view
    && section?.dataset.viewIdentity === view.identity ? section : null;
}
function policyQuery(execution) {
  return new URLSearchParams({ task: execution.task_id || executionTaskId, execution: execution.id, epoch: execution.epoch_id });
}
async function loadPolicy(execution, view, older = false, latest = false) {
  if (view.loading) return;
  view.loading = true; view.error = null;
  const token = ++view.listToken;
  try {
    const query = policyQuery(execution);
    if (older && view.cursor) query.set("before", view.cursor);
    const response = await fetch(`/api/execution/policy?${query}`, { cache: "no-store" });
    const data = await response.json();
    if (token !== view.listToken || !policySection(execution, view)) return;
    if (!response.ok) throw new Error();
    view.data = data;
    if (!view.capLoading && !view.capAdvanced) receiveCapabilities(view, data);
    if (older || latest || !view.items.length) {
      view.items = data.decisions; view.cursor = data.next_cursor;
      view.advanced = older; view.newer = false;
    } else if (view.advanced) {
      view.newer ||= data.decisions.some((item) => item.id !== view.items[0]?.id);
    } else {
      const fresh = data.decisions;
      if (fresh.some((item) => !view.items.some((old) => old.id === item.id))) view.newer = true;
    }
  } catch (_) {
    if (token === view.listToken) view.error = "Policy facts unavailable. Retry to refresh.";
  } finally {
    if (token === view.listToken) {
      view.loading = false;
      const section = policySection(execution, view);
      if (section) paintPolicy(execution, view, section);
    }
  }
}
function receiveCapabilities(view, data) {
  view.capItems = data.capabilities || [];
  view.capCursor = data.capabilities_next_cursor;
  view.capCoverage = data.capabilities_coverage;
  view.capReady = true;
}
async function loadCapabilities(execution, view, after = view.capPageAfter) {
  if (view.capLoading) return;
  view.capLoading = true; view.capError = null;
  view.capAdvanced = after !== null;
  const token = ++view.capToken;
  const current = policySection(execution, view);
  if (current) paintPolicy(execution, view, current);
  try {
    const query = policyQuery(execution);
    if (after !== null) query.set("cap_after", after);
    const response = await fetch(`/api/execution/policy?${query}`, { cache: "no-store" });
    const data = await response.json();
    if (token !== view.capToken || !policySection(execution, view)) return;
    if (!response.ok) throw new Error();
    view.capPageAfter = after;
    receiveCapabilities(view, data);
  } catch (_) {
    if (token === view.capToken) view.capError = "Capability page unavailable. Refresh to retry.";
  } finally {
    if (token === view.capToken) {
      view.capLoading = false;
      const section = policySection(execution, view);
      if (section) paintPolicy(execution, view, section);
    }
  }
}
async function loadPolicyDetail(execution, view, identifier) {
  if (view.detailLoading && view.selected === identifier) return;
  view.selected = identifier; view.detailLoading = true; view.detailError = null;
  const token = ++view.detailToken;
  try {
    const query = policyQuery(execution); query.set("attempt", identifier);
    const response = await fetch(`/api/execution/policy?${query}`, { cache: "no-store" });
    const data = await response.json();
    if (token !== view.detailToken || !policySection(execution, view)) return;
    if (!response.ok) throw new Error();
    view.detail = data;
  } catch (_) {
    if (token === view.detailToken) view.detailError = "Attempt details unavailable.";
  } finally {
    if (token === view.detailToken) {
      view.detailLoading = false;
      const section = policySection(execution, view);
      if (section) paintPolicy(execution, view, section);
    }
  }
}
function policyNode(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function policyButton(id, text, action, disabled = false) {
  const node = policyNode("button", text); node.type = "button"; node.id = id;
  node.disabled = disabled; node.onclick = action; return node;
}
async function submitPolicy(execution, view) {
  if (view.submitting) return;
  view.submitting = true; view.actionMessage = "Applying acknowledged authorization…";
  paintPolicy(execution, view, policySection(execution, view));
  try {
    const response = await fetch(`/api/policy/${view.action}`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task: execution.task_id || executionTaskId, execution: execution.id, epoch: execution.epoch_id,
        generation: execution.authority_generation, decision: view.decision, revision: Number(view.revision) }) });
    const data = await response.json();
    if (!policySection(execution, view)) return;
    view.actionMessage = response.ok ? "Authorization applied." : data.error || "Policy action refused.";
    if (response.ok) loadPolicy(execution, view);
  } catch (_) { view.actionMessage = "Response unavailable. Refresh facts before retrying the same decision."; }
  finally { view.submitting = false; const section = policySection(execution, view); if (section) paintPolicy(execution, view, section); }
}
function paintPolicy(execution, view, section) {
  if (!section) return;
  withExecutionFocus(() => {
    section.replaceChildren(policyNode("h3", "Runtime policy & budgets"));
    section.append(policyNode("p", "Limits count admitted invocations in this project database. Unknown usage stays unavailable; uncertain work is never replayed."));
    if (view.error) section.append(policyNode("p", view.error, "policy-error"));
    if (!view.data) section.append(policyNode("p", view.loading ? "Loading policy facts…" : "Policy facts unavailable."));
    else {
      section.append(policyNode("p", view.data.policies.length ? `${view.data.policies.length} active policy ${view.data.policies.length === 1 ? "version" : "versions"}` : "No optional runtime policy configured. Existing approval and containment rules apply."));
      const pools = policyNode("div", undefined, "policy-pools");
      for (const pool of view.data.pools) {
        const card = policyNode("div", undefined, "policy-pool");
        card.append(policyNode("strong", `${pool.id} · ${pool.dimension}`), policyNode("p", `Shared pool limit ${pool.maximum} · consumed ${pool.consumed} · reserved ${pool.reserved}`),
          policyNode("p", `Measured ${pool.measured ?? "unavailable"} · unknown ${pool.unknown ?? "unavailable"} · ${pool.domain}`)); pools.append(card);
      }
      section.append(pools,
        policyNode("p", "A shared pool limit is the current shared accounting ceiling. Each operation also obeys the ceilings in its selected and inherited policy versions; those ceilings do not reset shared counters."),
        policyNode("p", "Tokens, cost and native child usage: unavailable. Strict command/native deadlines are unsupported without qualified stoppability; ordinary timeouts are best effort. Uncertain process trees remain unresolved."));
      const controls = policyNode("div", undefined, "policy-controls");
      controls.append(policyButton("policy-latest", view.newer ? "Latest · new decisions" : "Latest", () => loadPolicy(execution, view, false, true), view.loading),
        policyButton("policy-older", "Older", () => loadPolicy(execution, view, true), !view.cursor || view.loading),
        policyButton("policy-refresh", "Refresh", () => loadPolicy(execution, view), view.loading)); section.append(controls);
      const list = policyNode("ul", undefined, "policy-decision-list");
      for (const item of view.items) {
        const row = policyNode("li");
        row.append(policyButton(`policy-row-${item.id}`, `${item.verdict} · ${item.code} · decision ${item.id.slice(0, 12)} · ${item.created_at}`, () => {
          if (view.selected !== item.attempt_id) { view.detail = null; view.open = true; }
          loadPolicyDetail(execution, view, item.attempt_id);
        }, !item.attempt_id)); list.append(row);
      }
      section.append(list);
      if (view.selected) {
        const details = policyNode("details"); details.open = view.open; details.dataset.policyAttempt = view.selected;
        details.ontoggle = () => { view.open = details.open; };
        details.append(policyNode("summary", "Policy attempt details"));
        if (view.detailError) details.append(policyNode("p", view.detailError));
        if (view.detail) {
          const attempt = view.detail.attempt;
          details.append(policyNode("p", `${attempt.operation} · ${attempt.state} · outcome ${attempt.outcome ?? "unknown"}`),
            policyNode("p", `Execution boundary ${attempt.boundary} · remote liability ${attempt.remote_liability}`),
            policyNode("p", `Started ${attempt.started_at ?? "not observed"} · settled ${attempt.settled_at ?? "unknown"}`));
          if (attempt.retry_reference) details.append(policyNode("p", `Retry of AL role ${attempt.retry_reference.target_role}; checkpoint ${attempt.retry_reference.failure_checkpoint_ref}. Native invocation identity unavailable.`));
          for (const r of view.detail.reservations) details.append(policyNode("p", `${r.pool_id}: reserved units ${r.units}, charged ${r.charged ? "yes" : "no"}, measured ${r.observed_units ?? "unavailable"}`));
        } else details.append(policyNode("p", "Loading attempt details…"));
        section.append(details);
      }
      const caps = policyNode("section", undefined, "policy-capabilities");
      caps.append(policyNode("h4", "Approval capabilities"));
      caps.setAttribute("aria-busy", String(view.capLoading));
      const coverage = view.capCoverage?.status || "unavailable";
      const status = policyNode("p", `Showing ${view.capItems.length} capabilities · coverage ${coverage}${view.capCursor ? " · more available" : ""}`);
      status.setAttribute("role", "status"); caps.append(status);
      if (view.capError) caps.append(policyNode("p", view.capError, "policy-error"));
      const capControls = policyNode("div", undefined, "policy-controls");
      capControls.append(policyButton("policy-caps-first", "First capability page", () => loadCapabilities(execution, view, null)),
        policyButton("policy-caps-more", "More capabilities", () => { if (view.capCursor) loadCapabilities(execution, view, view.capCursor); }, !view.capCursor),
        policyButton("policy-caps-refresh", "Refresh capabilities", () => loadCapabilities(execution, view)));
      if (view.capLoading) for (const button of capControls.querySelectorAll("button")) button.setAttribute("aria-disabled", "true");
      const grants = policyNode("ul");
      for (const grant of view.capItems) {
        const row = policyNode("li", `${grant.id} · ${grant.state} · ${grant.uses} uses · ${grant.units} units`);
        row.dataset.capabilityId = grant.id;
        row.append(policyNode("p", `Issued by authorization delivery ${grant.issuance_decision_ref}`));
        if (grant.revocation_decision_ref) row.append(policyNode("p", `Revoked by authorization delivery ${grant.revocation_decision_ref} · action ${grant.revocation_action_ref}`));
        grants.append(row);
      }
      caps.append(capControls, grants);
      if (!view.capItems.length && view.capReady) caps.append(policyNode("p", "No capabilities in this page."));
      section.append(caps);
    }
    const form = policyNode("form"); form.onsubmit = (event) => { event.preventDefault(); submitPolicy(execution, view); };
    for (const [id, label, key, type] of [["policy-decision", "Acknowledged developer delivery ID", "decision", "text"], ["policy-revision", "Expected revision", "revision", "number"]]) {
      const input = policyNode("input"); input.id = id; input.type = type; input.value = view[key]; input.required = true;
      if (type === "number") { input.min = "0"; input.step = "1"; }
      input.oninput = () => { view[key] = input.value; };
      const name = policyNode("label", label); name.htmlFor = id; form.append(name, input);
    }
    const select = policyNode("select"); select.id = "policy-action"; select.setAttribute("aria-label", "Policy action");
    for (const [value, label] of [["install", "Install approved policy version"], ["capability", "Issue approved capability"], ["revoke", "Revoke approved capability"], ["workflow", "Register approved workflow membership"]]) {
      const option = policyNode("option", label); option.value = value; select.append(option);
    }
    select.value = view.action; select.onchange = () => { view.action = select.value; };
    form.append(select, policyButton("policy-apply", view.submitting ? "Applying…" : "Apply authorization", () => submitPolicy(execution, view), view.submitting));
    section.append(policyNode("p", "Actions require an exact structured authorization from a current acknowledged developer decision. Approval prose cannot grant permission."), form);
    const message = policyNode("p", view.actionMessage); message.setAttribute("role", "status"); section.append(message);
  });
}
function renderExecutionPolicy(execution) {
  const view = policyState(execution);
  const section = policyNode("section", undefined, "execution-policy"); section.id = "execution-policy"; section.dataset.viewIdentity = view.identity;
  paintPolicy(execution, view, section);
  setTimeout(() => {
    if (!policySection(execution, view)) return;
    const mounted = () => Boolean(policySection(execution, view));
    reliabilityRefetch(view, "list", execution, {
      failed: Boolean(view.error), busy: () => view.loading, mounted, load: () => loadPolicy(execution, view),
    });
    if (view.capAdvanced) reliabilityRefetch(view, "capabilities", execution, {
      failed: Boolean(view.capError), busy: () => view.capLoading, mounted, load: () => loadCapabilities(execution, view),
    });
    const active = view.detail && ["reserved", "started", "unresolved"].includes(view.detail.attempt.state);
    if (view.selected && !view.detail) {
      reliabilityMark(view, "detail", execution);
      loadPolicyDetail(execution, view, view.selected);
    }
    else if (view.selected && active) reliabilityRefetch(view, "detail", execution, {
      busy: () => view.detailLoading, mounted, load: () => loadPolicyDetail(execution, view, view.selected),
    });
  }, 0);
  return section;
}
