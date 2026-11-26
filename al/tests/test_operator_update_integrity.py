"""Operator update integrity invariants."""

import pytest

from al.tests.operator_update_helpers import (
    O,
    Path,
    U,
    _prefix_cache as _prefix_cache,
    artifact,
    copy,
    digest,
    enrolled as enrolled,
    git_test,
    json,
    load_record,
    package_path,
    projection,
    projection_data as projection_data,
    public,
    read_json,
    retained,
    run_alctl,
    tag,
    write_json,
)


@pytest.mark.parametrize('failure', ['review-hash', 'review-fields', 'provenance', 'source-review', 'delta', 'projection', 'projection-version',
                                    'drift', 'collision', 'cache', 'tag', 'registry', 'pending', 'hardlink', 'symlink', 'overlap',
                                    'manifest-hardlink', 'cache-hardlink'])
def test_installation_integrity_refusals_are_not_warnings(enrolled, failure):
    w, target, row, runtime = enrolled
    req = artifact(enrolled, *tag(enrolled))
    review = req['reviews']['global_package_update']
    review_path = Path(req['operator_review_artifact']['path'])
    if failure == 'review-hash': review_path.write_text('{}')
    elif failure == 'review-fields': review['invented'] = True
    elif failure == 'provenance': Path(review['operator_authorization']['path']).write_text('changed')
    elif failure == 'source-review':
        ref = review['source_review']; value = read_json(ref['path']); value['reviewed'] = False
        write_json(ref['path'], value); ref['sha256'] = digest(Path(ref['path']).read_bytes())
    elif failure == 'delta': review['resources'] = {}
    elif failure == 'projection': review['projection']['authority_sha256'] = '0' * 64
    elif failure == 'projection-version': review['projection']['version'] = 1
    elif failure == 'drift': (package_path(target, 'codex') / 'SKILL.md').write_text('changed')
    elif failure == 'collision': (package_path(target, 'codex') / 'scripts/reviewed_addition.py').write_text('unmanaged')
    elif failure == 'cache': (Path(row['current']['cache']) / 'payload/codex/skills/al/SKILL.md').write_text('changed')
    elif failure == 'tag': git_test(w['source'], 'tag', '-f', 'v1.0.0')
    elif failure == 'registry':
        record = Path(row['current']['packages']['codex']['path']); value = read_json(record)
        value['generation'] += 1; write_json(record, value)
    elif failure == 'pending':
        value = dict(row, pending='unsettled-installer-operation')
        write_json(O.record_path(w['state'], target['target_id']), value)
    elif failure in {'hardlink', 'symlink'}:
        path = package_path(target, 'codex') / 'SKILL.md'; saved = w['root'] / 'aliased-resource'
        path.rename(saved)
        if failure == 'hardlink': path.hardlink_to(saved)
        else: path.symlink_to(saved)
    elif failure == 'overlap': req['runtime_roots'].append(str(package_path(target, 'codex') / 'reference'))
    elif failure in {'manifest-hardlink', 'cache-hardlink'}:
        suffix = 'manifest.json' if failure == 'manifest-hardlink' else 'payload/codex/skills/al/SKILL.md'
        path = Path(row['current']['cache']) / suffix
        (w['root'] / 'cache-alias').hardlink_to(path)
    if failure in {'review-fields', 'source-review', 'delta', 'projection', 'projection-version'}:
        review_path.write_text(json.dumps(req['reviews']))
        req['operator_review_artifact']['sha256'] = digest(review_path.read_bytes())
    before = retained(enrolled)
    result = public(enrolled, req, '--allow-running')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'genuine isolated' not in result.data['error']
    assert retained(enrolled) == before
    assert load_record(w['state'], target['target_id'])['current'] == row['current']


@pytest.mark.parametrize('overrides', [
    {'kind': 'install'}, {'kind': 'rollback'}, {'version': None}, {'version': 'latest'},
    {'policy': 'pinned'}, {'source': 'same-explicit-source'}, {'source_subdir': 'al'},
    {'all': True}, {'adoption': {'reviewed': True}}, {'status_line': True},
    {'documentation_only': True}, {'reviewed_runtime_update': True}, {'operator_review_artifact': None},
])
def test_scope_refuses_before_activity_or_mutation(overrides):
    target = {'scope': 'global'}
    row = {'target': target, 'current': {'release': {}}}
    req = {'kind': 'update', 'target': target, 'version': '1.1.0', 'allow_running': True,
           'operator_review_artifact': {'path': '/unused/reviews.json', 'sha256': '0' * 64}}
    before = copy.deepcopy((req, row))
    U.validate_request(req, row)
    with pytest.raises(ValueError):
        U.validate_request(dict(req, **overrides), row)
    assert (req, row) == before


def test_projection_allows_only_reviewable_body_changes(projection_data):
    old, contents = projection_data
    new, after = copy.deepcopy(old), dict(contents)
    for name in ('codex/agents/al-architect.toml', 'claude/agents/al-architect.md', 'codex/skills/al/SKILL.md'):
        after[name] = contents[name].replace(b'# AL', b'# Reviewed AL', 1)
        new['files'][name]['sha256'] = digest(after[name])
    assert projection(old, new, contents, after, {})['version'] == 2


@pytest.mark.parametrize('kind', ['model', 'effort', 'sandbox', 'extra-key', 'structured-key', 'missing-body',
                                  'claude-model', 'claude-tools', 'claude-unknown', 'skill-codex', 'skill-claude',
                                  'hook', 'hook-dependency', 'model-code', 'role-add', 'role-remove', 'role-mode'])
def test_authority_mutations_cannot_be_reviewed_in(projection_data, kind):
    old, contents = projection_data
    new, after = copy.deepcopy(old), dict(contents)
    name = 'codex/agents/al-architect.toml'
    raw = after[name]
    if kind == 'model': raw = raw.replace(b'gpt-6-astra', b'other-model', 1)
    elif kind == 'effort': raw = raw.replace(b'"medium"', b'"high"', 1)
    elif kind == 'sandbox': raw = raw.replace(b'"read-only"', b'"workspace-write"', 1)
    elif kind == 'extra-key': raw = b'future_permission = true\n' + raw
    elif kind == 'structured-key': raw = b'future_permission = { write = true, depth = 99 }\n' + raw
    elif kind == 'missing-body': raw = raw.replace(b'developer_instructions', b'other_instructions', 1)
    elif kind.startswith('claude-'):
        name = 'claude/agents/al-architect.md'; raw = after[name]
        if kind == 'claude-model': raw = raw.replace(b'model: fable', b'model: opus', 1)
        elif kind == 'claude-tools': raw = raw.replace(b'tools: Read', b'tools: Write, Read', 1)
        else: raw = raw.replace(b'---\n', b'---\nfuture-permission: true\n', 1)
    elif kind.startswith('skill-'):
        name = kind.split('-')[1] + '/skills/al/SKILL.md'
        raw = after[name].replace(b'---\n', b'---\nfuture-permission: true\n', 1)
    elif kind in {'hook', 'hook-dependency', 'model-code'}:
        name = {'hook': 'claude/skills/al/scripts/al_readonly_guard.py',
                'hook-dependency': 'claude/skills/al/scripts/al_shell.py',
                'model-code': 'codex/skills/al/scripts/al_models.py'}[kind]
        raw = after[name] + b'\n'
    elif kind == 'role-add':
        new['files']['codex/agents/al-extra.toml'] = dict(new['files'][name])
        after['codex/agents/al-extra.toml'] = raw
    elif kind == 'role-remove':
        new['files'].pop(name); after.pop(name)
    elif kind == 'role-mode': new['files'][name]['mode'] = 0o755
    if name in after:
        after[name] = raw; new['files'][name]['sha256'] = digest(raw)
    with pytest.raises(ValueError):
        projection(old, new, contents, after, {})


def test_public_flag_is_update_only_and_single_global():
    from al.installer.cli import parser
    for action in ('install', 'rollback', 'doctor'):
        with pytest.raises(SystemExit):
            parser().parse_args([action, '--allow-running'])
    value = parser().parse_args(['update', '--global', '--version', '1.1.0', '--allow-running', '--reviews', '/absolute/reviews.json'])
    assert value.allow_running and value.version == '1.1.0' and value.global_scope


def test_unmanaged_and_foreign_registry_cannot_use_operator_mode(enrolled):
    import shutil
    w, target, row, runtime = enrolled
    req = artifact(enrolled, *tag(enrolled))
    before = retained(enrolled)
    unmanaged = run_alctl('update', '--global', '--home', w['home'], '--state-root', w['state'],
                           '--version', '1.1.0', '--reviews', req['operator_review_artifact']['path'],
                           '--allow-running', cwd=w['root'])
    assert unmanaged.returncode == 1 and 'genuine isolated managed' in unmanaged.data['error']
    other = w['root'] / 'foreign-registry'
    shutil.copytree(w['state'], other)
    foreign = public(enrolled, req, '--allow-running', '--state-root', str(other))
    assert foreign.returncode == 1 and 'registry differs' in foreign.data['error']
    assert load_record(w['state'], target['target_id']) == row
    assert retained(enrolled) == before
