"""Repository brief (#30): what a change reaches, so agents do not rediscover it by hand.

`brief(st)` gives the Architect, Designer and Scouts (and every writer handoff) the reached
units, their consumers, the applicable gates, the coverage gaps and the relevant files.

The facts come from one function, `facts(st)`. Today it reads the run's latest admitted gate
manifest (units, gates, service coverage, rule sources). The content-addressed fact graph of
#25 can back the same function later without changing the brief's shape or its callers.
"""
import json

import al_artifacts as A
import al_store
from al_schema import refuse

LIMIT = 40


def facts(st):
    """Today's facts: the latest gate manifest, or None before gates are planned.

    Returns {'source', 'reference', 'paths', 'units', 'gates', 'rules', 'service_coverage'}.
    Replace this body with a fact-graph query (#25); keep the returned keys."""
    refs = st.get('gate_manifests') or []
    if not refs:
        return None
    manifest = A.read_json(refs[-1])
    return {'source': 'gate-manifest', 'reference': {'kind': refs[-1].get('kind'), 'sha256': refs[-1]['sha256']},
            'paths': manifest.get('paths', []), 'units': manifest.get('units', []), 'gates': manifest.get('gates', []),
            'rules': manifest.get('rules', []), 'service_coverage': manifest.get('service_coverage') or {}}


def _cap(rows):
    rows = list(rows)
    return rows[:LIMIT] + (['... ' + str(len(rows) - LIMIT) + ' more'] if len(rows) > LIMIT else [])


def brief(st, paths=None):
    """The brief as a dict (`repository-brief-v1`). Never refuses: missing facts are named."""
    known = facts(st)
    if known is None:
        return {'schema_version': 1, 'kind': 'repository-brief-v1', 'source': 'none',
                'missing': 'no gate manifest yet: run `gates discover` and `gates plan`; the brief then lists units, gates and gaps'}
    selected = sorted(set(paths or []) | set(known['paths']))
    assessment = (known['service_coverage'] or {}).get('assessment') or {}
    gates = known['gates']
    applicable = [{'id': g['id'], 'category': g['category'], 'units': g.get('units', []), 'required': g.get('required', False)}
                  for g in gates if g.get('applicability') == 'APPLICABLE']
    unresolved = [{'id': g['id'], 'category': g['category'], 'reason': g.get('reason') or ''}
                  for g in gates if g.get('applicability') == 'UNRESOLVED']
    gaps = []
    try:
        from al_service_coverage import admission_gaps, coverage_notes
        gaps = admission_gaps(st)
        notes = coverage_notes(st)
    except (SystemExit, OSError, ValueError, KeyError):
        notes = []
    consumers = sorted(set(assessment.get('known_affected', [])) | set(assessment.get('selected_services', [])))
    potential = sorted(set(assessment.get('potential_services', [])) - set(consumers))
    return {'schema_version': 1, 'kind': 'repository-brief-v1', 'source': known['source'], 'facts': known['reference'],
            'paths': _cap(selected), 'reached_units': _cap(sorted(known['units'])),
            'consumers': _cap(consumers), 'potential_consumers': _cap(potential),
            'applicable_gates': _cap(applicable), 'unverified_gates': _cap(unresolved),
            'coverage_gaps': _cap(gaps), 'coverage_notes': _cap(notes),
            'relevant_files': _cap(sorted({r['path'] for r in known['rules'] if isinstance(r, dict) and r.get('path')}))}


def render(value):
    """Compact text for a prompt: one line per field, original IDs, no paraphrase."""
    lines = ['REPOSITORY BRIEF (' + value['kind'] + ', source: ' + value['source']
             + ((', facts ' + value['facts']['sha256'][:12]) if value.get('facts') else '') + ')']
    if value.get('missing'):
        return '\n'.join(lines + ['- ' + value['missing']])
    def fmt(row):
        if isinstance(row, dict):
            return row['id'] + (' [' + row['category'] + ']' if row.get('category') else '') + \
                (' units=' + ','.join(row['units']) if row.get('units') else '') + (' required' if row.get('required') else '') + \
                (' (' + row['reason'] + ')' if row.get('reason') else '')
        return str(row)
    for key in ('paths', 'reached_units', 'consumers', 'potential_consumers', 'applicable_gates', 'unverified_gates',
                'coverage_gaps', 'coverage_notes', 'relevant_files'):
        rows = value.get(key) or []
        lines.append('- ' + key.replace('_', ' ') + ': ' + ('; '.join(fmt(r) for r in rows) if rows else 'none'))
    return '\n'.join(lines)


def cmd_brief(a, sid):
    st = al_store.load(sid)
    paths = None
    if a.paths_file:
        try:
            paths = json.loads(open(a.paths_file, 'rb').read())
        except (OSError, ValueError) as exc:
            refuse('brief --paths-file must be a JSON array of repository paths: ' + str(exc))
        if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
            refuse('brief --paths-file must be a JSON array of repository paths')
    value = brief(st, paths)
    print(json.dumps(value, indent=2, sort_keys=True) if a.format == 'json' else render(value))


def register(sub):
    p = sub.add_parser('brief', help='repository brief: reached units, consumers, gates, coverage gaps, relevant files')
    p.add_argument('--paths-file', help='JSON array of extra paths the change touches')
    p.add_argument('--format', choices=['text', 'json'], default='text')
    p.set_defaults(fn=cmd_brief)
