"""Reuse immutable publication provenance only through a live launcher anchor."""
import re
from .registry import canonical, digest, namespace, read_json
from .validator_origin import capture, safe, verify_snapshot
from .validator_boot import launcher


def existing_anchor(state_root, snapshot, receipt, expected, interpreter):
    for path in sorted((namespace(state_root)/'cli').glob('launcher-*.json')):
        _, metadata = capture(path)
        if metadata['mode'] != 0o600:
            raise ValueError('CLI owner metadata mode changed')
        row = read_json(path)
        if row.get('snapshot') != str(snapshot):continue
        saved, binding = row.get('receipt'), row.get('validator_binding')
        if not isinstance(saved, dict) or not isinstance(binding, dict):continue
        historical = saved.get('source_commit_if_available')
        if historical is not None and (not isinstance(historical, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', historical)):continue
        if type(saved.get('dirty')) is not bool:continue
        omit = {'source_commit_if_available', 'dirty'}
        if {k:v for k,v in saved.items() if k not in omit} != {k:v for k,v in receipt.items() if k not in omit}:continue
        protected = dict(expected, receipt_sha256=digest(canonical(saved)+b'\n'))
        if binding != protected:continue
        destination = safe(row.get('destination', ''))
        if path.name != 'launcher-'+digest(str(destination).encode())+'.json':continue
        request = {'mode':'snapshot', 'root':str(snapshot), 'expected':protected, 'interpreter':interpreter}
        raw, actual = capture(destination)
        wanted = launcher(request)
        if raw != wanted or actual != {'sha256':digest(wanted), 'mode':0o755} or row.get('state') != actual:continue
        verify_snapshot(snapshot, protected)
        return saved, protected
    raise ValueError('CLI snapshot lacks an intact managed launcher anchor')
