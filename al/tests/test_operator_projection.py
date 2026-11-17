"""Display-only status-line dependencies never waive real security contexts."""
# ruff: noqa: F811
import copy
import json

import pytest

from al.installer.operator_projection import authority, projection
from al.installer.registry import digest

PREFIX = 'claude/skills/al/scripts/'
STATUS = PREFIX + 'al_statusline.py'
STORE = PREFIX + 'al_display_store.py'
GUARD = PREFIX + 'al_readonly_guard.py'


@pytest.fixture
def payload():
    contents = {
        'codex/agents/al-architect.toml': b'model="fixed"\nsandbox_mode="read-only"\ndeveloper_instructions="body"\n',
        'claude/agents/al-architect.md': b'---\nname: al-architect\nmodel: fixed\ntools: Read\n---\nBody\n',
        'codex/skills/al/SKILL.md': b'---\nname: al\n---\nBody\n',
        'claude/skills/al/SKILL.md': b'---\nname: al\n---\nBody\n',
        'codex/skills/al/scripts/al_models.py': b'POLICY = "fixed"\n',
        PREFIX + 'al_models.py': b'POLICY = "fixed"\n',
        GUARD: b'import al_security_dependency\n',
        PREFIX + 'al_security_dependency.py': b'POLICY = "strict"\n',
        STATUS: b'import al_display_store\n',
        STORE: b'RENDER = "old"\n',
    }
    manifest = {'files': {name: {'sha256': digest(raw), 'mode': 0o755 if name == STATUS else 0o644,
                                 'kind': 'role' if '/agents/' in name else 'package', 'platform': name.split('/')[0]}
                          for name, raw in contents.items()}}
    return manifest, contents


def config(document, *, platform='claude', duplicates=False):
    command = {'type': 'command', 'command': 'python3 /home/operator/.claude/skills/al/scripts/al_statusline.py'}
    return {platform: {'raw': json.dumps(document), 'state': {'sha256': 'a' * 64, 'mode': 0o600},
                       'desired': {'statusLine': {'present': True, 'value': command}} if duplicates else {},
                       'owned': {'statusLine': {'original': {'present': False}, 'last': {'present': True, 'value': command}}} if duplicates else {}}}


def changed(payload, name=STORE, raw=b'RENDER = "new"\n'):
    old, contents = payload
    new, after = copy.deepcopy(old), dict(contents)
    after[name] = raw
    new['files'][name]['sha256'] = digest(raw)
    return old, new, contents, after


@pytest.mark.parametrize('duplicates', [False, True])
def test_legacy_unowned_or_owned_top_level_statusline_allows_reviewed_runtime_dependency(payload, duplicates):
    configurations = config({'statusLine': {'type': 'command', 'command': 'python3 al_statusline.py'},
                             'permissions': {'allow': ['Read']}}, duplicates=duplicates)
    value = projection(*changed(payload), configurations)
    assert value['version'] == 2
    protected = authority(payload[0], payload[1], configurations)['protected_resources']
    assert STATUS in protected and STORE not in protected
    assert GUARD in protected and PREFIX + 'al_security_dependency.py' in protected


@pytest.mark.parametrize('field,value', [('sha256', 'b' * 64), ('mode', 0o644), ('kind', 'role'), ('platform', 'codex')])
def test_display_entry_exact_fingerprint_remains_frozen(payload, field, value):
    old, contents = payload
    new = copy.deepcopy(old);new['files'][STATUS][field] = value
    with pytest.raises(ValueError):
        projection(old, new, contents, contents, config({'statusLine': {'command': 'al_statusline.py'}}))


def test_true_security_dependency_is_not_display_only(payload):
    with pytest.raises(ValueError):
        projection(*changed(payload, PREFIX + 'al_security_dependency.py'), config({'statusLine': {'command': 'al_statusline.py'}}))


@pytest.mark.parametrize('dual_use', ['header', 'config', 'dependency'])
def test_security_context_wins_even_if_display_seed_was_already_protected(payload, dual_use):
    manifest, contents = payload
    manifest, contents = copy.deepcopy(manifest), dict(contents)
    document = {'statusLine': {'command': 'al_statusline.py'}}
    if dual_use == 'header':
        name = 'claude/agents/al-architect.md'
        contents[name] = contents[name].replace(b'tools: Read', b'tools: Read\nhooks: al_statusline.py')
        manifest['files'][name]['sha256'] = digest(contents[name])
    elif dual_use == 'config': document['hooks'] = {'command': 'al_statusline.py'}
    else:
        contents[GUARD] = b'import al_statusline\n'
        manifest['files'][GUARD]['sha256'] = digest(contents[GUARD])
    with pytest.raises(ValueError):
        projection(*changed((manifest, contents)), config(document))


@pytest.mark.parametrize('document', [{'hooks': {'statusLine': {'command': 'al_statusline.py'}}},
                                      {'permissions': {'statusLine': 'al_statusline.py'}},
                                      {'statusline': 'al_statusline.py'}])
def test_only_exact_claude_top_level_statusline_is_display_only(payload, document):
    with pytest.raises(ValueError):
        projection(*changed(payload), config(document))


def test_codex_statusline_spelling_remains_a_security_context(payload):
    configurations = {'codex': {'raw': 'statusLine="al_statusline.py"\n', 'desired': {}, 'owned': {}, 'state': None}}
    with pytest.raises(ValueError):
        projection(*changed(payload), configurations)


def test_exact_raw_configuration_binding_includes_display_bytes_and_formatting(payload):
    old, contents = payload
    first = config({'statusLine': {'command': 'al_statusline.py'}})
    second = copy.deepcopy(first);second['claude']['raw'] += '\n'
    a = projection(old, old, contents, contents, first)
    b = projection(old, old, contents, contents, second)
    assert a['configuration_sha256'] != b['configuration_sha256']
    assert a['authority_sha256'] == b['authority_sha256']


@pytest.mark.parametrize('raw', ['{', '[]', '{"statusLine":{},"statusLine":{}}', '{"unsafe":NaN}'])
def test_malformed_claude_config_never_gains_display_exemption(payload, raw):
    configurations = config({});configurations['claude']['raw'] = raw
    with pytest.raises(ValueError):
        projection(*changed(payload), configurations)


def test_malformed_codex_config_fails_closed(payload):
    with pytest.raises(ValueError):
        projection(*changed(payload), {'codex': {'raw': '[broken', 'desired': {}, 'owned': {}, 'state': None}})


@pytest.mark.parametrize('representation', ['desired', 'owned'])
def test_non_statusline_duplicate_metadata_remains_a_security_context(payload, representation):
    configurations = config({'statusLine': {'command': 'al_statusline.py'}}, duplicates=True)
    configurations['claude'][representation]['hooks.command'] = {'command': 'al_statusline.py'}
    with pytest.raises(ValueError):
        projection(*changed(payload), configurations)


@pytest.mark.parametrize('field,value', [('raw', 4), ('desired', []), ('owned', 'invalid')])
def test_configuration_projection_shape_is_checked(payload, field, value):
    configurations = config({});configurations['claude'][field] = value
    with pytest.raises(ValueError):
        projection(*changed(payload), configurations)


# Imported fixture is genuinely bootstrapped by the existing installer harness.
from al.tests.test_operator_update import enrolled, artifact, tag, public  # noqa: E402,F401


def test_real_managed_update_preserves_legacy_unowned_statusline_and_reviews_dependency(enrolled):
    from al.installer.registry import load_record
    w, target, row, runtime = enrolled
    assert row['current']['configs']['claude']['owned'] == {}
    settings = w['home'] / '.claude/settings.json'
    settings.write_text(json.dumps({'statusLine': {'type': 'command', 'command': 'python3 ' + str(
        w['home'] / '.claude/skills/al/scripts/al_statusline.py')}, 'permissions': {'allow': ['Read']}}) + '\n')
    before = settings.read_bytes()
    def reviewed_dependency(root):
        path = root / 'claude/skills/al/scripts/al_scope.py'
        path.write_bytes(path.read_bytes() + b'\n# Independently reviewed fixture display dependency correction.\n')
    request = artifact(enrolled, *tag(enrolled, reviewed_dependency))
    assert request['reviews']['global_package_update']['projection']['version'] == 2
    result = public(enrolled, request, '--allow-running')
    assert result.returncode == 0, result.stdout + result.stderr
    assert settings.read_bytes() == before
    current = load_record(w['state'], target['target_id'])
    assert current['current']['configs']['claude']['owned'] == {}
    assert current['current']['status_line'] is False
