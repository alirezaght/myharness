"""Complete installations wrapped around the existing distribution transactions."""
import copy
from pathlib import Path
from . import journal as J
from .adapters import platform_resources
from .config import get, parse, plan_config, rollback_desired, same
from .engine import D, R, T, Roles
from .guards import (ActiveRuns, activity_roots, check_instructions, require_inactive,
                     resource_adoption, strict_package)
from .manifest import cache_release
from .payload_provenance import display_version, verify_expected
from .registry import (canonical, completed, coordination, digest, load_record, namespace,
                       read_json, record_path, records, reject_pending, write_json)
from .smoke import smoke_installation, smoke_release
from .source import resolve_release, safe_subdir, source_identity
from .targets import (config_path, package_path, review_for, roles_path,
                      target_descriptor, validate_target)


def check_snapshot(snapshot, state_root):
    journal = completed(snapshot)
    J.validate_backups(journal)
    manifest = verify_expected(snapshot['cache'], snapshot['release'])
    if manifest != snapshot['release']:
        raise ValueError('snapshot release identity mismatch')
    for role in [*snapshot['roles'].values(), *snapshot.get('retired_roles', {}).values()]:
        original = role['original']
        if original['state'] and (not original.get('blob') or T.state(R.safe_absolute(original['blob'])) != original['state']):
            raise ValueError('retained original role backup is corrupt')
    return manifest


def check_current(row, state_root):
    current = row.get('current') if row else None
    if not current:
        return
    check_snapshot(current, state_root)
    target = row['target']
    for platform, reference in current['packages'].items():
        root = package_path(target, platform)
        record, sha = R.load(Path(state_root), str(root), platform)
        if not record or sha != reference['sha256'] or record['generation'] != reference['generation']:
            raise ValueError('distribution ownership generation changed')
        for name, item in record['files'].items():
            if T.state(R.safe_absolute(root / R.relative(name))) != item:
                raise ValueError('managed resource drift: ' + str(root / name))
    for name, role in current['roles'].items():
        if T.state(R.safe_absolute(name)) != role['state']:
            raise ValueError('owned role drift: ' + name)
        if role.get('sentinel') and T.state(R.safe_absolute(role['sentinel']['path'])) != role['sentinel']['state']:
            raise ValueError('role ownership sentinel changed: ' + name)
    for name, role in current.get('retired_roles', {}).items():
        if role.get('sentinel') and T.state(R.safe_absolute(role['sentinel']['path'])) != role['sentinel']['state']:
            raise ValueError('retired role provenance changed: ' + name)
    for platform, config in current['configs'].items():
        path = config_path(target, platform)
        raw = path.read_bytes() if T.state(path) else None
        plan_config(raw, config['owned'], rollback_desired(config['owned']), format=config['format'])
        desired = platform_resources(current['cache'], current['release'], target, platform)['desired_config']
        document = parse(raw, config['format'])
        for key, value in desired.items():
            if not same(get(document, key.split('.')), value):
                raise ValueError('configuration wiring mismatch: ' + key)
    lock = current.get('lock')
    if lock and T.state(R.safe_absolute(lock['path'])) != lock['state']:
        raise ValueError('project lockfile drift')


def _role_preparation(resources, current, adoption):
    next_roles, retired, removals = {}, copy.deepcopy((current or {}).get('retired_roles', {})), []
    prior = (current or {}).get('roles', {})
    for name, desired in resources.items():
        actual = T.state(R.safe_absolute(name))
        if name in prior:
            if actual != prior[name]['state']:
                raise ValueError('owned role drift: ' + name)
            original = prior[name]['original']
        elif name in retired:
            if actual != retired[name]['released']:
                raise ValueError('relinquished role changed: ' + name)
            original = retired[name]['original']
            retired.pop(name)
        else:
            if actual:
                resource_adoption(name, actual, adoption)
            original = {'state': actual, 'blob': None}
        next_roles[name] = {'state': desired['state'], 'original': original}
    for name, prior_role in prior.items():
        if name in resources:
            continue
        if T.state(R.safe_absolute(name)) != prior_role['state']:
            raise ValueError('obsolete role drift: ' + name)
        original = prior_role['original']
        if original['state']:
            if not original.get('blob') or T.state(R.safe_absolute(original['blob'])) != original['state']:
                raise ValueError('adopted original role backup is corrupt')
        retired[name] = {'released': original['state'], 'original': original, 'sentinel': prior_role.get('sentinel')}
        removals.append((name, original))
    return next_roles, retired, removals


def _prepare(target, cache, manifest, row, policy, reviews, adoption, rollback=None, status_line=False):
    current = row.get('current')
    plans, all_roles, configs, preimages = {}, {}, {}, {}
    for platform in target['platforms']:
        resource = platform_resources(cache, manifest, target, platform)
        descriptor, owned, record = strict_package(target, platform, resource['files'], row['_state_root'], adoption)
        plans[platform] = resource
        for name in set(owned) | set(resource['files']):
            path = package_path(target, platform) / name
            preimages[str(path)] = T.state(path)
        all_roles.update(resource['roles'])
        if platform == 'claude' and status_line:
            script = package_path(target, platform) / 'scripts/al_statusline.py'
            import shlex
            resource['desired_config']['statusLine'] = {'present': True, 'value': {'type': 'command', 'command': 'python3 ' + shlex.quote(str(script))}}
        old_config = (current or {}).get('configs', {}).get(platform, {'owned': {}})
        if rollback:
            selected = rollback.get('configs', {}).get(platform, {'owned': {}})
            resource['desired_config'] = rollback_desired(selected['owned'])
        path = config_path(target, platform)
        state = T.state(path)
        preimages[str(path)] = state
        raw = path.read_bytes() if state else None
        format = 'toml' if platform == 'codex' else 'json'
        change = plan_config(raw, old_config['owned'], resource['desired_config'],
                             (reviews or {}).get('config', {}).get(str(path), {}), format=format)
        configs[platform] = {'path': str(path), 'format': format, 'owned': change['next_owned_keys'],
                             'raw': change['postimage_bytes'], 'mode': state['mode'] if state else 0o644}
    next_roles, retired, removals = _role_preparation(all_roles, current, adoption)
    for name in set(all_roles) | set((current or {}).get('roles', {})):
        preimages[name] = T.state(R.safe_absolute(name))
    lock = None
    if target['scope'] == 'repo':
        path = R.safe_absolute(Path(target['root']) / 'al.lock.json')
        state = T.state(path)
        preimages[str(path)] = state
        if state and not (current or {}).get('lock'):
            resource_adoption(path, state, adoption)
        raw = canonical({'schema_version': 1, 'version': display_version(manifest), 'release_digest': manifest['digest'],
                         'commit': manifest['commit'], 'source': manifest['source_identity'],
                         'source_subdir': manifest['subdir'], 'policy': policy}) + b'\n'
        lock = {'path': str(path), 'raw': raw, 'mode': state['mode'] if state else 0o644}
    return {'plans': plans, 'roles': all_roles, 'next_roles': next_roles, 'retired_roles': retired,
            'removals': removals, 'configs': configs, 'lock': lock, 'preimages': preimages}


def _recheck(prepared):
    for name, preimage in prepared['preimages'].items():
        if T.state(R.safe_absolute(name)) != preimage:
            raise ValueError('resource changed after preparation: ' + name)


def _source(request, row, state_root):
    enrolled = row.get('source')
    source = request.get('source') or (enrolled or {}).get('identity')
    if not source:
        raise ValueError('first installation requires --source to explicitly trust a Git repository')
    identity = source_identity(str(source))
    subdir = request.get('source_subdir')
    subdir = safe_subdir(subdir if subdir is not None else (enrolled or {}).get('subdir', 'al'))
    if enrolled and (identity != enrolled['identity'] or subdir != enrolled['subdir']):
        raise ValueError('source identity/subdirectory cannot change during update')
    path = namespace(state_root) / 'sources' / (digest(canonical({'identity': identity, 'subdir': subdir})) + '.json')
    prior = read_json(path) if path.exists() else {'identity': identity, 'subdir': subdir, 'tags': {}}
    if prior.get('identity') != identity or prior.get('subdir') != subdir or not isinstance(prior.get('tags'), dict):
        raise ValueError('source observation identity mismatch')
    release = resolve_release(identity, subdir, request.get('version'), prior['tags'])
    cache, manifest = cache_release(release, state_root)
    write_json(path, dict(prior, tags=release['observations']))
    return cache, manifest, {'identity': identity, 'subdir': subdir}


def check_target_overlap(target, rows):
    proposed = [path for p in target['platforms'] for path in
                (package_path(target, p), roles_path(target, p), config_path(target, p))]
    for existing in rows:
        other = existing['target']
        if other['target_id'] == target['target_id']:
            continue
        if R.safe_absolute(other['root']) == R.safe_absolute(target['root']):
            raise ValueError('overlapping installation target identity')
        occupied = [path for p in other['platforms'] for path in
                    (package_path(other, p), roles_path(other, p), config_path(other, p))]
        if any(a == b or a in b.parents or b in a.parents for a in proposed for b in occupied):
            raise ValueError('overlapping installation destinations')


def run_operation(request, state_root):
    """Request contains target, kind, optional version/source/policy/reviews/adoption/runtime_roots."""
    state_root = R.safe_absolute(state_root)
    target = request['target']
    kind = request.get('kind', 'install')
    validate_target(target, state_root, request.get('source'))
    with coordination(state_root):
        rows = records(state_root)
        check_target_overlap(target, rows)
        row = load_record(state_root, target['target_id'])
        if row and row['target'] != target:
            raise ValueError('registered target/platform identity differs')
        if kind != 'install' and not row:
            raise ValueError('target is not enrolled; install explicitly first')
        if kind == 'install' and row and row.get('current'):
            raise ValueError('target already enrolled; use update')
        row = row or {'schema_version': 1, 'target_id': target['target_id'], 'target': target,
                      'policy': 'follow', 'source': None, 'current': None, 'previous': None,
                      'pending': None, 'runtime_roots': []}
        reject_pending(row, state_root)
        check_current(row, state_root)
        roots = activity_roots(rows, target, [*row.get('runtime_roots', []), *request.get('runtime_roots', [])], request.get('invoking'))
        activity = require_inactive(roots)
        reviews, adoption = request.get('reviews') or {}, request.get('adoption') or {}
        reviewed = check_instructions(target, reviews)
        rollback = None
        if kind == 'rollback':
            rollback = row.get('previous')
            if not rollback:
                raise ValueError('no previous completed installation')
            manifest = check_snapshot(rollback, state_root)
            cache = Path(rollback['cache'])
            source = row['source']
            policy = rollback['policy']
        else:
            if kind == 'update' and row['policy'] == 'pinned' and not request.get('version') and request.get('policy') != 'follow':
                return {'target_id': target['target_id'], 'status': 'pinned', 'version': display_version(row['current']['release']), 'activity': activity}
            cache, manifest, source = _source(request, row, state_root)
            policy = request.get('policy') or ('pinned' if request.get('version') else row['policy'])
        validate_target(target, state_root, source['identity'])
        if policy not in {'follow', 'pinned'}:
            raise ValueError('invalid update policy')
        status_line = (bool(rollback.get('status_line')) if rollback else
                       request.get('status_line') or bool((row.get('current') or {}).get('status_line')))
        smoke_release(cache, manifest, target['platforms'])
        row_for_plan = dict(row, _state_root=str(state_root))
        prepared = _prepare(target, cache, manifest, row_for_plan, policy, reviews, adoption, rollback, status_line)
        if (kind == 'update' and row['current']['release']['digest'] == manifest['digest']
                and row['policy'] == policy and not request.get('status_line')):
            return {'target_id': target['target_id'], 'status': 'unchanged', 'version': display_version(manifest), 'activity': activity}
        return _execute(request, state_root, row, prepared, cache, manifest, source, policy,
                        roots, reviewed, reviews, adoption, status_line)


def _execute(request, state_root, row, prepared, cache, manifest, source, policy,
             roots, reviewed, reviews, adoption, status_line, *, workspace_guard=None):
    target = row['target']
    if workspace_guard is not None:
        from .workspace_payload import validate_payload
        validate_payload(manifest)
        if target['scope'] != 'global' or target['platforms'] != ['codex', 'claude']:
            raise ValueError('workspace guard requires both global platform targets')
    def activity_check():
        return workspace_guard() if workspace_guard is not None else require_inactive(roots)
    directory, path, journal = J.allocate(state_root, row['target_id'], request.get('kind', 'install'), row)
    candidate = {'operation': journal['id'], 'journal': str(path), 'cache': str(cache), 'release': manifest,
                 'policy': policy, 'packages': {}, 'roles': prepared['next_roles'],
                 'retired_roles': prepared['retired_roles'], 'configs': {}, 'lock': None,
                 'status_line': bool(status_line)}
    # Copy original adopted roles before any target mutation, retaining exact modes.
    for name, role in candidate['roles'].items():
        original = role['original']
        if original['state'] and not original.get('blob'):
            blob = directory / 'originals' / digest(name.encode())
            if T.state(R.safe_absolute(name)) != original['state']:
                raise ValueError('adopted role moved before backup')
            D.atomic(blob, Path(name).read_bytes(), original['state']['mode'])
            if T.state(blob) != original['state']:
                raise ValueError('original role backup mismatch')
            role['original'] = {'state': original['state'], 'blob': str(blob)}
    for platform, config in prepared['configs'].items():
        candidate['configs'][platform] = {'owned': config['owned'], 'format': config['format']}
        item = J.action(directory, config['path'], config['raw'], config['mode'], kind='config')
        if item:
            journal['ancillary_actions'].append(item)
    lock = prepared['lock']
    if lock:
        item = J.action(directory, lock['path'], lock['raw'], lock['mode'], kind='lock')
        if item:
            journal['ancillary_actions'].append(item)
        candidate['lock'] = {'path': lock['path'], 'state': {'sha256': digest(lock['raw']), 'mode': lock['mode']}}
    for name, original in prepared['removals']:
        raw = Path(original['blob']).read_bytes() if original['state'] else None
        item = J.action(directory, name, raw, original['state']['mode'] if original['state'] else 0o644, kind='retired-role')
        if item:
            journal['ancillary_actions'].append(item)
    journal['preimages'] = prepared['preimages']
    journal['instruction_reviews'] = reviewed
    journal['candidate'] = candidate
    J.checkpoint(journal, path, 'PREPARED')
    pending = dict(row, source=source, pending=journal['id'],
                   runtime_roots=sorted(set(row.get('runtime_roots', []) + list(request.get('runtime_roots', [])))))
    try:
        write_json(record_path(state_root, row['target_id']), pending)
        _recheck(prepared)
        check_current(row, state_root)
        if check_instructions(target, reviews) != reviewed:
            raise ValueError('instruction reviews changed')
        activity = activity_check()
        verify_expected(cache, manifest)
        J.checkpoint(journal, path, 'APPLYING')
        for platform, resource in prepared['plans'].items():
            if workspace_guard is not None:
                activity_check()
            # Engine repo_identity needs real directories; these creations remain journaled under pending.
            J.ensure_directory(package_path(target, platform), journal, path)
            J.ensure_directory(roles_path(target, platform), journal, path)
            descriptor, _, _ = strict_package(target, platform, resource['files'], state_root, adoption)
            review = review_for(Path(descriptor['path']), descriptor['identity'], reviews)
            engine_manifest = D.plan(Path(resource['package']), [descriptor], {descriptor['path']: review},
                                     registry=state_root, prior_managed=adoption if 'targets' in adoption else None)
            items = []
            for name, role in resource['roles'].items():
                identity = D.repo_identity(Path(name).parent)
                items.append({'source': role['source'], 'target': name,
                              'protocol_reason': 'versioned AL platform role installation',
                              'instruction_review': review_for(Path(name).parent, identity, reviews)})
            engine_manifest['role_changes'] = Roles.plan_roles(items)
            backup = directory / ('engine-' + platform)
            step = {'platform': platform, 'manifest': engine_manifest, 'backup': str(backup), 'status': 'PLANNED'}
            journal['engine_steps'].append(step)
            write_json(path, journal)
            D.apply(engine_manifest, backup)
            step['status'] = 'COMPLETE'
            step['journal_sha256'] = D.sha(backup / 'distribution-journal.json')
            record, sha = R.load(state_root, descriptor['path'], platform)
            candidate['packages'][platform] = {'path': str(R.record_path(state_root, descriptor['path'], platform)),
                                                'sha256': sha, 'generation': record['generation']}
            for name in resource['roles']:
                sentinel = state_root / ('role-' + R.key(name, 'role') + '.json')
                candidate['roles'][name]['sentinel'] = {'path': str(sentinel), 'state': T.state(sentinel)}
            write_json(path, journal)
        for item in journal['ancillary_actions']:
            if workspace_guard is not None:
                activity_check()
            J.execute_ancillary(item, journal, path)
        J.checkpoint(journal, path, 'VERIFYING')
        for step in journal['engine_steps']:
            D.verify(step['manifest'])
        for item in journal['ancillary_actions']:
            if T.state(R.safe_absolute(item['target_path'])) != item['source']:
                raise ValueError('ancillary verification failed')
        for platform, config in prepared['configs'].items():
            raw = Path(config['path']).read_bytes() if T.state(config['path']) else None
            plan_config(raw, config['owned'], rollback_desired(config['owned']), format=config['format'])
        journal['verification'] = {'smoke': smoke_installation(target, candidate['roles']), 'activity': activity}
        # The completed snapshot references the exact resulting engine generations.
        for platform, reference in candidate['packages'].items():
            record, sha = R.load(state_root, str(package_path(target, platform)), platform)
            if sha != reference['sha256'] or record['generation'] != reference['generation']:
                raise ValueError('ownership generation changed during verification')
        if workspace_guard is not None:
            activity_check()
        J.publish_completed(pending, candidate, journal, path, state_root)
        return {'target_id': row['target_id'], 'status': 'updated', 'version': display_version(manifest),
                'operation': journal['id'], 'activity': activity}
    except BaseException as exc:
        if J.publication_succeeded(state_root, row['target_id'], journal['id']):
            # Atomic publication may have succeeded even if its caller saw an I/O error.
            completed(load_record(state_root, row['target_id'])['current'])
            return {'target_id': row['target_id'], 'status': 'updated', 'version': display_version(manifest),
                    'operation': journal['id'], 'publication': 'confirmed after write uncertainty'}
        if journal['status'] != 'COMPLETE':
            journal.update(error=str(exc), recovery='Do not retry; inspect this journal, engine journals, preimages and backups. Pending requires manual reconciliation.')
            try:
                J.checkpoint(journal, path, 'FAILED')
            except (OSError, ValueError):
                pass
        raise


def rollback_previous(target, state_root, **options):
    return run_operation(dict(options, target=target, kind='rollback'), state_root)


def update_registered(selection, state_root, **options):
    rows = records(state_root)
    if selection is not None:
        rows = [row for row in rows if row['target_id'] in selection]
        if len(rows) != len(selection):
            raise ValueError('selected target is not enrolled')
    results = []
    for row in rows:
        try:
            result = run_operation(dict(options, target=row['target'], kind='update'), state_root)
        except ActiveRuns as exc:
            result = {'target_id': row['target_id'], 'status': 'deferred', 'error': str(exc)}
        except Exception as exc:
            result = {'target_id': row['target_id'], 'status': 'failed', 'error': type(exc).__name__ + ': ' + str(exc)}
        results.append(result)
    return results
