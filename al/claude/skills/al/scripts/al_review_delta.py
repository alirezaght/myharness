"""Incremental (delta) Architect review for correction loops (#30).

A re-review after a correction receives the diff between the last reviewed snapshot and the
current candidate, the earlier findings with their resolution status, and the criteria and
invariants the delta affects, instead of the whole candidate again.

Every reuse is keyed by content: each review-start stores the exact content of the changed paths
(`review-snapshot-v1`), and the next delta compares sha256 digests, so only byte-identical
content keeps its earlier verdict. A full review is still required when the specification, an
amendment, the plan or the review base changed, or when the delta touches an invariant the
earlier review approved (or whose location the earlier review did not record).
"""
import base64
import difflib
import hashlib
import json
import os

import al_artifacts as A
import al_store
import al_scope as S
from al_schema import refuse

SNAPSHOT_LIMIT = 8 * 1024 * 1024
DIFF_LINE_LIMIT = 4000


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _worktree(path):
    p = al_store.REPO_ROOT / S.safe_path(os.fsencode(path))
    if p.is_symlink():
        return os.fsencode(os.readlink(p))
    if p.is_file():
        return p.read_bytes()
    return None


def _base(base, path):
    try:
        return S.git_bytes('cat-file', 'blob', base + ':' + path)
    except SystemExit:
        return None


def evidence_ids(st):
    ev = st.get('evidence') or {}
    entries = ev.get('entries', []) if isinstance(ev, dict) else ev
    return sorted(e['id'] for e in entries if isinstance(e, dict) and e.get('id'))


def snapshot(st, sid, candidate):
    """Content of every changed path at review-start, so the next review can diff against it."""
    files, total, truncated = {}, 0, False
    for path in candidate.get('changed_paths', []):
        raw = _worktree(path)
        if raw is None:
            files[path] = {'deleted': True}
            continue
        total += len(raw)
        if total > SNAPSHOT_LIMIT:
            truncated = True
            files[path] = {'sha256': _digest(raw)}
            continue
        files[path] = {'sha256': _digest(raw), 'content_b64': base64.b64encode(raw).decode()}
    value = {'schema_version': 1, 'kind': 'review-snapshot-v1', 'candidate': candidate['fingerprint'],
             'base': candidate['base'], 'files': files, 'truncated': truncated, 'evidence_ids': evidence_ids(st)}
    return A.snapshot_bytes(sid, json.dumps(value, sort_keys=True).encode(), 'review-snapshot', 'runtime')


def plan_basis(st):
    """Everything a plan depends on; a change here requires a full review."""
    planning = [o for o in st.get('phase_occurrences', []) if o.get('phase') == 'PLANNING']
    value = {'amendments': [a.get('artifact', a) for a in st.get('amendments', [])],
             'planning_authorship': st.get('planning_authorship'), 'classification': st.get('classification'),
             'designer_required': st.get('designer_required'), 'planning_cycles': len(planning)}
    return _digest(json.dumps(value, sort_keys=True, default=str).encode())


def anchors(st):
    """(criteria entries, invariant entries) as [{id, text, value_sha256?}]."""
    package = A.read_json(st['task_package'])
    if 'proof_admission' in st:
        from al_verification_authority import authority_binding
        authority = authority_binding(st)['value']
        out = []
        for field in ('criteria', 'invariants'):
            rows = []
            for entry in authority[field]:
                text = None
                pointer = entry.get('pointer', '')
                parts = pointer.strip('/').split('/')
                if len(parts) == 2 and parts[1].isdigit() and parts[0] in package and int(parts[1]) < len(package[parts[0]]):
                    text = package[parts[0]][int(parts[1])]
                rows.append({'id': entry['id'], 'text': text, 'value_sha256': entry.get('value_sha256')})
            out.append(rows)
        return out[0], out[1]
    return ([{'id': c, 'text': c} for c in package['criteria']], [{'id': i, 'text': i} for i in package['invariants']])


def _paths(locations):
    out = set()
    for item in locations or []:
        if isinstance(item, str):
            out.add(item.partition(':')[0])
    return out


def _text_diff(path, old, new):
    if old == new:
        return ''
    try:
        a = old.decode() if old is not None else ''
        b = new.decode() if new is not None else ''
    except UnicodeDecodeError:
        return 'Binary ' + path + ' changed (' + (_digest(old) if old is not None else 'absent') + ' -> ' + (_digest(new) if new is not None else 'absent') + ')\n'
    lines = difflib.unified_diff(a.splitlines(True), b.splitlines(True), 'a/' + path if old is not None else '/dev/null',
                                 'b/' + path if new is not None else '/dev/null')
    return ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n' for line in lines)


def compute(prev_snapshot, candidate):
    """Paths whose content differs between the earlier reviewed snapshot and the candidate, and their diff."""
    base = candidate['base']
    current = set(candidate.get('changed_paths', []))
    earlier = prev_snapshot['files']
    paths, chunks, lines = [], [], 0
    for path in sorted(current | set(earlier)):
        old_row = earlier.get(path)
        if old_row is None:
            old = _base(base, path)
        elif old_row.get('deleted'):
            old = None
        elif 'content_b64' in old_row:
            old = base64.b64decode(old_row['content_b64'])
        else:
            old = b''  # truncated snapshot: content unknown (eligibility already refused)
        new = _worktree(path) if path in current else _base(base, path)
        if old == new:
            continue
        paths.append(path)
        text = _text_diff(path, old, new)
        lines += text.count('\n')
        chunks.append(text)
    diff = ''.join(chunks)
    truncated = lines > DIFF_LINE_LIMIT
    if truncated:
        diff = '\n'.join(diff.splitlines()[:DIFF_LINE_LIMIT]) + '\n... delta diff truncated; read the paths in the repository\n'
    return {'paths': paths, 'diff': diff, 'diff_truncated': truncated}


def _prior_report(prev):
    try:
        return A.read_json(prev['comparison'])
    except (KeyError, OSError, ValueError, SystemExit):
        return None


def _finding_status(finding):
    resolution = finding.get('resolution') or {}
    if resolution.get('status'):
        return resolution['status']
    return 'open' if finding['severity'] == 'BLOCKING' else 'note'


def prior_findings(st):
    from al_review_history import findings
    return [{'id': f['id'], 'source': f['source'], 'severity': f['severity'], 'status': _finding_status(f),
             'description': f.get('description', '')} for f in findings(st)]


def classify(criteria, invariants, report, history, touched, base_review):
    """Split anchors for a delta: (approved invariants the delta touches, affected anchors, kept verdicts).

    An anchor is affected when an earlier finding names it or its recorded location is in the delta.
    An invariant no finding names was approved; if the delta touches it, or its location was never
    recorded while anything changed, the caller must run a full review."""
    corrected = {f['source'] for f in history}
    located = {}
    for row in report.get('invariants', []) or []:
        if isinstance(row, dict) and row.get('id'):
            located[row['id']] = _paths(row.get('locations'))
    approved_touched = []
    for inv in invariants:
        if inv['id'] in corrected:
            continue
        where = located.get(inv['id'])
        if where is None:
            if touched:
                approved_touched.append(inv['id'] + ' (location not recorded in ' + base_review + ')')
        elif where & touched:
            approved_touched.append(inv['id'])
    criteria_rows = {c['id']: c for c in report.get('criteria', []) if isinstance(c, dict)}
    affected_c, kept = [], []
    for c in criteria:
        row = criteria_rows.get(c['id'], {})
        if c['id'] in corrected or _paths(row.get('locations')) & touched:
            affected_c.append(dict(c, prior_status=row.get('status'), prior_locations=row.get('locations', [])))
        elif row:
            kept.append({k: row[k] for k in ('id', 'status', 'rationale', 'locations', 'evidence') if k in row})
    affected_i = [dict(i, prior_locations=sorted(located.get(i['id'], ())))
                  for i in invariants if i['id'] in corrected or located.get(i['id'], set()) & touched]
    return approved_touched, {'criteria': affected_c, 'invariants': affected_i}, kept


def plan(st, prev, candidate, requested):
    """Decide delta vs full. Returns (mode, reasons, detail) — never refuses (help, not block)."""
    import al_task
    if requested == 'full':
        return 'full', ['full review requested'], None
    reasons = []
    if not prev or prev.get('stage') != 'comparison-saved':
        reasons.append('no completed earlier review to diff against')
    elif not prev.get('snapshot'):
        reasons.append('earlier review ' + prev.get('id', '?') + ' recorded no snapshot')
    if reasons:
        return 'full', reasons, None
    if prev.get('task_hash') != al_task.task_hash(st):
        reasons.append('specification or amendment changed since ' + prev['id'])
    if prev.get('plan_basis') != plan_basis(st):
        reasons.append('plan, amendments or classification changed since ' + prev['id'])
    try:
        earlier = A.read_json(prev['snapshot'])
    except (OSError, ValueError, SystemExit):
        return 'full', reasons + ['earlier snapshot unreadable'], None
    if earlier.get('base') != candidate['base']:
        reasons.append('review base changed since ' + prev['id'])
    if earlier.get('truncated'):
        reasons.append('earlier snapshot exceeded the content limit')
    report = _prior_report(prev)
    if report is None:
        reasons.append('earlier comparison report unreadable')
    if reasons:
        return 'full', reasons, None
    delta = compute(earlier, candidate)
    touched = set(delta['paths'])
    criteria, invariants = anchors(st)
    try:
        history = prior_findings(st)
    except SystemExit as exc:
        return 'full', ['earlier findings could not be reconstructed: ' + str(exc)], None
    approved_touched, affected, kept = classify(criteria, invariants, report, history, touched, prev['id'])
    if approved_touched:
        return 'full', ['delta touches previously approved invariant(s): ' + ', '.join(approved_touched)], None
    if requested == 'auto' and report.get('status') == 'APPROVED':
        return 'full', ['earlier review approved; a new review of an approved candidate is full'], None
    detail = {'base_review': prev['id'], 'base_candidate': earlier['candidate'], 'base_snapshot': prev['snapshot'],
              'paths': delta['paths'], 'diff': delta['diff'], 'diff_truncated': delta['diff_truncated'],
              'prior_findings': history, 'affected': affected,
              'kept_verdicts': kept, 'known_evidence': earlier.get('evidence_ids', []),
              'base_inputs': prev.get('comparison_inputs'), 'base_packets': prev.get('packets', {})}
    return 'delta', [], detail


def _sha(ref):
    return (ref or {}).get('sha256') if isinstance(ref, dict) else None


def _ref(ref):
    return {k: ref[k] for k in ('kind', 'sha256', 'stored_path') if k in ref} if isinstance(ref, dict) else ref


def _observation_delta(old, new):
    """Fields of `new` that differ from `old` once re-run evidence IDs and re-hashed sources are mapped."""
    renamed = {}
    if len(old.get('evidence_ids') or []) == len(new.get('evidence_ids') or []):
        renamed = {a: b for a, b in zip(old['evidence_ids'], new['evidence_ids']) if a != b}
    text = json.dumps(old, sort_keys=True)
    for a, b in renamed.items():
        text = text.replace(json.dumps(a), json.dumps(b))
    mapped = json.loads(text)
    hashes = {}
    def unhash(rows):
        out = []
        for row in rows or []:
            if isinstance(row, dict) and 'source_sha256' in row:
                hashes[row.get('path')] = row['source_sha256']
                row = {k: v for k, v in row.items() if k != 'source_sha256'}
            out.append(row)
        return out
    old_locations = unhash(mapped.get('assertion_locations'))
    seen_hashes = dict(hashes); hashes.clear()
    new_locations = unhash(new.get('assertion_locations'))
    changed = {k: v for k, v in new.items() if k not in ('binding', 'assertion_locations') and mapped.get(k) != v}
    if old_locations != new_locations:
        changed['assertion_locations'] = new.get('assertion_locations')
    out = {'changed': changed}
    if renamed:
        out['evidence_renamed'] = renamed
    rehashed = {p: h for p, h in hashes.items() if seen_hashes.get(p) != h}
    if rehashed:
        out['source_sha256'] = rehashed
    return out


def _slim_behavior(current, earlier, base_review):
    """Hash references for behavior obligations and observations the base review already saw."""
    out = dict(current)
    obligations = current.get('obligations') or {}
    if _sha(obligations.get('artifact')) and _sha(obligations.get('artifact')) == _sha((earlier.get('obligations') or {}).get('artifact')):
        out['obligations'] = {'artifact': obligations['artifact'], 'unchanged_since': base_review}
    seen = {_sha(o.get('artifact')) for o in earlier.get('observations', []) if isinstance(o, dict)}
    by_scenario = {}
    for o in earlier.get('observations', []):
        if isinstance(o, dict) and isinstance(o.get('content'), dict):
            by_scenario[o['content'].get('scenario_id')] = o
    rows = []
    for o in current.get('observations') or []:
        if not isinstance(o, dict):
            continue
        if _sha(o.get('artifact')) in seen:
            rows.append({'artifact': _ref(o['artifact']), 'unchanged_since': base_review})
            continue
        content = o.get('content') if isinstance(o.get('content'), dict) else {}
        before = by_scenario.get(content.get('scenario_id'))
        if not before:
            rows.append(o)
            continue
        # Structured delta: the fields that differ from the base review's observation of the same
        # scenario. The runtime validated the binding (candidate, run, authority) itself.
        rows.append(dict(_observation_delta(before['content'], content), artifact=_ref(o['artifact']),
                         scenario_id=content.get('scenario_id'), delta_from=_sha(before.get('artifact'))))
    out['observations'] = rows
    return out


def _slim_rules(rules, prev_packet):
    known = {}
    if prev_packet:
        for src in (prev_packet.get('repository_review_rules') or {}).get('sources', []):
            known[src.get('path')] = src.get('sha256')
    out = dict(rules)
    out['sources'] = [{'path': src.get('path'), 'sha256': src.get('sha256'), 'unchanged_since_base_review': True}
                      if known.get(src.get('path')) == src.get('sha256') else src for src in rules.get('sources', [])]
    return out


def record(sid, detail):
    """Store the delta as an immutable artifact; the run state keeps only its reference."""
    ref = A.snapshot_bytes(sid, json.dumps(detail, sort_keys=True).encode(), 'review-delta', 'runtime')
    return {'artifact': ref, 'base_review': detail['base_review'], 'base_inputs': detail.get('base_inputs'),
            'paths': len(detail['paths'])}


def slim(st, data, review):
    """Replace the full candidate and repeated context with the delta (both stages)."""
    detail = A.read_json(review['delta']['artifact'])
    candidate = review['candidate']
    data['review_mode'] = 'delta'
    data['task_package'] = {'reference': st['task_package'], 'note': 'unchanged since ' + detail['base_review'] + '; read by hash when needed'}
    data['amendments'] = [{'artifact': a.get('artifact', a)} if isinstance(a, dict) else a for a in st.get('amendments', [])]
    data['candidate'] = {k: candidate[k] for k in ('fingerprint', 'base', 'changed_paths', 'diff_limitations', 'preexisting_changes') if k in candidate}
    known = set(detail.get('known_evidence', []))
    ev = data.get('evidence')
    if isinstance(ev, list):
        data['evidence'] = [e for e in ev if e.get('id') not in known]
    elif isinstance(ev, dict) and isinstance(ev.get('entries'), list):
        data['evidence'] = dict(ev, entries=[e for e in ev['entries'] if e.get('id') not in known])
    prev_packet = None
    ref = (detail.get('base_packets') or {}).get(data['stage'])
    if ref:
        try:
            prev_packet = A.read_json(ref)
        except (OSError, ValueError, SystemExit):
            prev_packet = None
    if 'repository_review_rules' in data:
        data['repository_review_rules'] = _slim_rules(data['repository_review_rules'], prev_packet)
    if isinstance(data.get('behavior'), dict) and prev_packet:
        data['behavior'] = _slim_behavior(data['behavior'], prev_packet.get('behavior') or {}, detail['base_review'])
    if data['stage'] == 'compare':
        # The fresh delta packet carries the factual entries; the comparison needs their IDs and
        # verdicts to check fix evidence.
        import al_ledger as L
        data['evidence'] = {'entries': [{'id': e['id'], 'area': e.get('area'), 'status': e.get('status'),
                                         'verdict': L.entry_verdict(st, e)[0]}
                                        for e in L.ledger(st)['entries'] if e.get('id') not in known],
                            'details': 'factual entries are in the fresh delta packet'}
    data['delta'] = {k: detail[k] for k in ('base_review', 'base_candidate', 'base_snapshot', 'paths', 'diff', 'diff_truncated',
                                            'prior_findings', 'affected', 'kept_verdicts')}
    data['delta']['base_snapshot'] = _ref(data['delta']['base_snapshot'])
    data['task_package']['reference'] = _ref(data['task_package']['reference'])
    data['gates'] = [_ref(g) for g in data.get('gates', [])]
    if isinstance(data.get('behavior'), dict) and isinstance(data['behavior'].get('obligations'), dict) and 'content' not in data['behavior']['obligations']:
        data['behavior']['obligations']['artifact'] = _ref(data['behavior']['obligations']['artifact'])
    if data['stage'] == 'compare':
        # Implementer handoff records since the base review (never in the isolated fresh stage).
        import al_comms
        data['delta']['handoff_records'] = al_comms.records_since(st, review_iteration=int(detail['base_review'][1:]))
        # The approved plan is byte-identical to the one the base review inspected (checked at
        # review-compare-start); keep its hash references, not its text.
        if isinstance(data.get('approved_inputs'), dict):
            references = dict(data['approved_inputs'].get('references') or {})
            for name in ('plan', 'pseudocode'):
                if name in references:
                    references[name] = _ref(references[name])
            data['approved_inputs'] = {'references': references, 'contents': {},
                                       'unchanged_since': detail['base_review']}
    return data


def compare_inputs(st, a):
    """In delta mode the plan must be the one the base review inspected; omitted means reuse it."""
    review = st['review']
    base_inputs = (review.get('delta') or {}).get('base_inputs') or {}
    for name in ('plan', 'pseudocode'):
        ref = base_inputs.get(name)
        given = getattr(a, name, None)
        if given is None:
            if ref:
                setattr(a, name, ref['stored_path'])
            continue
        with open(given, 'rb') as stream:
            raw = stream.read()
        if ref and _digest(raw) != ref['sha256']:
            refuse('approved ' + name + ' changed since ' + review['delta']['base_review'] + ': a delta review keeps the '
                   'reviewed ' + name + '. Pass the reviewed ' + name + ' (or omit --' + name + ' to reuse it); a changed '
                   + name + ' is a replan, after which `review-start` runs a full review')
