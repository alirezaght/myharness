"""One exact reviewed tagged payload transition; never runtime execution authority."""
import copy
from pathlib import Path

from .adapters import platform_resources
from .engine import R, T
from .guards import check_instructions
from .manifest import build_manifest
from .payload_provenance import payload_kind, verify_expected
from .registry import canonical, digest, namespace, read_json, records
from .source import resolve_release
from .targets import package_path
from .validator_origin import capture

INDEPENDENT_REVIEW_SHA256 = '107715fb8e6fa6c7b1c189bd1605503af2e33076ad006476b68397d86d1798b1'
ACCEPTED_LIMITS = ['future-global-payload-only', 'historical-pins-and-state-unchanged',
                   'reserved-initialization-settlement-unknown', 'no-execution-or-approval-authority',
                   'bootstrap-selection-is-a-separate-publication', 'partial-failure-requires-manual-reconciliation']
APPROVED = {'old': {'version': '0.1.7',
         'tag': 'v0.1.7',
         'tag_object': '6b620f4cbb4012f3a354279bd0b21d61a21d9e4c',
         'commit': '53ccf77f86954a0752f79ee74085d393ee68ae83'},
 'new': {'version': '0.1.8',
         'tag': 'v0.1.8',
         'tag_object': '0ef5e442011bec568708dabe115c53c35c0ee629',
         'commit': '5388a2def872ff62473f6a8c04f3ec6a17bb478d'},
 'resources': {'claude/skills/al/reference/parity-manifest.json': {'before': {'sha256': '74cd62cb58e43e74632029dc9d3f52b26d88b771901f4dbe3ea50ef5ad07dfa0',
                                                                              'mode': 420,
                                                                              'platform': 'claude',
                                                                              'kind': 'package'},
                                                                   'after': {'sha256': '1816d00df83b6f1eac5a3c572e0a61eff17bc586b069cd4523b2be31fa2f49ac',
                                                                             'mode': 420,
                                                                             'platform': 'claude',
                                                                             'kind': 'package'}},
               'claude/skills/al/reference/workflow.md': {'before': {'sha256': '32ae4976aa9ac1cd2586b7204099c641f1c0889a92141688d764c5fc3e5f2384',
                                                                     'mode': 420,
                                                                     'platform': 'claude',
                                                                     'kind': 'package'},
                                                          'after': {'sha256': '2c1d0b41cc434846f57a57f5786883809af17c1492748dab98a26c8f8f218c90',
                                                                    'mode': 420,
                                                                    'platform': 'claude',
                                                                    'kind': 'package'}},
               'claude/skills/al/scripts/al_implementation.py': {'before': {'sha256': 'a4454e1c4a39b7aa13f6b2769759ad8c13a963e0711fa063ac82805a54d8d268',
                                                                            'mode': 420,
                                                                            'platform': 'claude',
                                                                            'kind': 'package'},
                                                                 'after': {'sha256': 'fd0f17ad9e8c731ada0bf4d8f9b16ffcf15cdeeb3e11a92028b252819c8c4c0a',
                                                                           'mode': 420,
                                                                           'platform': 'claude',
                                                                           'kind': 'package'}},
               'claude/skills/al/scripts/al_planning.py': {'before': {'sha256': '40ad17c2517805c75c32a3f3adac6a49f1d21409e2bc0598b70689bcc606886a',
                                                                      'mode': 420,
                                                                      'platform': 'claude',
                                                                      'kind': 'package'},
                                                           'after': {'sha256': '8802647052a909f6e1055ed691b4790224701869d34ea7e48bf406f65fd2cb8b',
                                                                     'mode': 420,
                                                                     'platform': 'claude',
                                                                     'kind': 'package'}},
               'claude/skills/al/scripts/al_repository_facts.py': {'before': {'sha256': '1839e79cf56eb48563d93652192b4bddafac9d606b0ffdaf4829320eb51a36dd',
                                                                              'mode': 420,
                                                                              'platform': 'claude',
                                                                              'kind': 'package'},
                                                                   'after': {'sha256': '4f54acd2d8815fb1af05867f6aa0ff6a678ca263aaded662e0bf62638946c481',
                                                                             'mode': 420,
                                                                             'platform': 'claude',
                                                                             'kind': 'package'}},
               'claude/skills/al/scripts/al_scope.py': {'before': {'sha256': '35a5ba33ad3823a41e0b6332fb958efa209f36237f364f32c36da310acd7c9d3',
                                                                   'mode': 420,
                                                                   'platform': 'claude',
                                                                   'kind': 'package'},
                                                        'after': {'sha256': '01ef380d7c75688401b88346e0b111792eb19de6b9fdd35205011f8e8e135333',
                                                                  'mode': 420,
                                                                  'platform': 'claude',
                                                                  'kind': 'package'}},
               'claude/skills/al/scripts/al_task_attribution.py': {'before': {'sha256': 'b3bdfcf47623395429a0b7975baba8972f1c578f0c85835a0d41b6f70530a594',
                                                                              'mode': 420,
                                                                              'platform': 'claude',
                                                                              'kind': 'package'},
                                                                   'after': {'sha256': '15d894181b94c0d16f6af44f4d437b66b28118a3cda510bad89a8ff4a9efa7a0',
                                                                             'mode': 420,
                                                                             'platform': 'claude',
                                                                             'kind': 'package'}},
               'claude/skills/al/scripts/al_writer_progress.py': {'before': None,
                                                                  'after': {'sha256': '2cbd67a4e9113b00663fc10a5f4fcd9f0c6e278e645b24ab380006eba9c3dd4b',
                                                                            'mode': 420,
                                                                            'platform': 'claude',
                                                                            'kind': 'package'}},
               'claude/skills/al/scripts/al_writers.py': {'before': {'sha256': '14c51678f3b8196ee8b7531444f9f8bf178ebcc3fcf0af5cc3b75cefa507d3e6',
                                                                     'mode': 420,
                                                                     'platform': 'claude',
                                                                     'kind': 'package'},
                                                          'after': {'sha256': 'e748696844d003ef1ee25c3768c192d6dbbac59267669458b2f3cfb0114e0d95',
                                                                    'mode': 420,
                                                                    'platform': 'claude',
                                                                    'kind': 'package'}},
               'codex/skills/al/reference/parity-manifest.json': {'before': {'sha256': '74cd62cb58e43e74632029dc9d3f52b26d88b771901f4dbe3ea50ef5ad07dfa0',
                                                                             'mode': 420,
                                                                             'platform': 'codex',
                                                                             'kind': 'package'},
                                                                  'after': {'sha256': '1816d00df83b6f1eac5a3c572e0a61eff17bc586b069cd4523b2be31fa2f49ac',
                                                                            'mode': 420,
                                                                            'platform': 'codex',
                                                                            'kind': 'package'}},
               'codex/skills/al/reference/workflow.md': {'before': {'sha256': '45e99c23fdc52429b9159d0966eefcad415ab6e6523eb122cf1cd2d63c2d96bc',
                                                                    'mode': 420,
                                                                    'platform': 'codex',
                                                                    'kind': 'package'},
                                                         'after': {'sha256': '3a1326d4a0dee57d5f0b796c90db32c9391c256364f73a51dcd57549bb5aabea',
                                                                   'mode': 420,
                                                                   'platform': 'codex',
                                                                   'kind': 'package'}},
               'codex/skills/al/scripts/al_implementation.py': {'before': {'sha256': 'a4454e1c4a39b7aa13f6b2769759ad8c13a963e0711fa063ac82805a54d8d268',
                                                                           'mode': 420,
                                                                           'platform': 'codex',
                                                                           'kind': 'package'},
                                                                'after': {'sha256': 'fd0f17ad9e8c731ada0bf4d8f9b16ffcf15cdeeb3e11a92028b252819c8c4c0a',
                                                                          'mode': 420,
                                                                          'platform': 'codex',
                                                                          'kind': 'package'}},
               'codex/skills/al/scripts/al_planning.py': {'before': {'sha256': '40ad17c2517805c75c32a3f3adac6a49f1d21409e2bc0598b70689bcc606886a',
                                                                     'mode': 420,
                                                                     'platform': 'codex',
                                                                     'kind': 'package'},
                                                          'after': {'sha256': '8802647052a909f6e1055ed691b4790224701869d34ea7e48bf406f65fd2cb8b',
                                                                    'mode': 420,
                                                                    'platform': 'codex',
                                                                    'kind': 'package'}},
               'codex/skills/al/scripts/al_repository_facts.py': {'before': {'sha256': '1839e79cf56eb48563d93652192b4bddafac9d606b0ffdaf4829320eb51a36dd',
                                                                             'mode': 420,
                                                                             'platform': 'codex',
                                                                             'kind': 'package'},
                                                                  'after': {'sha256': '4f54acd2d8815fb1af05867f6aa0ff6a678ca263aaded662e0bf62638946c481',
                                                                            'mode': 420,
                                                                            'platform': 'codex',
                                                                            'kind': 'package'}},
               'codex/skills/al/scripts/al_scope.py': {'before': {'sha256': '35a5ba33ad3823a41e0b6332fb958efa209f36237f364f32c36da310acd7c9d3',
                                                                  'mode': 420,
                                                                  'platform': 'codex',
                                                                  'kind': 'package'},
                                                       'after': {'sha256': '01ef380d7c75688401b88346e0b111792eb19de6b9fdd35205011f8e8e135333',
                                                                 'mode': 420,
                                                                 'platform': 'codex',
                                                                 'kind': 'package'}},
               'codex/skills/al/scripts/al_task_attribution.py': {'before': {'sha256': 'b3bdfcf47623395429a0b7975baba8972f1c578f0c85835a0d41b6f70530a594',
                                                                             'mode': 420,
                                                                             'platform': 'codex',
                                                                             'kind': 'package'},
                                                                  'after': {'sha256': '15d894181b94c0d16f6af44f4d437b66b28118a3cda510bad89a8ff4a9efa7a0',
                                                                            'mode': 420,
                                                                            'platform': 'codex',
                                                                            'kind': 'package'}},
               'codex/skills/al/scripts/al_writer_progress.py': {'before': None,
                                                                 'after': {'sha256': '2cbd67a4e9113b00663fc10a5f4fcd9f0c6e278e645b24ab380006eba9c3dd4b',
                                                                           'mode': 420,
                                                                           'platform': 'codex',
                                                                           'kind': 'package'}},
               'codex/skills/al/scripts/al_writers.py': {'before': {'sha256': '14c51678f3b8196ee8b7531444f9f8bf178ebcc3fcf0af5cc3b75cefa507d3e6',
                                                                    'mode': 420,
                                                                    'platform': 'codex',
                                                                    'kind': 'package'},
                                                         'after': {'sha256': 'e748696844d003ef1ee25c3768c192d6dbbac59267669458b2f3cfb0114e0d95',
                                                                   'mode': 420,
                                                                   'platform': 'codex',
                                                                   'kind': 'package'}}},
 'unchanged_sha256': '735164927fb53f357e8518d0f5c5d3b8f7ad20d22d55793aa84c9b79314dc3ef'}


def validate_request(request, row):
    if (request.get('kind') != 'update' or request['target']['scope'] != 'global'
            or not row or not row.get('current') or row['target'] != request['target']
            or request.get('version') not in {'0.1.8', 'v0.1.8'}):
        raise ValueError('reviewed-runtime-update requires the enrolled global target and explicit version 0.1.8')
    if (request.get('all') or request.get('documentation_only') or request.get('adoption')
            or request.get('status_line') or request.get('policy') is not None):
        raise ValueError('reviewed-runtime-update forbids mixed modes, adoption, batch, policy and configuration changes')


def release_identity(manifest):
    return {key: manifest[key] for key in ('version', 'tag', 'tag_object', 'commit', 'digest',
                                          'source_identity', 'subdir')}


def approved_delta(old, new):
    for manifest, name in ((old, 'old'), (new, 'new')):
        if payload_kind(manifest) != 'tagged' or any(manifest.get(k) != v for k, v in APPROVED[name].items()):
            raise ValueError('release is outside the exact independently reviewed transition: ' + name)
    identity = {'digest', 'tag', 'tag_object', 'commit', 'version', 'files'}
    if ({k: v for k, v in old.items() if k not in identity} !=
            {k: v for k, v in new.items() if k not in identity}):
        raise ValueError('reviewed runtime source/schema/configuration compatibility differs')
    delta, unchanged = {}, {}
    for name in old['files'].keys() | new['files'].keys():
        before, after = old['files'].get(name), new['files'].get(name)
        if before != after:
            delta[name] = {'before': before, 'after': after}
        else:
            unchanged[name] = before
    if delta != APPROVED['resources'] or digest(canonical(unchanged)) != APPROVED['unchanged_sha256']:
        raise ValueError('payload differs from exact reviewed resource fingerprints')
    return delta


def provenance(reference, *, expected=None):
    if not isinstance(reference, dict) or set(reference) != {'path', 'sha256'}:
        raise ValueError('closed hash-bound review/authorization reference required')
    if not isinstance(reference['path'], str) or not Path(reference['path']).is_absolute():
        raise ValueError('review/authorization reference must be absolute')
    path = R.safe_absolute(reference['path'])
    raw, _ = capture(path)
    if digest(raw) != reference['sha256'] or expected is not None and reference['sha256'] != expected:
        raise ValueError('review/authorization provenance changed or is not the independently reviewed artifact')
    return dict(reference)


class ReviewedRuntimeGuard:
    """A fresh metadata/pin proof at every installation barrier, with fixed payload bytes."""

    def __init__(self, request, row, prepared, cache, manifest, state_root, instruction_reviews):
        validate_request(request, row)
        self.request, self.row = copy.deepcopy(request), copy.deepcopy(row)
        self.live_prepared, self.prepared = prepared, copy.deepcopy(prepared)
        self.cache, self.manifest, self.state_root = Path(cache), copy.deepcopy(manifest), Path(state_root)
        self.instruction_reviews = copy.deepcopy(instruction_reviews)
        self.source_path = namespace(state_root) / 'sources' / (
            digest(canonical({'identity': row['source']['identity'], 'subdir': row['source']['subdir']})) + '.json')
        self.source_observation = read_json(self.source_path)
        self.baseline = None
        self.receipt = self.revalidate()
        self.baseline = self.receipt['retained']['digest']

    def _plan(self, applied):
        live, frozen = copy.deepcopy(self.live_prepared), copy.deepcopy(self.prepared)
        for plan in (live, frozen):
            for role in plan['next_roles'].values():
                role.pop('sentinel', None)
        if live != frozen:
            raise ValueError('reviewed runtime prepared plan changed')
        current, prepared = self.row['current'], self.prepared
        if (prepared['lock'] or prepared['removals'] or prepared['retired_roles'] != current.get('retired_roles', {})
                or set(prepared['next_roles']) != set(current['roles'])):
            raise ValueError('reviewed runtime cannot change role/retirement/lock actions')
        for name, role in prepared['next_roles'].items():
            if (role['state'] != current['roles'][name]['state'] or role['original'] != current['roles'][name]['original']
                    or T.state(R.safe_absolute(name)) != role['state']):
                raise ValueError('reviewed runtime role action/preimage differs')
        for platform, config in prepared['configs'].items():
            state = T.state(R.safe_absolute(config['path']))
            raw = Path(config['path']).read_bytes() if state else None
            if (state != prepared['preimages'][config['path']] or raw != config['raw']
                    or config['owned'] != current['configs'][platform]['owned']):
                raise ValueError('reviewed runtime configuration action/preimage differs')
        for platform in self.row['target']['platforms']:
            resource = platform_resources(self.cache, self.manifest, self.row['target'], platform)
            plan = prepared['plans'][platform]
            if any(plan[k] != resource[k] for k in ('files', 'roles', 'package', 'target')):
                raise ValueError('reviewed runtime prepared resource mapping differs')
            manifest = self.manifest if platform in applied else current['release']
            desired = platform_resources(self.cache, manifest, self.row['target'], platform)['files']
            names = set(plan['files']) | set(desired)
            for name in names:
                if T.state(R.safe_absolute(package_path(self.row['target'], platform) / name)) != desired.get(name):
                    raise ValueError('reviewed runtime resource changed at barrier: ' + platform + '/' + name)

    def revalidate(self, *, applied=()):
        old, new = self.row['current']['release'], self.manifest
        verify_expected(self.row['current']['cache'], old)
        verify_expected(self.cache, new)
        delta = approved_delta(old, new)
        if check_instructions(self.row['target'], self.request['reviews']) != self.instruction_reviews:
            raise ValueError('instruction reviews changed at runtime update barrier')
        self._plan(set(applied))
        if read_json(self.source_path) != self.source_observation:
            raise ValueError('reviewed source tag observations changed')
        source = self.row['source']
        fresh = resolve_release(source['identity'], source['subdir'], '0.1.8', self.source_observation['tags'])
        if build_manifest(fresh) != new:
            raise ValueError('reviewed source tag/payload changed at barrier')
        reviews = self.request.get('reviews')
        if not isinstance(reviews, dict) or set(reviews) - {'reviewed_runtime_update', 'instructions', 'config'}:
            raise ValueError('unknown fields in reviewed runtime update review file')
        review = reviews.get('reviewed_runtime_update')
        fields = {'schema_version', 'kind', 'reviewed', 'reason', 'target', 'source', 'old_release', 'new_release',
                  'resources', 'independent_review', 'instruction_review', 'operator_authorization', 'accepted_limits'}
        if (not isinstance(review, dict) or set(review) != fields or type(review['schema_version']) is not int
                or review['schema_version'] != 1 or review['kind'] != 'reviewed-runtime-update-v1'
                or review['reviewed'] is not True or not isinstance(review['reason'], str) or not review['reason'].strip()
                or review['target'] != self.row['target'] or review['source'] != source
                or review['old_release'] != release_identity(old) or review['new_release'] != release_identity(new)
                or review['resources'] != delta or review['accepted_limits'] != ACCEPTED_LIMITS):
            raise ValueError('closed exact reviewed-runtime-update artifact required')
        references = {key: provenance(review[key], expected=INDEPENDENT_REVIEW_SHA256 if key == 'independent_review' else None)
                      for key in ('independent_review', 'instruction_review', 'operator_authorization')}
        artifact = provenance(self.request.get('runtime_review_artifact'))
        if read_json(artifact['path']) != self.request['reviews']:
            raise ValueError('runtime update review artifact content changed')
        from .reviewed_runtime_pins import observe
        retained = observe(self.state_root, records(self.state_root), self.row['target'],
                           [*self.row.get('runtime_roots', []), *self.request.get('runtime_roots', [])],
                           self.request.get('invoking'))
        if self.baseline is not None and retained['digest'] != self.baseline:
            raise ValueError('retained runtime evidence changed between installation barriers')
        return {'kind': 'reviewed-runtime-compatibility-v1', 'target': self.row['target'],
                'old_release': release_identity(old), 'new_release': release_identity(new),
                'resources': delta, 'unchanged_resource_set_sha256': APPROVED['unchanged_sha256'],
                'review_artifact': artifact, 'provenance': references, 'accepted_limits': ACCEPTED_LIMITS,
                'instruction_reviews': self.instruction_reviews, 'retained': retained}
