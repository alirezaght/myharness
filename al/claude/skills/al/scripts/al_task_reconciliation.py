"""Explicit observed overlap resolution never manufactures task ownership or approval."""
import json
from pathlib import Path
import al_artifacts as A
import al_store
import al_task
from al_schema import refuse


def _digest(value):
    return A.digest(json.dumps(value, sort_keys=True, allow_nan=False).encode())


def _unsupported(row):
    child = (row or {}).get('child')
    return bool(child and (child.get('dirty') or child.get('state') == 'inspection-failed'))


class Replay:
    def __init__(self, st, expected, owned, ambiguous):
        self.st, self.expected, self.owned, self.ambiguous = st, expected, owned, ambiguous
        self.refs = st['task_attribution'].get('reconciliations', [])
        self.position = 0
        self.external = set()

    def apply(self, count):
        import al_task_attribution as T
        contributions = self.st['task_attribution']['contributions']
        while self.position < len(self.refs):
            ref = self.refs[self.position]
            data = T._read(self.st, ref, 'task-attribution-reconciliation-v1')
            expected_keys = {'schema_version', 'kind', 'binding', 'task_hash', 'snapshot_sha',
                             'contribution_shas', 'prior_reconciliation_shas', 'paths', 'capture_ref', 'source', 'reason'}
            if set(data) != expected_keys:refuse('invalid attribution reconciliation shape')
            index = len(data['contribution_shas'])
            if index > count:return
            if (index != count or data['contribution_shas'] != [r['sha256'] for r in contributions[:count]]
                    or data['prior_reconciliation_shas'] != [r['sha256'] for r in self.refs[:self.position]]
                    or data['snapshot_sha'] != self.st['task_attribution']['snapshot']['sha256']
                    or data['task_hash'] not in al_task.revision_hashes(self.st)):
                refuse('attribution reconciliation observation differs')
            current = T._map(T._capture(self.st, data['capture_ref']))
            ambiguous = self.ambiguous | (set(T._changes(self.expected, current)) & self.owned)
            paths = data['paths']
            if (not isinstance(paths, list) or not paths or paths != sorted(set(paths)) or not set(paths) <= ambiguous
                    or any(_unsupported(current.get(p)) for p in paths)
                    or any(not isinstance(data[k], str) or not data[k].strip() for k in ('source', 'reason'))):
                refuse('unsupported attribution overlap reconciliation')
            for path in paths:
                T.S.safe_path(path)
                if path in current:self.expected[path] = current[path]
                else:self.expected.pop(path, None)
                self.owned.discard(path);self.ambiguous.discard(path);self.external.add(path)
            self.position += 1

    def finish(self):
        if self.position != len(self.refs):refuse('attribution reconciliation checkpoint missing')


def view(st):
    import al_task_attribution as T
    capture = T.observe(st)
    classification = T.classify_changes(st, capture)
    binding = None
    if classification['attributed']:
        registry = st['task_attribution']
        binding = {'schema_version': 1, 'kind': 'task-attribution-observation-v1', 'binding': T._binding(st),
                   'task_hash': al_task.task_hash(st), 'snapshot_sha': registry['snapshot']['sha256'],
                   'contribution_shas': [r['sha256'] for r in registry['contributions']],
                   'prior_reconciliation_shas': [r['sha256'] for r in registry.get('reconciliations', [])],
                   'capture_digest': _digest(capture['files'])}
    return {'classification': classification, 'reconciliation_binding': binding}


def cmd_reconcile(a, sid):
    import al_task_attribution as T
    from al_writers import operation_gaps
    st = al_store.load(sid)
    allowed = {'PLANNING', 'PSEUDOCODE', 'IMPLEMENTATION', 'FINALIZATION', 'VERIFICATION', 'REWORK', 'CORRECTION'}
    if st['status'] != 'RUNNING' or st['phase'] not in allowed or operation_gaps(st):
        refuse('attribution reconciliation requires a running pre-review run without unresolved operations')
    request = json.loads(Path(a.file).read_bytes())
    if (not isinstance(request, dict) or set(request) != {'schema_version', 'kind', 'binding', 'paths', 'source', 'reason'}
            or request['schema_version'] != 1 or request['kind'] != 'task-attribution-reconciliation-request-v1'
            or any(not isinstance(request[k], str) or not request[k].strip() for k in ('source', 'reason'))):
        refuse('invalid explicit attribution reconciliation request')
    current = view(st);binding = current['reconciliation_binding']
    if binding is None or request['binding'] != binding:refuse('stale or foreign attribution reconciliation binding')
    paths = request['paths']
    if not isinstance(paths, list) or not paths or paths != sorted(set(paths)) or not set(paths) <= set(current['classification']['ambiguous']):
        refuse('reconciliation requires exact observed overlapping paths')
    capture = T.observe(st, retain_content=True)
    rows = T._map(capture)
    if _digest(capture['files']) != binding['capture_digest'] or any(_unsupported(rows.get(p)) for p in paths):
        refuse('changed or unavailable attribution source cannot be reconciled')
    data = {k: binding[k] for k in ('task_hash', 'snapshot_sha', 'contribution_shas', 'prior_reconciliation_shas')}
    data.update(schema_version=1, kind='task-attribution-reconciliation-v1', binding=T._binding(st),
                paths=paths, source=request['source'], reason=request['reason'],
                capture_ref=T._save(st, capture, capture['kind']))
    ref = T._save(st, data, data['kind'])
    st['task_attribution'].setdefault('reconciliations', []).append(ref)
    # Independent replay must accept exactly this immutable observation before publication.
    result = T.classify_changes(st)
    al_store.save(sid, st)
    print(json.dumps({'reconciliation': ref, 'classification': result}, sort_keys=True))
