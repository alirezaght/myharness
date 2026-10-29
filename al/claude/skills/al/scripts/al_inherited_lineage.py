"""Authenticated inherited requirements and source anchors; never inherited authority."""
import copy

import al_artifacts as A
from al_schema import refuse


def current(st=None):
    from al_new_origin import context
    from installer.runtime_successor_store import lineage_for, read_reference
    origin = context()
    prepared = getattr(origin, 'prepared_start', None)
    if prepared:
        reference = prepared['lineage_ref']
        lineage = read_reference(reference)
    else:
        reference, lineage = lineage_for(origin.transaction)
    if st is not None and st.get('inherited_lineage') != {
            'reference': reference, 'prefix_length': len(read_reference(lineage['requirements_ref'])['successor_amendments'])}:
        refuse('inherited lineage binding differs')
    expected = lineage['successor_transaction']
    if any(expected[k] != origin.transaction[k] for k in expected if k not in {'initialization', 'status'}):
        refuse('foreign successor lineage transaction')
    return reference, lineage


def seed(st):
    from al_new_origin import context
    if not getattr(context(), 'prepared_start', None):
        return
    from installer.runtime_successor_store import read_reference
    reference, lineage = current()
    requirements = read_reference(lineage['requirements_ref'])
    if st['task_package']['sha256'] != requirements['original_package']['sha256']:
        refuse('successor original approved package differs')
    for amendment in requirements['successor_amendments']:
        raw = A.read(amendment['artifact'])
        import json
        payload = json.loads(raw)
        original = requirements['amendments'][len(st['amendments'])]
        if A.digest(raw) != original['artifact']['sha256'] or any(payload.get(k) != amendment.get(k) for k in ('id', 'text', 'source', 'affected_criteria')):
            refuse('inherited amendment payload differs')
        st['amendments'].append(copy.deepcopy(amendment))
    st['inherited_lineage'] = {'reference': reference, 'prefix_length': len(st['amendments'])}
    anchors = lineage['source_anchors']
    st['baseline_head'] = anchors['baseline_head_oid']
    st['preexisting_changes'] = copy.deepcopy(anchors['original_preexisting_changes'])
    st['review_mode'] = anchors['review_mode']
    st['identity'].update(target=anchors['target_label'], target_oid=anchors['target_oid'], merge_base=anchors['merge_base_oid'])
    st['observed_start_head'] = anchors['observed_head_oid']
    validate(st)


def validate(st):
    if 'inherited_lineage' not in st:
        from al_new_origin import context
        from installer.runtime_successor_store import fence_path
        origin = context()
        subject = {k: origin.transaction[k] for k in ('repository_identity', 'platform', 'session_id')}
        if getattr(origin, 'prepared_start', None):
            refuse('inherited requirement lineage was removed')
        try:
            fence_path(subject).lstat()
        except FileNotFoundError:
            return
        refuse('inherited requirement lineage was removed')
    from installer.runtime_successor_store import read_reference
    from al_task import revision_hashes
    _, lineage = current(st)
    requirements = read_reference(lineage['requirements_ref'])
    prefix = requirements['successor_amendments']
    if (st['task_package']['sha256'] != requirements['original_package']['sha256']
            or st['amendments'][:len(prefix)] != prefix
            or revision_hashes(st)[:len(prefix)+1] != requirements['revision_hashes']):
        refuse('inherited requirement prefix was removed, reordered or rewritten')
    for task in st.get('integration',{}).get('tasks',{}).values():
        if task.get('inherited_scopes',[]) != map_scope(st,task):
            refuse('inherited requirement scope mapping changed')


def predecessor_layers(st, seen=()):
    if 'inherited_lineage' not in st:
        return []
    from installer.runtime_successor_store import read_reference
    reference = st['inherited_lineage']['reference']
    if reference['sha256'] in seen or len(seen) >= 16:
        refuse('cyclic inherited source lineage')
    lineage = read_reference(reference)
    previous = read_reference(lineage['predecessor_state_ref'])
    prior_binding = read_reference(lineage['predecessor_binding_ref'])
    if (previous['run_id'] != prior_binding['run_id'] or previous['session_id'] != prior_binding['session_id']
            or previous['identity']['root'] != st['identity']['root']):
        refuse('foreign predecessor source lineage')
    return predecessor_layers(previous, (*seen, reference['sha256'])) + [previous]


def classify(st, current_capture=None):
    return compose([*predecessor_layers(st), st], st, current_capture)


def compose(layers, st, current_capture=None):
    import al_task_attribution as T
    if not layers or layers[-1] != st:
        refuse('source lineage does not terminate at current state')
    if any('task_attribution' not in layer for layer in layers):
        paths = T.S.changed_paths(st['identity']['merge_base'])
        return {'attributed': False, 'task_owned': [], 'unchanged_preexisting': [],
                'externally_changed': paths, 'ambiguous': [], 'overlapping_or_ambiguous': [],
                'discovery_paths': paths, 'limitations': ['inherited source attribution unavailable; conservative actual-change universe']}
    first = T._snapshot(layers[0])
    baseline = T._map(T._capture(layers[0], first['baseline_capture_ref']))
    expected, owned, ambiguous, reconciled = dict(baseline), set(), set(), set()
    for layer in layers:
        T._snapshot(layer)
        reconciled.update(T._replay(layer, expected, owned, ambiguous, content_only=True))
    current = T._map(current_capture or T.observe(st))
    limitations = T.S.child_content_gaps({'files': list(current.values())})
    ambiguous.update(row['path'] for row in current.values() if row.get('child') and
                     (row['child'].get('dirty') or row['child']['state'] == 'inspection-failed'))
    external = set(T._content_changes(expected, current)) | (reconciled - owned)
    ambiguous.update(external & owned)
    owned -= ambiguous | external
    owned &= set(T._content_changes(baseline, current))
    preexisting = {p for p in first['baseline_changed_paths']
                   if T._content(baseline.get(p)) == T._content(current.get(p))} - owned - ambiguous
    return {'attributed': True, 'task_owned': sorted(owned), 'unchanged_preexisting': sorted(preexisting),
            'externally_changed': sorted(external - ambiguous), 'ambiguous': sorted(ambiguous),
            'overlapping_or_ambiguous': sorted(ambiguous), 'limitations': limitations,
            'discovery_paths': sorted(owned | external | ambiguous)}


def map_scope(st, task):
    if 'inherited_lineage' not in st:
        return []
    from installer.runtime_successor_store import read_reference
    _, lineage = current(st)
    requirements = read_reference(lineage['requirements_ref'])
    layers = predecessor_layers(st)
    mapped = []
    def overlaps(a,b):
        return a == b or a.startswith(b.rstrip('/')+'/') or b.startswith(a.rstrip('/')+'/')
    for amendment in requirements['amendments']:
        if not amendment.get('scoped'):
            continue
        origin = next((layer for layer in reversed(layers) if amendment['id'] in layer['integration']['deliveries']),None)
        if origin is None or any(t not in origin['integration']['tasks'] for t in amendment['affected_tasks']):
            refuse('inherited amendment scope provenance missing')
        paths = sorted({p for tid in amendment['affected_tasks'] for p in origin['integration']['tasks'][tid]['files']})
        if (set(task['criteria']) & set(amendment['affected_criteria'])
                or any(overlaps(a,b) for a in task['files'] for b in paths)):
            mapped.append({'id':amendment['id'],'artifact_sha256':amendment['artifact']['sha256'],
                'predecessor_tasks':amendment['affected_tasks'],'paths':paths,'criteria':amendment['affected_criteria']})
    return mapped


def scoped_amendments(st, task_id):
    task = st['integration']['tasks'][task_id]
    prefix = st.get('inherited_lineage',{}).get('prefix_length',0)
    inherited = {row['id'] for row in task.get('inherited_scopes',[])}
    return [a for index,a in enumerate(st['amendments'])
            if (a['id'] in inherited if index < prefix else task_id in a.get('affected_tasks',[]))]


def verification_debt(st):
    if 'inherited_lineage' not in st:return []
    _,lineage=current(st)
    debt=lineage['source_anchors']['verification_obligations']
    return debt['required'] + ['fresh verification of predecessor check obligation: '+area for area in debt['predecessor_check_areas']] + [
        'fresh observation of predecessor operational issue: '+issue for issue in debt['predecessor_open_issues']]
