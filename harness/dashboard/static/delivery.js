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
  const rows = [
    ["Status", deliveryValue(delivery.status, "not configured")],
    [reviewLabel, mr.iid ? `${github ? "#" : "!"}${mr.iid} · ${deliveryValue(mr.state, "unknown")}` : "not created"],
    [github ? "Actions" : "Pipeline", deliveryValue(pipeline.status, "not available")],
    ["Staging", delivery.staging?.ready ? "ready" : deliveryValue(delivery.staging?.status, "unknown")],
    ["Browser validation", "not run · Phase 5"],
    ["Retries", `${delivery.retries?.used ?? 0} of ${delivery.retries?.limit ?? 0}`],
  ];
  for (const [label, value] of rows) summary.append(deliveryNode("dt", label), deliveryNode("dd", value));
  if (mr.web_url) {
    const link = deliveryLink(github ? "Open pull request" : "Open merge request", mr.web_url);
    const row = deliveryNode("dd");
    row.append(link);
    summary.append(deliveryNode("dt", "Link"), row);
  }
  if (pipeline.web_url) {
    const row = deliveryNode("dd");
    row.append(deliveryLink(github ? "Open Actions" : "Open pipeline", pipeline.web_url));
    summary.append(deliveryNode("dt", github ? "Actions link" : "Pipeline link"), row);
  }
  section.append(summary);

  const jobs = Array.isArray(delivery.jobs) ? delivery.jobs : [];
  const jobSection = deliveryNode("div");
  jobSection.className = "delivery-list";
  jobSection.append(deliveryNode("h4", `Jobs (${jobs.length})`));
  if (jobs.length) {
    const list = deliveryNode("ul");
    for (const job of jobs) list.append(deliveryNode("li", `${deliveryValue(job.name, "job")} · ${deliveryValue(job.status, "unknown")}`));
    jobSection.append(list);
  } else jobSection.append(deliveryNode("p", "No job results yet."));
  section.append(jobSection);

  const report = delivery.test_report;
  const testSection = deliveryNode("div");
  testSection.className = "delivery-list";
  testSection.append(deliveryNode("h4", "Test results"));
  if (report?.format === "github_check_summaries") {
    testSection.append(deliveryNode("p", "Check summaries · test counts depend on the repository’s reporter."));
    const list = deliveryNode("ul");
    for (const check of report.checks || []) {
      list.append(deliveryNode("li", `${deliveryValue(check.name, "check")} · ${deliveryValue(check.conclusion, "pending")}${check.summary ? ` · ${check.summary}` : ""}`));
    }
    testSection.append(list);
  } else testSection.append(deliveryNode("p", report
    ? `${report.success_count ?? 0} passed · ${report.failed_count ?? 0} failed · ${report.skipped_count ?? 0} skipped of ${report.total_count ?? 0}`
    : "No test report yet."));
  section.append(testSection);

  const blockers = Array.isArray(delivery.blockers) ? delivery.blockers : [];
  const blockerSection = deliveryNode("div");
  blockerSection.className = "delivery-list";
  blockerSection.append(deliveryNode("h4", `Remaining blockers (${blockers.length})`));
  if (blockers.length) {
    const list = deliveryNode("ul");
    for (const blocker of blockers) list.append(deliveryNode("li", typeof blocker === "string" ? blocker : deliveryValue(blocker.message || blocker.reason, "delivery blocked")));
    blockerSection.append(list);
  } else blockerSection.append(deliveryNode("p", "None reported."));
  section.append(blockerSection);
  if (delivery.observed_at) section.append(deliveryNode("p", `Last observed: ${delivery.observed_at}`));
  return section;
}

function withDeliveryFocus(callback) {
  const active = document.activeElement;
  const panel = active?.closest?.("#delivery");
  const key = panel ? active.dataset.deliveryLink || active.id || active.textContent : null;
  callback();
  if (key === null) return;
  const candidates = [...document.querySelectorAll("#delivery [data-delivery-link], #delivery [id]")];
  const restored = candidates.find((node) => (node.dataset.deliveryLink || node.id) === key || node.textContent === key);
  if (restored) restored.focus({preventScroll: true});
}
