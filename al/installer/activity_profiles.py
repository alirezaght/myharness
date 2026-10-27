"""Closed structural profiles for read-only workspace activity classification."""
import hashlib
import json
import math
from pathlib import Path
import re

from .registry import pairs

ID = re.compile(r'[A-Za-z0-9_-]+\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')
STATUS = {'RUNNING', 'APPROVED', 'STOPPED_WITH_BLOCKERS', 'SPEC_BLOCKED', 'AGENT_FAILED',
          'WAITING_FOR_USER', 'INTERRUPTED', 'TRANSFERRED'}
PHASE = {'INTAKE', 'SPEC', 'CLASSIFICATION', 'PLANNING', 'PSEUDOCODE', 'PLAN_CONFLICT', 'REWORK_REASSESSMENT',
         'IMPLEMENTATION', 'FINALIZATION', 'VERIFICATION', 'FINAL_VERIFICATION', 'REVIEW',
         'CORRECTION', 'REWORK', 'DONE', 'WAITING_FOR_USER', 'STOPPED', 'INVESTIGATION'}
LEGACY_KEYS = set('''agent agents approved_with_notes aux_runs baseline_head classification
classification_reason completed_phases correction_iteration designer_reason designer_required
 effort escalations exec_mode failure fanout fanouts findings implementers max_correction_iterations
model model_fallbacks model_restorations model_tier optional_phases oscillations path pending_phases phase phase_timings
preexisting_changes retries review_history review_iteration review_status roles_run session_id
skipped_phases snapshots spec_status started_at status stop_reason task task_title unavailable_models
updated_at workflow write_ownership'''.split())
LEDGER_KEYS = {'evidence', 'handoffs', 'overlaps', 'rework', 'run_id'}
MODERN_KEYS = LEGACY_KEYS | LEDGER_KEYS | set('''schema_version identity task_package amendments
phase_occurrences writers gate_manifests gate_runs gate_decisions review review_mode approval
checkpoints stopping_checkpoint comparison_artifacts gate_recovery gate_recovery_history escapes
recovery_replans resumes retry_history native_conversation_id integration gate_recovery_supersessions task_attribution verification_preparations
runtime_origin proof_admission planning_authorship planning_history behavior_observations editorial_history
conflict_resolution conflict_resolution_history notes_triage publication_authorizations publication_records
baseline_exceptions gate_retentions delivery_completion_history rework_recovery
classification_assessment code_assessments business_acceptances'''.split())


def decode(raw):
    if len(raw) > 64 * 1024 * 1024:raise ValueError('activity JSON byte limit')
    def nonfinite(_):raise ValueError('nonfinite activity JSON')
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
    count = 0
    def bounded(item, depth=0):
        nonlocal count
        count += 1
        if count > 1000000 or depth > 64:raise ValueError('activity JSON structure limit')
        if isinstance(item, dict):
            for child in item.values():bounded(child, depth + 1)
        elif isinstance(item, list):
            for child in item:bounded(child, depth + 1)
    bounded(value)
    return value


def _time(value):
    return type(value) in {int, float} and math.isfinite(value) and value >= 0


def _references(value, members, runtime, external=()):
    if isinstance(value, dict):
        if any(value is item for item in external):
            return
        if 'stored_path' in value:
            if not SHA.fullmatch(value.get('sha256', '')):raise ValueError('invalid artifact reference digest')
            path = Path(value['stored_path'])
            if not path.is_absolute():raise ValueError('relative immutable activity artifact')
            try:relative = path.relative_to(runtime).as_posix()
            except ValueError:raise ValueError('activity artifact outside bound runtime')
            row = members.get(relative)
            if not row or row['type'] != 'file' or row['sha256'] != value['sha256']:
                raise ValueError('missing or corrupt immutable activity artifact')
        for child in value.values():_references(child, members, runtime, external)
    elif isinstance(value, list):
        for child in value:_references(child, members, runtime, external)


def _workflow(value, path, allowed):
    if set(value) - allowed:raise ValueError('unknown activity-bearing profile fields: ' + ','.join(sorted(set(value) - allowed)))
    if value.get('workflow') != 'AL' or not ID.fullmatch(value.get('session_id', '')) or value['session_id'] != path.stem:
        raise ValueError('invalid workflow/session identity')
    if value.get('status') not in STATUS or value.get('phase') not in PHASE:
        raise ValueError('unsupported historical lifecycle')
    if not isinstance(value.get('task'), str) or not value['task'].strip():raise ValueError('missing historical task')
    for key in ('started_at', 'updated_at'):
        if not _time(value.get(key)):raise ValueError('invalid historical timestamp: ' + key)
    for key in ('review_iteration', 'correction_iteration', 'max_correction_iterations'):
        if type(value.get(key)) is not int or value[key] < 0:raise ValueError('invalid historical counter: ' + key)
    for key in ('completed_phases', 'pending_phases'):
        if not isinstance(value.get(key), list):raise ValueError('invalid historical list: ' + key)
    if not isinstance(value.get('findings'), dict):raise ValueError('invalid historical findings')
    if not isinstance(value.get('phase_timings'), dict):raise ValueError('invalid historical phase timings')
    if value.get('failure') is not None and not isinstance(value['failure'], dict):raise ValueError('invalid historical failure')
    if value.get('fanout') is not None:raise ValueError('unresolved/unsupported historical fanout')
    for key in ('agents', 'escalations', 'fanouts', 'model_fallbacks', 'optional_phases', 'overlaps', 'review_history',
                'roles_run', 'skipped_phases', 'unavailable_models', 'aux_runs'):
        if key in value and value[key] is not None and not isinstance(value[key], list):
            raise ValueError('invalid historical metadata list: ' + key)
    for key in ('exec_mode', 'write_ownership', 'snapshots', 'model_tier', 'rework'):
        if key in value and value[key] is not None and not isinstance(value[key], dict):
            raise ValueError('invalid historical metadata mapping: ' + key)
    for row in value.get('aux_runs') or []:
        if (not isinstance(row, dict) or set(row) != {'role', 'kind', 'count', 'at'}
                or not isinstance(row['role'], str) or not isinstance(row['kind'], str)
                or type(row['count']) is not int or row['count'] < 1 or not _time(row['at'])):
            raise ValueError('unsupported historical auxiliary ownership metadata')
    for row in value.get('fanouts') or []:
        if (not isinstance(row, dict) or set(row) - {'at', 'count', 'kind', 'labels', 'phase', 'role', 'tree_changed'}
                or not {'at', 'count', 'kind', 'labels', 'phase', 'role'} <= set(row)
                or not _time(row['at']) or type(row['count']) is not int or row['count'] < 1
                or not isinstance(row['labels'], list)):
            raise ValueError('unsupported historical fanout metadata')

    for row in value.get('model_restorations') or []:
        if (not isinstance(row, dict) or set(row) != {'model', 'reason', 'at'}
                or not isinstance(row['model'], str) or not row['model'].strip()
                or not isinstance(row['reason'], str) or not row['reason'].strip() or not _time(row['at'])):
            raise ValueError('unsupported historical model restoration metadata')
    if 'model_restorations' in value and not isinstance(value['model_restorations'], list):
        raise ValueError('invalid historical model restoration list')

    if 'retries' in value and (not isinstance(value['retries'], dict) or any(type(n) is not int or n < 0 for n in value['retries'].values())):
        raise ValueError('invalid historical retry counters')
    for key, item in value.get('phase_timings', {}).items():
        if not isinstance(item, dict):raise ValueError('invalid historical phase timing')
        for name in ('started', 'ended', 'duration'):
            if item.get(name) is not None and not _time(item[name]):raise ValueError('invalid historical phase timing value')


ENTRY_KEYS = set('''id area kind tier argv cwd scope sources env preflight needs status exit note counts
started_at ended_at pid owner_pid log log_sha256 fp_before fp_after phase correction_iteration run
execution_key gate_manifest gate_stage interrupted invocation reconciliation reservation rejected
fingerprint_error process owner_identity cleanup aggregate_report complete_verification gate_reservation
execution_projection_digest execution_completion execution_receipt execution_association review_state audit_receipt
execution_stage setup'''.split())
WRITER_KEYS = {'id', 'role', 'files', 'commands', 'phase', 'started_at', 'ended_at', 'state',
               'outcome', 'reconciliation', 'task', 'agent', 'contribution_before'}


def _pin_initialization(reference, admitted):
    """The one state reference outside the runtime: the launcher-protected initialization record."""
    if (not isinstance(reference, dict) or set(reference) - {'id', 'kind', 'sha256', 'stored_path', 'source', 'captured_at'}
            or reference != admitted or not SHA.fullmatch(str(reference.get('sha256')))):
        raise ValueError('invalid pinned run initialization reference')
    path = Path(reference.get('stored_path', ''))
    if (not path.is_absolute() or path.name != reference['sha256'] or path.parent.name != 'initializations'
            or path.parent.parent.name != 'al-run-pins' or path.is_symlink() or not path.is_file()):
        raise ValueError('pinned run initialization outside protected pin storage')
    if hashlib.sha256(path.read_bytes()).hexdigest() != reference['sha256']:
        raise ValueError('pinned run initialization bytes changed')
    return [reference, admitted]


def _ledger(value, *, modern):
    evidence = value.get('evidence')
    if not isinstance(evidence, dict) or set(evidence) - {'seq', 'entries', 'plan', 'invalidated_at', 'invalidation_reason', 'retired', 'supersessions'}:
        raise ValueError('unknown evidence ledger profile')
    entries = evidence.get('entries')
    if not isinstance(entries, list) or not isinstance(evidence.get('plan'), dict):raise ValueError('invalid evidence ledger')
    if type(evidence.get('seq')) is not int or evidence['seq'] < len(entries):raise ValueError('invalid evidence sequence')
    seen = set()
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) - ENTRY_KEYS or not re.fullmatch(r'E[1-9][0-9]*', entry.get('id', ''))
                or entry['id'] in seen or entry.get('status') not in {'RUNNING', 'PASS', 'FAIL', 'EMPTY', 'INFRA', 'TORN'}
                or not isinstance(entry.get('argv'), list)):
            raise ValueError('malformed historical evidence entry')
        seen.add(entry['id'])
        if modern and (entry.get('run') != value['run_id'] or not entry.get('reservation')):
            raise ValueError('foreign modern evidence reservation')
    return entries


def _modern(value, path, provenance):
    _workflow(value, path, MODERN_KEYS)
    if value.get('schema_version') != 2 or not ID.fullmatch(value.get('run_id', '')):
        raise ValueError('invalid modern immutable run identity')
    identity = value.get('identity')
    if not isinstance(identity, dict):raise ValueError('missing modern repository identity')
    for key in ('root', 'git_dir', 'common_dir'):
        if not isinstance(identity.get(key), str) or Path(identity[key]).resolve() != Path(provenance['repository'][key]).resolve():
            raise ValueError('foreign modern repository identity: ' + key)
    for key in ('amendments', 'phase_occurrences', 'review_history'):
        if not isinstance(value.get(key), list):raise ValueError('missing modern ' + key)
    for occurrence in value['phase_occurrences']:
        if (not isinstance(occurrence, dict) or occurrence.get('phase') not in PHASE
                or type(occurrence.get('completed')) is not bool
                or occurrence['completed'] and not _time(occurrence.get('ended'))):
            raise ValueError('invalid actual phase completion proof')
    package = value.get('task_package')
    if not isinstance(package, dict) or not SHA.fullmatch(package.get('sha256', '')):raise ValueError('invalid immutable task package')
    native = value.get('native_conversation_id')
    if native is not None and (not isinstance(native, str) or not native or len(native) > 1024):
        raise ValueError('invalid native conversation attribution')
    writers = value.get('writers')
    if not isinstance(writers, dict):raise ValueError('missing modern writer registry')
    for wid, writer in writers.items():
        if not isinstance(writer, dict) or set(writer) - WRITER_KEYS or writer.get('id') != wid or writer.get('state') not in {'ACTIVE', 'UNRESOLVED', 'FINISHED', 'RECONCILED'}:
            raise ValueError('malformed modern writer')
    # New pinned runs bind their state to an independently protected launcher transaction.
    origin = value.get('runtime_origin')
    if origin is not None:
        if (not isinstance(origin, dict) or origin.get('schema_version') not in {1, 2} or origin.get('kind') != 'run-pin-binding-v' + str(origin.get('schema_version'))
                or origin.get('run_id') != value['run_id'] or not isinstance(origin.get('pin'), dict)
                or not re.fullmatch(r'[0-9a-f]{64}', str(origin.get('publication_transaction')))):
            raise ValueError('invalid pinned runtime origin')
        if origin['schema_version'] == 2:
            expected = Path(provenance['repository']['root']) / ('.' + provenance['platform']) / 'al-runtime' / 'sessions' / value['session_id']
            if (origin.get('runtime_layout') != 'harness-session-directory-v1'
                    or origin.get('runtime_directory') != str(expected) or path.parent != expected):
                raise ValueError('private runtime identity/layout differs')
        admission = value.get('proof_admission')
        if not isinstance(admission, dict) or admission.get('status') not in {'pending', 'admitted'}:
            raise ValueError('pinned run lacks a valid proof admission')
        pinned_initialization = _pin_initialization(origin.get('initialization'), admission.get('initialization'))
    elif 'proof_admission' in value:
        raise ValueError('proof admission without pinned runtime origin')
    integration = value.get('integration')
    if 'rework_recovery' in value:
        from .activity_recovery import validate
        validate(value)
    for field in ('code_assessments','business_acceptances'):
        if field in value and not isinstance(value[field],list):
            raise ValueError('invalid acceptance activity reference list')
    if integration is not None:
        if not isinstance(integration, dict) or integration.get('schema_version') != 1:
            raise ValueError('unknown execution integration')
        binding = integration.get('binding', {})
        if binding.get('al_run_id') != value['run_id'] or binding.get('al_session_id') != value['session_id']:
            raise ValueError('foreign execution integration')
        if type(integration.get('sequence')) is not int or not isinstance(integration.get('outbox'), list) or integration['sequence'] != len(integration['outbox']):
            raise ValueError('invalid integration outbox')
    external = [] if origin is None else pinned_initialization
    for amendment in value['amendments']:
        if not isinstance(amendment, dict):raise ValueError('invalid modern amendment')
        if 'supporting_source_artifact' not in amendment:continue
        reference = amendment['supporting_source_artifact']
        if (not isinstance(reference, dict) or set(reference) != {'stored_path', 'sha256'}
                or not isinstance(reference['stored_path'], str) or not Path(reference['stored_path']).is_absolute()
                or not isinstance(reference['sha256'], str) or not SHA.fullmatch(reference['sha256'])):
            raise ValueError('unsupported external supporting-source provenance')
        from .engine import R
        source = R.safe_absolute(reference['stored_path'])
        before = source.stat()
        if not source.is_file() or before.st_size > 64 * 1024 * 1024:
            raise ValueError('unsupported external supporting-source file')
        raw = source.read_bytes();after = source.stat()
        if (any(getattr(before, key) != getattr(after, key) for key in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))
                or hashlib.sha256(raw).hexdigest() != reference['sha256']
                or not isinstance(decode(raw), dict)):
            raise ValueError('external supporting-source bytes/type changed')
        external.append(reference)
    _references(value, provenance['members'], Path(provenance['runtime_root']), external)
    from .activity_workflow import validate
    validate(value, provenance)
    return _ledger(value, modern=True)


def classify_artifact(path, raw, *, provenance):
    path = Path(path)
    result = {'classification': 'unknown', 'profile': {'id': 'unknown', 'version': 1},
              'historical_status': None, 'historical_phase': None, 'operation_facts': {}, 'errors': []}
    try:
        value = decode(raw)
        if not isinstance(value, dict):raise ValueError('activity artifact is not an object')
        reviewed = provenance.get('nonstate', {}).get(str(path))
        if reviewed:
            if (set(reviewed) != {'path', 'sha256', 'mode', 'profile', 'reason', 'review_artifact'}
                    or reviewed['path'] != str(path) or reviewed['sha256'] != hashlib.sha256(raw).hexdigest()
                    or reviewed['mode'] != provenance['mode'] or not reviewed['reason'] or not reviewed['review_artifact']):
                raise ValueError('nonstate classification does not bind exact reviewed bytes')
            if any(k in value for k in ('session_id', 'status', 'phase', 'writers', 'evidence')):
                raise ValueError('state-shaped artifact cannot be classified nonstate')
            if reviewed['profile'] != {'id': 'reviewed-nonstate-v1', 'version': 1}:
                raise ValueError('unsupported nonstate classification profile')
            reference = reviewed['review_artifact']
            if not isinstance(reference, dict) or set(reference) != {'path', 'sha256'} or not SHA.fullmatch(reference['sha256']):
                raise ValueError('missing immutable nonstate review binding')
            from .engine import R
            review_path = R.safe_absolute(reference['path'])
            review_raw = review_path.read_bytes()
            if hashlib.sha256(review_raw).hexdigest() != reference['sha256']:
                raise ValueError('nonstate review artifact changed')
            review = decode(review_raw)
            expected = {k: v for k, v in reviewed.items() if k != 'review_artifact'}
            if (not isinstance(review, dict) or set(review) != {'schema_version', 'kind', 'records'}
                    or review['schema_version'] != 1 or review['kind'] != 'activity-nonstate-review'
                    or not isinstance(review['records'], list) or expected not in review['records']):
                raise ValueError('nonstate review does not cover exact classification')
            result.update(classification='nonstate-artifact', profile=reviewed['profile'], provenance=reviewed)
            return result
        if value.get('schema_version') == 2:
            entries = _modern(value, path, provenance)
            profile = 'modern-state-v2'
        elif 'schema_version' in value:
            raise ValueError('unsupported activity schema')
        else:
            ledger = 'evidence' in value or 'run_id' in value
            _workflow(value, path, LEGACY_KEYS | LEDGER_KEYS if ledger else LEGACY_KEYS)
            if 'identity' in value or 'writers' in value or 'phase_occurrences' in value:
                raise ValueError('ambiguous legacy/modern hybrid')
            if 'run_id' in value and not ID.fullmatch(value['run_id']):raise ValueError('invalid historical run identity')
            entries = _ledger(value, modern=False) if ledger else []
            profile = 'legacy-ledger-v1' if ledger else 'legacy-workflow-v1'
        from .activity_operations import reconciliation_validation, setup_checks
        state_sha256 = hashlib.sha256(raw).hexdigest()
        validations = {e['id']: reconciliation_validation(e, value, provenance, state_sha256) for e in entries}
        result.update(classification='current-session' if value.get('schema_version') == 2 else 'legacy-session',
                      profile={'id': profile, 'version': 1}, historical_status=value['status'], historical_phase=value['phase'],
                      session_id=value['session_id'], run_id=value.get('run_id'),
                      operation_facts={'writers': value.get('writers', {}),
                                       'check_reconciliations': validations,
                                       'checks': [{k: e.get(k) for k in ('id', 'status', 'pid', 'owner_pid', 'started_at', 'ended_at', 'process', 'owner_identity', 'interrupted', 'reconciliation', 'log', 'cleanup', 'note')} for e in entries]+setup_checks(entries),
                                       'integration': value.get('integration'), 'aux_runs': value.get('aux_runs', [])},
                      native_conversation_id=value.get('native_conversation_id'))
    except (OSError, ValueError, TypeError, KeyError) as exc:result['errors'].append(str(exc))
    return result
