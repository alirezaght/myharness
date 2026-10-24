"""Complete inputs of positively supported read-only local execution profiles."""
import copy
import math

import al_gate_reuse as R
import al_task
import al_verification_inputs as Inputs

# Manifests and lockfiles of every ecosystem: a change to any of them changes what a run installs.
DEPENDENCY_LOCKS = {'uv.lock', 'pyproject.toml', 'poetry.lock', 'Pipfile.lock', 'requirements.txt', 'package.json',
                    'package-lock.json', 'pnpm-lock.yaml', 'yarn.lock', 'bun.lockb', 'go.mod', 'go.sum', 'Cargo.toml',
                    'Cargo.lock', 'Gemfile.lock', 'composer.lock', 'pom.xml', 'gradle.lockfile', 'packages.lock.json'}


def epoch(st):
    evidence = st.get('evidence', {})
    return Inputs.digest({'global': evidence.get('invalidated_at'),
                          'scoped': evidence.get('scope_invalidations', [])})


def project_execution(st, spec, materialization, preparation, timeout):
    if (preparation.get('blockers') or len(preparation.get('command_profiles', [])) != 1
            or preparation.get('spec') != spec):
        raise ValueError('execution requires a current complete preparation')
    profile = preparation['command_profiles'][0]
    if (profile['id'] not in {'isolated-json-tool-v1', 'bundled-module-ban-v1'}
            or profile['stage_independent'] is not True or profile['argv'] != spec['argv']
            or spec.get('sources') or spec.get('execution_kind', 'local') != 'local'):
        raise ValueError('execution profile has no complete reusable closure')
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('finite positive execution timeout required')
    # Only positively reviewed selectors may omit unrelated source/Git inputs.
    scratch = R.scratch_state(materialization['root'])
    from al_verification_scope import materialized_binding
    work = materialized_binding(profile, scratch['work'])
    for path in profile['repository_inputs']:
        if path not in work or work[path][0] != 'file':
            raise ValueError('materialized verification input missing or nonregular')
    value = {'schema_version': 1, 'kind': 'execution-projection-v1',
            'repository_identity': copy.deepcopy(st['identity']),
            'worktree_identity': st['identity']['root'], 'run_id': st['run_id'],
            'task_hash': al_task.task_hash(st), 'invalidation_epoch': epoch(st),
            'runtime_binding': copy.deepcopy(preparation['runtime_binding']),
            'verifier_policy_binding': {'version': 1, 'profile': profile['id'],
                                        'implementation': preparation['runtime_binding']['implementation']},
            'adapter_resource_binding': copy.deepcopy(profile['resources']),
            'execution_profile': dict(copy.deepcopy(profile), timeout=timeout,
                                      stage_independence_proof={'profile': profile['id'], 'version': 1,
                                                                'observes_git': False, 'writes': False}),
            'source_projection': {'records': work, 'membership': sorted(work), 'absent_paths': [],
                                  'relevant_git_state': {'observes_git': False, 'profile': profile['id']},
                                  'source_capabilities': copy.deepcopy(spec.get('sources', [])),
                                  'configuration': copy.deepcopy(preparation['configuration_bindings'])},
            'environment': copy.deepcopy(preparation['environment_policy']),
            'setup_binding': copy.deepcopy(preparation['setup_recipe']),
            'dependency_lock_binding': {p: row for p, row in work.items()
                                        if p.rsplit('/', 1)[-1] in DEPENDENCY_LOCKS},
            'prerequisites': copy.deepcopy(preparation['prerequisite_receipts']),
            'isolation_policy': copy.deepcopy(preparation['isolation_policy']),
            'generated_output_policy': profile['generated_output_policy']}
    from al_execution_versions import enable
    return enable(st, value)


def current_gaps(st, projection):
    import al_runtime_resources as Resources
    gaps = []
    from al_execution_versions import version, bridge, enable
    number = version(projection, 'execution-projection')
    if number == 2:
        bridge(st, projection, enable(st, projection))
    if (projection['repository_identity'] != st['identity'] or projection['run_id'] != st['run_id']
            or projection['worktree_identity'] != st['identity']['root']):gaps.append('foreign execution identity')
    if number == 1 and projection['task_hash'] != al_task.task_hash(st):gaps.append('execution task revision differs')
    if projection['invalidation_epoch'] != epoch(st):gaps.append('execution invalidation epoch differs')
    expected = {'implementation': Resources.implementation_digest(), 'origin': Resources.execution_origin()}
    if projection['runtime_binding'] != expected:gaps.append('execution runtime/policy differs')
    gaps += Inputs.environment_gaps(projection['environment'])
    return gaps
