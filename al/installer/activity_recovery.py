"""Data-only recognition of bounded recovery metadata; grants no AL authority."""

def validate(state):
    from .activity_profiles import SHA, _time

    def closed(value, fields):
        if not isinstance(value, dict) or set(value) != set(fields.split()):
            raise ValueError('unknown recovery activity profile')
        return value

    value = closed(state['rework_recovery'], 'schema_version kind origin grants occurrences '
                   'decisions continuations pending stop automatic_authorizations')
    expected = {'session_id':state['session_id'], 'run_id':state['run_id'],
                'identity':state['identity'],
                'original_task_hash':state['proof_admission']['original_task_hash'],
                'runtime_pin':state['runtime_origin']['pin']}
    if type(value['schema_version']) is not int or value['schema_version'] != 1 or value['kind'] != 'rework-recovery-v1' or value['origin'] != expected:
        raise ValueError('foreign recovery activity origin')
    for key in ('grants','occurrences','decisions','continuations'):
        if not isinstance(value[key],list):raise ValueError('invalid recovery activity list')
    if type(value['automatic_authorizations']) is not int or not 0 <= value['automatic_authorizations'] <= 2:
        raise ValueError('invalid recovery activity authorization count')
    grants=value['grants']
    if not grants:raise ValueError('missing initial recovery grant')
    for index, item in enumerate(grants, 1):
        closed(item,'id kind limit authorization at')
        if item['id'] != 'G'+str(index) or item['kind'] not in {'initial','automatic','user'} or type(item['limit']) is not int or item['limit'] != 2 or not _time(item['at']):
            raise ValueError('invalid recovery activity grant')
    if grants[0]['kind'] != 'initial' or grants[0]['authorization'] is not None or any(g['kind']=='initial' for g in grants[1:]):
        raise ValueError('invalid initial recovery activity grant')
    for key in ('decisions','continuations'):
        for row in value[key]:
            closed(row,'request_id request_hash report receipt')
            if not isinstance(row['request_id'],str) or not row['request_id'] or not SHA.fullmatch(str(row['request_hash'])):
                raise ValueError('invalid recovery authorization activity')
    occurrences={}
    for index, item in enumerate(value['occurrences'],1):
        closed(item,'id request checkpoint phase_occurrence binding packets dispositions')
        if item['id'] != 'RR'+str(index) or type(item['phase_occurrence']) is not int or not 0 <= item['phase_occurrence'] < len(state['phase_occurrences']):
            raise ValueError('invalid recovery occurrence activity')
        occurrences[item['id']]=item
        binding=closed(item['binding'],'origin candidate_hash task_hash history_hash occurrence_id')
        if binding['origin'] != expected or binding['occurrence_id'] != item['id'] or any(not SHA.fullmatch(str(binding[k])) for k in ('candidate_hash','task_hash','history_hash')):
            raise ValueError('foreign recovery occurrence activity binding')
        request=closed(item['request'],'arguments source findings files label repro reasons route')
        arguments=closed(request['arguments'],'source findings files label repro cause_demonstrated local '
               'contract_change scope_ambiguous safety_decision spec_conflict')
        if (request['source'] not in {'finalization','verification','core-flaw'}
                or request['route'] not in {'architect','fast'}
                or type(request['findings']) is not int or request['findings']<1
                or not isinstance(request['label'],str)
                or request['repro'] is not None and not isinstance(request['repro'],str)
                or any(not isinstance(request[k],list) or any(not isinstance(v,str) for v in request[k])
                       for k in ('files','reasons'))
                or any(arguments[k] not in {'yes','no'} for k in ('cause_demonstrated','local',
                           'contract_change','scope_ambiguous','safety_decision','spec_conflict'))):
            raise ValueError('unknown pending recovery request activity')
        if not isinstance(item['packets'],list) or not isinstance(item['dispositions'],list) or len(item['dispositions']) > 1:
            raise ValueError('invalid recovery activity history')
        for packet in item['packets']:closed(packet,'attempt packet')
        for disposition in item['dispositions']:
            if disposition.get('kind') == 'allowed':closed(disposition,'kind request_id receipt')
            else:
                closed(disposition,'occurrence_id checkpoint_id kind reason checkpoint')
                if disposition['kind'] not in {'cap','declined','model'} or disposition['occurrence_id'] != item['id']:
                    raise ValueError('invalid recovery stopping activity')
    if value['pending'] is not None:
        if value['pending'] not in occurrences or state['phase'] != 'REWORK_REASSESSMENT':
            raise ValueError('unknown pending recovery activity')
    if value['stop'] is not None:
        stop=closed(value['stop'],'occurrence_id checkpoint_id kind reason')
        if stop['occurrence_id'] not in occurrences or state['status'] != 'STOPPED_WITH_BLOCKERS' or stop['kind'] not in {'cap','declined','model'}:
            raise ValueError('unknown recovery stop activity')
    if sum(g['kind']=='automatic' for g in grants) != value['automatic_authorizations']:
        raise ValueError('inconsistent recovery activity grant count')
