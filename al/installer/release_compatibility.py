"""Trusted historical minimums, independent of today's additive resources."""
import re

# Source-audited v0.1.3--v0.1.24 layouts. Keep this contract fixed when new
# workspace requirements are introduced; never infer it from missing files.
_HISTORICAL = frozenset("""
al_acceptance.py al_analysis_cache.py al_architecture_check.py al_artifact_cli.py
al_behavior_obligations.py al_behavior_observation_items.py al_behavior_observations.py
al_behavior_sections.py al_check_setup.py al_ci_environment.py al_ci_github.py
al_ci_verification.py al_counterexample_search.py al_distribution.py
al_distribution_registry.py al_distribution_roles.py al_distribution_transaction.py
al_environment_observation.py al_events.py al_evidence_supersession.py
al_execution_contract.py al_execution_corrections.py al_execution_decisions.py
al_execution_projection.py al_execution_receipts.py al_execution_tasks.py
al_execution_versions.py al_failures.py al_gate_continuation.py al_git_cache.py
al_harness_process.py al_inspection.py al_new_origin.py al_planning.py
al_planning_attempts.py al_planning_template.py al_profile.py al_proof_json.py
al_publication.py al_publication_checks.py al_publication_contract.py
al_publication_records.py al_pytest_profile.py al_pytest_runner.py al_pytest_sources.py
al_python_imports.py al_recovery_gates.py al_recovery_guidance.py
al_recovery_prerequisites.py al_recovery_subjects.py al_recovery_types.py
al_repo_adapters.py al_repo_contract.py al_repo_python.py al_repository_audit.py
al_repository_facts.py al_requirement_bindings.py al_review_behavior.py
al_review_rules.py al_rework_recovery.py al_rework_recovery_attempts.py
al_rework_recovery_contract.py al_runtime_resources.py al_schema.py
al_service_coverage.py al_start.py al_state.py al_task_attribution.py
al_task_editorial.py al_task_reconciliation.py al_task_risk.py
al_verification_authority.py al_verification_inputs.py al_verification_prepare.py
al_verification_scope.py al_writer_handoff.py
""".split())
_INTRODUCED_013 = frozenset("""
al_acceptance.py al_check_setup.py al_evidence_supersession.py al_repository_audit.py
al_rework_recovery.py al_rework_recovery_attempts.py al_rework_recovery_contract.py
al_task_risk.py
""".split())
# Helpers first required by the release after the named published version. Published
# releases up to that version keep their genuine layout; never infer it from missing files.
_INTRODUCED_AFTER = {'al_planning_revision.py': (0, 1, 71)}
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def required_scripts(layout, release=None):
    """Only a canonical, explicitly supported release can use a historical set."""
    current = frozenset(layout['required_scripts'])
    if release is None:
        return current
    version = release.get('version')
    if (not isinstance(version, str) or len(version) > 64
            or not _VERSION.fullmatch(version) or release.get('tag') != 'v' + version):
        raise ValueError('invalid release version/tag identity')
    major, minor, patch = map(int, version.split('.'))
    if (major, minor) != (0, 1):
        # Unsupported release lines (0.0.x, 1.x, ...) were never audited: the full current set.
        return current
    if patch > 24:
        # Only the audited published 0.1 interval keeps a later helper's historical omission.
        return frozenset(name for name in current
                         if (major, minor, patch) > _INTRODUCED_AFTER.get(name, (-1,)))
    required = _HISTORICAL
    if patch < 3:
        required = required - _INTRODUCED_013
    if patch == 0:
        required = required - {'al_writer_handoff.py'}
    return required
