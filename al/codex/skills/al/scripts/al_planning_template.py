"""A ready-to-fill Architect planning report for an issued packet.

Every identity, binding and routing field is filled from the packet; the Architect replaces
each `FILL:` text and edits the example states, transitions, outcomes and scenarios.
`planning report` refuses a report that still contains a placeholder."""
import copy
import json

import al_proof_json as J

PLACEHOLDER = 'FILL:'


def _section(items, reason):
    return {'applicability': 'applicable' if items else 'not_applicable', 'reason': reason, 'items': items}


def _fill(what):
    return PLACEHOLDER + ' ' + what


def template(packet_ref, value):
    reference = value['task_package']
    package = J.read(reference)
    criteria = list(package.get('criteria', []))
    registry = [{'id': name, 'artifact': reference, 'pointer': '/criteria/' + str(index),
                 'value_sha256': J.digest(name)} for index, name in enumerate(criteria)]
    group = {
        'id': 'primary-behavior',
        'criterion_ids': criteria,
        'requirement_refs': registry,
        'observable_outcome': _fill('the externally observable result this task delivers'),
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
        # {id, role, kind, contract, locations, source_refs}.
        'consumer_inventory': {'applicability': 'not_applicable', 'reason': _fill('consumers of changed exports, or why none'),
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
    behavior = {
        'schema_version': 1, 'kind': 'behavior-obligations-v1', 'id': 'task-behavior', 'revision': 1, 'supersedes': None,
        'binding': {key: binding[key] for key in ('repository_identity', 'session_id', 'run_id', 'requirement_lineage_hash')},
        'authorship': {'role': 'al-architect', 'planning_occurrence': binding['planning_occurrence'], 'report_ref': None},
        'criteria_registry': registry,
        'implementation_context': {'status': 'not_started', 'observation_ref': None},
        'obligations': [group],
    }
    behavior['binding']['document_hash_at_authoring'] = binding['document_hash']
    attempt = value['attempt']
    return {
        'schema_version': 1, 'kind': 'architect-planning-report-v1',
        'attempt_id': attempt['attempt_id'], 'invocation_id': _fill('your actual Architect invocation id'),
        'model': attempt['model'], 'effort': attempt['effort'], 'policy_digest': attempt['policy_digest'],
        'packet_sha256': packet_ref['sha256'], 'binding': copy.deepcopy(binding),
        'specification': {'status': 'SPEC_READY', 'reason': _fill('why the specification is ready')},
        'routing': value['routing'], 'authored_obligations': behavior,
    }


def placeholders(report):
    return PLACEHOLDER in json.dumps(report)
