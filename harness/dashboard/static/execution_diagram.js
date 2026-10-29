"use strict";

function agentDisplayName(agent) {
  const name = String(agent.name || agent.role || "Agent")
    .replace(/^(successor|continuation)_/, "").replace(/_/g, " ");
  return name.charAt(0).toUpperCase() + name.slice(1);
}

function agentRoleLabel(role) {
  return ({ "al-architect": "Architect", "al-engineer": "Engineer", "al-builder": "Builder", "al-designer": "Designer" })[role] || "Agent";
}

function diagramNode(title, status, description, state) {
  const node = el("article");
  node.className = "agent-map-node";
  node.dataset.state = state;
  node.append(el("strong", title));
  const badge = el("span", status);
  badge.className = "agent-map-badge";
  node.append(badge);
  if (description) {
    const text = el("p", description);
    text.title = description;
    node.append(text);
  }
  return node;
}

function renderAgentDiagram(execution) {
  const section = el("section");
  section.className = "execution-diagram";
  section.setAttribute("aria-label", "Agent activity diagram");
  const executing = ACTIVE_EXECUTION_STATUSES.has(execution.status);
  const agents = execution.agents || [];
  const live = agents.filter(agent => agent.status !== "Completed");
  const running = live.filter(agent => agent.status === "Running");
  const phase = String(execution.phase?.phase || "").toUpperCase();
  const verified = execution.status === "Completed" && execution.verification?.status === "complete";
  const current = verified ? 4 : phase === "DONE" ? -1 : phase.includes("REVIEW") ? 3
    : phase.includes("VERIFICATION") ? 2 : ["IMPLEMENTATION", "FINALIZATION"].includes(phase) ? 1
      : ["PLANNING", "PSEUDOCODE", "DESIGN"].includes(phase) ? 0 : -1;
  const stages = ["Plan", "Build", "Check", "Review", "Done"];
  const heading = el("div");
  heading.className = "agent-map-heading";
  heading.append(el("h3", "Live work map"));
  const status = executing ? (running.length ? `${running.length} agent${running.length === 1 ? "" : "s"} working` : "Supervisor coordinating")
    : verified ? "Verified complete" : `${execution.status} · no active execution`;
  heading.append(el("p", status));
  section.append(heading);
  const timeline = el("ol");
  timeline.className = "agent-map-stages";
  timeline.setAttribute("aria-label", "Recorded workflow stage");
  for (let i = 0; i < stages.length; i++) {
    const step = el("li", stages[i]);
    if (i === current) step.setAttribute("aria-current", "step");
    timeline.append(step);
  }
  section.append(timeline);
  const map = el("div");
  map.className = "agent-map";
  const supervisor = diagramNode("Supervisor", executing ? "Active" : execution.status,
    executing ? (running.length ? "Coordinating assigned work" : "No agent is currently reported as running")
      : "Native execution is not active", executing ? "running" : "inactive");
  supervisor.classList.add("agent-map-supervisor");
  map.append(supervisor);
  const branches = el("div");
  branches.className = "agent-map-branches";
  if (!live.length) {
    branches.append(diagramNode("Agents", "None active", `${agents.length} agent runs finished`, "inactive"));
  }
  for (const agent of live) {
    const isRunning = executing && agent.status === "Running";
    const action = isRunning && agent.last_operation ? agent.last_operation.action : agent.activity;
    const node = diagramNode(agentDisplayName(agent), isRunning ? "Working" : executing ? agent.status || "Waiting" : "Last reported work",
      action || "No specific activity reported", isRunning ? "running" : agent.status === "Blocked" ? "attention" : "inactive");
    node.dataset.agentId = agent.id;
    node.append(el("small", agentRoleLabel(agent.role)));
    node.title = `${agent.name} · ${agent.role}${agent.task ? ` · ${agent.task}` : ""}`;
    branches.append(node);
  }
  const checks = execution.activity?.checks || [];
  const unresolved = checks.filter(check => ["FAIL", "INFRA", "BLOCKED", "NOT_RUN"].includes(check.status));
  const pending = checks.filter(check => check.status === "RUNNING");
  const verificationComplete = verified;
  const checkStatus = verificationComplete ? "Verified" : unresolved.length ? `${unresolved.length} need attention`
    : pending.length ? `${pending.length} reported running` : "Pending";
  const checkText = unresolved.length ? "Failed or unavailable checks remain unresolved"
    : verificationComplete ? "Required checks satisfied" : "Verification is not complete";
  branches.append(diagramNode("Checks", checkStatus, checkText,
    verificationComplete ? "complete" : unresolved.length ? "attention" : "inactive"));
  map.append(branches);
  section.append(map);
  const finished = agents.filter(agent => agent.status === "Completed");
  if (finished.length) {
    const history = document.createElement("details");
    history.dataset.viewDisclosure = "agent-map-finished";
    history.className = "agent-map-finished";
    history.append(el("summary", `${finished.length} finished agent runs · show who did what`));
    const list = el("ul");
    for (const agent of finished) {
      const item = el("li");
      item.append(el("strong", `${agentDisplayName(agent)} · ${agentRoleLabel(agent.role)}`),
        el("span", "Run finished"), el("p", agent.activity || "No work summary reported"));
      list.append(item);
    }
    history.append(list);
    section.append(history);
  }
  const footer = el("p");
  footer.className = "agent-map-footer";
  const updated = execution.activity?.last_activity_at;
  footer.append(el("span", updated ? `Last work update: ${activityTime(updated)}` : "No work update recorded"));
  footer.append(el("span", developerDecisions(execution).some(d => d.state !== "Resolved")
    ? "Developer decision needed" : "No developer decision needed"));
  section.append(footer);
  return section;
}
