"""A ready-to-fill Architect planning report for an issued packet.

Every identity, binding and routing field is filled from the packet; the Architect replaces
each `FILL:` text and edits the example states, transitions, outcomes and scenarios.
`planning report` refuses a report that still contains a placeholder."""
import copy
import json

import al_proof_json as J
import al_requirement_bindings as Q

PLACEHOLDER = 'FILL:'


def _section(items, reason):
    return {'applicability': 'applicable' if items else 'not_applicable', 'reason': reason, 'items': items}


def _fill(what):
    return PLACEHOLDER + ' ' + what


def template(packet_ref, value, *, partially_built=False, lineage=None):
    """The report template for an issued packet.

    `lineage` is the {revision, supersedes} a recorded `planning revise` requires of obligations
    authored for this packet; without one the obligations are the first revision."""
    registry = Q.accepted_criteria(value['task_package'], value['accepted_amendments'])
    criteria = [entry['id'] for entry in registry]
    group = {
        'id': 'primary-behavior',
        'criterion_ids': criteria,
        'requirement_refs': registry,
        'observable_outcome': _fill('the externally observable result; every preserve, reset, journey_end '
                                    'and persistence_observations item ID must occur in at least one scenario outcome_ids'),
        'applicability': {'status': 'required', 'reason': _fill('why this behavior is required by the specification')},
        'initial_state': _section([{'id': 'before', 'description': _fill('state before the change')},
                                   {'id': 'after', 'description': _fill('state after the change')}],
                                  _fill('which states matter')),
        'transitions': _section([{'id': 'change', 'from_state': 'before', 'action': _fill('the triggering action'),
                                  'to_state': 'after'}], _fill('which transitions matter')),
        'owners': _section([{'id': 'owner', 'subject': _fill('what is owned'),
                             'responsible_component_id': 'component'}], _fill('who owns the behavior')),
        # One example item per section shows the required fields; delete an item (and its ids in the
        # scenarios) when a section does not apply, and give the section a not-applicable reason.
        'preserve': _section([{'id': 'unchanged', 'subject': _fill('what must stay unchanged'),
                               'expected_observation': _fill('how a test sees it unchanged')}],
                             _fill('what must stay unchanged')),
        'reset': _section([], _fill('what state is reset, or why nothing applies')),
        'journey_end': _section([{'id': 'after', 'subject': _fill('end state subject'),
                                  'expected_observation': _fill('what a test observes at the end')}],
                                _fill('terminal state of the journey')),
        'persistence_observations': _section([{'id': 'persisted', 'subject': _fill('what persists'),
                                               'expected_observation': _fill('how a test reads it back')}],
                                             _fill('what persists')),
        'operational_failures': _section([{'id': 'failure', 'trigger': _fill('the supported failure'),
                                           'expected_observation': _fill('what a test observes on failure')}],
                                         _fill('supported failures')),
        'required_real_components': _section([{'id': 'component', 'description': _fill('real component exercised'),
                                               'boundary': _fill('what runs for real')}], _fill('real components')),
        'permitted_simulated_boundaries': _section([{'id': 'simulated', 'description': _fill('simulated dependency'),
                                                     'boundary': _fill('exactly what is simulated')}],
                                                   _fill('simulated boundaries')),
        # When changed exports have consumers: applicability 'applicable' and entries shaped
        # {id, role, kind, contract, locations, source_refs}. Any uncertainties entry blocks
        # admission: list only unresolved consumer discovery there, and put risks (merge-time,
        # parallel-branch, residual) in reason. A read-only role leaves discovery_refs empty and
        # says so in reason; the supervisor supplies them with planning report --discovery-ref.
        'consumer_inventory': {'applicability': 'not_applicable', 'reason': _fill('consumers of changed exports, or why none; '
            'entries have id, role (producer|consumer), kind (code|link|message|export|other), contract, '
            'locations (string array), source_refs (typed artifact references). Create discovery_refs/source_refs '
            'with artifact record --file PATH --kind consumer-discovery, or leave discovery_refs empty and say '
            'here that you could not record them (the supervisor adds them). Put risks here, not in uncertainties: '
            'any uncertainties entry blocks admission; see reference/artifacts.md'),
                               'entries': [], 'discovery_refs': [], 'uncertainties': []},
        # Every journey_end, preserve and persistence item appears in some scenario's outcome_ids.
        'scenarios': [{'id': 'happy-path', 'initial_state_ids': ['before'], 'transition_ids': ['change'],
                       'outcome_ids': ['after', 'persisted'], 'failure_case_ids': [], 'real_component_ids': ['component'],
                       'permitted_simulated_component_ids': ['simulated']},
                      {'id': 'failure-path', 'initial_state_ids': ['before'], 'transition_ids': [],
                       'outcome_ids': ['unchanged'], 'failure_case_ids': ['failure'], 'real_component_ids': ['component'],
                       'permitted_simulated_component_ids': ['simulated']}],
    }
    binding = value['binding']
    lineage = lineage or {'revision': 1, 'supersedes': None}
    behavior = {
        'schema_version': 1, 'kind': 'behavior-obligations-v1', 'id': 'task-behavior',
        'revision': lineage['revision'], 'supersedes': copy.deepcopy(lineage['supersedes']),
        'binding': {key: binding[key] for key in ('repository_identity', 'session_id', 'run_id', 'requirement_lineage_hash')},
        'authorship': {'role': 'al-architect', 'planning_occurrence': binding['planning_occurrence'], 'report_ref': None},
        'criteria_registry': registry,
        'implementation_context': {'status': 'partially_built' if partially_built else 'not_started',
                                   'observation_ref': value['source_observation'] if partially_built else None},
        'obligations': [group],
    }
    behavior['binding']['document_hash_at_authoring'] = binding['document_hash']
    attempt = value['attempt']
    return {
        'schema_version': 1, 'kind': 'architect-planning-report-v1',
        'attempt_id': attempt['attempt_id'], 'invocation_id': _fill('your Architect invocation id if you can see it; otherwise leave this, the supervisor stamps it'),
        'model': attempt['model'], 'effort': attempt['effort'], 'policy_digest': attempt['policy_digest'],
        'packet_sha256': packet_ref['sha256'], 'binding': copy.deepcopy(binding),
        'specification': {'status': 'SPEC_READY', 'reason': _fill('why the specification is ready')},
        'routing': value['routing'], 'authored_obligations': behavior,
    }


def observation_templates(st, packet):
    """One ready-to-fill behavior-observation-v1 per scenario still missing an observation."""
    artifact = packet['obligations']
    seen = {(o['content']['obligation_id'], o['content']['scenario_id']) for o in packet.get('observations', [])}
    # An item's evidence_ids defaults to the observation's evidence_ids in `behavior observe`;
    # add it to an item only when that item was exercised by a narrower set of runs.
    def rows(scenario, key, identifier, text=False):
        return [{identifier: name, **({'observation': _fill('what the run showed for ' + name)} if text else {}),
                 'artifact_refs': []} for name in scenario[key]]
    out = []
    for group in artifact['content']['obligations']:
        for scenario in group['scenarios']:
            if (group['id'], scenario['id']) in seen:
                continue
            required = sorted(set().union(*(set(scenario[k]) for k in (
                'initial_state_ids', 'transition_ids', 'outcome_ids', 'failure_case_ids', 'real_component_ids'))))
            out.append({
                'schema_version': 1, 'kind': 'behavior-observation-v1', 'obligation_id': group['id'],
                'obligation_revision': {'artifact': artifact['artifact'], 'revision': artifact['content']['revision']},
                'scenario_id': scenario['id'], 'evidence_ids': [_fill('ledger id(s) of the passing runs')],
                'binding': {'repository_identity': st['identity'], 'session_id': st['session_id'], 'run_id': st['run_id'],
                            'verification_authority_hash': packet['verification_authority']['hash'],
                            'document_hash': packet['document_hash'], 'candidate_fingerprint': packet['candidate_fingerprint'],
                            'input_bindings': [packet['verification_authority']['artifact'], artifact['artifact']]},
                'starting_state': rows(scenario, 'initial_state_ids', 'state_id', text=True),
                'real_components': rows(scenario, 'real_component_ids', 'component_id'),
                'simulated_dependencies': [{'component_id': name, 'mechanism': _fill('how it was simulated'),
                                            'limitations': _fill('what the simulation does not prove')}
                                           for name in scenario['permitted_simulated_component_ids']],
                'exercised_transitions': rows(scenario, 'transition_ids', 'transition_id'),
                'observed_outcomes': rows(scenario, 'outcome_ids', 'outcome_id', text=True),
                'operational_failures': rows(scenario, 'failure_case_ids', 'failure_case_id', text=True),
                # source_sha256 is computed by `behavior observe` from the path when omitted.
                'assertion_locations': [{'scenario_item_id': name, 'path': _fill('test file asserting this item'), 'line': 1}
                                        for name in required],
                'artifact_refs': [], 'limitations': [],
            })
    return out


# Fields the template fills from the issued packet/attempt and the Architect must not change.
# `planning report` restores any of them the Architect omits and refuses any changed value.
# `model` also accepts the attempt slug and `specification.status` keeps its own blocked-spec
# refusal, so both are restored when omitted but compared by the report validator instead.
RESTORED = {
    'schema_version': None, 'kind': None, 'attempt_id': None, 'model': None, 'effort': None,
    'policy_digest': None, 'packet_sha256': None, 'binding': None, 'routing': None,
    'specification': {'status': None},
    'authored_obligations': {'schema_version': None, 'kind': None, 'binding': None,
                             'authorship': None, 'criteria_registry': None},
}
# After `planning revise` the obligation revision lineage is runtime-fixed as well.
REVISED = copy.deepcopy(RESTORED)
REVISED['authored_obligations'].update(revision=None, supersedes=None)


def restored(lineage):
    """The runtime-fixed fields for a report whose packet carries `lineage` (or none)."""
    return REVISED if lineage else RESTORED


COMPARED_ELSEWHERE = {'model', 'specification.status', 'authored_obligations.criteria_registry'}


def restore_fixed(report, template, fields=RESTORED, path=''):
    """Fill omitted runtime-fixed fields from the template; refuse a changed one by JSON path."""
    if not isinstance(report, dict):
        return report
    for key, nested in fields.items():
        where = J.at(path, key)
        if key not in report:
            # A wholly omitted section with author-filled fields stays missing and is named.
            if nested is None:
                report[key] = copy.deepcopy(template[key])
            continue
        if nested is not None:
            restore_fixed(report[key], template[key], nested, where)
        elif isinstance(report[key], dict) and isinstance(template[key], dict):
            _restore_missing(report[key], template[key])
            if where not in COMPARED_ELSEWHERE and J.raw(report[key]) != J.raw(template[key]):
                refuse_fixed(where)
        elif where not in COMPARED_ELSEWHERE and J.raw(report[key]) != J.raw(template[key]):
            refuse_fixed(where)
    return report


def _restore_missing(value, template):
    for key, expected in template.items():
        if key not in value:
            value[key] = copy.deepcopy(expected)
        elif isinstance(value[key], dict) and isinstance(expected, dict):
            _restore_missing(value[key], expected)


def refuse_fixed(where):
    from al_schema import refuse
    refuse('Architect planning report changed runtime-fixed template field ' + where +
           '; keep the value from report_template or omit the field')


def placeholders(report):
    return PLACEHOLDER in json.dumps(report)
