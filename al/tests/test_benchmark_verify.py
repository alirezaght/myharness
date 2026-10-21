"""Controlled copies bind real module origins and identical fixture workloads."""
import hashlib
import json
from pathlib import Path

import pytest
from al.benchmark_verify import compare
from al.verification_profile import capture_source_binding


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
