import json

from evidence_helpers import plan_na_gates
from execution_helpers import bound
import launcher_context
from launcher_context import _reports


def _packet(cli, repo, tmp_path, monkeypatch):
    bound(cli, tmp_path, repo, monkeypatch)
    # Exercise the real packet -> report -> admit path instead of the fixture's automatic admission.
    monkeypatch.setattr(launcher_context, 'admit_planning', lambda *args, **kwargs: None)
    assert cli('spec-gate', 'SPEC_READY')[0] == 0
    assert cli('classify', 'STANDARD', '--designer', 'no', '--implementers', 'al-engineer')[0] == 0
    plan_na_gates(cli, tmp_path, ('docs/readme.md',))
    code, out, err = cli('planning', 'packet')
    assert code == 0, (code, out, err)
    return json.loads(out)


def test_packet_prints_a_template_with_every_mechanical_field_filled(cli, repo, tmp_path, monkeypatch):
    packet = _packet(cli, repo, tmp_path, monkeypatch)
    template, expected = packet['report_template'], _reports().report_for(packet)
    for key in ('schema_version', 'kind', 'attempt_id', 'model', 'effort', 'policy_digest', 'packet_sha256', 'binding', 'routing'):
        assert template[key] == expected[key], key
    behavior, reference = template['authored_obligations'], expected['authored_obligations']
    for key in ('binding', 'authorship', 'criteria_registry'):
        assert behavior[key] == reference[key], key


def test_unedited_template_is_refused_and_a_filled_one_is_admitted(cli, repo, tmp_path, monkeypatch):
    packet = _packet(cli, repo, tmp_path, monkeypatch)
    path = tmp_path / 'report.json'
    path.write_text(json.dumps(packet['report_template']))
    code, out, err = cli('planning', 'report', '--file', str(path))
    assert code != 0 and 'FILL:' in str(code) + out + err
    filled = json.loads(json.dumps(packet['report_template']).replace('FILL: ', 'Architect: '))
    path.write_text(json.dumps(filled))
    code, out, err = cli('planning', 'report', '--file', str(path))
    assert code == 0, (code, out, err)
    obligations = json.loads(out)['materialized_obligations']['stored_path']
    assert cli('planning', 'admit', '--file', obligations)[0] == 0


def test_behavior_packet_prints_an_observation_template_per_scenario(cli, repo, tmp_path, monkeypatch):
    packet = _packet(cli, repo, tmp_path, monkeypatch)
    filled = json.loads(json.dumps(packet['report_template']).replace('FILL: ', 'Architect: '))
    path = tmp_path / 'report.json';path.write_text(json.dumps(filled))
    code, out, err = cli('planning', 'report', '--file', str(path))
    assert code == 0, (code, out, err)
    assert cli('planning', 'admit', '--file', json.loads(out)['materialized_obligations']['stored_path'])[0] == 0
    code, out, err = cli('behavior', 'packet')
    assert code == 0, (code, out, err)
    behavior = json.loads(out)
    templates = behavior['observation_templates']
    scenarios = [s['id'] for g in behavior['obligations']['content']['obligations'] for s in g['scenarios']]
    assert [t['scenario_id'] for t in templates] == scenarios
    path = tmp_path / 'observation.json';path.write_text(json.dumps(templates[0]))
    code, out, err = cli('behavior', 'observe', '--file', str(path))
    assert code != 0 and 'FILL:' in str(code) + out + err


def _filled(packet):
    return json.loads(json.dumps(packet['report_template']).replace('FILL: ', 'Architect: '))


def test_omitted_runtime_fixed_template_fields_are_restored(cli, repo, tmp_path, monkeypatch):
    packet = _packet(cli, repo, tmp_path, monkeypatch)
    report = _filled(packet)
    del report['specification']['status']
    for key in ('kind', 'binding', 'packet_sha256'):
        del report[key]
    del report['authored_obligations']['authorship']
    path = tmp_path / 'report.json'
    path.write_text(json.dumps(report))
    code, out, err = cli('planning', 'report', '--file', str(path))
    assert code == 0, (code, out, err)
    stored = json.loads(open(json.loads(out)['report']['stored_path']).read())
    assert stored == _filled(packet)


def test_changed_runtime_fixed_template_field_is_refused_by_path(cli, repo, tmp_path, monkeypatch):
    packet = _packet(cli, repo, tmp_path, monkeypatch)
    report = _filled(packet)
    report['packet_sha256'] = '0' * 64
    path = tmp_path / 'report.json'
    path.write_text(json.dumps(report))
    code, out, err = cli('planning', 'report', '--file', str(path))
    assert code != 0 and 'packet_sha256' in str(code) + out + err


def test_missing_and_unexpected_fields_name_their_full_json_path(cli, repo, tmp_path, monkeypatch):
    packet = _packet(cli, repo, tmp_path, monkeypatch)
    report = _filled(packet)
    del report['specification']['reason']
    report['specification']['extra'] = 1
    path = tmp_path / 'report.json'
    path.write_text(json.dumps(report))
    code, out, err = cli('planning', 'report', '--file', str(path))
    text = str(code) + out + err
    assert code != 0 and 'specification.reason' in text and 'specification.extra' in text
    report = _filled(packet)
    del report['authored_obligations']['implementation_context']['status']
    path.write_text(json.dumps(report))
    code, out, err = cli('planning', 'report', '--file', str(path))
    text = str(code) + out + err
    assert code != 0 and 'authored_obligations.implementation_context.status' in text


def test_identical_report_resubmit_is_idempotent_and_a_different_one_is_refused(
        cli, repo, tmp_path, monkeypatch):
    packet = _packet(cli, repo, tmp_path, monkeypatch)
    report = _filled(packet)
    path = tmp_path / 'report.json'
    path.write_text(json.dumps(report))
    code, first, err = cli('planning', 'report', '--file', str(path))
    assert code == 0, (code, first, err)
    path.write_text(json.dumps(report, indent=2))
    code, again, err = cli('planning', 'report', '--file', str(path))
    assert code == 0, (code, again, err)
    first, again = json.loads(first), json.loads(again)
    assert again['report'] == first['report']
    assert again['materialized_obligations'] == first['materialized_obligations']
    report['specification']['reason'] = 'Architect: a different rationale'
    path.write_text(json.dumps(report))
    code, out, err = cli('planning', 'report', '--file', str(path))
    assert code != 0 and 'cannot be replaced' in str(code) + out + err


def test_additive_amendment_routes_seed_exact_current_template_and_admitted_authority(
        cli,repo,tmp_path,monkeypatch,state_file,run_state):
    import copy
    from pathlib import Path
    import al_proof_json as J
    from al_schema import refuse
    from test_amendment_feedback import ok
    run_state('implemented')
    monkeypatch.setattr(launcher_context,'admit_planning',lambda *args,**kwargs:None)
    initial=json.loads(state_file().read_bytes())
    original_authority=initial['proof_admission']['authority']
    expected=copy.deepcopy(J.read(original_authority)['criteria'])
    original_package=copy.deepcopy(initial['task_package'])
    old_report=None
    routes=[{'criteria':['  Exact criterion α preserves spaces  ']},
            {'supplemental_acceptance':['Exact supplemental β']},
            {'criteria':['Exact criterion γ'],'supplemental_acceptance':['Exact supplemental δ']}]
    for number,fields in enumerate(routes,1):
        amendment=dict(id='AM-'+str(number),source='explicit user acceptance',
                       text='Preserve prior accepted outcomes and add the following exact acceptance obligations.',**fields)
        if number>1:amendment['affected_criteria']=[expected[-1]['id']]
        path=tmp_path/'amendment.json';path.write_text(json.dumps(amendment,ensure_ascii=False))
        raw=path.read_bytes();ok(cli,'task','amend','--file',str(path))
        current=json.loads(state_file().read_bytes())
        assert current['task_package']==original_package
        assert current['proof_admission']['status']=='pending' and 'authority' not in current['proof_admission']
        assert current['evidence']['invalidated_at'] and current['review'] is None and current['spec_status'] is None
        ref=current['amendments'][-1]['artifact'];assert Path(ref['stored_path']).read_bytes()==raw
        for field in ('criteria','supplemental_acceptance'):
            expected.extend({'id':value,'artifact':ref,'pointer':'/'+field+'/'+str(index),'value_sha256':J.digest(value)}
                            for index,value in enumerate(fields.get(field,[])))
        ok(cli,'spec-gate','SPEC_READY')
        packet=json.loads(ok(cli,'planning','packet')[1])
        assert packet['content']['accepted_amendments']==[a['artifact'] for a in current['amendments']]
        behavior=packet['report_template']['authored_obligations'];group=behavior['obligations'][0]
        assert behavior['criteria_registry']==expected
        assert group['criterion_ids']==[entry['id'] for entry in expected] and group['requirement_refs']==expected
        # Generating from the issued packet cannot consult a newer mutable run.
        import al_planning_template as T
        import al_store
        with monkeypatch.context() as context:
            context.setattr(al_store,'load',lambda *args,**kwargs:refuse('template consulted mutable state'))
            assert T.template(packet['packet'],packet['content'])['authored_obligations']['criteria_registry']==expected
        if old_report:
            before=state_file().read_bytes();code,out,err=cli('planning','report','--file',str(old_report))
            assert code!=0,(out,err)
            assert state_file().read_bytes()==before
        old_report=tmp_path/('old-report-'+str(number)+'.json');old_report.write_text(json.dumps(_filled(packet)))
    report=_filled(packet);artifacts=Path(packet['packet']['stored_path']).parent
    for defect in ('omission','wrong-pointer','wrong-hash','invented'):
        invalid=copy.deepcopy(report);behavior=invalid['authored_obligations'];group=behavior['obligations'][0]
        if defect=='omission':
            behavior['criteria_registry'].pop();group['criterion_ids'].pop();group['requirement_refs'].pop()
        elif defect=='wrong-pointer':behavior['criteria_registry'][-1]['pointer']='/criteria/0'
        elif defect=='wrong-hash':behavior['criteria_registry'][-1]['value_sha256']='0'*64
        else:
            entry={'id':amendment['text'],'artifact':ref,'pointer':'/text','value_sha256':J.digest(amendment['text'])}
            behavior['criteria_registry'].append(entry);group['criterion_ids'].append(entry['id']);group['requirement_refs'].append(entry)
        path=tmp_path/'invalid-report.json';path.write_text(json.dumps(invalid));before=state_file().read_bytes()
        retained={p.name:p.read_bytes() for p in artifacts.iterdir() if p.is_file()}
        code,out,err=cli('planning','report','--file',str(path));assert code!=0,(defect,out,err)
        assert state_file().read_bytes()==before
        assert {p.name:p.read_bytes() for p in artifacts.iterdir() if p.is_file()}==retained
    path=tmp_path/'current-report.json';path.write_text(json.dumps(report))
    materialized=json.loads(ok(cli,'planning','report','--file',str(path))[1])['materialized_obligations']
    ok(cli,'planning','admit','--file',materialized['stored_path'])
    final=json.loads(state_file().read_bytes());assert final['proof_admission']['status']=='admitted'
    authority=J.read(final['proof_admission']['authority'])
    assert authority['criteria']==expected and final['proof_admission']['authority']!=original_authority
    assert authority['substantive_amendments']==packet['content']['accepted_amendments']
    assert J.read(authority['behavior_obligations'])['criteria_registry']==expected


def test_real_asserting_test_scope_requires_new_execution_before_observation(
        cli,repo,tmp_path,state_file,run_state):
    import copy
    import sys
    from pathlib import Path
    from hook_regression_helpers import invoke
    run_state('planned')
    cli=getattr(cli,'raw',cli)
    assertion_path='svc/tests/test_scope.py'
    implementation='svc/app.py'
    # Use a current accepted clarification and authored behavior matching the actual check.
    amendment=tmp_path/'scope-amendment.json'
    amendment.write_text(json.dumps({'id':'AM-scope','source':'explicit fixture acceptance',
        'text':'The owned read-only answer function must return 42; verification must preserve its implementation bytes.'}))
    invoke(cli,'task','amend','--file',str(amendment));invoke(cli,'spec-gate','SPEC_READY')
    plan_na_gates(cli,tmp_path,(implementation,assertion_path))
    packet=json.loads(invoke(cli,'planning','packet')[1]);report=_filled(packet)
    group=report['authored_obligations']['obligations'][0]
    def section(items,reason):return {'applicability':'applicable' if items else 'not_applicable','reason':reason,'items':items}
    group['observable_outcome']='Actual execution returns 42 while preserving exact implementation bytes.'
    group['applicability']={'status':'required','reason':'The accepted clarification requires this read-only computation.'}
    group['initial_state']=section([{'id':'ready','description':'The exact owned implementation bytes are installed.'},
        {'id':'evaluated','description':'The answer function has returned.'}],'The check reads the implementation then calls it.')
    group['transitions']=section([{'id':'evaluate','from_state':'ready','action':'Execute the answer function through runpy.','to_state':'evaluated'}],'Real Python execution.')
    group['required_real_components']=section([{'id':'implementation','description':'Owned answer function.','boundary':'The actual repository Python file executes in the check process.'}],'No simulated implementation.')
    group['owners']=section([{'id':'owner','subject':'Read-only answer evaluation','responsible_component_id':'implementation'}],'The owned implementation supplies the value.')
    group['preserve']=section([{'id':'unchanged','subject':'Implementation bytes','expected_observation':'Bytes after the call equal the captured bytes before it.'}],'Read-only behavior is required.')
    group['journey_end']=section([{'id':'value','subject':'Return value','expected_observation':'answer() returns 42.'}],'The check asserts the value.')
    for field in ('reset','persistence_observations','operational_failures','permitted_simulated_boundaries'):
        group[field]=section([],'This input-free read-only function has no reset, durable store, external dependency or task-owned failure route.')
    group['consumer_inventory']={'applicability':'not_applicable','reason':'Disposable implementation and its sole explicit asserting test are introduced together; no pre-existing API consumer.','entries':[],'discovery_refs':[],'uncertainties':[]}
    group['scenarios']=[{'id':'evaluate-readonly','initial_state_ids':['ready'],'transition_ids':['evaluate'],
        'outcome_ids':['value','unchanged'],'failure_case_ids':[],'real_component_ids':['implementation'],
        'permitted_simulated_component_ids':[]}]
    path=tmp_path/'scope-planning.json';path.write_text(json.dumps(report))
    retained=json.loads(invoke(cli,'planning','report','--file',str(path))[1])
    invoke(cli,'planning','admit','--file',retained['materialized_obligations']['stored_path'])
    invoke(cli,'phase','IMPLEMENTATION')
    invoke(cli,'writer','start','--id','scope-example','--role','al-engineer','--files',implementation+','+assertion_path)
    (repo/implementation).write_text('def answer():\n    return 42\n')
    test=repo/assertion_path;test.parent.mkdir(parents=True,exist_ok=True)
    test.write_text('from pathlib import Path\nimport runpy\npath = Path("svc/app.py")\nbefore = path.read_bytes()\nassert before == b"def answer():\\n    return 42\\n"\nvalue = runpy.run_path(str(path))["answer"]()\nassert value == 42\nassert path.read_bytes() == before\nprint("3 passed")\n')
    invoke(cli,'writer','finish','--id','scope-example','--outcome','success')
    invoke(cli,'gates','run','--stage','candidate');invoke(cli,'phase','VERIFICATION')
    argv=(sys.executable,'-B',assertion_path)
    invoke(cli,'evidence','plan','--area','scope-example','--tier','focused','--kind','test','--scope',implementation,'--',*argv)
    invoke(cli,'evidence','run','--area','scope-example')
    state=json.loads(state_file().read_bytes());old=copy.deepcopy(state['evidence']['entries'][-1]);old_id=old['id']
    assert old['status']=='PASS' and old['counts'].get('passed')==3
    assert old['scope']==[implementation] and old['argv']==list(argv)
    log=Path(old['log']).read_bytes();assert b'3 passed' in log
    packet=json.loads(invoke(cli,'behavior','packet')[1]);observation=copy.deepcopy(packet['observation_templates'][0])
    observation['evidence_ids']=[old_id]
    observations={'ready':'The real test asserted the exact installed implementation bytes.',
                  'value':'The real test asserted answer() returns 42.',
                  'unchanged':'The real test asserted implementation bytes were unchanged after the call.'}
    for row in observation['starting_state']:row['observation']=observations[row['state_id']]
    for row in observation['observed_outcomes']:row['observation']=observations[row['outcome_id']]
    lines={'ready':5,'evaluate':7,'value':7,'unchanged':8,'implementation':7}
    for row in observation['assertion_locations']:
        row.update(path=assertion_path,line=lines[row['scenario_item_id']])
    path=tmp_path/'scope-observation.json'
    artifacts=Path(packet['obligations']['artifact']['stored_path']).parent
    def refused(value):
        path.write_text(json.dumps(value));before=state_file().read_bytes()
        saved={p.name:p.read_bytes() for p in artifacts.iterdir() if p.is_file()}
        code,out,err=invoke(cli,'behavior','observe','--file',str(path),ok=False)
        assert state_file().read_bytes()==before
        assert {p.name:p.read_bytes() for p in artifacts.iterdir() if p.is_file()}==saved
        return str(code)+out+err
    message=refused(observation)
    assert assertion_path in message and old_id in message
    assert 'evidence plan' in message and 'evidence run' in message and '--fresh' in message
    assert 'evidence_scope_guidance' in packet and 'asserting test files' in packet['evidence_scope_guidance']
    assert 'evidence_scope_guidance' not in observation
    forged=copy.deepcopy(observation);forged['scope']=[implementation,assertion_path]
    refused(forged)
    # Replanning is metadata only; it cannot retrospectively credit the old execution.
    invoke(cli,'evidence','plan','--area','scope-example','--tier','focused','--kind','test','--scope',implementation+','+assertion_path,'--',*argv)
    refused(observation)
    assert next(e for e in json.loads(state_file().read_bytes())['evidence']['entries'] if e['id']==old_id)==old
    invoke(cli,'evidence','run','--area','scope-example','--fresh')
    state=json.loads(state_file().read_bytes());new=state['evidence']['entries'][-1]
    assert new['id']!=old_id and new['status']=='PASS' and new['counts'].get('passed')==3
    assert new['scope']==[implementation,assertion_path] and new['argv']==old['argv']
    observation['evidence_ids']=[new['id']]
    path.write_text(json.dumps(observation));published=json.loads(invoke(cli,'behavior','observe','--file',str(path))[1])
    final=json.loads(state_file().read_bytes());stored=json.loads(Path(published['artifact']['stored_path']).read_bytes())
    assert stored['evidence_ids']==[new['id']] and published['artifact'] in final['behavior_observations']
    assert {row['path'] for row in stored['assertion_locations']}=={assertion_path}
    from al_behavior_observations import behavior_gaps
    assert not behavior_gaps(final)
    assert all(row['evidence_ids']==[new['id']] for field in ('real_components','exercised_transitions','observed_outcomes') for row in stored[field])
    assert next(e for e in final['evidence']['entries'] if e['id']==old_id)==old and Path(old['log']).read_bytes()==log
    before=state_file().read_bytes();code,out,err=cli('evidence','plan','--help')
    assert code==0 and 'asserting test files' in ' '.join(out.split())
    assert 'Command arguments do not expand' in ' '.join(out.split()) and state_file().read_bytes()==before
