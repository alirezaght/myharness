"""Explicit disruptive package replacement; observations never grant runtime authority."""
import copy
import itertools
import os
import shlex
from pathlib import Path
import stat

from .adapters import platform_resources
from .engine import R, T
from .guards import activity_roots, check_instructions
from .manifest import build_manifest
from .operator_projection import projection
from .payload_provenance import payload_kind, verify_expected
from .registry import canonical, digest, namespace, read_json, records, load_record
from .source import TAG, resolve_release
from .targets import package_path, roles_path, validate_target
from .validator_origin import capture, decode

LIMITS = ['running-or-unknown-consumers-may-be-disrupted', 'legacy-independence-and-settlement-not-assessed',
          'no-execution-or-approval-authority', 'bootstrap-is-a-separate-publication',
          'partial-visibility-and-manual-reconciliation']
SCAN_LIMIT = 10000


def validate_request(request, row):
    version = request.get('version')
    if (request.get('allow_running') is not True or request.get('kind') != 'update' or request['target']['scope'] != 'global'
            or not row or not row.get('current') or request['target'] != row['target']
            or not isinstance(version, str) or not TAG.fullmatch(version if version.startswith('v') else 'v' + version)):
        raise ValueError('--allow-running requires an enrolled single global update and explicit numeric version')
    if (request.get('all') or request.get('adoption') or request.get('documentation_only')
            or request.get('reviewed_runtime_update') or request.get('status_line')
            or request.get('policy') is not None or request.get('source') is not None
            or request.get('source_subdir') is not None):
        raise ValueError('--allow-running forbids batch, adoption, mixed modes, source, policy and configuration overrides')
    if not request.get('operator_review_artifact'):
        raise ValueError('--allow-running requires a hash-bound --reviews artifact')


def identity(manifest):
    return {key: manifest[key] for key in ('version', 'tag', 'tag_object', 'commit', 'digest', 'source_identity', 'subdir')}


def delta(old, new):
    changed, unchanged = {}, {}
    for name in sorted(old['files'].keys() | new['files'].keys()):
        before, after = old['files'].get(name), new['files'].get(name)
        if before != after:
            changed[name] = {'before': before, 'after': after}
        else:
            unchanged[name] = before
    return changed, digest(canonical(unchanged))


def reference(value):
    if (not isinstance(value, dict) or set(value) != {'path', 'sha256'}
            or not isinstance(value['path'], str) or not Path(value['path']).is_absolute()
            or not isinstance(value['sha256'], str) or len(value['sha256']) != 64):
        raise ValueError('exact absolute review/provenance reference required')
    path = R.safe_absolute(value['path'])
    raw, _ = capture(path)
    metadata = path.stat()
    if (not raw.strip() or metadata.st_uid != os.getuid() or metadata.st_nlink != 1
            or digest(raw) != value['sha256']):
        raise ValueError('review/provenance changed or has unsafe ownership/alias')
    return raw


def observe(state_root, rows, target, explicit=(), invoking=None):
    """Read bounded metadata only; no durable validation, process census or prose output."""
    roots, gaps, counts = [], [], {}
    directory_budget, metadata_budget = SCAN_LIMIT, SCAN_LIMIT
    def note(category):
        counts[category] = counts.get(category, 0) + 1
    try:
        roots = activity_roots(rows, target, explicit, invoking, state_root=None)
    except (ValueError, OSError, TypeError, KeyError):
        gaps.append('repository-root-discovery-unavailable')
        roots = [(Path(path), None) for path in explicit]
    storage = R.safe_absolute(state_root) / 'al-run-pins'
    # Discover physical durable directories without trusting retained state/index paths.
    pending = [storage / 'harness-runtimes']
    for _ in range(3):
        following = []
        for path in pending[:SCAN_LIMIT]:
            try:
                path = R.safe_absolute(path)
                if path.exists():
                    entries = list(itertools.islice(path.iterdir(), directory_budget + 1))
                    if len(entries) > directory_budget:
                        gaps.append('durable-directory-enumeration-truncated')
                    selected = entries[:directory_budget]
                    directory_budget -= len(selected)
                    for entry in selected:
                        if entry.is_dir() and not entry.is_symlink():
                            following.append(entry)
            except (ValueError, OSError):
                gaps.append('durable-directory-unreadable-or-unsafe')
        if len(following) > SCAN_LIMIT:
            gaps.append('durable-directory-enumeration-truncated')
        pending = following[:SCAN_LIMIT]
    roots.extend((path, None) for path in pending)
    inspected = []
    # Index files are observations too; RESERVED is unknown, never settlement proof.
    roots.append((storage / 'runs', None))
    for path, _ in roots[:SCAN_LIMIT]:
        label = str(Path(path).expanduser().absolute())
        if label in inspected:
            continue
        inspected.append(label)
        try:
            root = R.safe_absolute(path)
            if not root.exists():
                continue
            if not root.is_dir():
                gaps.append('runtime-root-not-directory'); continue
            entries = list(itertools.islice(root.glob('*.json'), metadata_budget + 1))
            if len(entries) > metadata_budget:
                gaps.append('metadata-enumeration-truncated')
            selected = entries[:metadata_budget]
            metadata_budget -= len(selected)
            for entry in selected:
                try:
                    entry = R.safe_absolute(entry)
                    if entry.stat().st_size > 16 * 1024 * 1024:
                        note('oversize'); continue
                    value = read_json(entry)
                    if not isinstance(value, dict):
                        note('unsupported'); continue
                    if root == storage / 'runs':
                        note('index-' + value['status'] if value.get('status') in {'ACTIVE', 'RESERVED', 'SUPERSEDED'} else 'index-unknown')
                        continue
                    if type(value.get('schema_version')) is not int or value.get('schema_version') != 2:
                        note('unsupported'); continue
                    note(value['status'] if value.get('status') in {'RUNNING', 'APPROVED', 'INTERRUPTED', 'STOPPED_WITH_BLOCKERS'} else 'unknown')
                    writers = value.get('writers')
                    if not isinstance(writers, dict):
                        note('unknown-writers')
                    elif any(not isinstance(w, dict) or w.get('state') not in {'FINISHED', 'RECONCILED'} for w in writers.values()):
                        note('active-or-unsettled-writers')
                    evidence = value.get('evidence')
                    if not isinstance(evidence, dict) or not isinstance(evidence.get('entries'), list):
                        note('unknown-evidence')
                    elif any(not isinstance(e, dict) or e.get('status') not in {'PASS', 'FAIL', 'EMPTY', 'INFRA', 'TORN'} for e in evidence['entries']):
                        note('active-or-unsettled-evidence')
                except (ValueError, OSError, TypeError, KeyError, RecursionError):
                    note('unreadable-or-malformed')
        except (ValueError, OSError, TypeError):
            gaps.append('runtime-root-unreadable-or-unsafe')
    if len(roots) > SCAN_LIMIT:
        gaps.append('runtime-root-enumeration-truncated')
    return {'kind': 'bounded-activity-warning-v1', 'roots': inspected, 'counts': counts,
            'gaps': sorted(set(gaps)), 'metadata_file_limit': SCAN_LIMIT, 'durable_directory_entry_limit': SCAN_LIMIT,
            'coverage': 'registered/invoking/explicit and physically discovered durable metadata only; no exhaustive process census',
            'settlement': 'NOT_ASSESSED', 'legacy_independence': 'NOT_ASSESSED', 'accepted_limits': LIMITS}


class OperatorGlobalUpdate:
    """Typed operation-scoped evidence at preparation, apply and publication barriers."""

    def __init__(self, request, row, prepared, cache, manifest, state_root, instruction_reviews, held, registry_proof):
        validate_request(request, row)
        self.request, self.row = copy.deepcopy(request), copy.deepcopy(row)
        self.prepared, self.live_prepared = copy.deepcopy(prepared), prepared
        self.cache, self.manifest, self.state_root = Path(cache), copy.deepcopy(manifest), Path(state_root)
        self.instructions = copy.deepcopy(instruction_reviews)
        self.held, self.registry_proof = held, copy.deepcopy(registry_proof)
        self.source_path = namespace(state_root) / 'sources' / (digest(canonical(row['source'])) + '.json')
        self.source_observation = read_json(self.source_path)
        self.receipt = self.revalidate()

    def _plan(self, applied, observation):
        live, frozen = copy.deepcopy(self.live_prepared), copy.deepcopy(self.prepared)
        for plan in (live, frozen):
            for role in plan['next_roles'].values():
                role.pop('sentinel', None)
        if live != frozen:
            raise ValueError('operator update prepared plan changed')
        current, prepared, target = self.row['current'], self.prepared, self.row['target']
        if (prepared['lock'] or prepared['removals'] or prepared['retired_roles'] != current.get('retired_roles', {})
                or set(prepared['next_roles']) != set(current['roles'])):
            raise ValueError('operator update cannot change role membership/retirement/lock')
        protected = [self.state_root, Path(current['cache']), self.cache]
        if not self.row['source']['identity'].startswith('https://'):
            protected.append(R.safe_absolute(self.row['source']['identity']))
        for value in [*self.row.get('runtime_roots', []), *self.request.get('runtime_roots', []), *observation['roots']]:
            lexical = Path(value).expanduser().absolute()
            protected.append(lexical)
            try:
                protected.append(lexical.resolve())
            except (OSError, RuntimeError, ValueError):
                observation['gaps'] = sorted(set([*observation['gaps'], 'runtime-root-canonicalization-unavailable']))
        configurations = {}
        for platform in target['platforms']:
            new_resource = platform_resources(self.cache, self.manifest, target, platform)
            old_resource = platform_resources(current['cache'], current['release'], target, platform)
            if platform == 'claude' and current.get('status_line'):
                for resource in (old_resource, new_resource):
                    resource['desired_config']['statusLine'] = {'present': True, 'value': {
                        'type': 'command', 'command': 'python3 ' + shlex.quote(str(package_path(target, platform) / 'scripts/al_statusline.py'))}}
            if prepared['plans'][platform] != new_resource:
                raise ValueError('operator update prepared resource mapping differs')
            if new_resource['desired_config'] != old_resource['desired_config']:
                raise ValueError('operator update desired host configuration differs')
            config = prepared['configs'][platform]
            state = T.state(R.safe_absolute(config['path']))
            raw = Path(config['path']).read_bytes() if state else None
            if (state != prepared['preimages'][config['path']] or raw != config['raw']
                    or config['owned'] != current['configs'][platform]['owned']):
                raise ValueError('operator update host configuration/preimage differs')
            self._destination(Path(config['path']), Path(target['root']), protected)
            configurations[platform] = {'desired': old_resource['desired_config'], 'state': state,
                                        'owned': config['owned'], 'raw': raw.decode('utf-8') if raw is not None else None}
            expected = new_resource if platform in applied else old_resource
            ownership, sha = R.load(self.state_root, str(package_path(target, platform)), platform)
            record = applied[platform] if platform in applied else current['packages'][platform]
            if (not ownership or sha != record['sha256'] or ownership['generation'] != record['generation']
                    or ownership['files'] != expected['files']):
                raise ValueError('operator update ownership generation/files changed')
            for name in old_resource['files'].keys() | new_resource['files'].keys():
                path = package_path(target, platform) / R.relative(name)
                self._destination(path, package_path(target, platform), protected)
                if T.state(path) != expected['files'].get(name):
                    raise ValueError('operator update resource preimage/postimage differs: ' + name)
            for name, item in new_resource['roles'].items():
                path = Path(name)
                self._destination(path, roles_path(target, platform), protected)
                if T.state(path) != expected['roles'][name]['state']:
                    raise ValueError('operator update role preimage/postimage differs')
                if prepared['next_roles'][name]['state'] != item['state']:
                    raise ValueError('operator update prepared role state differs')
                sentinel = (self.live_prepared['next_roles'][name] if platform in applied else current['roles'][name]).get('sentinel')
                if (not sentinel or sentinel['path'] != str(self.state_root / ('role-' + R.key(name, 'role') + '.json'))
                        or T.state(R.safe_absolute(sentinel['path'])) != sentinel['state']):
                    raise ValueError('operator update role ownership sentinel changed')
                if prepared['next_roles'][name]['original'] != current['roles'][name]['original']:
                    raise ValueError('operator update original role provenance changed')
        return configurations

    def _destination(self, path, root, protected):
        path, root = R.safe_absolute(path), R.safe_absolute(root)
        if path == root or root not in path.parents:
            raise ValueError('operator update destination escapes enrolled mutable root')
        if any(path == p or p in path.parents or path in p.parents for p in protected):
            raise ValueError('operator update destination overlaps protected storage/runtime/source')
        home = Path(self.row['target']['root'])
        for parent in (p for p in path.parents if p == home or home in p.parents):
            if parent.exists() and (not parent.is_dir() or parent.stat().st_uid != os.getuid()):
                raise ValueError('operator update destination directory ownership differs')
        if path.exists():
            value = path.stat()
            if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or value.st_uid != os.getuid():
                raise ValueError('operator update destination has unsafe ownership/alias/type')

    def revalidate(self, *, applied=None, operation=None):
        applied = applied or {}
        from .operator_registry import revalidate
        issuer = revalidate(self.state_root, self.held, self.row, self.registry_proof)
        old, new, target = self.row['current']['release'], self.manifest, self.row['target']
        registered = load_record(self.state_root, target['target_id'])
        if (not isinstance(registered, dict) or any(registered.get(k) != self.row.get(k) for k in ('current', 'previous', 'source', 'policy', 'target'))
                or registered.get('pending') != operation):
            raise ValueError('operator update enrolled installation record changed')
        if payload_kind(old) != 'tagged' or payload_kind(new) != 'tagged':
            raise ValueError('operator update requires numeric tagged payloads')
        verify_expected(self.row['current']['cache'], old)
        verify_expected(self.cache, new)
        if (old['source_identity'], old['subdir']) != (new['source_identity'], new['subdir']):
            raise ValueError('operator update release source identity differs')
        validate_target(target, self.state_root, self.row['source']['identity'])
        if read_json(self.source_path) != self.source_observation:
            raise ValueError('operator update source observations changed')
        source = self.row['source']
        if build_manifest(resolve_release(source['identity'], source['subdir'], self.request['version'], self.source_observation['tags'])) != new:
            raise ValueError('operator update source tag/payload changed')
        if check_instructions(target, self.request['reviews']) != self.instructions:
            raise ValueError('operator update instruction review changed')
        observation = observe(self.state_root, records(self.state_root), target,
                              [*self.row.get('runtime_roots', []), *self.request.get('runtime_roots', [])], self.request.get('invoking'))
        configurations = self._plan(applied, observation)
        contents = []
        for cache, manifest in ((self.row['current']['cache'], old), (self.cache, new)):
            payload = {}
            for name, item in manifest['files'].items():
                path = Path(cache) / 'payload' / name
                raw, fingerprint = capture(path)
                metadata = path.stat()
                if (metadata.st_uid != os.getuid() or metadata.st_nlink != 1
                        or fingerprint != {k: item[k] for k in ('sha256', 'mode')}):
                    raise ValueError('operator update cache resource ownership/alias/integrity changed')
                payload[name] = raw
            contents.append(payload)
        projections = projection(old, new, *contents, configurations)
        changed, unchanged = delta(old, new)
        manifests = {label: {'path': str(Path(cache) / 'manifest.json'), 'sha256': digest(capture(Path(cache) / 'manifest.json')[0])}
                     for label, cache in (('old', self.row['current']['cache']), ('new', self.cache))}
        for manifest_ref in manifests.values():
            reference(manifest_ref)
        reviews = self.request['reviews']
        if not isinstance(reviews, dict) or set(reviews) - {'global_package_update', 'instructions', 'config'}:
            raise ValueError('unknown operator update review fields')
        review = reviews.get('global_package_update')
        expected = {'schema_version': 1, 'kind': 'operator-global-package-update-v1', 'operation': 'global-update-allow-running',
                    'target': target, 'source': source, 'old_release': identity(old), 'new_release': identity(new),
                    'manifests': manifests, 'resources': changed, 'unchanged_resource_set_sha256': unchanged,
                    'projection': projections, 'accepted_limits': LIMITS}
        fields = set(expected) | {'operator_authorization', 'instruction_review', 'source_review', 'reason', 'provenance'}
        if (not isinstance(review, dict) or set(review) != fields or type(review['schema_version']) is not int
                or any(review.get(k) != v for k, v in expected.items())
                or any(not isinstance(review[k], str) or not review[k].strip() for k in ('reason', 'provenance'))):
            raise ValueError('closed computed operator global package update review required')
        refs = {key: reference(review[key]) for key in ('operator_authorization', 'instruction_review', 'source_review')}
        semantic = decode(refs['source_review'])
        evidence = {'schema_version': 1, 'kind': 'operator-global-package-source-review-v1', 'reviewed': True,
                    'old_release': identity(old), 'new_release': identity(new), 'resources': changed, 'projection': projections}
        if (not isinstance(semantic, dict) or set(semantic) != set(evidence) | {'reviewer', 'reason'}
                or type(semantic['schema_version']) is not int or semantic['reviewed'] is not True
                or any(semantic.get(k) != v for k, v in evidence.items())
                or any(not isinstance(semantic[k], str) or not semantic[k].strip() for k in ('reviewer', 'reason'))):
            raise ValueError('independent source review must cover the exact computed delta and projections')
        if decode(reference(self.request['operator_review_artifact'])) != reviews:
            raise ValueError('operator review artifact changed')
        return {**expected, 'kind': 'operator-global-package-update-receipt-v1', 'managed_issuer': issuer,
                'review_artifact': self.request['operator_review_artifact'],
                'provenance': {key: review[key] for key in refs}, 'instruction_reviews': self.instructions,
                'activity': observation}
