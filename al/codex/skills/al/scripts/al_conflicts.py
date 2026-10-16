"""Architect conflict proof bound to an actual phase occurrence."""
import al_artifacts as A
from al_schema import refuse


def current(st):
    return next((i for i in range(len(st['phase_occurrences'])-1,-1,-1) if st['phase_occurrences'][i]['phase']=='PLAN_CONFLICT'),None)


def require_resolution(st):
    resolution=st.get('conflict_resolution') or {}
    if current(st) is None or resolution.get('occurrence')!=current(st):refuse('current conflict occurrence lacks Architect resolution')
    if resolution.get('role')!='al-architect' or not resolution.get('reason','').strip():refuse('conflict requires Architect role and reason')
    if not resolution.get('artifact') or not A.read(resolution['artifact']).strip():refuse('conflict resolution artifact missing/empty')


def enter(st):
    old=st.pop('conflict_resolution',None)
    if old:st.setdefault('conflict_resolution_history',[]).append(old)
