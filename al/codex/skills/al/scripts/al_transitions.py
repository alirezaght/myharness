"""Command-specific workflow edges, checked before any state write."""
from al_schema import refuse

WRITING = {'IMPLEMENTATION', 'FINALIZATION', 'REWORK', 'CORRECTION'}
BARRIERS = {'VERIFICATION', 'REVIEW', 'FINAL_VERIFICATION', 'DONE'}

COMPLETION_EDGES = {
    'PLANNING': {'PSEUDOCODE','IMPLEMENTATION'}, 'PSEUDOCODE': {'IMPLEMENTATION'},
    'IMPLEMENTATION': {'FINALIZATION','VERIFICATION'}, 'FINALIZATION': {'VERIFICATION'},
    'CORRECTION': {'FINALIZATION','VERIFICATION'}, 'REWORK': {'FINALIZATION','VERIFICATION'},
    'VERIFICATION': {'REVIEW'}, 'REVIEW': {'FINAL_VERIFICATION','CORRECTION'},
    'FINAL_VERIFICATION': {'DONE'},
}

def structural_gaps(st):
    from al_artifacts import read
    out=[]
    if st.get('spec_status')!='SPEC_READY': out.append('SPEC_READY planning verdict missing')
    if st.get('classification') not in {'SMALL','STANDARD','COMPLEX'}: out.append('classification missing')
    if type(st.get('designer_required')) is not bool or (st.get('classification')=='COMPLEX' and not st.get('designer_required')):out.append('classification design obligation missing')
    required=['PLANNING','IMPLEMENTATION']
    if st.get('designer_required'): required.append('PSEUDOCODE')
    if st.get('classification')=='COMPLEX': required.append('FINALIZATION')
    occurrences=st.get('phase_occurrences',[])
    cycle=max((i for i,o in enumerate(occurrences) if o['phase']=='PLANNING'),default=0)
    latest={o['phase']:o for o in occurrences[cycle:]}
    for phase in required:
        if phase not in st.get('completed_phases',[]) or latest.get(phase,{}).get('completed') is not True:
            out.append('required actual phase completion missing: '+phase)
    writes=[o for o in occurrences[cycle:] if o['phase'] in WRITING]
    if writes and writes[-1].get('completed') is not True: out.append('latest writing occurrence is incomplete')
    conflicts=[i for i,o in enumerate(occurrences) if i>=cycle and o['phase']=='PLAN_CONFLICT']
    if conflicts:
        from al_conflicts import require_resolution
        require_resolution(st)
    return out


def workflow_gaps(st):
    from al_gates import base_gaps
    from al_execution_tasks import completion_gaps
    from al_task_attribution import classify_changes
    ambiguous = classify_changes(st)['ambiguous'] if 'task_attribution' in st else []
    return structural_gaps(st)+base_gaps(st)+completion_gaps(st)+['unreconciled task attribution overlap: '+p for p in ambiguous]


def require_transition(st, command, destination):
    from al_new_origin import require_admitted
    if destination in WRITING | BARRIERS or command in {'review-start', 'approved'}:
        require_admitted(st)
    src = st['phase'];guarded=False
    if st['status'] != 'RUNNING':
        refuse('run is ' + st['status'] + '; use explicit recovery')
    if command == 'phase':
        if destination in {'REVIEW','CORRECTION','REWORK','DONE'}:
            refuse('use the specialized command for ' + destination)
        edges = {'PLANNING': {'PSEUDOCODE'} if st.get('designer_required') else {'IMPLEMENTATION'},
                 'PSEUDOCODE': {'PLAN_CONFLICT','IMPLEMENTATION'},
                 'PLAN_CONFLICT': {'PSEUDOCODE'},
                 'IMPLEMENTATION': {'FINALIZATION','VERIFICATION'},
                 'FINALIZATION': {'VERIFICATION'},
                 'REWORK': {'FINALIZATION','VERIFICATION'},
                 'CORRECTION': {'FINALIZATION','VERIFICATION'},
                 'REVIEW': {'FINAL_VERIFICATION'}}
        if destination not in edges.get(src, set()):
            refuse(f'illegal phase edge {src} → {destination}')
        if src == 'PLANNING':
            if st.get('spec_status') != 'SPEC_READY' or not st.get('classification'):
                refuse('planning requires SPEC_READY and classification')
        from al_gate_recovery import ready,RETAINED
        if src in RETAINED:
            ready(st);guarded=True
        if src == 'PLAN_CONFLICT':
            from al_conflicts import require_resolution
            require_resolution(st)
        if destination == 'VERIFICATION' and st.get('classification') == 'COMPLEX' and src == 'IMPLEMENTATION':
            refuse('COMPLEX requires finalization')
        if destination == 'FINAL_VERIFICATION':
            from al_review import review_gaps
            missing = review_gaps(st)
            if missing: refuse('; '.join(missing))
    else:
        allowed = {'review-start': {'VERIFICATION'}, 'correction': {'REVIEW'},
                   'rework': {'FINALIZATION','VERIFICATION'}, 'approved': {'FINAL_VERIFICATION'}}
        if src not in allowed.get(command, set()):
            refuse(f'{command} is not allowed from {src}')
    if st.get('gate_recovery') and not guarded:
        from al_gate_recovery import ready
        ready(st);guarded=True
    if command in {'review-start','approved'} or destination=='FINAL_VERIFICATION':
        gaps=workflow_gaps(st)
        if gaps: refuse('; '.join(gaps))
    if destination in BARRIERS or src in BARRIERS or (command=='phase' and src!=destination):
        from al_writers import operation_gaps
        missing = operation_gaps(st)
        if missing: refuse('; '.join(missing))

    if guarded and st.get('gate_recovery'):
        from al_gate_recovery import complete
        complete(st)
