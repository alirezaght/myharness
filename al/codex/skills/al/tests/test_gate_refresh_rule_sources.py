"""Issue #32: a writer's edit to a sourced rule file refreshes the gates instead of making them INFRA.

A writer documents a unit's new behaviour in the unit's nested AGENTS.md. After `writer finish`
and the phase steps, gates run against a refreshed manifest; none ends INFRA with 'source rule
changed before launch'. When a refresh cannot run, `gates run` stops before any gate with one
message naming the changed sources and the recovery commands."""
import json
import subprocess
from hook_regression_helpers import invoke
from batch5_helpers import state
from launcher_context import admit_planning


def _commit(repo, rel, text):
    (repo/rel).write_text(text)
    for args in (('add', rel), ('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'nested rules')):
        subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True)


def _implementing(cli, tmp, repo, paths):
    _commit(repo, 'svc/AGENTS.md', 'Service instructions.\n')
    invoke(cli, 'start', '--spec', 'Change the service and document it in its AGENTS.md.')
    invoke(cli, 'spec-gate', 'SPEC_READY')
    invoke(cli, 'classify', 'SMALL', '--designer', 'no', '--implementers', 'al-engineer')
    file = tmp/'paths.json'; file.write_text(json.dumps(paths))
    invoke(cli, 'gates', 'discover', '--paths-file', str(file))
    invoke(cli, 'gates', 'run', '--stage', 'base')
    admit_planning(cli); invoke(cli, 'phase', 'IMPLEMENTATION')
    invoke(cli, 'writer', 'start', '--id', 'w1', '--role', 'al-engineer', '--files', ','.join(paths))


def _sourced(st):
    import al_artifacts as A
    return {r['path'] for r in A.read_json(st['gate_manifests'][-1])['rules']}


def _no_infra(out):
    text = ''.join(out[1:])
    assert 'source rule changed before launch' not in text
    assert 'INFRA' not in text


def test_nested_instruction_edit_refreshes_at_writer_finish_and_gates_run(cli, tmp_path, repo):
    _implementing(cli, tmp_path, repo, ['svc/app.py', 'svc/AGENTS.md'])
    before = state(cli); old = before['gate_manifests'][-1]['sha256']
    assert 'svc/AGENTS.md' in _sourced(before)
    (repo/'svc/app.py').write_text('x = 2\n')
    (repo/'svc/AGENTS.md').write_text('Service instructions.\nThe service now returns 2.\n')
    invoke(cli, 'writer', 'finish', '--id', 'w1', '--outcome', 'success')
    after = state(cli)
    assert after['gate_manifests'][-1]['sha256'] != old
    assert any('svc/AGENTS.md' in r for r in after['gate_refreshes'][-1]['reasons'])
    invoke(cli, 'phase', 'FINALIZATION'); invoke(cli, 'phase', 'VERIFICATION')
    _no_infra(invoke(cli, 'gates', 'run', '--stage', 'base'))
    _no_infra(invoke(cli, 'gates', 'run', '--stage', 'candidate'))


def test_sourced_rule_outside_planned_file_entries_refreshes_at_phase_step(cli, tmp_path, repo):
    # The plan names only the code file; the unit's AGENTS.md is sourced by discovery, not planned.
    _implementing(cli, tmp_path, repo, ['svc/app.py'])
    before = state(cli); old = before['gate_manifests'][-1]['sha256']
    assert 'svc/AGENTS.md' in _sourced(before)
    invoke(cli, 'writer', 'finish', '--id', 'w1', '--outcome', 'success')
    # Edited outside any writer: the next phase step refreshes it.
    (repo/'svc/AGENTS.md').write_text('Service instructions, revised.\n')
    invoke(cli, 'phase', 'FINALIZATION')
    assert state(cli)['gate_manifests'][-1]['sha256'] != old
    invoke(cli, 'phase', 'VERIFICATION')
    _no_infra(invoke(cli, 'gates', 'run', '--stage', 'candidate'))


def test_gates_run_refreshes_itself_before_materialising(cli, tmp_path, repo):
    _implementing(cli, tmp_path, repo, ['svc/app.py', 'svc/AGENTS.md'])
    invoke(cli, 'writer', 'finish', '--id', 'w1', '--outcome', 'success')
    invoke(cli, 'phase', 'FINALIZATION'); invoke(cli, 'phase', 'VERIFICATION')
    old = state(cli)['gate_manifests'][-1]['sha256']
    (repo/'svc/AGENTS.md').write_text('Edited after the last step.\n')
    out = invoke(cli, 'gates', 'run', '--stage', 'candidate')
    _no_infra(out)
    assert 'gates refreshed' in ''.join(out[1:])
    assert state(cli)['gate_manifests'][-1]['sha256'] != old


def test_gates_run_refuses_once_with_recovery_when_refresh_cannot_run(cli, tmp_path, repo, monkeypatch):
    _implementing(cli, tmp_path, repo, ['svc/app.py', 'svc/AGENTS.md'])
    invoke(cli, 'writer', 'finish', '--id', 'w1', '--outcome', 'success')
    invoke(cli, 'phase', 'FINALIZATION'); invoke(cli, 'phase', 'VERIFICATION')
    before = state(cli)
    import al_gate_refresh
    monkeypatch.setattr(al_gate_refresh, 'refresh', lambda st: {'current': False, 'reasons': ['blocked'], 'paths': []})
    (repo/'svc/AGENTS.md').write_text('Edited after the last step.\n')
    out = ''.join(map(str, invoke(cli, 'gates', 'run', '--stage', 'candidate', ok=False)))
    assert 'svc/AGENTS.md' in out
    assert 'gates discover --extend current --paths-file' in out and 'gates plan' in out
    assert 'INFRA' not in out and out.count('source rule changed') <= 1
    after = state(cli)
    assert after['gate_manifests'] == before['gate_manifests']
    assert len(after['evidence']['entries']) == len(before['evidence']['entries'])


def _removed_case():
    import al_coverage_relevance as R
    import al_gate_currency as C
    uncertainty={'code':'uncertain-artifact-classification','unit':None,'path':'.','target':None}
    projection={'schema_version':2,'selected_services':['.'],'relevant_uncertainty_ids':[],
                'relevant_gap_ids':[],'relevant_obligation_ids':['g'],'required_binding_ids':['g'],
                'task_inventory':{},'relevant_ci_ids':[],'dependency_semantic_sha256':'deps',
                'ci_requirements_sha256':'ci','question':{'changed_paths':['Makefile','svc/app.py'],
                'governing_source_refs':[{'path':'Makefile'}]}}
    binding={'id':'g','argv':['check'],'cwd':'.','env_names':[],'required_scope':['.','Makefile'],
             'source_chain':[{'sha256':'source'}],'runner_resource':{'sha256':'runner'},
             'obligation_ids':['g'],'execution_signature':'old'}
    import copy
    manifest={'rules':[{'path':'Makefile'}],'gates':[],
              'service_coverage':{'projection':projection,'assessment':{'required_bindings':[binding]}}}
    current={'paths':['Makefile','svc/app.py'],'repository_facts':{'enumeration':{'absent_paths':['Makefile']},'dependencies':{
             'unclassified_artifact_paths':['Makefile'],'uncertainties':[uncertainty]}},
             'service_coverage':{'gaps':[],'required_bindings':[copy.deepcopy(binding)]},
             'relevance_projection':copy.deepcopy(projection)}
    current['relevance_projection']['question']['governing_source_refs']=[]
    current['relevance_projection']['relevant_uncertainty_ids']=[R.record_id(uncertainty)]
    current['service_coverage']['required_bindings'][0].update(required_scope=['.'],execution_signature='new')
    return manifest,current,['source rule missing: Makefile',C.REQUIREMENTS,
                            'sourced execution requirements changed; rediscover repository gates']


def test_removed_rule_requires_complete_causal_witness():
    import copy
    import al_gate_currency as C
    m,n,reasons=_removed_case()
    assert C._removed_rules(m,n,reasons)
    for value in (None,[],['Makefile','data.bin'],['.'],[None],'Makefile'):
        changed=copy.deepcopy(n);changed['repository_facts']['dependencies']['unclassified_artifact_paths']=value
        assert not C._removed_rules(m,changed,reasons)
    for key,value in [('argv',['other']),('cwd','svc'),('env_names',['TOKEN']),
                      ('source_chain',[{'sha256':'changed'}]),('runner_resource',{'sha256':'changed'}),
                      ('obligation_ids',['other']),('required_scope',[])]:
        changed=copy.deepcopy(n);changed['service_coverage']['required_bindings'][0][key]=value
        assert not C._removed_rules(m,changed,reasons),key
    for key,value in [('selected_services',['svc']),('dependency_semantic_sha256','new'),
                      ('ci_requirements_sha256','new'),('relevant_gap_ids',['gap'])]:
        changed=copy.deepcopy(n);changed['relevance_projection'][key]=value
        assert not C._removed_rules(m,changed,reasons),key
    changed=copy.deepcopy(n);changed['repository_facts']['enumeration']['absent_paths']=[]
    assert not C._removed_rules(m,changed,reasons)
    assert not C._removed_rules(m,n,reasons+['coverage implementation policy changed; rediscover repository gates'])


def test_artifact_provenance_collects_all_contributors():
    from types import SimpleNamespace
    import al_python_imports as P
    scan=SimpleNamespace(paths=['a.unknown','b.unknown','c.csv'],files={'a.unknown':b'', 'b.unknown':b''},
                         base={},members=[],rules={})
    paths=set();rows,unknown=P.artifact_consumers(scan,{}, {},unclassified_paths=paths)
    assert unknown and not rows and paths=={'a.unknown','b.unknown','c.csv'}
    assert P.artifact_consumers(scan,{}, {})==(rows,unknown)


def test_removed_rule_failures_do_not_publish_partial_state(monkeypatch):
    import copy
    import al_gate_refresh as R
    m,n,reasons=_removed_case()
    st={'session_id':'test','gate_manifests':[{'sha256':'old'}]}
    currency={'current':False,'pathless':True,'paths':['Makefile'],
              'reasons':reasons,'removed_sourced_rules':True}
    monkeypatch.setattr(R,'_stale',lambda *a:['Makefile'])
    monkeypatch.setattr(R,'_unsettled',lambda *a:None)
    monkeypatch.setattr(R.Currency,'check',lambda *a:currency)
    monkeypatch.setattr(R.A,'read_json',lambda *a:m)
    m['paths']=['svc/app.py'];m['gates']=[]
    before=copy.deepcopy(st)
    def refused(*a,**kw):raise SystemExit('rediscovery refused')
    monkeypatch.setattr(R.D,'discover',refused)
    assert R.settle(st)['blocked'] and st==before
    monkeypatch.setattr(R.D,'discover',lambda *a,**kw:copy.deepcopy(m))
    monkeypatch.setattr(R.D,'validate_manifest',refused)
    assert R.settle(st)['blocked'] and st==before


def test_scope_change_carries_no_old_base_or_decision():
    import copy
    import al_gate_refresh as R
    gate={'id':'g','scope':['svc','Makefile']}
    old={'gates':[gate]};new=copy.deepcopy(old);new['gates'][0]['scope']=['svc']
    st={'gate_runs':[{'manifest':'old','stage':'base','gate':'g','evidence':'E1'}],
        'gate_decisions':{'g':{'manifest':'old','action':'fix'}}}
    before=copy.deepcopy(st)
    assert R._carry(st,old,'old',new,'new')==[] and st==before


def test_mixed_python_go_facts_forward_provenance(repo):
    import subprocess
    import al_repository_facts as F
    (repo/'backend').mkdir()
    (repo/'backend/go.mod').write_text('module example.invalid/app\n\ngo 1.22\n')
    (repo/'backend/main.go').write_text('package main\nfunc main() {}\n')
    base=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    facts=F.collect(repo,base_oid=base,paths=['svc/app.py','backend/main.go','missing.bin'],opaque=set())
    deps=facts['dependencies']
    assert deps['unclassified_artifact_paths']==['missing.bin']
    assert any('go' in u['languages'] for u in facts['units'])
    assert any('python' in u['languages'] for u in facts['units'])
    assert any(u['code']=='uncertain-artifact-classification' for u in deps['uncertainties'])
    assert F.semantic_requirements(facts)['dependencies']['unclassified_artifact_paths']==['missing.bin']



def test_tracked_rule_question_addition_is_exact():
    import copy
    import al_gate_currency as C
    m,n,reasons=_removed_case()
    m['service_coverage']['projection']['question']['changed_paths']=['svc/app.py']
    assert C._removed_rules(m,n,reasons)
    for paths in (['Makefile','svc/app.py','other.py'],['Makefile'],['svc/app.py','existing-rule'],
                  ['svc/app.py','unsourced-rule'],['svc/app.py','absent.bin']):
        changed=copy.deepcopy(n);changed['relevance_projection']['question']['changed_paths']=paths
        assert not C._removed_rules(m,changed,reasons),paths
    for key,value in [('affected_source_paths',['other.py']),('anticipated_paths',['other.py']),
                      ('directory_paths',['svc']),('selected_service_ids',['svc']),
                      ('ci_context_binding','new'),('comparison_bindings',['new']),('kind','comparison')]:
        changed=copy.deepcopy(n);changed['relevance_projection']['question'][key]=value
        assert not C._removed_rules(m,changed,reasons),key
    for defect in ('existing','unsourced','unrecognized'):
        old=copy.deepcopy(m);changed=copy.deepcopy(n)
        if defect=='existing':changed['repository_facts']['enumeration']['absent_paths']=[]
        elif defect=='unsourced':old['rules']=[]
        else:
            old['rules']=[{'path':'absent.bin'}]
            assert not C._removed_rules(old,changed,['source rule missing: absent.bin',*reasons[1:]])
            continue
        assert not C._removed_rules(old,changed,reasons)



def test_removed_rule_witness_does_not_read_facts_without_missing_source():
    import al_gate_currency as C
    class Unused(dict):
        def __getitem__(self,key):raise AssertionError('irrelevant witness input read: '+key)
    manifest,current=Unused(),Unused()
    for reasons in ([],[C.REQUIREMENTS]):
        assert C._removed_rules(manifest,current,reasons) is False
    assert manifest=={} and current=={}
