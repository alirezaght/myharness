# AL Engineering Harness
## Design Specification — V1

**Status:** Design Draft — Awaiting Final Approval  
**Environment:** Claude Code / Terminal  
**Implementation Engine:** Existing AL Orchestrator  
**Interface:** Local Web Dashboard + CLI  
**Specification Format:** Markdown  
**Local State:** SQLite  
**Shared Decisions:** Git-versioned Markdown  
**Source Control:** GitLab

---

## 1. Vision and Core Principles

The AL Engineering Harness is an AI-driven requirements discovery, architecture design, approval, and execution supervision system.

Its primary purpose is to eliminate ambiguity before implementation begins.

The Harness investigates the existing codebase, interviews the developer, proposes architectural solutions, visualizes system design, documents decisions, and produces a complete implementation specification.

Only after explicit approval does it delegate implementation to the existing AL Orchestrator.

### Fundamental Rules

1. The developer owns all significant architecture and design decisions.
2. The Harness must never invent requirements or silently make significant design decisions.
3. The Harness may proactively recommend solutions, but recommendations require developer approval.
4. Existing code and approved decisions must be investigated before asking questions.
5. Implementation cannot begin without an approved specification.
6. AL must implement within the approved architecture.
7. Any newly discovered design ambiguity must return to the developer.
8. Independent work may continue while another task is blocked.
9. Every significant decision must be traceable and versioned.
10. The system should minimize unnecessary questions without sacrificing clarity.

The Harness is not intended to replace AL.

**The Harness owns requirements, design, approval, and supervision. AL owns implementation and technical verification.**

---

## 2. Activation and Supported Tasks

The Harness activates only when explicitly invoked by the developer.

Proposed command:

`/harness`

Supported tasks include:

- New features
- Bug fixes
- Refactoring
- Architecture changes
- Configuration changes
- Performance improvements
- Infrastructure-related development
- Other codebase modifications

The interview and design process must adapt to the complexity of each request.

For trivial changes, only essential clarification is required.

For complex changes, the Harness performs comprehensive requirements discovery and architecture review.

There is no mandatory fixed number of questions.

---

## 3. End-to-End Workflow

### Phase 1 — Repository Investigation

Before asking implementation questions, the Harness examines the relevant repository.

It identifies:

- Existing components and architecture
- Relevant files and dependencies
- Interfaces and data contracts
- Database schemas
- Existing configuration
- Relevant tests
- Coding conventions
- Existing architectural constraints
- Previously approved decisions

The investigation is read-only by default.

The Harness distinguishes verified facts, assumptions, and unresolved questions.

It must not modify the codebase during this phase.

### Phase 2 — Requirements Interview

The Harness conducts an adaptive interview to establish exactly what the developer wants.

Topics include, when relevant:

- Business requirements
- Functional behavior
- Expected inputs and outputs
- Edge cases
- Component responsibilities
- Component communication
- Data schemas
- API contracts
- Configuration
- Error handling
- Performance
- Security
- Observability
- Acceptance criteria

Questions can be presented individually or in related groups.

When a decision is needed, the Harness should present a recommendation with its rationale before requesting the developer's decision.

The developer can accept, reject, or modify any proposal.

Unanswered questions remain unresolved rather than being filled with assumptions.

### Phase 3 — Architecture and Scaffolding Design

The Harness collaborates with the developer to define the complete logical design.

The design must explain:

- Which components exist
- What each component does
- How components interact
- How information moves through the system
- What interfaces and contracts are required
- Which configurations are needed
- How failures are handled
- What behavior must be implemented

The developer controls the architectural decisions.

Internal implementation details, such as method names, class names, and exact file placement, may be delegated to AL if they do not violate the approved design.

### Phase 4 — Visual Architecture Review

The Harness generates diagrams appropriate to the task.

Supported diagram types include:

- Component diagrams
- Sequence diagrams
- Data-flow diagrams
- State diagrams
- Deployment diagrams

Only relevant diagrams should be generated.

Diagrams are displayed through the local web dashboard.

The developer can select a component or connection and add comments.

Comments may request clarification, correction, or a complete redesign.

The Harness must incorporate the feedback and present revised diagrams.

Unresolved architectural comments block final approval.

### Phase 5 — Test Planning

Before implementation, the Harness proposes a complete, relevant test plan.

The plan should cover:

- Unit tests
- Integration tests
- End-to-end tests
- Regression tests
- Edge cases
- Failure scenarios
- Relevant acceptance criteria

Only applicable test categories are required.

The developer reviews the test plan and can add, remove, or modify scenarios.

**Tests must verify meaningful behavior rather than merely passing through artificial mocks.**

Mocks and test doubles are allowed only when technically justified, such as isolating external systems or avoiding unsafe side effects.

They must not conceal missing functionality.

The approved test plan becomes part of the specification delivered to AL.

### Phase 6 — Final Design Review

Before implementation, the Harness performs a mandatory completeness check.

It verifies that:

- Requirements are clear
- Significant decisions are approved
- Architecture is consistent
- Component responsibilities are defined
- Component interactions are understood
- Acceptance criteria are testable
- The test plan is sufficient
- No blocking questions remain
- No unresolved diagram comments remain
- No known conflicts are ignored

If issues remain, the Harness returns to the appropriate interview or design step.

### Phase 7 — Final Approval

The developer reviews the completed specification in the web dashboard.

The specification includes:

- Requirements
- Architecture
- Components and interfaces
- Relevant diagrams
- Configuration decisions
- Acceptance criteria
- Approved test plan
- Design rationale
- Known constraints

A single explicit **Approve & Implement** button authorizes the handoff to AL.

Approval creates an immutable, versioned specification snapshot.

No implementation may begin before this approval.

### Phase 8 — AL Orchestrator Execution

After approval, the Harness directly invokes the existing AL Orchestrator.

AL receives the approved specification and test plan.

AL is responsible for:

- Implementation planning
- Agent task allocation
- Code implementation
- Test implementation and execution
- Technical review
- Fixing implementation defects
- Creating a GitLab merge request
- Monitoring CI/CD pipelines
- Resolving technical failures

The Harness receives structured execution events from AL.

AL may make implementation-level decisions within the approved design.

If a new architectural or product decision becomes necessary, AL must report it to the Harness.

The affected task becomes blocked pending developer approval.

Independent tasks may continue.

### Phase 9 — Merge Request and CI/CD

AL creates a GitLab merge request after implementation.

GitLab integration uses the official GitLab API with the developer's authorized credentials.

The system monitors:

- Merge request creation
- Pipeline execution
- Individual CI job status
- Test results
- Pipeline failures
- Staging deployment status

Technical implementation failures can be routed back to AL for correction.

Design changes must return to the developer for approval.

The Harness does not automatically merge into protected branches.

### Phase 10 — Staging Browser Validation

When staging deployment is available, the Harness evaluates which approved acceptance criteria can be tested through a browser.

The Harness proposes a browser validation plan.

**Browser tests require separate explicit developer approval.**

After approval, the system may use Claude in Chrome, Codex browser automation, or another compatible browser automation integration.

Browser tests must validate the actual staging application.

Results include:

- Passed criteria
- Failed criteria
- Evidence where available
- Errors
- Blockers

Failures are routed for investigation.

No new architecture decision is automatically authorized by a failed test.

### Phase 11 — Completion Summary

After execution, the Harness presents a concise summary covering:

- Implemented functionality
- Completed tasks
- Test results
- Merge request link and status
- CI/CD status
- Staging status
- Browser validation results
- Remaining blockers
- Any deviations requiring approval

Detailed implementation verification belongs to AL.

The Harness summarizes and supervises the outcome.

---

## 4. Read-Only Investigation and Command Execution

The Harness must be capable of investigating technical problems using real system information.

Permitted read-only operations may include:

- Reading source files
- Searching repository contents
- Inspecting Git history and status
- Reading application and server logs
- Inspecting container or service status
- Reading configuration
- Executing diagnostic commands
- Connecting to databases for read-only queries
- Inspecting CI/CD results
- Reading monitoring information

### Command Authorization

Commands are classified by their potential effects.

**Safe read-only operations**

Known, verified read-only commands may run without additional approval.

**Potentially mutating operations**

Commands that can modify files, databases, services, infrastructure, or external systems require explicit developer approval before the specification is approved.

**Unknown or high-risk operations**

Commands with uncertain effects must not be assumed safe.

They require approval or must be replaced with a verified read-only alternative.

### Database Safety

Read-only database access should use:

- Read-only credentials where supported
- Restricted permissions
- Query validation
- Timeouts and resource limits
- Protection against mutating queries

A SQL `SELECT` statement alone is not sufficient proof that an operation is harmless.

### Important Boundary

Automatic execution modes in Claude Code or Codex may assist with tool execution, but they must not bypass the Harness's authorization rules.

A command's actual side effects determine whether approval is required.

---

## 5. Specification Storage

The official specification format is Markdown.

Each task produces a versioned Markdown specification.

Specifications are stored in the project repository and tracked through Git.

They represent the shared source of truth for approved requirements and architectural decisions.

The design must distinguish:

- Draft specifications
- Approved specifications
- Superseded specifications
- Decision history

Every approved specification must be traceable to a version and repository revision.

---

## 6. Persistent Memory and Decision History

The Harness maintains two categories of persistent information.

### 6.1 Shared Project Knowledge

Stored as Git-versioned Markdown in the repository.

Includes:

- Approved architectural decisions
- Design rationale
- Relevant project constraints
- Approved specifications
- Superseded decisions
- Change history

This information is shared among developers through normal Git workflows.

### 6.2 Local Execution State

Stored in a local SQLite database.

Includes:

- Active task state
- Interview progress
- Pending questions
- Agent execution state
- Local dashboard information
- Runtime events
- Temporary working data
- Resume checkpoints

The SQLite database does not need to be shared among developers.

Each developer maintains their own local runtime state.

### 6.3 Cross-Project Knowledge

The Harness may maintain reusable knowledge across projects.

Shared patterns can inform recommendations but must not automatically override project-specific decisions.

Cross-project knowledge must not expose confidential information from another repository without authorization.

### 6.4 Decision Versioning

Every significant approved decision records:

- Unique identifier
- Decision content
- Rationale
- Scope
- Approval status
- Version
- Timestamp
- Previous decision, if superseded
- Impact of changes

Decisions are automatically recorded after approval.

When a developer changes an earlier decision, the Harness presents its original rationale and the consequences of the proposed change.

The latest approved version is shown by default.

Older versions remain accessible.

---

## 7. Task Persistence and Recovery

Each task has an independent persistent workspace.

The Harness saves:

- User requests
- Interview answers
- Open questions
- Proposed solutions
- Approved decisions
- Diagrams and comments
- Test plans
- Specification versions
- AL execution progress
- Relevant runtime events

Interrupted tasks can resume from their saved state.

The system must avoid duplicating side effects when recovering from interruptions.

Approved execution must remain tied to the exact specification version that was authorized.

---

## 8. Web Dashboard

The Harness provides a lightweight local web application.

The dashboard is accessible through a local browser.

It serves as the primary interface for architecture review, approvals, and execution monitoring.

### 8.1 Task Overview

Display:

- Task name
- Task description
- Current phase
- Overall progress
- Current status
- Elapsed time
- Pending approvals
- Latest activity

### 8.2 Agent Monitoring

Display the total number of participating agents.

For each agent, show:

- Agent name
- Role
- Assigned task
- Current activity
- Status
- Progress
- Execution duration
- Dependencies
- Blockers
- Latest meaningful output

Supported statuses include:

- Idle
- Running
- Waiting
- Blocked
- Failed
- Completed

### 8.3 Progress Tracking

The dashboard must show:

- Overall task completion percentage
- Per-agent task progress
- Completed tasks
- Pending tasks
- Blocked tasks
- Current execution phase

Progress must be based on measurable completion or explicitly identified estimates.

The Harness must never fabricate completion percentages.

### 8.4 Agent Orchestration View

Visualize:

- Active agents
- Parallel work
- Task ownership
- Task dependencies
- Agent handoffs
- Blocked execution paths
- Completed execution paths

### 8.5 Live Activity Feed

Show meaningful events, including:

- Agent starts and completions
- Task assignments
- Design approvals
- Implementation milestones
- Test results
- Merge request updates
- Pipeline status changes
- Staging deployments
- Browser validation events
- Errors and blockers

Detailed logs should be available on demand.

### 8.6 Architecture Review

The dashboard provides interactive diagram viewing.

Users can:

- Select components
- Select connections
- Add comments
- Request redesign
- Review revised diagrams
- Resolve review comments
- Approve the final design

Direct diagram editing is not required for V1.

### 8.7 Approval Center

The dashboard presents all actions requiring explicit developer authorization.

Examples include:

- Architecture decisions
- Final specification approval
- Test plan approval
- Potentially mutating diagnostic operations
- Browser testing approval
- Changes to previously approved designs

### 8.8 GitLab and Delivery Monitoring

Display:

- Merge request reference
- Merge request status
- Pipeline status
- CI job results
- Staging deployment status
- Browser validation status
- Remaining delivery blockers

### 8.9 Multiple Tasks

The dashboard supports multiple independent tasks.

Each task maintains its own:

- Progress
- State
- Agent assignments
- History
- Approvals
- Specification

Developers can switch between tasks and resume interrupted sessions.

### 8.10 Local-Only Runtime Visibility

Each developer sees their own local Harness execution state.

Local SQLite runtime data is not shared automatically.

Approved project decisions and specifications remain available to other developers through Git.

### 8.11 Live Updates

The dashboard receives live status updates without requiring manual page refreshes.

Connection interruptions must be handled gracefully.

The dashboard must clearly distinguish current information from stale or unavailable status data.

---

## 9. AL Integration Contract

The Harness directly launches and supervises AL.

The integration must support:

- Starting an AL execution
- Passing the approved specification
- Passing the approved test plan
- Identifying the approved specification version
- Receiving agent lifecycle events
- Receiving task progress events
- Receiving blockers
- Receiving implementation results
- Receiving test results
- Receiving merge request information
- Receiving pipeline and deployment updates
- Recovering or reconnecting to an existing execution

Structured events are preferred over parsing terminal output.

The existing AL Orchestrator should be reused rather than reimplemented.

The Harness must not introduce a competing implementation orchestrator.

---

## 10. GitLab Integration

The Harness and AL use the official GitLab API.

Required capabilities include:

- Reading repository and branch information
- Reading merge request information
- Creating merge requests through the authorized execution workflow
- Reading pipeline status
- Reading CI job results
- Monitoring staging deployment status

Access must respect GitLab permissions and repository protections.

Credentials must not be stored in Markdown specifications, committed to Git, or exposed in dashboard logs.

---

## 11. Security and Authorization

The system must enforce a clear separation between investigation and mutation.

### Before Specification Approval

Allowed:

- Read-only codebase investigation
- Verified read-only diagnostic commands
- Authorized read-only database queries
- Requirements interviews
- Design generation
- Specification editing
- Diagram generation and revision
- Test planning

Not allowed without explicit authorization:

- Modifying application code
- Changing database records
- Modifying infrastructure
- Restarting or reconfiguring services
- Performing potentially destructive operations

### After Specification Approval

AL may perform authorized implementation work within the approved scope.

Actions outside that scope require additional authorization.

### General Security Requirements

- Least-privilege access
- Secure credential handling
- No secrets in Git-versioned specifications
- Redaction of sensitive logs
- Command risk classification
- Database access restrictions
- Audit records for significant operations
- No bypass of approval gates through automatic execution modes

---

## 12. Failure Handling

### Missing Requirement

Return to the interview phase.

### Architecture Conflict

Explain the conflict, propose options, and request developer approval.

### Missing Decision During Implementation

Block the affected task.

Allow independent work to continue.

### Agent Failure

Record the failure and provide diagnostic information.

AL may retry technical failures within its approved execution scope.

### Pipeline Failure

Report the failure and delegate technical correction to AL.

### Staging Test Failure

Collect available evidence and route the issue for investigation.

### Interrupted Session

Restore the latest consistent persisted state.

Avoid repeating completed external actions.

### External Integration Unavailable

Report the limitation clearly.

Do not fabricate successful execution or validation.

---

## 13. Proposed Logical Architecture

The Harness contains the following logical responsibilities.

### Harness Controller

Controls activation, lifecycle, and phase transitions.

### Repository Analyzer

Investigates the existing codebase and technical environment.

### Interview Engine

Collects requirements and resolves ambiguity.

### Architecture Designer

Coordinates architecture definition with the developer.

### Specification Manager

Builds and versions Markdown specifications.

### Decision Registry

Maintains approved decisions and rationale.

### Diagram Service

Generates and revises architecture diagrams.

### Test Planner

Produces reviewable test plans.

### Approval Controller

Enforces design and execution approval gates.

### Command Safety Controller

Classifies and authorizes investigative commands.

### Local State Manager

Persists runtime state in SQLite.

### Web Dashboard

Provides architecture review, monitoring, and approvals.

### AL Integration Adapter

Launches AL and receives structured execution events.

### GitLab Integration

Handles repository, merge request, and pipeline interactions.

### Browser Validation Controller

Coordinates approved staging acceptance tests.

### Reporting Service

Produces execution summaries.

These are logical responsibilities, not mandatory separate processes or microservices.

The exact implementation architecture will be determined in a later phase.

---

## 14. V1 Acceptance Criteria

The Harness V1 is considered complete when it can:

1. Activate only through an explicit developer command.
2. Investigate a repository before asking design questions.
3. Adapt interview depth to task complexity.
4. Ask for clarification instead of inventing important requirements.
5. Propose solutions while preserving developer decision authority.
6. Produce a complete Markdown specification.
7. Generate relevant architecture diagrams.
8. Accept and resolve diagram comments.
9. Produce an approved test plan.
10. Perform a mandatory completeness review.
11. Prevent implementation before explicit approval.
12. Persist tasks and resume interrupted sessions.
13. Store approved decisions in Git-versioned Markdown.
14. Maintain local runtime state in SQLite.
15. Preserve decision rationale and history.
16. Execute verified read-only investigations safely.
17. Require approval for potentially mutating operations outside authorized execution.
18. Directly invoke the existing AL Orchestrator.
19. Receive structured AL agent status and progress.
20. Display active agents, task progress, and blockers in the web dashboard.
21. Display live execution activity.
22. Track merge requests and CI/CD pipelines through GitLab.
23. Detect staging deployment readiness.
24. Request approval before browser-based staging validation.
25. Report browser acceptance test results.
26. Present a concise final execution summary.
27. Keep local execution state private to each developer.
28. Share approved specifications and architectural decisions through Git.
29. Prevent AL from silently making new architecture decisions.
30. Preserve traceability between approved specifications and implementation runs.

---

## 15. V1 Design Boundaries

This document defines the required behavior and architecture responsibilities of the Harness.

It does not yet prescribe:

- A specific backend framework
- A specific frontend framework
- Exact database tables
- Internal class or method names
- Detailed UI styling
- Specific browser automation APIs
- Exact command parser implementation
- A detailed development schedule

These are implementation-level decisions to be made during the subsequent engineering design and execution phase.

---

## 16. Final Design Principle

The Harness must make implementation predictable by ensuring that the developer understands and approves what will be built before AL begins.

The Harness should be proactive in investigation, recommendations, visualization, and validation.

It must be conservative in decision-making and authorization.

**Investigate first. Ask precisely. Design collaboratively. Approve explicitly. Implement through AL. Validate and report.**

---

**End of Design Specification V1**

**Approval status:** Pending developer confirmation.
