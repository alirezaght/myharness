# Separate acceptance dimensions

`acceptance status` reports code_assessment, provider_verification and business_acceptance with current task/source/authority binding. It never equates code review with provider PASS or business approval. Per-provider rows retain real results; a provider FAIL outranks another provider BLOCKED in the aggregate.

`acceptance packet` supplies the current binding and criterion registry for a separate diagnostic code assessment. Submit `acceptance assess --file FILE` with schema_version 1, kind code-assessment-v1, binding, reviewer, and criteria. Each criterion has id, status (SATISFIED, UNSATISFIED or BLOCKED), rationale, concrete locations and evidence. SATISFIED requires current valid evidence. Derived status is CODE_ACCEPTABLE, CHANGES_REQUIRED or BLOCKED; authorizes_approval is always false. Changed source or evidence makes the assessment STALE. This artifact cannot be submitted as a formal v2 Architect review or clear findings.

Business acceptance is NOT_APPLICABLE unless the accepted task package explicitly declares business_acceptance with id, required, source and description. Required decisions use `acceptance business --file FILE`: schema_version 1, kind business-acceptance-v1, current binding, requirement_id, decision (ACCEPTED or REJECTED), exact authorized source and rationale. The decision is immutable and stale after task/source changes. No routine internal task acquires a mandatory human signoff.

Final approval still requires independent formal fresh/comparison review, all current mandatory checks and any explicit business requirement. Publication payloads retain their existing closed contracts; acceptance is available through its own endpoint and `inspect --sections acceptance`.
