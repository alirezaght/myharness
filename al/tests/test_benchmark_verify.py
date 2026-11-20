"""Controlled copies bind real module origins and identical fixture workloads."""
import hashlib
import json
from pathlib import Path

import pytest
from al.benchmark_verify import compare
from al.verification_profile import capture_source_binding


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_HOME', str(tmp_path/'harness-home'))
    monkeypatch.setenv('HARNESS_STORAGE_MINIMUM_FREE_BYTES', '0')


def assert_durable_result(result, report):
    assert json.loads(report.read_bytes()) == result
    for run in result['runs'].values():
        scratch=Path(run['storage_binding']['allocation']['path'])
        for name in ('probe_path','log'):
            path=Path(run[name])
            assert path.is_file() and not path.is_relative_to(scratch)
        publication=Path(run['storage_result']['reference'])
        assert publication.is_file() and not publication.is_relative_to(scratch)
    return json.loads(Path(result['storage_cleanup_path']).read_bytes())


def fixture(tmp_path):
    roots=[]
    for name,value in [('baseline',1),('candidate',2)]:
        root=tmp_path/name;roots.append(root)
        package=root/'al/codex/skills/al'
        (package/'scripts').mkdir(parents=True);(package/'tests').mkdir()
        (package/'scripts/al_sample.py').write_text('VALUE='+str(value)+'\n')
        (package/'tests/conftest.py').write_text('import sys\nfrom pathlib import Path\nsys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))\n')
        (package/'tests/test_cohort.py').write_text('import al_sample\ndef test_workload():\n assert al_sample.VALUE>0\n')
    binding=capture_source_binding(roots[0]);receipt=tmp_path/'baseline-files.json'
    receipt.write_text(json.dumps({'baseline':str(roots[0]),'files':[{'path':p,'type':'file',**v} for p,v in binding['files'].items()]}))
    return roots,receipt,hashlib.sha256(receipt.read_bytes()).hexdigest()


def test_actual_comparison_has_no_candidate_runtime_fallback_and_common_fixtures(tmp_path):
    (baseline,candidate),receipt,sha=fixture(tmp_path)
    result=compare(baseline=baseline,candidate=candidate,suite='codex',nodes=['tests/test_cohort.py::test_workload'],
                   report=tmp_path/'report.json',baseline_files=receipt,baseline_files_sha256=sha)
    assert result['status']=='PASS'
    a,b=(result['runs'][name] for name in ('baseline','candidate'))
    assert a['probe']['runtime_origins']['al_sample']['sha256']!=b['probe']['runtime_origins']['al_sample']['sha256']
    assert result['common_fixture_overlay']
    for run in (a,b):
        assert run['status']=='PASS' and run['ownership']['identity']
        assert set(run['probe']['stages'])=={'tests/test_cohort.py::test_workload'}
    assert 'no all-suite timing claim' in result['conditions']
    cleanup=assert_durable_result(result,tmp_path/'report.json')
    assert all(row['state']=='RETAINED' for row in cleanup['results'])
    assert all(Path(run['storage_binding']['allocation']['path']).is_dir() for run in (a,b))
    assert not list(tmp_path.glob('al-controlled-benchmark-*/baseline/source'))
    assert not list(tmp_path.glob('al-controlled-benchmark-*/candidate/execution/home'))


def test_baseline_receipt_drift_refuses_before_execution(tmp_path):
    (baseline,candidate),receipt,sha=fixture(tmp_path)
    (baseline/'al/codex/skills/al/scripts/al_sample.py').write_text('VALUE=9\n')
    with pytest.raises(ValueError,match='preserved baseline changed'):
        compare(baseline=baseline,candidate=candidate,suite='codex',nodes=['tests/test_cohort.py::test_workload'],
                report=tmp_path/'report.json',baseline_files=receipt,baseline_files_sha256=sha)
    assert not list(tmp_path.glob('al-controlled-benchmark-*'))


def test_foreign_runtime_origin_cannot_masquerade_as_baseline(tmp_path):
    (baseline,candidate),receipt,sha=fixture(tmp_path)
    foreign=tmp_path/'foreign';foreign.mkdir();(foreign/'al_sample.py').write_text('VALUE=99\n')
    conftest=candidate/'al/codex/skills/al/tests/conftest.py'
    conftest.write_text('import sys\nsys.path.insert(0,'+repr(str(foreign))+')\n')
    result=compare(baseline=baseline,candidate=candidate,suite='codex',nodes=['tests/test_cohort.py::test_workload'],
                   report=tmp_path/'report.json',baseline_files=receipt,baseline_files_sha256=sha)
    assert result['status']=='FAIL'
    assert all('runtime module outside target: al_sample' in run['probe']['errors'] for run in result['runs'].values())
    cleanup=assert_durable_result(result,tmp_path/'report.json')
    assert all(row['state']=='RETAINED' for row in cleanup['results'])
    assert all(Path(run['storage_binding']['allocation']['path']).is_dir() for run in result['runs'].values())


def test_value_free_command_kinds_do_not_record_arguments():
    from al.benchmark_probe import kind
    assert kind(['git','-c','user.name=secret-value','diff','secret-filename'])=='git:diff'
    assert kind(['git','--no-optional-locks','rev-parse','private-ref'])=='git:rev-parse'
    assert kind(['/private/secret/program','secret-token'])=='other'


def test_additional_relevant_baseline_module_is_not_accepted_as_original(tmp_path):
    (baseline,candidate),receipt,sha=fixture(tmp_path)
    (baseline/'al/codex/skills/al/scripts/al_unexpected.py').write_text('VALUE=99\n')
    with pytest.raises(ValueError,match='baseline relevant membership changed'):
        compare(baseline=baseline,candidate=candidate,suite='codex',nodes=['tests/test_cohort.py::test_workload'],
                report=tmp_path/'report.json',baseline_files=receipt,baseline_files_sha256=sha)
    assert not list(tmp_path.glob('al-controlled-benchmark-*'))


def test_simulated_qualified_settlement_removes_scratch_preserving_reports(tmp_path,monkeypatch):
    """Adapter-contract simulation only; this does not qualify real OS containment."""
    import al.benchmark_verify as benchmark
    original=benchmark.execute
    def qualified(*args,**kwargs):
        owner=original(*args,**kwargs)
        owner.last_cleanup={**owner.last_cleanup,'descendant_scope':'pid_namespace',
            'descendants_settled':True,'settlement_basis':'qualified-bwrap-pidns-teardown-v1',
            'settlement_evidence':{'schema_version':1,'kind':'qualified-bwrap-private-lifecycle-v1',
                'launch_sha256':'a'*64,'teardown_sha256':'b'*64,'namespace_identity':{'simulated':True}}}
        return owner
    monkeypatch.setattr(benchmark,'execute',qualified)
    (baseline,candidate),receipt,sha=fixture(tmp_path)
    result=compare(baseline=baseline,candidate=candidate,suite='codex',nodes=['tests/test_cohort.py::test_workload'],
        report=tmp_path/'report.json',baseline_files=receipt,baseline_files_sha256=sha)
    cleanup=assert_durable_result(result,tmp_path/'report.json')
    assert result['status']=='PASS' and cleanup['complete'] is True
    assert [row['state'] for row in cleanup['results']]==['REMOVED','REMOVED']
    assert all(not Path(run['storage_binding']['allocation']['path']).exists() for run in result['runs'].values())
    assert all((root/'al/codex/skills/al/scripts/al_sample.py').is_file() for root in (baseline,candidate))


def test_incomplete_copy_is_registered_and_retained(tmp_path,monkeypatch):
    import al.benchmark_verify as benchmark
    (baseline,candidate),receipt,sha=fixture(tmp_path)
    def fail(*args,**kwargs):raise OSError('injected copy failure')
    monkeypatch.setattr(benchmark,'copy_bound',fail)
    with pytest.raises(OSError,match='injected copy failure'):
        compare(baseline=baseline,candidate=candidate,suite='codex',nodes=['tests/test_cohort.py::test_workload'],
            report=tmp_path/'report.json',baseline_files=receipt,baseline_files_sha256=sha)
    records=[json.loads(p.read_bytes()) for p in (tmp_path/'harness-home/managed-storage/records').glob('*.json')]
    assert len(records)==1 and records[0]['state']=='RETAINED'
    assert records[0]['retention_reason']=='benchmark_execution_incomplete'
    assert not (tmp_path/'report.json').exists()


def test_changed_original_source_cannot_finalize_passing_cohorts(tmp_path,monkeypatch):
    import al.benchmark_verify as benchmark
    original=benchmark.execute
    (baseline,candidate),receipt,sha=fixture(tmp_path)
    def change(*args,**kwargs):
        owner=original(*args,**kwargs)
        if kwargs['assignment'].startswith('benchmark:candidate:'):
            (candidate/'al/codex/skills/al/scripts/al_sample.py').write_text('VALUE=99\n')
        return owner
    monkeypatch.setattr(benchmark,'execute',change)
    with pytest.raises(ValueError,match='original benchmark source moved'):
        compare(baseline=baseline,candidate=candidate,suite='codex',nodes=['tests/test_cohort.py::test_workload'],
            report=tmp_path/'report.json',baseline_files=receipt,baseline_files_sha256=sha)
    records=[json.loads(p.read_bytes()) for p in (tmp_path/'harness-home/managed-storage/records').glob('*.json')]
    assert len(records)==2 and all(row['state']=='RETAINED' for row in records)
    assert not (tmp_path/'report.json').exists()
