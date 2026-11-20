"""Typed companions of existing authoritative AL records, never substitutes for their guards."""

import copy

import al_agent_contracts as C
import al_artifacts as A


def context(st, producer=None, task=None):
    binding = st.get("integration", {}).get("binding", {})
    values = dict(
        al_run=st["run_id"],
        al_session=st["session_id"],
        task=binding.get("task_id"),
        execution=binding.get("execution_id"),
        subtask=task,
        graph_revision=st.get("integration", {}).get("graph_revision"),
        task_revision=st.get("integration", {}).get("tasks", {}).get(task, {}).get("revision"),
        producer=producer,
        epoch=binding.get("epoch_id"),
        authority_generation=binding.get("authority_generation"),
    )
    values["applicability"] = {
        k: ("standalone" if not binding else "not-observed") for k, v in values.items() if v is None
    }
    return values


def specification(st):
    return {
        "reference": C.ref(st["task_package"]),
        "amendments": [C.ref(a["artifact"]) for a in st.get("amendments", [])],
    }


def inputs(st):
    return [
        {
            "reference": C.ref(st["task_package"]),
            "purpose": "accepted specification",
            "required": True,
        }
    ]


def record_context(st, kind, record, producer, task):
    ids = record.get('producer_invocation_ids', []) if isinstance(record, dict) and kind == 'implementation-result' else []
    selected = producer or (ids[0] if len(ids) == 1 else None)
    value = context(st, selected, task)
    if not selected and len(ids) > 1:
        value['applicability']['producer'] = 'multiple-producers'
    return value


def produce(
    st, kind, identifier, record, *, producer=None, task=None, outputs=(), refs=(), status=None
):
    if not st.get("task_package"):
        return None
    package = A.read_json(st["task_package"])
    value = C.make(
        kind,
        record_context(st, kind, record, producer, task),
        specification(st),
        identifier,
        record,
        status=status,
        inputs=[*inputs(st), *refs],
        outputs=outputs,
        criteria=st.get("integration", {}).get("tasks", {}).get(task, {}).get("criteria", package["criteria"]),
        failure=({'classification': 'operational', 'diagnostic_ref': C.ref(record['diagnostic_ref'])}
                 if kind == 'failure-report' else None),
        constraints=[
            {
                "reference": C.ref(st["task_package"]),
                "purpose": "accepted constraints",
                "required": True,
            }
        ],
    )
    st.setdefault("agent_contracts", {})[kind + ":" + str(identifier)] = copy.deepcopy(value)
    return value


def consume(st, kind, identifier, record, *, producer=None, task=None, resolve=True, current=True):
    value = st.get("agent_contracts", {}).get(kind + ":" + str(identifier))
    if value is None:
        return {"coverage": "legacy"}
    return consume_value(st, kind, identifier, record, value, producer=producer, task=task,
                         resolve=resolve, current=current)


def consume_value(st, kind, identifier, record, value, *, producer=None, task=None,
                  resolve=True, current=True):
    if not isinstance(value, dict):
        raise C.ContractError('/', kind=kind)
    if value.get("context", {}).get("al_run") != st['run_id'] or value.get("context", {}).get("al_session") != st['session_id']:
        raise C.ContractError('/context', kind=kind)
    read = reader(st)
    C.validate(
        value,
        context=record_context(st, kind, record, producer, task) if current else None,
        specification=specification(st) if current else None,
        resolve=read if resolve else None,
    )
    if (
        value["kind"] != kind
        or value["payload"]["record_id"] != str(identifier)
        or value["payload"]["record_digest"] != C.fingerprint(record)
    ):
        raise C.ContractError("/payload", kind=kind)
    return value


def reader(st):
    references = [
        st["task_package"],
        *[a["artifact"] for a in st.get("amendments", [])],
        *st.get("recorded_artifacts", []),
    ]
    references += [r["artifact"] for r in st.get("agent_outputs", {}).values()]
    references += [
        h["artifact"] for w in st.get("writers", {}).values() for h in w.get("handoffs", [])
    ]
    references += [ref for entry in st.get('evidence', {}).get('entries', [])
                   for key, ref in (entry.get('reconciliation') or {}).items()
                   if key in {'artifact', 'observation'}]

    def read(ref):
        actual = next((r for r in references if C.ref(r) == ref), None)
        if actual is None:
            raise C.ContractError("/required_inputs")
        return A.read(actual)

    return read


def analysis_record(st):
    package = A.read_json(st['task_package'])
    return package if not st.get('amendments') else {
        'original': package, 'amendments': [A.read_json(a['artifact']) for a in st['amendments']]}


def refresh_analysis(st):
    old = st.get('agent_contracts', {}).get('requirement-analysis:' + st['task_package']['id'])
    if old:
        st.setdefault('agent_contract_history', []).append(copy.deepcopy(old))
    return produce(st, 'requirement-analysis', st['task_package']['id'], analysis_record(st), status='observed')


def require_analysis(st):
    value = consume(st, 'requirement-analysis', st['task_package']['id'], analysis_record(st), current=False)
    if value.get('coverage') == 'legacy':
        return value
    expected = context(st)
    expected['graph_revision'] = value['context']['graph_revision']
    C.validate(value, context=expected, specification=specification(st))
    return value


def consume_verification(st, entry):
    """A reconciliation authenticates both observations; it never rewrites the original."""
    value = consume(st, 'verification-result', entry['id'], entry, resolve=False, current=False)
    reconciliation = entry.get('reconciliation')
    if not reconciliation or 'schema_version' not in reconciliation:
        return value  # explicit existing legacy representation
    try:
        if (set(reconciliation) != {'schema_version', 'reason', 'artifact', 'observation'}
                or type(reconciliation['schema_version']) is not int
                or reconciliation['schema_version'] != 1 or entry['status'] != 'INFRA'
                or not isinstance(reconciliation['reason'], str) or not reconciliation['reason'].strip()):
            raise C.ContractError('/payload', kind='verification-result')
        retained = A.read_json(reconciliation['observation'])
        if set(retained) != {'schema_version', 'entry', 'contract'} or retained['schema_version'] != 1:
            raise C.ContractError('/payload', kind='verification-result')
        original = retained['entry']
        if original.get('reconciliation') or original['id'] != entry['id']:
            raise C.ContractError('/payload', kind='verification-result')
        if retained['contract'] is not None:
            consume_value(st, 'verification-result', original['id'], original,
                          retained['contract'], current=False)
        allowed = {'status', 'note', 'ended_at', 'reconciliation'}
        if ({k: v for k, v in original.items() if k not in allowed}
                != {k: v for k, v in entry.items() if k not in allowed}
                or entry['note'] != 'interrupted check explicitly reconciled'):
            raise C.ContractError('/payload', kind='verification-result')
        A.read(reconciliation['artifact'])
    except (OSError, KeyError, TypeError, ValueError):
        raise C.ContractError('/required_inputs', kind='verification-result') from None
    return value


def consume_issue(st, identifier, record):
    """Authenticate the captured observation, allowing only later independent graph growth."""
    value = consume(st, 'failure-report', identifier, record, current=False)
    if value.get('coverage') == 'legacy':
        return value
    expected = record_context(st, 'failure-report', record, None, None)
    captured = value['context']['graph_revision']
    if (type(captured) is not int or type(expected['graph_revision']) is not int
            or not 0 <= captured <= expected['graph_revision']):
        raise C.ContractError('/context', kind='failure-report')
    expected['graph_revision'] = captured
    C.validate(value, context=expected, resolve=reader(st))
    old, current = value['source_specification'], specification(st)
    if (old['reference'] != current['reference']
            or old['amendments'] != current['amendments'][:len(old['amendments'])]):
        raise C.ContractError('/source_specification', kind='failure-report')
    return value


def consume_plan(st, current, report):
    """The immutable publication authenticates historical graph observation only."""
    reference = current.get('contract_observation')
    value = consume(st, 'implementation-plan', current['report']['id'], report,
                    producer=report['invocation_id'], current=reference is None)
    if reference is None:
        return value
    if value.get('coverage') == 'legacy':
        raise C.ContractError('/required_inputs', kind='implementation-plan')
    if A.read_json(reference) != value:
        raise C.ContractError('/payload', kind='implementation-plan')
    expected = record_context(st, 'implementation-plan', report, report['invocation_id'], None)
    captured = value['context']['graph_revision']
    actual = expected['graph_revision']
    if ((captured is None) != (actual is None)
            or captured is not None and (type(captured) is not int or type(actual) is not int
                                        or not 0 <= captured <= actual)):
        raise C.ContractError('/context', kind='implementation-plan')
    expected['graph_revision'] = captured
    C.validate(value, context=expected, specification=specification(st), resolve=reader(st))
    return value


def record_escalation(st, sid, identifier, transition=None):
    import json
    row = st['integration']['blockers'][identifier]
    refs = []
    if transition:
        ref = transition['artifact']
        if ref not in st.setdefault('recorded_artifacts', []):
            st['recorded_artifacts'].append(ref)
        refs = [{'reference': C.ref(ref), 'purpose': 'authenticated escalation transition', 'required': True}]
    value = produce(st, 'escalation-request', identifier, row,
                    status='resolved' if row['state'] == 'Resolved' else 'requested', refs=refs)
    if value is not None:
        snapshot = {'schema_version': 1, 'record': row, 'contract': value, 'transition': transition}
        ref = A.snapshot_bytes(sid, json.dumps(snapshot, sort_keys=True).encode(),
                               'escalation-observation-v1', 'authorized escalation observation')
        st.setdefault('escalation_observations', {}).setdefault(identifier, []).append(ref)


def _escalation_read(reference):
    try:
        value = A.read_json(reference)
        if not isinstance(value, dict):
            raise ValueError('invalid observation')
        return value
    except (OSError, ValueError, TypeError, KeyError):
        raise C.ContractError('/required_inputs', kind='escalation-request') from None


def consume_escalation(st, identifier, row, *, historical=False):
    from al_execution_contract import digest, validate_delivery
    history = st.get('escalation_observations', {}).get(identifier)
    if history is None:
        return consume(st, 'escalation-request', identifier, row, current=not historical)
    if not isinstance(history, list) or not 1 <= len(history) <= 3:
        raise C.ContractError('/required_inputs', kind='escalation-request')
    previous = None
    latest = None
    for ref in history:
        observed = _escalation_read(ref)
        if (set(observed) != {'schema_version', 'record', 'contract', 'transition'}
                or type(observed['schema_version']) is not int or observed['schema_version'] != 1):
            raise C.ContractError('/payload', kind='escalation-request')
        record, value = observed['record'], observed['contract']
        consume_value(st, 'escalation-request', identifier, record, value, current=False)
        expected = record_context(st, 'escalation-request', record, None, None)
        captured, actual = value['context']['graph_revision'], expected['graph_revision']
        if type(captured) is not int or type(actual) is not int or not 0 <= captured <= actual:
            raise C.ContractError('/context', kind='escalation-request')
        expected['graph_revision'] = captured
        C.validate(value, context=expected, resolve=reader(st))
        old, spec = value['source_specification'], specification(st)
        if old['reference'] != spec['reference'] or old['amendments'] != spec['amendments'][:len(old['amendments'])]:
            raise C.ContractError('/source_specification', kind='escalation-request')
        transition = observed['transition']
        if previous is None:
            registration = {k: record[k] for k in ('id', 'kind', 'tasks', 'question', 'context', 'alternatives', 'recommendation')}
            if (transition is not None or record['registration'] != digest(registration)
                    or record['state'] != 'Open' or record['revision'] != 1 or 'delivery_id' in record):
                raise C.ContractError('/payload', kind='escalation-request')
        else:
            if not isinstance(transition, dict) or set(transition) != {'kind', 'artifact'}:
                raise C.ContractError('/required_inputs', kind='escalation-request')
            expected_record = copy.deepcopy(previous)
            data = _escalation_read(transition['artifact'])
            if transition['kind'] == 'decision':
                validate_delivery(data)
                ack = st['integration']['deliveries'].get(data['id'])
                state = 'RevalidationRequired' if data['amendment'] else 'Resolved'
                if (previous['state'] != 'Open' or data['verdict'] == 'reject'
                        or data['blocker_id'] != identifier or data['blocker_revision'] != previous['revision']
                        or data['binding'] != st['integration']['binding'] or not ack
                        or ack != {'delivery_id': data['id'], 'blocker_id': identifier,
                                   'state': state, 'decision_hash': digest(data)}):
                    raise C.ContractError('/payload', kind='escalation-request')
                expected_record.update(state=state, delivery_id=data['id'])
            elif transition['kind'] == 'revalidation':
                obligation = st['integration']['revalidations'].get(previous.get('delivery_id'))
                if (previous['state'] != 'RevalidationRequired' or not obligation
                        or obligation['state'] != 'Validated' or obligation['artifact'] != transition['artifact']):
                    raise C.ContractError('/payload', kind='escalation-request')
                expected_record['state'] = 'Resolved'
            else:
                raise C.ContractError('/required_inputs', kind='escalation-request')
            if record != expected_record:
                raise C.ContractError('/payload', kind='escalation-request')
        previous, latest = record, value
    if row != previous or st.get('agent_contracts', {}).get('escalation-request:' + identifier) != latest:
        raise C.ContractError('/payload', kind='escalation-request')
    return latest
