"""Complete node groups retain closure currency and independent processes."""
import copy
import json
from pathlib import Path
import time

from al.verification_isolation import SPLIT_FILES, closure_proof
from al.verification_plan import build_plan
from al.verification_runner import run
from al.tests.verification_helpers import source, declarations, collected


def reviewed(collections, binding, size=2):
    value = {'schema_version': 1, 'shared': [], 'isolated_files': {}, 'node_groups': {},
             'isolation_closure': closure_proof(binding)}
    for suite, collection in collections.items():
        for node in collection['nodes']:
            file = node['source']['path'];key = suite + ':' + file
            conftest = 'al/tests/conftest.py' if suite == 'installer' else 'al/' + suite + '/skills/al/tests/conftest.py'
            value['isolated_files'][key] = {'test': node['source']['sha256'], 'fixtures': binding['files'][conftest]}
            value['node_groups'][key] = {'max_nodes': size, 'reason': 'Reviewed complete-node subprocess isolation.'}
    return value


def test_new_nodes_run_in_fast_and_affected_without_inherited_classification(tmp_path):
    root = source(tmp_path);declared = declarations(root)
    path = root / 'al/codex/skills/al/tests/test_a.py'
    path.write_text(path.read_text() + '\ndef test_new():\n    assert True\n')
    collections, binding = collected(root, tmp_path)
    declared['affected'] = [{'source': 'known/**', 'tests': ['installer:*']}]
    for tier in ('fast', 'affected'):
        plan = build_plan(collections, declared, tier=tier, changed_paths=['known/file'], binding=binding)
        assert 'codex:tests/test_a.py::test_new' in {row['id'] for row in plan['execution_assignments']}
        assert not plan['complete_required']


def test_all_reviewed_templates_conserve_nodes_and_fall_back_on_any_closure_drift(tmp_path):
    root = source(tmp_path);collections, binding = collected(root, tmp_path)
    for file in sorted(SPLIT_FILES):
        expanded = copy.deepcopy(collections)
        for suite in ('codex', 'claude'):
            template = expanded[suite]['nodes'][0]
            expanded[suite]['nodes'] = []
            for index in range(37):
                node = copy.deepcopy(template);node['nodeid'] = file + '::test_case[' + str(index) + ']'
                node['source']['path'] = file;expanded[suite]['nodes'].append(node)
        declared = reviewed(expanded, binding, size=16)
        first = build_plan(expanded, declared, tier='full', changed_paths=[], binding=binding)
        second = build_plan(expanded, declared, tier='full', changed_paths=[], binding=binding)
        assert first['execution_assignments'] == second['execution_assignments']
        assert len({a['id'] for a in first['execution_assignments']}) == 75
        for suite in ('codex', 'claude'):
            assignments = [a for a in first['execution_assignments'] if a['suite'] == suite]
            groups = {a['group'] for a in assignments}
            assert len(groups) == 3 and all(not a['serialization_group'] for a in assignments)
            assert sorted(sum(([a['id'] for a in assignments if a['group'] == group] for group in groups), [])) == sorted(a['id'] for a in assignments)
        for path in ('al/codex/skills/al/tests/conftest.py', 'al/codex/skills/al/tests/helper.py',
                     'al/codex/skills/al/scripts/pure.py', 'al/resource.json'):
            changed = copy.deepcopy(binding);changed['files'][path] = {'sha256': 'changed', 'mode': 0o644}
            fallback = build_plan(expanded, declared, tier='full', changed_paths=[], binding=changed)
            assert not fallback['isolation_closure_current']
            assert all(a['serialization_group'] and ':chunk-' not in a['group'] for a in fallback['execution_assignments'])


def grouped_source(tmp_path, body):
    root = source(tmp_path, {'codex': body, 'claude': body})
    for suite in ('codex', 'claude'):
        directory = root / 'al' / suite / 'skills/al/tests'
        (directory / 'test_a.py').rename(directory / 'test_runtime_batch13_remote.py')
        (directory / 'conftest.py').write_text('''import shutil
import subprocess
import pytest

@pytest.fixture(scope='session')
def seed(tmp_path_factory):
    root = tmp_path_factory.mktemp('seed')
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    return root

@pytest.fixture(autouse=True)
def repo(seed, tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    shutil.copytree(seed, root)
    monkeypatch.chdir(root)
    return root
''')
    collections, binding = collected(root, tmp_path)
    return build_plan(collections, reviewed(collections, binding, size=1), tier='full', changed_paths=[], binding=binding)


def test_real_groups_isolate_seed_indexes_native_homes_responses_and_logs(tmp_path):
    plan = grouped_source(tmp_path, '''import os
from pathlib import Path
import subprocess
import time
import pytest

@pytest.mark.parametrize('response', ['success', 'failure'])
def test_a(repo, response):
    (repo / 'response.json').write_text(response)
    (repo / 'same-file').write_text(response)
    subprocess.run(['git', 'add', 'same-file'], check=True)
    assert subprocess.run(['git', 'show', ':same-file'], capture_output=True, check=True).stdout.decode() == response
    files = [Path(os.environ[key]) / 'same-file' for key in ('HOME', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'AL_RUNTIME_DIR')]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        assert not path.exists()
        path.write_text(response)
    time.sleep(0.1)
    assert all(path.read_text() == response for path in files)
    assert (repo / 'response.json').read_text() == response
    print('private-response=' + response)
''')
    result = run(plan, workers=4, artifact_root=tmp_path / 'workers', cache_mode='cold')
    assert result['reconciliation']['complete'] and result['reconciliation']['executed_passed'] == 5
    assert len(result['shards']) == 5 and result['cleanup']['complete']
    logs = [Path(row['log']) for row in result['shards']]
    assert len(set(logs)) == 5
    assignments = [json.loads(Path(row['assignment']['path']).read_bytes()) for row in result['shards']]
    assert len({row['shard_id'] for row in assignments}) == 5


def test_group_signal_failure_does_not_cancel_sibling_or_allow_descendant_late_write(tmp_path):
    plan = grouped_source(tmp_path, '''import os
from pathlib import Path
import signal
import subprocess
import sys
import pytest

@pytest.mark.parametrize('mode', ['signal', 'descendant', 'ordinary'])
def test_a(mode):
    if mode == 'signal':
        os.kill(os.getpid(), signal.SIGTERM)
    elif mode == 'descendant':
        marker = str(Path(os.environ['HOME']) / 'late-write')
        subprocess.Popen([sys.executable, '-c', "import time;from pathlib import Path;time.sleep(2);Path(" + repr(marker) + ").write_text('escaped')"])
    else:
        (Path(os.environ['HOME']) / 'sibling-survived').write_text('yes')
''')
    result = run(plan, workers=4, artifact_root=tmp_path / 'workers', cache_mode='cold')
    assert not result['reconciliation']['complete'] and result['cleanup']['complete']
    assert len(result['shards']) == 7 and result['reconciliation']['executed_passed'] == 5
    assert len(result['reconciliation']['unaccounted']) == 2
    assert len(list((tmp_path / 'workers').glob('*/home/sibling-survived'))) == 2
    time.sleep(2.1)
    assert not list((tmp_path / 'workers').glob('*/home/late-write'))


def test_focused_cohort_executes_complete_platform_templates_without_full_claim(tmp_path):
    from al.verification_cohort import execute
    from al.verification_profile import capture_source_binding
    root = source(tmp_path)
    for suite in ('codex', 'claude'):
        directory = root / 'al' / suite / 'skills/al/tests'
        (directory / 'test_a.py').rename(directory / 'test_runtime_batch13_remote.py')
    collections, binding = collected(root, tmp_path)
    declared = reviewed(collections, binding)
    (root / 'al/verification-obligations.json').write_text(json.dumps(declared))
    report = tmp_path / 'cohort.json'
    value = execute(root, ['tests/test_runtime_batch13_remote.py'], report, workers=2)
    assert value['status'] == 'PASS' and not value['full_suite']
    assert value['selected_obligations'] == 2 and value['collected_obligations'] == 3
    assert value['verification']['reconciliation']['executed_passed'] == 2
    assert not value['verification']['reconciliation']['complete']
    assert value['source_binding'] == capture_source_binding(root)
    assert json.loads(report.read_bytes())['status'] == 'PASS'
