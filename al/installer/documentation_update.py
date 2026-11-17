"""Hash-bound compatibility for an explicit, enrolled global documentation update.

This acknowledges only terminal-workflow eligibility. It grants no runtime authority.
"""
import copy
from pathlib import Path

from .engine import R, T
from .guards import ActiveRuns, activity_roots, scan_activity
from .payload_provenance import payload_kind, verify_expected
from .registry import canonical, digest, read_json, records
from .source import TAG
from .targets import package_path, roles_path

DOCUMENTS = frozenset({'claude/skills/al/SKILL.md',
                       'codex/skills/al/reference/handoffs.md',
                       'claude/skills/al/reference/handoffs.md'})
PARITY = frozenset(p + '/skills/al/reference/parity-manifest.json' for p in ('codex', 'claude'))


def validate_request(request, row):
    version = request.get('version')
    if (request.get('kind') != 'update' or request['target']['scope'] != 'global'
            or not row or not row.get('current') or row['target'] != request['target']
            or not isinstance(version, str) or not TAG.fullmatch(version if version.startswith('v') else 'v' + version)):
        raise ValueError('documentation-only requires an enrolled global update and explicit numeric version')
    if (request.get('all') or request.get('adoption') or request.get('status_line')
            or request.get('policy') is not None):
        raise ValueError('documentation-only cannot change policy/configuration or use adoption/--all')


def activity(roots):
    report = scan_activity(roots)
    if any(item.get('category') != 'terminal-workflow' for item in report['blockers']):
        raise ActiveRuns('documentation-only refuses malformed/unresolved runtime activity: ' +
                         '; '.join(item['path'] + ': ' + item['reason'] for item in report['blockers']))
    return report


def _provenance(value):
    if not isinstance(value, dict) or set(value) != {'path', 'sha256'}:
        raise ValueError('documentation review requires exact instruction/authorization provenance')
    if not isinstance(value['path'], str) or not Path(value['path']).is_absolute():
        raise ValueError('documentation provenance path must be absolute')
    path = R.safe_absolute(value['path'])
    if not path.is_file() or digest(path.read_bytes()) != value['sha256']:
        raise ValueError('documentation review provenance changed')
    return dict(value)


def _parity(before, after, changed):
    """Current schema permits only the reviewed handoffs rationale to change.

    All inventories and every other value, including any future digest fields, must
    be identical. Unknown schema changes fail closed instead of extending authority.
    """
    left, right = copy.deepcopy(before), copy.deepcopy(after)
    if left.get('schema_version') != 1 or right.get('schema_version') != 1:
        raise ValueError('documentation parity schema is unsupported')
    for value in (left, right):
        adapters = value.get('platform_adapters')
        rationales = adapters.get('reference_rationales') if isinstance(adapters, dict) else None
        if not isinstance(rationales, dict) or not isinstance(rationales.get('handoffs.md'), str):
            raise ValueError('documentation parity rationale is malformed')
        if not any(name.endswith('/reference/handoffs.md') for name in changed):
            raise ValueError('parity rationale requires a reviewed handoffs change')
        rationales['handoffs.md'] = '<reviewed handoffs rationale>'
    if left != right:
        raise ValueError('documentation parity inventories or non-documentation fields changed')


class DocumentationGuard:
    """Dedicated compatibility receipt, rechecked at apply and publication barriers."""

    def __init__(self, request, row, prepared, cache, manifest, state_root, instruction_reviews):
        validate_request(request, row)
        self.row, self.prepared = copy.deepcopy(row), copy.deepcopy(prepared)
        self.live_prepared = prepared
        self.cache, self.manifest = Path(cache), copy.deepcopy(manifest)
        self.state_root = Path(state_root)
        self.runtime_roots = copy.deepcopy(request.get('runtime_roots', []))
        self.invoking = request.get('invoking') or str(Path.cwd())
        self.review = copy.deepcopy((request.get('reviews') or {}).get('documentation_update'))
        self.instruction_reviews = copy.deepcopy(instruction_reviews)
        self.receipt = self.revalidate()

    def revalidate(self, *, installed=False):
        live, frozen = copy.deepcopy(self.live_prepared), copy.deepcopy(self.prepared)
        # The engine records new ownership sentinels after apply; every actual
        # resource/configuration/retirement plan must retain its prepared identity.
        for plan in (live, frozen):
            for role in plan['next_roles'].values():
                role.pop('sentinel', None)
        if live != frozen:
            raise ValueError('documentation-only prepared plan changed')
        old = self.row['current']['release']
        new = self.manifest
        if payload_kind(old) != 'tagged' or payload_kind(new) != 'tagged':
            raise ValueError('documentation-only requires numeric tagged payloads')
        verify_expected(self.row['current']['cache'], old)
        verify_expected(self.cache, new)
        mutable_identity = {'digest', 'tag', 'tag_object', 'commit', 'version', 'files'}
        if ({k: v for k, v in old.items() if k not in mutable_identity} !=
                {k: v for k, v in new.items() if k not in mutable_identity}):
            raise ValueError('documentation-only release source/compatibility identity differs')
        if old['files'].keys() != new['files'].keys():
            raise ValueError('documentation-only resource membership changed')
        changed = {}
        unchanged = {}
        for name, before in old['files'].items():
            after = new['files'][name]
            if {k: v for k, v in before.items() if k != 'sha256'} != {k: v for k, v in after.items() if k != 'sha256'}:
                raise ValueError('documentation-only resource kind/platform/mode changed: ' + name)
            if before != after:
                if name not in DOCUMENTS | PARITY or before['mode'] != 0o644 or before['kind'] != 'package':
                    raise ValueError('unreviewable documentation-only resource: ' + name)
                changed[name] = {'before': before['sha256'], 'after': after['sha256']}
            else:
                unchanged[name] = before
        review = self.review
        if (not changed or not isinstance(review, dict) or review.get('schema_version') != 1
                or review.get('reviewed') is not True or not isinstance(review.get('reason'), str) or not review['reason'].strip()
                or review.get('old_release_digest') != old['digest'] or review.get('new_release_digest') != new['digest']
                or review.get('resources') != changed):
            raise ValueError('exact hash-bound documentation review required')
        provenance = {key: _provenance(review.get(key)) for key in ('instruction_review', 'user_authorization')}
        for name in changed.keys() & PARITY:
            _parity(read_json(Path(self.row['current']['cache']) / 'payload' / name),
                    read_json(self.cache / 'payload' / name), changed)
        current, prepared = self.row['current'], self.prepared
        if (prepared['lock'] or prepared['removals'] or prepared['retired_roles'] != current.get('retired_roles', {})
                or set(prepared['next_roles']) != set(current['roles'])):
            raise ValueError('documentation-only changes role/retirement/lock actions')
        for name, role in prepared['next_roles'].items():
            if role['state'] != current['roles'][name]['state'] or role['original'] != current['roles'][name]['original']:
                raise ValueError('documentation-only role action differs')
            if T.state(R.safe_absolute(name)) != role['state']:
                raise ValueError('documentation-only role preimage changed')
        for platform, config in prepared['configs'].items():
            expected = prepared['preimages'][config['path']]
            raw = Path(config['path']).read_bytes() if T.state(R.safe_absolute(config['path'])) else None
            if (raw != config['raw'] or T.state(R.safe_absolute(config['path'])) != expected
                    or config['owned'] != current['configs'][platform]['owned']):
                raise ValueError('documentation-only configuration action differs')
        if installed:
            target = self.row['target']
            for name, item in new['files'].items():
                if item['platform'] not in target['platforms']:
                    continue
                path = (roles_path(target, item['platform']) / Path(name).name if item['kind'] == 'role'
                        else package_path(target, item['platform']) / name.split('/skills/al/', 1)[1])
                if T.state(R.safe_absolute(path)) != {k: item[k] for k in ('sha256', 'mode')}:
                    raise ValueError('documentation-only installed postimage changed: ' + name)
        roots = activity_roots(records(self.state_root), self.row['target'],
                               [*self.row.get('runtime_roots', []), *self.runtime_roots],
                               self.invoking, state_root=self.state_root)
        report = activity(roots)
        return {'kind': 'documentation-compatibility-v1', 'target': self.row['target'],
                'old_release': {k: old[k] for k in ('digest', 'commit')},
                'new_release': {k: new[k] for k in ('digest', 'commit')},
                'resources': changed, 'unchanged_resource_set_sha256': digest(canonical(unchanged)),
                'unchanged_executable_set_sha256': digest(canonical(
                    {name: item for name, item in unchanged.items() if name not in DOCUMENTS | PARITY})),
                'review_sha256': digest(canonical(review)), 'review_reason': review['reason'],
                'provenance': provenance, 'instruction_reviews': self.instruction_reviews, 'activity': report}
