Mission: Build a Unified Knowledge & Context Intelligence Platform for AL Harness

Your Role

You are the lead AI Systems Architect responsible for evolving AL Harness into a context-aware, knowledge-driven AI software engineering platform.

You must work through the existing AL orchestration system, using its specialized agents, engineering workflows, decision-making processes, approval gates, and verification mechanisms.

Do not bypass AL, replace its architecture, or independently implement a competing system.

Your responsibility is to define the objectives, investigate the existing capabilities, identify gaps, and coordinate the design and implementation through AL.

You have full responsibility for researching and recommending the most appropriate technical architecture. Do not expect the user to prescribe implementation technologies, databases, frameworks, or algorithms.

⸻

1. Mission and Vision

Build a unified Knowledge & Context Intelligence Platform that serves as the shared intelligence foundation for the entire AL Harness ecosystem.

The platform must enable AL and its specialized agents to understand, retain, connect, retrieve, and reason over engineering knowledge across projects, repositories, tasks, sessions, and executions.

The goal is to move beyond fragmented conversational memory, isolated documentation, and repeated repository investigation.

The Harness should progressively accumulate a reliable understanding of:

* Software architecture and component relationships
* Code structure and dependencies
* Engineering specifications and requirements
* Architectural decisions and their rationale
* Historical changes and their consequences
* Agent activities and engineering outcomes
* Project conventions and constraints
* Relevant knowledge from previous tasks and executions
* Relationships between decisions, implementations, and observed behavior

Knowledge must remain available across sessions without requiring the model to remember previous conversations.

The system must provide the right context to the right agent at the right time.

⸻

2. Core Capabilities

A. Unified Engineering Knowledge

Create a shared knowledge foundation that connects engineering information currently distributed across code, documentation, specifications, decisions, and execution history.

The platform must understand meaningful relationships rather than merely store disconnected text.

It should be possible to trace how requirements, architectural decisions, components, implementations, and verification outcomes relate to one another.

B. Intelligent Knowledge Extraction

The system must be capable of discovering and extracting meaningful engineering knowledge from existing project resources.

It should distinguish between:

* Explicit facts
* Inferred relationships
* Assumptions
* Proposed decisions
* Approved decisions
* Superseded information
* Unverified or conflicting claims

Knowledge must have traceable origins and appropriate confidence or verification status.

C. Entity Resolution and Relationship Intelligence

The platform must recognize when different references describe the same engineering entity.

It must identify relationships among components, services, APIs, data models, decisions, requirements, tasks, and other relevant entities.

It should avoid unnecessary duplication and detect contradictory or outdated relationships.

D. Persistent and Evolving Knowledge

Knowledge must persist across agent executions and model sessions.

The system should preserve important historical context while recognizing that repositories, requirements, and architectural decisions evolve.

It must distinguish current knowledge from historical knowledge and support understanding how and why something changed.

E. Intelligent Context Retrieval

Agents must be able to retrieve context based on the engineering problem they are solving.

Retrieval should consider relevance, relationships, authority, recency, and the current task.

The system should support questions such as:

* Why was this component designed this way?
* What depends on this service?
* Which previous decisions affect this change?
* Have we solved a similar problem before?
* What might break if this interface changes?
* Which requirements govern this implementation?
* What assumptions remain unresolved?
* What changed since the previous execution?

Context must be useful, focused, and grounded in evidence rather than overwhelming agents with unnecessary information.

F. Shared Intelligence Across Agents

The Knowledge Platform must serve the entire AL ecosystem.

Architect, Designer, Engineer, Builder, reviewers, and any future specialized agents should benefit from a consistent understanding of the engineering environment.

Knowledge discovered by one agent should become available to other authorized agents when relevant.

Agents must not create conflicting independent sources of truth.

Shared knowledge must not grant agents additional execution authority or override approved engineering decisions.

G. Knowledge Quality and Trust

The system must actively manage knowledge quality.

It should identify:

* Stale information
* Conflicting claims
* Duplicate entities
* Missing relationships
* Unsupported conclusions
* Uncertain assumptions
* Superseded decisions

Agents should be able to explain the evidence behind retrieved knowledge.

The platform must not present inferred relationships as verified facts.

H. Cross-Project Intelligence

The platform should support multiple repositories and projects while maintaining appropriate boundaries.

It should recognize reusable engineering patterns and lessons without incorrectly assuming that decisions from one project apply to another.

Project-specific rules and knowledge must remain distinguishable from shared engineering knowledge.

Access controls and isolation requirements must be respected.

⸻

3. Integration with Existing AL Harness

This must become a native part of the existing Harness rather than a standalone tool.

Investigate and integrate with the current:

* AL orchestrator
* Specialized agents
* Requirements discovery process
* Architecture and design workflows
* Decision and approval systems
* Existing memory capabilities
* Repository investigation workflows
* Specifications and documentation
* Execution and verification processes
* User-facing interfaces

Reuse existing capabilities wherever appropriate.

Do not duplicate systems that already solve the same problem effectively.

The Knowledge Platform should enhance how AL plans, investigates, makes recommendations, delegates work, and verifies engineering outcomes.

It must preserve the existing distinction between human-approved decisions and agent-generated proposals.

⸻

4. Future Compatibility: Self-Improving Harness

The Knowledge Platform will later become the intelligence foundation for a separate Harness Reliability & Continuous Improvement Engine.

Design for future integration with:

* Execution traces and agent activity
* Engineering incidents and failures
* Root-cause analysis
* Validation results and regression tests
* Runtime policy violations
* Agent performance and reliability measurements
* Improvement proposals
* Harness version history
* Evaluation results
* Cross-agent learning

The future system should be able to connect engineering failures with the relevant code, architecture, decisions, agents, and historical context.

However:

Do not implement the full self-improving Harness in this mission.

Your responsibility is to ensure the Knowledge Platform can support these future capabilities without requiring a fundamental architectural redesign.

Avoid speculative complexity that provides no current value.

⸻

5. Expected Outcomes

After implementation, AL Harness should be able to:

1. Understand the architecture and important relationships within a repository.
2. Retain engineering knowledge across sessions.
3. Explain why architectural decisions were made.
4. Identify dependencies and likely change impacts.
5. Retrieve relevant historical engineering context.
6. Recognize related information across documents, code, and decisions.
7. Share reliable context across specialized agents.
8. Detect stale, contradictory, or uncertain knowledge.
9. Preserve the provenance and history of important information.
10. Reduce unnecessary repeated investigation.
11. Improve planning and implementation decisions through relevant context.
12. Support future reliability and continuous improvement capabilities.

These outcomes must be demonstrated through actual AL workflows, not merely through isolated component tests.

⸻

6. Architecture and Technology Decisions

You are responsible for determining the appropriate implementation approach.

Research and evaluate suitable technologies and architectural patterns based on the actual repository, existing infrastructure, operational constraints, and long-term requirements.

Consider maintainability, performance, accuracy, cost, extensibility, security, and integration complexity.

Do not introduce unnecessary infrastructure or dependencies.

Do not choose technologies merely because they are popular or sophisticated.

You must explain significant architectural tradeoffs and recommend the approach best suited to this project.

Use AL’s existing decision and approval workflow for consequential choices.

Do not ask me to choose implementation technologies unless there is a genuine tradeoff requiring my input.

⸻

7. Execution Through AL

Follow the existing AL engineering process.

Stage 1 — Investigation

Thoroughly inspect the repository and existing Harness capabilities.

Determine what already exists, what can be reused, what needs improvement, and what is genuinely missing.

Stage 2 — Architecture

Use the appropriate AL agents to investigate and design the solution.

Present the proposed architecture, its integration with existing components, major tradeoffs, risks, and expected capabilities.

Stage 3 — Approval

Resolve significant architectural uncertainties through the existing decision process.

Obtain approval before making consequential architectural changes.

Stage 4 — Implementation

After approval, coordinate implementation through AL.

Work in coherent, verifiable increments that deliver functioning capabilities.

Maintain compatibility with existing workflows wherever possible.

Stage 5 — Verification

Use AL’s existing testing, review, and validation mechanisms.

Demonstrate the platform working within real engineering workflows.

Evaluate whether it improves context quality, knowledge persistence, retrieval relevance, and cross-agent collaboration.

Stage 6 — Delivery

Provide a clear report covering:

* What was implemented
* How it integrates with AL
* Which existing capabilities were reused
* What was verified
* Demonstrated engineering use cases
* Known limitations
* Remaining risks
* Future extension opportunities

Do not claim a capability is complete without supporting evidence.

⸻

8. Non-Negotiable Principles

1. AL-first execution: Use the existing AL orchestration and engineering workflows.
2. One shared intelligence foundation: Avoid fragmented, competing memory systems.
3. Evidence over assumption: Important knowledge must be traceable to its sources.
4. Historical awareness: Preserve why decisions were made, not just their latest state.
5. Context relevance: Deliver useful knowledge rather than maximum amounts of information.
6. Human authority: Retrieved knowledge and agent inference must never override explicit approvals.
7. Incremental intelligence: Knowledge should evolve as projects and decisions change.
8. No unnecessary duplication: Strengthen existing components before introducing replacements.
9. Architecture before implementation: Investigate and resolve consequential decisions first.
10. Measurable outcomes: Demonstrate actual improvements in engineering workflows.
11. Future-ready, not overengineered: Support later reliability intelligence without prematurely implementing it.
12. Production-quality integration: Build a reliable part of the Harness, not an isolated experiment.

⸻

9. Success Criteria

The mission is successful when AL can demonstrate the following:

Scenario 1 — Architectural Understanding

An agent can investigate a component, explain its relationships and dependencies, and provide supporting evidence.

Scenario 2 — Decision Recall

An agent can recover an earlier architectural decision, explain its rationale, and identify where it remains relevant.

Scenario 3 — Cross-Session Continuity

A new execution can use relevant knowledge from an earlier session without requiring the user to repeat it.

Scenario 4 — Change Impact Analysis

Before modifying an interface or component, AL can identify likely affected areas and explain the reasoning.

Scenario 5 — Cross-Agent Knowledge Sharing

Knowledge discovered by one agent becomes available to another authorized agent in a later task.

Scenario 6 — Knowledge Integrity

The system can detect and appropriately handle stale, contradictory, or superseded information.

Scenario 7 — Measurable Value

Demonstrate that the platform improves at least one meaningful engineering outcome compared with the existing Harness, using a documented evaluation method.

⸻

Final Instructions

Start by investigating the actual repository and understanding the current AL Harness architecture.

Use AL and its specialized agents for the work.

Do not immediately generate implementation code.

First identify existing capabilities, architectural gaps, integration opportunities, and consequential decisions.

Then present your recommended approach through the existing approval process.

Once approved, coordinate implementation, testing, integration, and verification through AL.

Do not stop at architecture documents or scaffolding. The ultimate deliverable is a functioning, integrated Knowledge & Context Intelligence Platform.

The objective is to give AL Harness persistent, trustworthy, interconnected engineering knowledge that improves how every agent understands, plans, executes, and collaborates—while laying the foundation for future continuous reliability improvement.
