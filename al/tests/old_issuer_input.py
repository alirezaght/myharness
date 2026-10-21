"""Portable source-only copies of independently retained old issuer inputs."""
import base64
import hashlib
import json
from pathlib import Path
import zlib

FIXTURE=Path(__file__).with_name('fixtures')/'issuing-runtime-v1.json'
SHA256='01dd98eb2bebd8f9b979d47ef393b27c2ee7c346682987c04054c414bda59245'


def materialize(home,platform):
    raw=FIXTURE.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=SHA256:raise ValueError('old issuer fixture receipt changed')
    value=json.loads(raw)
    if value['kind']!='source-only-old-issuer-fixture' or value['schema_version']!=1:raise ValueError('unknown old issuer fixture')
    rows=value['platforms'][platform]['members'];root=Path(home)/('.'+platform)/'skills/al';expected={}
    for logical,row in rows.items():
        prefix,name=logical.split('/',1);relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts or prefix not in {'skill','roles'}:raise ValueError('unsafe old fixture member')
        target=root/relative if prefix=='skill' else root.parent.parent/'agents'/relative
        body=zlib.decompress(base64.b64decode(row['data'],validate=True))
        if len(body)>2*1024*1024 or hashlib.sha256(body).hexdigest()!=row['sha256']:raise ValueError('old issuer fixture bytes changed')
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(body);target.chmod(row['mode'])
        expected[str(target)]={key:row[key] for key in ('sha256','mode')}
    verify(home,platform,expected)
    return root,expected


def verify(home,platform,expected):
    base=Path(home)/('.'+platform)
    actual={str(p) for p in base.rglob('*') if p.is_file()}
    if actual!=set(expected) or any(p.is_symlink() for p in base.rglob('*')):raise ValueError('old fixture membership changed')
    for name,row in expected.items():
        path=Path(name)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256'] or path.stat().st_mode&0o777!=row['mode']:
            raise ValueError('old fixture origin/resource changed')
