"use strict";

const EXECUTION_STAGE_VIEWS = new Map();
const CHECK_STATUS_LABELS = { PASS: "Passed", FAIL: "Failed", INFRA: "Tooling unavailable", RUNNING: "Reported running", BLOCKED: "Unavailable", NOT_RUN: "Not run" };

function relatedCheckIssues(execution, check) {
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(check.id)) return [];
  const pattern = new RegExp(`(^|[^A-Za-z0-9_-])${check.id}(?=$|[^A-Za-z0-9_-])`);
  return Object.values(execution.operational_issues || {}).filter(issue => issue.state !== "Resolved"
    && pattern.test(`${issue.capability_or_check_ref || ""} ${issue.next_action || ""}`));
}

function renderCheckDetails(execution) {
  const section = el("section");
  section.append(el("h4", "Check attempts"), el("p", "These are recorded attempts, including retries. Failed setup is not proof of a product defect."));
  const checks = execution.activity?.checks || [];
  if (!checks.length) section.append(el("p", "No check attempts have been recorded in this view."));
  for (const check of checks) {
    const row = el("article");
    row.className = "agent-map-check-result";
    row.append(el("strong", `${check.id} · ${check.area || "Check"}`),
      el("span", CHECK_STATUS_LABELS[check.status] || check.status || "Not reported"));
    if (check.counts?.passed !== undefined) row.append(el("p", `${check.counts.passed} passing tests recorded`));
    const issues = relatedCheckIssues(execution, check);
    if (!issues.length) row.append(el("p", check.status === "INFRA"
      ? "Infrastructure prevented a usable verification result."
      : "A specific cause is not attached to this check event. See recorded reports and repair actions below."));
    for (const issue of issues) {
      row.append(el("p", issue.capability_or_check_ref));
      const details = document.createElement("details");
      details.dataset.viewDisclosure = `check-repair-${check.id}-${issue.id}`;
      details.append(el("summary", "Recorded cause and repair"), el("p", issue.next_action));
      row.append(details);
    }
    section.append(row);
  }
  const issues = Object.values(execution.operational_issues || {}).filter(issue => issue.state !== "Resolved");
  if (issues.length) {
    const repair = document.createElement("details");
    repair.dataset.viewDisclosure = "stage-operational-repairs";
    repair.append(el("summary", `All tooling repair actions (${issues.length})`));
    for (const issue of issues) repair.append(el("h5", issue.capability_or_check_ref || issue.id), el("p", issue.next_action));
    section.append(repair);
  }
  return section;
}

function renderStageDetails(execution, stage, stages) {
  const panel = el("div");
  const active = ACTIVE_EXECUTION_STATUSES.has(execution.status);
  const complete = execution.status === "Completed" && execution.verification?.status === "complete";
  panel.append(el("h4", stages[stage]));
  const decisions = developerDecisions(execution).filter(item => item.state !== "Resolved");
  const action = el("p");
  action.className = "agent-map-next-action";
  action.append(el("strong", "Your action: "), el("span", decisions.length
    ? "Answer the recorded developer decision below."
    : complete ? "None. Verified completion has been recorded."
      : active ? "None requested. Agents or the supervisor are responsible for the next work."
        : "Choose Continue task above; Harness checks prerequisites and recovers interrupted work automatically. Choose Retry checks to focus on verification. No new product approval is requested."));
  panel.append(action);
  if (stage === 2) {
    panel.append(renderCheckDetails(execution));
    return panel;
  }
  const notes = [
    "Planning reports and Architect activity are shown below. A returned report alone is not a planning approval.",
    complete ? "Implementation has a verified completion result." : "Implementation is unfinished or unverified. Returned agent work is not feature completion.",
    "",
    "Formal review is not established by a completed Architect invocation. The recorded review stage and approval evidence determine completion.",
    complete ? "The run has recorded verified completion." : "The task is not complete. Implementation, required checks and formal review still need a verified result.",
  ];
  panel.append(el("p", notes[stage]));
  const roles = stage === 0 || stage === 3 ? ["al-architect"] : stage === 1 ? ["al-engineer", "al-builder", "al-designer"] : [];
  const agents = (execution.agents || []).filter(agent => roles.includes(agent.role));
  if (agents.length) {
    const records = document.createElement("details");
    records.dataset.viewDisclosure = `stage-agent-records-${stage}`;
    records.append(el("summary", `${agents.length} recorded agent runs`));
    const list = el("ul");
    for (const agent of agents) {
      const item = el("li");
      item.append(el("strong", `${agentDisplayName(agent)} · ${agent.status}`), el("p", agent.activity || "No work summary reported"));
      list.append(item);
    }
    records.append(list);
    panel.append(records);
  } else if (stage !== 4) panel.append(el("p", "No corresponding agent activity is recorded in this view."));
  if (stage === 1) {
    const issues = Object.values(execution.operational_issues || {}).filter(issue => issue.state !== "Resolved");
    if (issues.length) panel.append(el("p", `${issues.length} tooling repair records remain open. Select Check for causes and repair actions.`));
  }
  return panel;
}
