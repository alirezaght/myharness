"""The visible guard executes real pytest; metadata is not a passing verdict."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

SCRIPTS=Path(__file__).resolve().parents[1]/'codex/skills/al/scripts'
NAMES=('al_pytest_profile.py','al_pytest_sources.py','al_pytest_runner.py','al_ci_yaml.py')


def run(root,body,*,mode='1',args=()):
    (root/'tests').mkdir(exist_ok=True);(root/'tests/test_case.py').write_text(body)
    (root/'pyproject.toml').write_text('[tool.pytest.ini_options]\ntestpaths=["tests"]\n')
    version=importlib.metadata.version('pytest')
    (root/'uv.lock').write_text('version=1\n[[package]]\nname="pytest"\nversion='+json.dumps(version)+'\n')
    resources={name:{'path':str(SCRIPTS/name),'sha256':hashlib.sha256((SCRIPTS/name).read_bytes()).hexdigest()} for name in NAMES}
    env={k:v for k,v in os.environ.items() if not k.startswith(('PYTEST_','PYTHON'))}
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD']=mode
    return subprocess.run([sys.executable,'-I','-B',str(SCRIPTS/'al_pytest_runner.py'),'--resources',json.dumps(resources),'--',*args],
                          cwd=root,env=env,capture_output=True,text=True,timeout=30)


def test_real_pass_requires_nonempty_actual_outcomes(tmp_path):
    result=run(tmp_path,'from pathlib import Path\ndef test_real():\n    Path("executed").write_text("actual call")\n    assert 2+2==4\n')
    assert result.returncode==0,result.stderr[-2500:]+result.stdout[-2500:]
    assert (tmp_path/'executed').read_text()=='actual call'
    report=json.loads(next(line.removeprefix('AL_PYTEST_EXECUTION ') for line in result.stdout.splitlines() if line.startswith('AL_PYTEST_EXECUTION ')))
    assert report['outcomes']=={'collected':1,'passed':1,'failed':0,'skipped':0,'missing':[],'errors':[]}
    assert report['profile']['collection_roots']==['tests'] and report['environment']['mode']=='disabled'


@pytest.mark.parametrize('body',[
    'def test_bad():\n    assert False\n',
    'VALUE=1\n',
    'import pytest\n@pytest.mark.skip(reason="not executed")\ndef test_skip():pass\n',
    'import pytest\n@pytest.mark.xfail\ndef test_xfail():assert False\n',
])
def test_failed_empty_or_skipped_testing_cannot_pass(tmp_path,body):
    result=run(tmp_path,body)
    assert result.returncode!=0,result.stdout[-2000:]


def test_explicit_test_module_plugin_is_not_approved_by_a_hash(tmp_path):
    (tmp_path/'plugin.py').write_text('def pytest_collection_modifyitems(items):items.clear()\n')
    (tmp_path/'tests').mkdir();(tmp_path/'tests/__init__.py').write_text('')
    result=run(tmp_path,'pytest_plugins=["plugin"]\ndef test_required():assert True\n')
    assert result.returncode!=0,result.stdout[-2500:]


def test_ancestor_configuration_and_hooks_are_not_loaded(tmp_path):
    root=tmp_path/'project';root.mkdir()
    (tmp_path/'pytest.ini').write_text('[pytest]\naddopts=--collect-only\n')
    (tmp_path/'conftest.py').write_text('raise RuntimeError("ancestor hook must never execute")\n')
    result=run(root,'def test_real():assert True\n')
    assert result.returncode==0,result.stdout[-2500:]+result.stderr[-2500:]


@pytest.mark.parametrize('fixture',[
    '    class Plugin:pass\n    plugin=Plugin()\n    request.config.pluginmanager.register(plugin,"during-fixture")\n',
    '    class Plugin:pass\n    plugin=Plugin()\n    request.config.pluginmanager.register(plugin,"transient")\n    request.config.pluginmanager.unregister(plugin)\n',
    '    request.session.items.clear()\n',
    '    request.config.hook.pytest_deselected(items=[request.node])\n',
    '    request.node._nodeid="unexpected-case"\n',
    '    raise RuntimeError("actual setup failure")\n',
    '    yield\n    raise RuntimeError("actual teardown failure")\n',
])
def test_fixture_runtime_cannot_change_required_execution(tmp_path,fixture):
    tests=tmp_path/'tests';tests.mkdir()
    (tests/'conftest.py').write_text('import pytest\n@pytest.fixture(autouse=True)\ndef behavior(request):\n'+fixture)
    result=run(tmp_path,'def test_required():assert True\n')
    assert result.returncode!=0,result.stderr[-1500:]+result.stdout[-2500:]


def runner_module():
    import importlib.util
    spec=importlib.util.spec_from_file_location('reviewed_pytest_guard',SCRIPTS/'al_pytest_runner.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_builtin_class_identity_cannot_be_spoofed():
    import _pytest.legacypath
    guard=runner_module()
    assert guard.builtin_plugin(_pytest.legacypath.LegacyTmpdirPlugin)
    fake=type('LegacyTmpdirPlugin',(),{'__module__':'_pytest.legacypath'})
    assert not guard.builtin_plugin(fake) and not guard.builtin_plugin(fake())


def test_duplicate_and_unexpected_success_reports_are_rejected(tmp_path):
    from types import SimpleNamespace
    recorder=runner_module().Outcomes(tmp_path,[]);recorder.collected=['required']
    for identity in ('required','required','foreign'):
        recorder.pytest_runtest_logreport(SimpleNamespace(nodeid=identity,when='call',outcome='passed',failed=False,skipped=False))
    assert recorder.summary()['errors']==['duplicate successful pytest call identity','unexpected pytest outcome identity']


def test_eager_annotation_cannot_hide_a_required_test(tmp_path):
    tests=tmp_path/'tests';tests.mkdir()
    (tests/'conftest.py').write_text('def helper() -> (collect_ignore := ["test_hidden.py"]):pass\n')
    (tests/'test_hidden.py').write_text('def test_required_failure():assert False\n')
    result=run(tmp_path,'def test_visible():assert True\n')
    assert result.returncode!=0,result.stdout[-2500:]+result.stderr[-1000:]
