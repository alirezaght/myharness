"""Audited planning derivation; a draft never renews Architect proof authority."""
import copy
from pathlib import Path

import al_proof_json as J
import al_requirement_bindings as Q
import al_store
from al_schema import refuse


def supplied_packet(st, value):
    """The retained packet content that `--from-packet` names.

    Accepts the packet content itself (its retained `stored_path`), the whole JSON that `planning
    packet` printed (`{"packet": {...}, "content": {...}, ...}`), or the sha256 of a packet
    retained in this run (bare, or as the file's only text). Identity is always checked against
    this run's retained issuance by `historical`."""
    retained = [item['packet'] for item in [*st.get('planning_history', []), st.get('planning_authorship', {})]
                if item.get('packet')]
    text = value.strip() if isinstance(value, str) else None
    if text is None and isinstance(value, (bytes, bytearray)):
        text = bytes(value).decode('utf-8', 'replace').strip()
    if text is not None and len(text) == 64 and all(c in '0123456789abcdef' for c in text.lower()):
        match = next((ref for ref in retained if ref['sha256'] == text.lower()), None)
        if match is None:
            refuse('rebind --from-packet ' + text + ' names no packet retained in this run; retained: '
                   + ', '.join(ref['sha256'] for ref in retained))
        return J.read(match)
    data = J.loads(value) if text is not None else value
    if isinstance(data, dict) and {'packet', 'content'} <= data.keys() and isinstance(data['packet'], dict):
        # The printed `planning packet` response: its content must be the packet it names.
        if data['packet'] not in retained or J.raw(data['content']) != J.raw(J.read(data['packet'])):
            refuse('rebind --from-packet output names a packet this run did not retain or whose content differs')
        return data['content']
    return data


def historical(st, supplied):
    """Construct an isolated validation view from this run's retained issuance."""
    import al_planning as P

    retained = [*st.get('planning_history', []), st.get('planning_authorship', {})]
    previous = next((item for item in retained if item.get('packet')
                     and J.raw(J.read(item['packet'])) == J.raw(supplied)), None)
    if previous is None:
        refuse('rebind requires a packet retained in this same run; pass the packet `stored_path`, the '
               'JSON `planning packet` printed, or a retained packet sha256: '
               + ', '.join(item['packet']['sha256'] for item in retained if item.get('packet')))
    count = len(supplied['accepted_amendments'])
    prefix = [item['artifact'] for item in st['amendments'][:count]]
    if supplied['task_package'] != st['task_package'] or prefix != supplied['accepted_amendments']:
        refuse('rebind packet does not describe an accepted amendment prefix')
    view = copy.deepcopy(st)
    view['amendments'] = view['amendments'][:count]
    view['classification'] = supplied['routing']['classification']
    view['designer_required'] = supplied['routing']['designer_required']
    view['planning_authorship'] = copy.deepcopy(previous)
    view['proof_admission']['status'] = 'pending'
    view['proof_admission']['requirement_lineage_hash'] = Q.lineage(view)
    current, contents, attempt = P.packet(view, current_source=False)
    return view, current, contents, attempt


def draft_report(original, current_template):
    draft = copy.deepcopy(original)
    for field in ('attempt_id', 'model', 'effort', 'policy_digest', 'packet_sha256', 'binding', 'routing'):
        draft[field] = copy.deepcopy(current_template[field])
    draft['invocation_id'] = 'FILL: actual Architect invocation that revalidates this draft'
    draft['specification'] = {
        'status': 'FILL: revalidate current specification readiness',
        'reason': 'FILL: assess amendments and source; inherited rationale: ' + original['specification']['reason'],
    }
    behavior = draft['authored_obligations']
    fresh = current_template['authored_obligations']
    behavior['binding'] = copy.deepcopy(fresh['binding'])
    behavior['authorship'] = copy.deepcopy(fresh['authorship'])
    # Coverage, normative references and authored behavior stay inherited.
    # A changed implementation context requires an explicit current assessment.
    if behavior['implementation_context'] != fresh['implementation_context']:
        behavior['implementation_context'] = {
            'status': 'FILL: revalidate implementation context',
            'observation_ref': fresh['implementation_context']['observation_ref'],
        }
    return draft


def cmd_rebind(a, sid):
    import al_planning as P
    import al_planning_template as T

    st = al_store.load(sid)
    P.ready(st, authoring=True)
    current, contents, _ = P.packet(st)
    if 'report' in current:
        refuse('current Architect planning report is already immutable')
    source = Path(a.from_packet)
    old_packet = supplied_packet(st, source.read_bytes() if source.is_file() else a.from_packet)
    view, old, old_contents, old_attempt = historical(st, old_packet)
    if old['packet'] == current['packet']:
        refuse('rebind requires a different retained packet; use its ordinary report template')
    original = J.loads(Path(a.from_report).read_bytes())
    if 'report' in old and J.raw(original) != J.raw(J.read(old['report'])):
        refuse('supplied report differs from the immutable retained Architect report')
    # Old authored claims are checked against their exact issued inputs. Today's
    # operational writer requirements are enforced only on the current report.
    P.validate_report(view, original, old, old_contents, old_attempt, None, current_implementation=False)
    original_ref = old.get('report') or J.snapshot(sid, original, 'unsubmitted-architect-planning-report-v1', a.from_report)
    template = T.template(current['packet'], contents,
                          partially_built=bool(st.get('writers') or st.get('task_attribution', {}).get('contributions')))
    draft = draft_report(original, template)
    diagnostics = []
    try:
        Q.criteria(st, draft['authored_obligations']['criteria_registry'])
    except SystemExit as exc:
        diagnostics.append(str(exc))
    inherited = {'specification': original['specification'],
                 'criteria_registry': original['authored_obligations']['criteria_registry'],
                 'obligations': original['authored_obligations']['obligations']}
    receipt = {
        'schema_version': 1, 'kind': 'planning-rebind-derivation-v1',
        'status': 'requires-architect-revalidation',
        'original_packet': old['packet'], 'original_attempt': old['attempt'],
        'original_report': original_ref,
        'original_report_status': 'retained' if 'report' in old else 'unsubmitted',
        'original_authorship': {key: original[key] for key in ('invocation_id', 'model', 'effort', 'policy_digest')},
        'current_packet': current['packet'], 'current_attempt': current['attempt'],
        'changed_binding_fields': {key: {'before': old_contents['binding'][key], 'draft': value}
                                   for key, value in contents['binding'].items()
                                   if old_contents['binding'][key] != value},
        'added_normative_inputs': contents['accepted_amendments'][len(old_contents['accepted_amendments']):],
        'removed_normative_inputs': [],
        'preserved_authored_content_sha256': J.digest(inherited),
        'coverage_diagnostics': diagnostics,
    }
    reference = J.snapshot(sid, receipt, receipt['kind'])
    draft_ref = J.snapshot(sid, draft, 'planning-rebind-draft-v1')
    current.setdefault('rebinds', []).append({'receipt': reference, 'draft': draft_ref})
    # A second actual source check prevents publishing a draft after movement.
    P.packet(st)
    al_store.save(sid, st)
    print(J.raw({'receipt': reference, 'draft': draft_ref, 'report_template': draft,
                 'authority': 'none', 'status': receipt['status'],
                 'coverage_diagnostics': diagnostics,
                 'revalidation_template': {
                     'schema_version': 1, 'kind': 'planning-rebind-revalidation-v1',
                     'receipt_sha256': reference['sha256'], 'packet_sha256': current['packet']['sha256'],
                     'report_sha256': 'FILL: canonical JSON SHA256 of the final report',
                     'invocation_id': 'FILL: actual Architect invocation matching the final report',
                     'assessment': {key: 'FILL: current Architect assessment of ' + key
                                    for key in ('amendments', 'source', 'routing', 'specification')},
                 }}).decode())


def revalidation(st, current, report, identifier, value):
    import al_planning as P
    import al_planning_template as T

    entry = next((item for item in current.get('rebinds', [])
                  if identifier in (item['receipt']['sha256'], item['receipt']['stored_path'])), None)
    if entry is None:
        refuse('revalidation requires a derivation receipt issued for this current packet')
    receipt = J.read(entry['receipt'])
    if receipt['current_packet'] != current['packet'] or receipt['current_attempt'] != current['attempt']:
        refuse('stale or foreign planning rebind receipt')
    # Reconstruct old provenance again; a receipt does not replace old-report validation.
    view, old, old_contents, old_attempt = historical(st, J.read(receipt['original_packet']))
    P.validate_report(view, J.read(receipt['original_report']), old, old_contents, old_attempt, None,
                      current_implementation=False)
    J.closed(value, {'schema_version', 'kind', 'receipt_sha256', 'packet_sha256',
                     'report_sha256', 'invocation_id', 'assessment'}, 'planning revalidation')
    if (type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['kind'] != 'planning-rebind-revalidation-v1'
            or value['receipt_sha256'] != entry['receipt']['sha256']
            or value['packet_sha256'] != current['packet']['sha256']
            or value['report_sha256'] != J.digest(report)
            or value['invocation_id'] != report['invocation_id'] or T.placeholders(value)):
        refuse('revalidation differs from the exact current receipt, report or invocation')
    J.closed(value['assessment'], {'amendments', 'source', 'routing', 'specification'}, 'Architect revalidation assessment')
    for key, text in value['assessment'].items():
        if not isinstance(text, str) or len(text.strip()) < 20:
            refuse('substantive current Architect assessment required: ' + key)
    return entry['receipt']


def submitted(st, current, report):
    saved = current.get('rebind_validation')
    if current.get('rebinds') and saved is None:
        refuse('issued planning rebind lacks saved current Architect revalidation')
    if saved is not None:
        reference = revalidation(st, current, report, saved['receipt']['sha256'], J.read(saved['revalidation']))
        if saved['receipt'] != reference:
            refuse('saved revalidation receipt differs from its issued reference')
