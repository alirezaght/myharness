"use strict";

function deliveryNode(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function safeDeliveryUrl(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const parsed = new URL(value, window.location.href);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
    if (parsed.username || parsed.password) return null;
    if (!/^https?:\/\//i.test(value)) return null;
    return parsed.href;
  } catch (_) {
    return null;
  }
}

function deliveryLink(label, value) {
  const url = safeDeliveryUrl(value);
  if (!url) return deliveryNode("span", label);
  const link = deliveryNode("a", label);
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.dataset.deliveryLink = url;
  return link;
}

function deliveryValue(value, fallback = "not available") {
  return value === undefined || value === null || value === "" ? fallback : String(value);
}

function deliveryList(title, items, renderItem = (item) => deliveryNode("li", item)) {
  const section = deliveryNode("section");
  section.className = "delivery-list";
  section.append(deliveryNode("h4", title));
  if (items.length) {
    const list = deliveryNode("ul");
    for (const item of items) list.append(renderItem(item));
    section.append(list);
  } else section.append(deliveryNode("p", "None recorded."));
  return section;
}

function deliveryDisclosure(title, id, contents, open = false) {
  const details = deliveryNode("details");
  details.dataset.deliveryDisclosure = id;
  details.open = open;
  const summary = deliveryNode("summary", title);
  summary.id = `delivery-disclosure-${id}`;
  details.append(summary, contents);
  return details;
}

function renderCompletionSummary(summary) {
  const section = deliveryNode("section");
  section.className = "delivery-completion";
  section.append(deliveryNode("h4", "Completion summary"));
  if (!summary || typeof summary !== "object") {
    section.append(deliveryNode("p", "Completion summary not available."));
    return section;
  }
  const implementation = summary.implementation || {};
  const ci = summary.ci || {};
  const tests = ci.tests || {};
  const browser = summary.browser_validation || summary.browser || {};
  const mr = summary.merge_request || {};
  const rows = [
    ["Implementation", deliveryValue(implementation.status, "unavailable")],
    ["Completed tasks", Array.isArray(summary.completed_tasks) ? summary.completed_tasks.length : "unavailable"],
    ["AL tests", deliveryValue(summary.al_tests?.status, "unavailable")],
    ["CI pipeline", deliveryValue(ci.pipeline?.status, "unavailable")],
    ["CI tests", tests.counts_available === false ? "counts unavailable" : deliveryValue(tests.total_count, "unavailable")],
    ["Review", deliveryValue(mr.state || mr.status, "unavailable")],
    ["Staging", deliveryValue(summary.staging?.status, "unavailable")],
    ["Browser validation", `${deliveryValue(browser.status, "unavailable")} · ${deliveryValue(browser.currency, "unavailable")}`],
    ["Live acceptance", deliveryValue(summary.live_acceptance, "outstanding")],
  ];
  const table = deliveryNode("dl");
  table.className = "delivery-summary";
  for (const [label, value] of rows) table.append(deliveryNode("dt", label), deliveryNode("dd", value));
  section.append(table);
  const links = deliveryNode("p");
  if (mr.web_url) links.append(deliveryLink("Open review", mr.web_url));
  if (mr.web_url && summary.ci?.pipeline?.web_url) links.append(document.createTextNode(" · "));
  if (summary.ci?.pipeline?.web_url) links.append(deliveryLink("Open pipeline", summary.ci.pipeline.web_url));
  if (links.childNodes.length) section.append(links);
  const blockers = Array.isArray(summary.blockers) ? summary.blockers : [];
  section.append(deliveryList("Outstanding items", blockers));
  const deviations = Array.isArray(summary.deviations_requiring_approval)
    ? summary.deviations_requiring_approval
    : [];
  section.append(deliveryList("Deviations requiring approval", deviations, (item) => {
    const text = typeof item === "string"
      ? item
      : item.summary || item.title || item.reason || JSON.stringify(item);
    return deliveryNode("li", text);
  }));
  return section;
}

function renderBrowserValidation(browser) {
  const section = deliveryNode("section");
  section.className = "delivery-browser";
  section.append(deliveryNode("h4", "Browser validation"));
  if (!browser || typeof browser !== "object") {
    section.append(deliveryNode("p", "Browser validation has not been proposed."));
    return section;
  }
  const planEntry = browser.plan;
  const plan = planEntry?.plan;
  const status = deliveryValue(browser.status, "not run");
  const currency = deliveryValue(browser.currency, "unavailable");
  const state = deliveryNode("p", `Status: ${status}. Result currency: ${currency}.`);
  state.className = "delivery-status";
  state.setAttribute("role", "status");
  state.dataset.state = status.toLowerCase();
  section.append(state);
  if (browser.reason) section.append(deliveryNode("p", `Currency: ${browser.reason}`));
  if (!plan) {
    section.append(deliveryNode("p", "No browser plan is available. Use the CLI to inspect capability and propose a plan."));
  } else {
    const target = plan.target || {};
    const stagingDeployment = plan.binding?.staging || {};
    const coverage = browser.coverage || {};
    const planInfo = deliveryNode("dl");
    planInfo.className = "delivery-summary";
    for (const [label, value] of [
      ["Plan", planEntry.plan_id || plan.plan_id || "available"],
      ["Target", target.url || "not available"],
      ["Deployment", target.deployment_id || target.deployment || stagingDeployment.deployment_id || stagingDeployment.last_deployment?.id || "not available"],
      ["Criteria selected / excluded / unresolved", `${coverage.selected ?? 0} / ${coverage.excluded ?? 0} / ${coverage.unresolved ?? 0}`],
    ]) planInfo.append(deliveryNode("dt", label), deliveryNode("dd", value));
    section.append(planInfo);
    const criteria = Array.isArray(plan.criteria) ? plan.criteria : [];
    section.append(deliveryDisclosure("Review plan criteria", `plan:${planEntry.plan_id || "current"}`,
      deliveryList("Acceptance criteria", criteria, (criterion) => {
        const item = deliveryNode("li");
        item.id = `browser-criterion-${criterion.id || "unknown"}`;
        item.append(deliveryNode("strong", `${deliveryValue(criterion.id, "criterion")} · ${deliveryValue(criterion.classification, "unresolved")}`));
        item.append(deliveryNode("p", deliveryValue(criterion.text, "Criterion text unavailable")));
        if (criterion.reason) item.append(deliveryNode("p", `Reason: ${criterion.reason}`));
        if (Array.isArray(criterion.steps) && criterion.steps.length) {
          item.append(deliveryNode("p", `Steps: ${criterion.steps.map((step) => deliveryValue(step.op, "step")).join(" → ")}`));
        }
        return item;
      }), true));
    const limitations = Array.isArray(browser.limitations) ? browser.limitations : (Array.isArray(plan.limitations) ? plan.limitations : []);
    if (limitations.length) section.append(deliveryList("Plan limitations", limitations));
  }
  const approvals = Array.isArray(browser.approvals) ? browser.approvals : [];
  section.append(deliveryDisclosure(`Approval history (${approvals.length})`, "approvals:history",
    deliveryList("Approvals", approvals, (approval) => {
      const item = deliveryNode("li");
      item.id = `browser-approval-${approval.id || "unknown"}`;
      item.append(deliveryNode("strong", `Approval ${deliveryValue(approval.id, "unavailable")}`));
      item.append(deliveryNode("p", `Plan: ${deliveryValue(approval.plan_id, "unavailable")} · request: ${deliveryValue(approval.request_id, "unavailable")} · attempt: ${deliveryValue(approval.attempt, "unavailable")} · ${deliveryValue(approval.created_at, "time unavailable")}`));
      return item;
    }), false));
  if (browser.status === "proposed" || (plan && approvals.length === 0)) {
    const instructions = deliveryNode("p", "Browser runs require a separate explicit approval bound to this exact plan. Review the plan, then use the browser approve command with its plan hash and a unique request ID.");
    instructions.className = "delivery-approval-instructions";
    section.append(instructions);
  }
  const results = Array.isArray(browser.results) ? browser.results : [];
  section.append(deliveryDisclosure(`Latest criterion results (${results.length})`, "results:latest",
    deliveryList("Results", results, (result) => {
      const item = deliveryNode("li");
      item.id = `browser-result-${result.criterion_id || result.id || "unknown"}`;
      item.append(deliveryNode("strong", `${deliveryValue(result.criterion_id, "criterion")} · ${deliveryValue(result.status, "unavailable")}`));
      if (result.error) item.append(deliveryNode("p", `Error: ${result.error}`));
      if (result.origin) item.append(deliveryNode("p", `Evidence origin: ${result.origin}`));
      const evidence = Array.isArray(result.evidence) ? result.evidence : [];
      for (const artifact of evidence) {
        item.append(deliveryNode("p", `Private evidence: ${deliveryValue(artifact.kind, "artifact")} · ${deliveryValue(artifact.sha256, "hash unavailable")} · ${deliveryValue(artifact.status, "stored")}`));
      }
      return item;
    }), false));
  const investigations = Array.isArray(browser.investigations) ? browser.investigations : [];
  section.append(deliveryDisclosure(`Investigations (${investigations.length})`, "investigations",
    deliveryList("Investigation records", investigations, (record) => {
      const item = deliveryNode("li");
      item.id = `browser-investigation-${record.id || record.criterion_id || "record"}`;
      item.append(deliveryNode("strong", `${deliveryValue(record.status, "open")} · ${deliveryValue(record.criterion_id, "criterion unavailable")}`));
      if (record.message || record.reason) item.append(deliveryNode("p", record.message || record.reason));
      item.append(deliveryNode("p", "Investigation records do not authorize implementation or architecture changes."));
      return item;
    }), false));
  const runs = Array.isArray(browser.runs) ? browser.runs : [];
  section.append(deliveryDisclosure(`Run history (${runs.length})`, "runs:history",
    deliveryList("Runs", runs, (run) => {
      const item = deliveryNode("li");
      item.id = `browser-run-${run.id || "unknown"}`;
      item.append(deliveryNode("strong", `${deliveryValue(run.state || run.status, "unknown")} · ${deliveryValue(run.id, "run id unavailable")}`));
      item.append(deliveryNode("p", `Approval: ${deliveryValue(run.approval_id, "unavailable")} · started ${deliveryValue(run.started_at, "time unavailable")} · finished ${deliveryValue(run.finished_at, "pending")}`));
      return item;
    }), false));
  const planHistory = Array.isArray(browser.plans) ? browser.plans : [];
  if (planHistory.length > 1) section.append(deliveryDisclosure(`Earlier plans (${planHistory.length - 1})`, "plans:history",
    deliveryList("Plans", planHistory.slice(0, -1), (entry) => {
      const historical = entry.plan || {};
      const item = deliveryNode("li");
      item.id = `browser-plan-${entry.plan_id || "unknown"}`;
      item.append(deliveryNode("strong", `Plan ${deliveryValue(entry.plan_id, "unavailable")}`));
      item.append(deliveryNode("p", `Target: ${deliveryValue(historical.target?.url, "unavailable")} · created ${deliveryValue(entry.created_at, "time unavailable")}`));
      return item;
    }), false));
  const blockers = Array.isArray(browser.blockers) ? browser.blockers : [];
  if (blockers.length) section.append(deliveryList("Browser blockers", blockers));
  return section;
}

function renderDelivery(delivery) {
  const section = deliveryNode("section");
  section.id = "delivery";
  section.setAttribute("aria-labelledby", "delivery-heading");
  const heading = deliveryNode("h3", "Delivery");
  heading.id = "delivery-heading";
  section.append(heading);

  const summary = deliveryNode("dl");
  summary.className = "delivery-summary";
  const mr = delivery.mr && typeof delivery.mr === "object" ? delivery.mr : {};
  const pipeline = delivery.pipeline && typeof delivery.pipeline === "object" ? delivery.pipeline : {};
  const github = delivery.provider === "github";
  const reviewLabel = github ? "Pull request" : "Merge request";
  const browser = delivery.browser_validation || {};
  const rows = [
    ["Status", deliveryValue(delivery.status, "not configured")],
    [reviewLabel, mr.iid ? `${github ? "#" : "!"}${mr.iid} · ${deliveryValue(mr.state, "unknown")}` : "not created"],
    [github ? "Actions" : "Pipeline", deliveryValue(pipeline.status, "not available")],
    ["Staging", delivery.staging?.ready ? "ready" : deliveryValue(delivery.staging?.status, "unknown")],
    ["Browser validation", `${deliveryValue(browser.status, "not run")} · ${deliveryValue(browser.currency, "unavailable")}`],
    ["Retries", `${delivery.retries?.used ?? 0} of ${delivery.retries?.limit ?? 0}`],
  ];
  for (const [label, value] of rows) summary.append(deliveryNode("dt", label), deliveryNode("dd", value));
  if (mr.web_url) {
    const row = deliveryNode("dd");
    row.append(deliveryLink(github ? "Open pull request" : "Open merge request", mr.web_url));
    summary.append(deliveryNode("dt", "Link"), row);
  }
  if (pipeline.web_url) {
    const row = deliveryNode("dd");
    row.append(deliveryLink(github ? "Open Actions" : "Open pipeline", pipeline.web_url));
    summary.append(deliveryNode("dt", github ? "Actions link" : "Pipeline link"), row);
  }
  section.append(summary);
  section.append(renderCompletionSummary(delivery.completion_summary));
  section.append(renderBrowserValidation(browser));

  const jobs = Array.isArray(delivery.jobs) ? delivery.jobs : [];
  section.append(deliveryList(`Jobs (${jobs.length})`, jobs, (job) => deliveryNode("li", `${deliveryValue(job.name, "job")} · ${deliveryValue(job.status, "unknown")}`)));
  const report = delivery.test_report;
  const testSection = deliveryNode("section");
  testSection.className = "delivery-list";
  testSection.append(deliveryNode("h4", "Test results"));
  if (report?.format === "github_check_summaries") {
    testSection.append(deliveryNode("p", "Check summaries · test counts depend on the repository’s reporter."));
    const list = deliveryNode("ul");
    for (const check of report.checks || []) list.append(deliveryNode("li", `${deliveryValue(check.name, "check")} · ${deliveryValue(check.conclusion, "pending")}${check.summary ? ` · ${check.summary}` : ""}`));
    testSection.append(list);
  } else testSection.append(deliveryNode("p", report
    ? `${report.success_count ?? 0} passed · ${report.failed_count ?? 0} failed · ${report.skipped_count ?? 0} skipped of ${report.total_count ?? 0}`
    : "No test report yet."));
  section.append(testSection);

  const blockers = Array.isArray(delivery.blockers) ? delivery.blockers : [];
  section.append(deliveryList(`Remaining blockers (${blockers.length})`, blockers, (blocker) => deliveryNode("li", typeof blocker === "string" ? blocker : deliveryValue(blocker.message || blocker.reason, "delivery blocked"))));
  if (delivery.observed_at) section.append(deliveryNode("p", `Last observed: ${delivery.observed_at}`));
  return section;
}

function withDeliveryFocus(callback) {
  const active = document.activeElement;
  const panel = active?.closest?.("#delivery");
  const key = panel ? active.dataset.deliveryLink || active.id || active.textContent : null;
  const open = new Set([...document.querySelectorAll("#delivery details[data-delivery-disclosure][open]")]
    .map((node) => node.dataset.deliveryDisclosure));
  callback();
  for (const node of document.querySelectorAll("#delivery details[data-delivery-disclosure]")) {
    node.open = open.has(node.dataset.deliveryDisclosure);
  }
  if (key === null) return;
  const candidates = [...document.querySelectorAll("#delivery [data-delivery-link], #delivery [id]")];
  const restored = candidates.find((node) => (node.dataset.deliveryLink || node.id) === key || node.textContent === key);
  if (restored) restored.focus({preventScroll: true});
}
