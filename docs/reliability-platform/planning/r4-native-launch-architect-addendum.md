PLAN_STATUS: REVISED

RESOLUTION

Confirm the minimal additional ownership and reciprocal native-launch guard. This closes the identified race within the already authorized narrow Designer protocol.

In the immutable R3 source, `execution_worker.py:720` commits `Launching`, while `native_admission()` and `Popen` occur later around `:792`. `policy/reconciliation.py:26` checks selected policies but does not serialize launch against strict source claims. Checking existing native PIDs during strict-read admission therefore cannot exclude a concurrent launch.

IMPLEMENTATION_PLAN

- Extend Engineer ownership to `execution_worker.py`, `policy/reconciliation.py` and the exact native no-start/settlement integration needed for this protocol.
- In the existing short, current-epoch transaction, check conflicting strict claims and commit trusted native launch presence together. Strict source admission must inspect that pending/launching presence in its own atomic check-and-reserve transaction.
- Commit before `Popen`; never hold database or epoch locks while launching or waiting.
- Treat recorded launch intent as potential execution, not qualified closure. Clear it only through exact fenced no-start proof or applicable qualified closure—not status changes, missing PID, parent exit or group disappearance.
- Preserve the existing Attempt and dependency claims before hashes through actual consumption. An outer unqualified native producer receives no same-execution or same-principal exemption.
- Relevant active immutable bridges lacking this reciprocal protocol remain unsupported for strict coordination. Module presence is insufficient qualification; do not modify old pins.
- Permit the proposed separately qualified `sealed-al-writer-handoff-v1` within the earlier resolution: exact method arguments, authenticated writer/areas/phase/graph bindings, immutable body buffer delivered through the fixed private channel, complete source/interpreter/pin/helper qualification, and method-specific normal completion. Preserve existing handoff/save guards. No generic AL exemption or Git grammar expansion.

INVARIANTS

- Immutable assignment presentation remains available without asserting mutable-source stability.
- Native presence, AL publication and qualified execution closure are distinct facts.
- Existing legacy liabilities remain intact.
- Missing strict coordination capability refuses affected source consumption; it does not block independent immutable adapters or the entire mission.

ACCEPTANCE_CRITERIA

- Barrier tests race strict source reservation against native launch: whichever transaction wins prevents unsafe overlap.
- A committed launch intent with no PID remains a predecessor; exact fenced no-start can release it.
- An active unqualified outer native process cannot consume strict mutable dependencies through a self-exemption.
- Unsupported old bridges refuse strict coordination without pin mutation.
- Exact qualified handoff generation preserves publication semantics and does not leave a false unknown legacy producer; interrupted or unqualified handoffs retain uncertainty.
- Assignment snapshot delivery remains usable when mutable-source consumption is unavailable.
- Tests preserve current epoch fencing, lock ordering and claims-through-use.

Designer and root received this scope directly. No edits, checks, services or formal review actions were performed.
